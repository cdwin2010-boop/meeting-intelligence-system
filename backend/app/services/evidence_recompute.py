"""근거 위치 재계산(작업 B-1c-1). 저장된 전사문과 업무별 근거 인용문·시각으로 근거 시각을 다시 정한다(AI·외부 호출 없음, 전사문 불변).

규칙(업무 1건)
- 인용문이 있고 전사문에서 찾으면 그 구간 시작 시각(정확). 저장 시각과 같으면 변경 없음(단, 이전에 근사로 표시돼 있었다면 근사 표시만 해제).
- 인용문이 없거나 못 찾았고 저장된 시각이 있으면 그 시각 이전에서 가장 가까운 발화(없으면 가장 이른 발화)로 대체하고 "근사 위치"로 표시.
- 시각도 인용문도 없으면(또는 전사문 구간이 없으면) 변경하지 않는다(미변경).
- 대상은 진행 중(pending) 업무만. 확정·종결된 업무와 수기 등록 업무는 대상 밖이며 확정 제외 건수로만 센다.
근사 표시는 새 칸 없이 업무 이력(구분 "근거 위치 재계산", payload.after.approx)에서 읽는다: 최신 이력이 근사이고 그 시각이 지금 값과 같을 때만 근사.
"""
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import ActionItem, Event, Transcript
from app.models.action_item import MANUAL_ORIGIN
from app.models.item_conditions import live_item, not_deleted_item
from app.pipeline.evidence_time import Segment, locate, segments_of
from app.services.history import KIND_EVIDENCE_RECOMPUTE, record_change

EXACT = "exact"
APPROX = "approx"
UNCHANGED = "unchanged"


@dataclass(frozen=True)
class ItemResult:
    item: ActionItem
    kind: str  # exact | approx | unchanged
    new_sec: float | None
    old_sec: float | None
    flag_cleared: bool = False  # 근사였던 표시만 해제하는 경우(시각은 같음)


@dataclass(frozen=True)
class RecomputeSummary:
    targets: int  # 재계산 대상(진행 중 업무)
    changed: int  # 정확한 위치로 바뀐 건수
    approx: int  # 근사 위치로 대체한 건수
    unchanged: int  # 미변경(시각·인용문 없음, 이미 같은 값 등)
    excluded: int  # 확정·종결 업무(대상 제외)


def previous_start(segments: list[Segment], sec: float) -> float | None:
    """sec 이전(같은 시각 포함)에서 가장 가까운 발화 시작 시각, 없으면 가장 이른 발화 시작 시각. 구간이 없으면 None."""
    if not segments:
        return None
    starts = [s.start_sec for s in segments]
    before = [s for s in starts if s <= sec]
    return max(before) if before else min(starts)


def approx_evidence_items(session: Session, item_rows: Any) -> dict[int, bool]:
    """회의록 상세용: 업무 id → 근사 위치 여부. item_rows 는 row.ActionItem 을 가진 조회 결과."""
    current = {row.ActionItem.id: row.ActionItem.evidence_start_sec for row in item_rows}
    if not current:
        return {}
    events = session.execute(
        select(Event.entity_id, Event.payload)
        .where(Event.entity_type == "action_item", Event.event_type == KIND_EVIDENCE_RECOMPUTE, Event.entity_id.in_(list(current)))
        .order_by(Event.id)
    ).all()
    latest: dict[int, dict] = {}
    for entity_id, payload in events:
        after = (payload or {}).get("after")
        if isinstance(after, dict):
            latest[entity_id] = after
    return {
        item_id: bool(after.get("approx")) and after.get("evidenceStartSec") == current.get(item_id)
        for item_id, after in latest.items()
    }


def _previously_approx(session: Session, item_id: int, sec: float | None) -> bool:
    return approx_evidence_items_for(session, item_id, sec)


def approx_evidence_items_for(session: Session, item_id: int, sec: float | None) -> bool:
    payload = session.scalars(
        select(Event.payload)
        .where(Event.entity_type == "action_item", Event.event_type == KIND_EVIDENCE_RECOMPUTE, Event.entity_id == item_id)
        .order_by(Event.id.desc())
        .limit(1)
    ).first()
    after = (payload or {}).get("after") if isinstance(payload, dict) else None
    return isinstance(after, dict) and bool(after.get("approx")) and after.get("evidenceStartSec") == sec


def compute(session: Session, meeting_id: int) -> tuple[list[ItemResult], int]:
    """(진행 중 업무별 결과, 확정·종결로 제외한 건수). 저장하지 않는다."""
    transcript = session.scalar(select(Transcript).where(Transcript.meeting_id == meeting_id))
    segments = segments_of(transcript.full_text, transcript.segments) if transcript is not None else []
    items = session.scalars(
        select(ActionItem)
        .where(ActionItem.meeting_id == meeting_id, not_deleted_item(), live_item(), ActionItem.extract_model != MANUAL_ORIGIN)
        .order_by(ActionItem.id)
    ).all()
    results: list[ItemResult] = []
    excluded = 0
    for item in items:
        if item.status != "pending":
            excluded += 1
            continue
        old = item.evidence_start_sec
        quote = (item.evidence_quote or "").strip()
        was_approx = _previously_approx(session, item.id, old)
        found = locate(segments, quote, old) if quote and segments else None
        if found is not None:
            if found == old:
                results.append(ItemResult(item, UNCHANGED, old, old, flag_cleared=was_approx))
            else:
                results.append(ItemResult(item, EXACT, found, old))
            continue
        if old is not None and segments:
            results.append(ItemResult(item, APPROX, previous_start(segments, old), old))
            continue
        results.append(ItemResult(item, UNCHANGED, old, old))
    return results, excluded


def summarize(results: list[ItemResult], excluded: int) -> RecomputeSummary:
    changed = sum(1 for r in results if r.kind == EXACT)
    approx = sum(1 for r in results if r.kind == APPROX)
    return RecomputeSummary(
        targets=len(results), changed=changed, approx=approx, unchanged=len(results) - changed - approx, excluded=excluded,
    )


def apply(session: Session, meeting, actor_id: int, reason: str, results: list[ItemResult], summary: RecomputeSummary) -> None:
    """저장 + 변경 이력(커밋은 호출부). 업무마다 전후 시각·근사 여부, 회의록에는 건수 요약."""
    for r in results:
        if r.kind == UNCHANGED and not r.flag_cleared:
            continue
        r.item.evidence_start_sec = r.new_sec
        record_change(
            session, tenant_id=r.item.tenant_id, target_type="action_item", target_id=r.item.id, kind=KIND_EVIDENCE_RECOMPUTE,
            actor_id=actor_id, before={"evidenceStartSec": r.old_sec},
            after={"evidenceStartSec": r.new_sec, "approx": r.kind == APPROX}, extra={"reason": reason, "meetingId": meeting.id},
        )
    record_change(
        session, tenant_id=meeting.tenant_id, target_type="meeting", target_id=meeting.id, kind=KIND_EVIDENCE_RECOMPUTE,
        actor_id=actor_id, before={}, after={
            "targets": summary.targets, "changed": summary.changed, "approx": summary.approx,
            "unchanged": summary.unchanged, "excluded": summary.excluded,
        },
        extra={"reason": reason},
    )
