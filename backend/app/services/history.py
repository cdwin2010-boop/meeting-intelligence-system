"""공통 변경 이력. 별도 표를 만들지 않고 기존 사건 원장(events)을 그대로 쓴다.
공통 구조 = 대상 종류(entity_type) · 대상 ID(entity_id) · 구분(event_type) · 변경자(actor_account_id) · 시각(created_at)
            · 변경 전·후(payload["before"]·payload["after"], 바뀐 칸만).
이력으로 보이는 구분은 HISTORY_KINDS 에만 등록한다. 업무 갱신·감사 로그도 같은 구조에 구분만 더해 쓴다(기능마다 표를 따로 만들지 않음)."""
import logging
from typing import Any

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import Account, Event, Meeting, MeetingView, append_event
from app.models.common import utcnow

log = logging.getLogger("app.history")

# 사건 종류 → 화면에 보이는 구분
KIND_MINUTES_OVERRIDE = "minutes.overridden"
KIND_PARTICIPANTS_OVERRIDE = "participants.overridden"
KIND_ITEM_UPLOAD_UPDATE = "item.upload_updated"
KIND_ITEM_MANUAL_ADD = "item.manual_added"
# 업무 수정 API(PATCH /api/action-items/{id})·재개로 비워진 기한·화자 매핑 자동 채움이 같은 사건 종류(item.updated)를 쓴다
KIND_ITEM_UPDATE = "item.updated"
# 실패한 회의록 재처리(실행자·시각·이전 오류 코드)
KIND_MEETING_REPROCESS = "meeting.reprocessed"
# 기존 업무의 빈 근거 시각을 전사문과 대조해 채운 보정(app/jobs/backfill_evidence_time.py)
KIND_EVIDENCE_BACKFILL = "evidence.backfilled"
HISTORY_KINDS: dict[str, str] = {
    KIND_MINUTES_OVERRIDE: "직권 수정",
    KIND_PARTICIPANTS_OVERRIDE: "직권 수정",
    KIND_ITEM_UPLOAD_UPDATE: "업무 갱신",
    KIND_ITEM_MANUAL_ADD: "직권 등록",
    KIND_ITEM_UPDATE: "업무 수정",
    KIND_MEETING_REPROCESS: "재처리",
    KIND_EVIDENCE_BACKFILL: "근거 시각 보정",
}


def record_change(
    session: Session,
    *,
    tenant_id: int,
    target_type: str,
    target_id: int,
    kind: str,
    actor_id: int | None,
    before: dict[str, Any],
    after: dict[str, Any],
    extra: dict[str, Any] | None = None,
) -> Event:
    """변경 이력 1건을 남긴다(커밋은 호출부: 변경과 같은 트랜잭션). kind 는 HISTORY_KINDS 에 있어야 한다."""
    if kind not in HISTORY_KINDS:
        raise ValueError(f"unknown history kind: {kind}")
    return append_event(
        session,
        tenant_id=tenant_id,
        entity_type=target_type,
        entity_id=target_id,
        event_type=kind,
        actor_account_id=actor_id,
        # extra: 같은 업로드 묶음(batchId)·출처(source)·열람 영향 같은 덧붙임. before/after 와 같은 층에 둔다
        payload={"before": before, "after": after, **(extra or {})},
    )


def history_fields(event: Event) -> dict[str, Any]:
    """사건 1건 → 이력 응답 칸. 변경 전 값이 없는 옛 사건(payload 에 before 가 없음)은 before 를 비우고 before_missing 으로 구분한다.
    열람 영향(viewImpact)은 참석자 변경 사건에만 있다."""
    payload = event.payload or {}
    has_before = isinstance(payload.get("before"), dict)
    impact = payload.get("viewImpact")
    return {
        "before": payload["before"] if has_before else {},
        "after": payload.get("after") if isinstance(payload.get("after"), dict) else {},
        "before_missing": not has_before,
        "batch_id": payload.get("batchId"),
        "view_impact": impact if isinstance(impact, list) else None,
    }


# ---------------- 열람 기록 ----------------
def record_meeting_view(session: Session, account: Account, meeting: Meeting) -> bool:
    """회의록 상세 조회가 성공했을 때 본인의 열람을 기록한다(호출부는 열람 판정을 통과한 경우에만 부른다).
    첫 열람 시각은 유지하고 마지막 열람 시각만 갱신하므로 같은 사용자의 반복 열람이 행을 늘리지 않는다.
    기록이 실패해도 조회를 막지 않는다(예외를 삼키고 False). 커밋까지 한다.
    메일·자동 확정 대상 선정(app/jobs/daily_mail.py)과 할 일 \"자동 확정됨(미열람)\"(app/api/me.py)이 이 표를 읽는다."""
    try:
        now = utcnow()
        view = session.get(MeetingView, (meeting.id, account.id))
        if view is None:
            session.add(
                MeetingView(meeting_id=meeting.id, account_id=account.id, tenant_id=meeting.tenant_id,
                            first_viewed_at=now, last_viewed_at=now)
            )
            try:
                session.commit()
                return True
            except IntegrityError:
                # 같은 계정이 동시에 처음 열어 다른 요청이 먼저 만든 경우: 마지막 열람만 갱신
                session.rollback()
                view = session.get(MeetingView, (meeting.id, account.id))
                if view is None:
                    raise
        view.last_viewed_at = now
        session.commit()
        return True
    except Exception as exc:  # noqa: BLE001 — 기록 실패로 조회가 실패하면 안 된다
        session.rollback()
        log.warning("meeting view not recorded: %s", type(exc).__name__)
        return False
