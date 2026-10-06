"""기존 업무의 빈 근거 시각 보정 도구(Gemini 호출 없음). 이미 저장된 전사문과 근거 인용문을 서버가 대조해 채운다.
실행(backend/ 에서):  python -m app.jobs.backfill_evidence_time [--meeting-id N] [--apply]
- 기본은 dry-run: 변경 예정 목록만 출력하고 아무것도 저장하지 않는다. --apply 가 있을 때만 저장한다.
- 대상: 근거 시각이 비어 있고 근거 인용문이 있는 업무(수기 등록 업무 제외). 삭제된 업무와 삭제·보류 단계 회의록은 제외한다.
  확정·종결 업무도 근거 시각 칸만 채운다(다른 칸은 건드리지 않는다). 이미 값이 있는 업무는 건드리지 않는다.
- 저장할 때 공통 변경 이력(구분 "근거 시각 보정", 실행자 시스템, 변경 전 None · 후 값)에 남긴다.
- 출력: 업무 id, 회의록 id, 새 시각(초) 또는 "대조 실패", 인용문 앞 30자. 키·.env 값은 출력하지 않는다.
- 운영 DB 에 적용하기 전에는 백업 후 사용자가 직접 실행한다.
"""
import argparse
import sys
from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.models import ActionItem, Meeting, Transcript
from app.models.action_item import MANUAL_ORIGIN
from app.pipeline.evidence_time import locate, segments_of
from app.services.history import KIND_EVIDENCE_BACKFILL, record_change

FAILED = "대조 실패"
QUOTE_PREVIEW = 30


def run_backfill(session: Session, *, meeting_id: int | None = None, apply: bool = False) -> list[dict]:
    """대상 업무마다 {itemId, meetingId, sec(또는 None), quote} 를 돌려준다. apply 이고 시각을 찾은 업무만 저장한다(커밋은 호출부)."""
    stmt = (
        select(ActionItem, Meeting)
        .join(Meeting, Meeting.id == ActionItem.meeting_id)
        .where(
            ActionItem.evidence_start_sec.is_(None),
            ActionItem.evidence_quote.is_not(None),
            ActionItem.evidence_quote != "",
            ActionItem.status != "deleted",
            ActionItem.extract_model != MANUAL_ORIGIN,
            ~Meeting.deleted,
            ~Meeting.on_hold,
        )
        .order_by(ActionItem.id)
    )
    if meeting_id is not None:
        stmt = stmt.where(ActionItem.meeting_id == meeting_id)
    cache: dict[int, list] = {}
    rows: list[dict] = []
    for item, meeting in session.execute(stmt).all():
        if meeting.id not in cache:
            transcript = session.scalar(select(Transcript).where(Transcript.meeting_id == meeting.id))
            cache[meeting.id] = segments_of(transcript.full_text, transcript.segments) if transcript is not None else []
        seconds = locate(cache[meeting.id], item.evidence_quote or "")
        rows.append({"itemId": item.id, "meetingId": meeting.id, "sec": seconds, "quote": (item.evidence_quote or "")[:QUOTE_PREVIEW]})
        if apply and seconds is not None:
            item.evidence_start_sec = seconds  # 근거 시각 칸만 채운다
            record_change(
                session, tenant_id=item.tenant_id, target_type="action_item", target_id=item.id, kind=KIND_EVIDENCE_BACKFILL,
                actor_id=None, before={"evidenceStartSec": None}, after={"evidenceStartSec": seconds},
            )
    return rows


def main(argv: Sequence[str] | None = None, session_factory: sessionmaker | None = None) -> int:
    parser = argparse.ArgumentParser(description="기존 업무의 빈 근거 시각을 저장된 전사문과 대조해 채운다(기본 dry-run)")
    parser.add_argument("--apply", action="store_true", help="실제로 저장한다(없으면 변경 예정 목록만 출력)")
    parser.add_argument("--meeting-id", type=int, default=None, help="이 회의록의 업무만 대상으로 한다(없으면 전체)")
    args = parser.parse_args(argv)

    if session_factory is None:
        from app.db import SessionLocal

        session_factory = SessionLocal
    with session_factory() as session:
        rows = run_backfill(session, meeting_id=args.meeting_id, apply=args.apply)
        if args.apply:
            session.commit()
        else:
            session.rollback()

    prefix = "[적용] " if args.apply else "[dry-run] "
    for row in rows:
        shown = f"{row['sec']:g}초" if row["sec"] is not None else FAILED
        print(f"{prefix}item={row['itemId']} meeting={row['meetingId']} {shown} \"{row['quote']}\"")
    found = sum(1 for r in rows if r["sec"] is not None)
    print(f"{prefix}대상 {len(rows)}건, 시각 찾음 {found}건, 대조 실패 {len(rows) - found}건" + ("" if args.apply else " (저장하지 않음, --apply 로 저장)"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
