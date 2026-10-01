"""
자동 확정(백스톱) 작업. 사람이 확정하지 않은 회의록·업무를 기한이 지나면 시스템이 확정한다(부록 C-4).
운영 시 OS 스케줄러(작업 스케줄러·cron)로 하루 1회 이상 실행한다:  python -m app.jobs.auto_confirm [--dry-run]

규칙
- 회의록: awaiting_confirmation 이고 now >= auto_confirm_at → confirmed(confirm_kind=period_elapsed, 확정자 없음).
- 업무: pending 이고 보완 필요가 아니며(업무명·담당자·기한/미확정 완비), 소속 회의록이 processing·failed·no_content 가 아닐 때만.
  · 마감일이 있으면 (APP_TIMEZONE 기준 오늘 >= 마감일) 또는 (now >= 회의록 auto_confirm_at) 중 먼저 오는 쪽.
    마감일이 먼저(같은 날 포함)면 due_reached, 아니면 period_elapsed.
  · 기한 '미확정'이면 auto_confirm_at 만 본다(period_elapsed).
  · 보완 필요·deleted·closed 업무는 절대 자동 확정하지 않는다.
- auto_confirm_at 은 회의록 생성 때 저장된 값을 쓴다(고객사 auto_confirm_days 를 지금 다시 계산하지 않음).
- 멱등·경쟁 안전: UPDATE 에 상태 조건을 걸어 이미 확정된 것은 건드리지 않고(사건 중복 없음), 동시 실행·수동 확정과 겹쳐도 한쪽만 성공.
- 건별 트랜잭션: 한 건이 실패해도 나머지는 처리한다. 실패 건은 종류와 id 만 돌려준다(예외 내용은 남기지 않음).
- 확정 안내(시스템 안)는 같은 건별 트랜잭션에서 만든다(수행자 없음 → 관리자 이상 관련자 전원). 메일은 보내지 않는다.
"""
import argparse
import logging
import sys
from collections.abc import Sequence
from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import select, update
from sqlalchemy.orm import Session, sessionmaker

from app.config import settings
from app.models import ActionItem, Meeting, append_event
from app.services.notices import create_confirm_notices

log = logging.getLogger("app.jobs.auto_confirm")

# 이 상태의 회의록에 속한 업무는 자동 확정하지 않는다(처리 중·실패·말소리 없음)
EXCLUDED_MEETING_STATUSES = ("processing", "failed", "no_content")


def _local_date(moment: datetime) -> date:
    """UTC 시각 → APP_TIMEZONE(기본 KST) 기준 날짜."""
    return moment.astimezone(ZoneInfo(settings.app_timezone)).date()


def item_auto_confirm_kind(
    *, due_date: date | None, due_undetermined: bool, auto_confirm_at: datetime | None, now: datetime
) -> str | None:
    """업무(보완 완비 전제)가 지금 자동 확정 대상이면 confirm_kind, 아니면 None."""
    today = _local_date(now)
    period_reached = auto_confirm_at is not None and now >= auto_confirm_at
    if due_date is None:
        # 기한 '미확정'(또는 빈칸)은 회의록 기한만 본다. 빈칸은 보완 필요라 호출 전에 걸러진다
        return "period_elapsed" if period_reached else None
    due_reached = today >= due_date
    if not (due_reached or period_reached):
        return None
    if auto_confirm_at is None:
        return "due_reached"
    # 먼저 오는 쪽(현지 날짜 기준). 같은 날이면 마감일 우선
    return "due_reached" if due_date <= _local_date(auto_confirm_at) else "period_elapsed"


def _confirm_meeting(session: Session, meeting_id: int, now: datetime) -> bool:
    changed = session.execute(
        update(Meeting)
        .where(Meeting.id == meeting_id, Meeting.status == "awaiting_confirmation")
        .values(status="confirmed", confirm_kind="period_elapsed", confirmed_by=None, confirmed_at=now)
    ).rowcount
    if changed != 1:
        return False  # 그 사이 수동 확정 등으로 상태가 바뀜
    meeting = session.get(Meeting, meeting_id)
    append_event(
        session, tenant_id=meeting.tenant_id, entity_type="meeting", entity_id=meeting_id,
        event_type="meeting.auto_confirmed", payload={"confirmKind": "period_elapsed"},
    )
    create_confirm_notices(
        session, meeting=meeting, entity_type="meeting", entity_id=meeting_id,
        confirm_kind="period_elapsed", title=meeting.title, actor_id=None,
    )
    return True


def _confirm_item(session: Session, item_id: int, kind: str, now: datetime) -> bool:
    changed = session.execute(
        update(ActionItem)
        .where(
            ActionItem.id == item_id,
            ActionItem.status == "pending",
            ~ActionItem.needs_supplement,  # 그 사이 빈칸이 되었으면 확정하지 않는다
        )
        .values(status="confirmed", confirm_kind=kind, confirmed_by=None, confirmed_at=now)
        .execution_options(synchronize_session=False)
    ).rowcount
    if changed != 1:
        return False
    item_tenant_id, meeting_id, title = session.execute(
        select(ActionItem.tenant_id, ActionItem.meeting_id, ActionItem.title).where(ActionItem.id == item_id)
    ).one()
    append_event(
        session, tenant_id=item_tenant_id, entity_type="action_item", entity_id=item_id,
        event_type="item.auto_confirmed", payload={"confirmKind": kind},
    )
    create_confirm_notices(
        session, meeting=session.get(Meeting, meeting_id), entity_type="action_item", entity_id=item_id,
        confirm_kind=kind, title=title, actor_id=None,
    )
    return True


def _plan(session: Session, now: datetime) -> tuple[list[int], list[tuple[int, str]], int]:
    """대상 계산(변경 없음): (회의록 id, [(업무 id, confirm_kind)], 보완 필요로 건너뛴 업무 수)."""
    meeting_ids = list(
        session.scalars(
            select(Meeting.id)
            .where(
                Meeting.status == "awaiting_confirmation",
                Meeting.auto_confirm_at.is_not(None),
                Meeting.auto_confirm_at <= now,
            )
            .order_by(Meeting.id)
        )
    )

    rows = session.execute(
        select(
            ActionItem.id,
            ActionItem.due_date,
            ActionItem.due_undetermined,
            ActionItem.needs_supplement.label("incomplete"),
            Meeting.auto_confirm_at,
        )
        .join(Meeting, Meeting.id == ActionItem.meeting_id)
        .where(ActionItem.status == "pending", Meeting.status.not_in(EXCLUDED_MEETING_STATUSES))
        .order_by(ActionItem.id)
    ).all()

    items: list[tuple[int, str]] = []
    skipped_incomplete = 0
    for row in rows:
        auto_at = row.auto_confirm_at
        if auto_at is not None and auto_at.tzinfo is None:  # 방어: UTC 로 저장된 값
            auto_at = auto_at.replace(tzinfo=timezone.utc)
        kind = item_auto_confirm_kind(
            due_date=row.due_date, due_undetermined=row.due_undetermined, auto_confirm_at=auto_at, now=now
        )
        if row.incomplete:
            # 시점이 됐는데 보완 필요라 확정하지 않는 업무(빈 기한은 회의록 기한 기준으로 셈)
            if kind is not None or (auto_at is not None and now >= auto_at):
                skipped_incomplete += 1
            continue
        if kind is not None:
            items.append((row.id, kind))
    return meeting_ids, items, skipped_incomplete


def run_auto_confirm(session: Session, now: datetime | None = None, *, dry_run: bool = False) -> dict:
    """자동 확정을 1회 실행하고 건수를 돌려준다. dry_run 이면 대상 건수만 세고 아무것도 바꾸지 않는다."""
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        raise ValueError("now 는 시간대가 있는 시각이어야 합니다")
    now = now.astimezone(timezone.utc)

    meeting_ids, items, skipped_incomplete = _plan(session, now)
    session.rollback()  # 계획 단계의 읽기 트랜잭션을 닫는다(아래는 건별 트랜잭션)

    result = {"meetingsConfirmed": 0, "itemsConfirmed": 0, "itemsSkippedIncomplete": skipped_incomplete, "failed": []}
    if dry_run:
        result["meetingsConfirmed"], result["itemsConfirmed"] = len(meeting_ids), len(items)
        return result

    for meeting_id in meeting_ids:
        try:
            if _confirm_meeting(session, meeting_id, now):
                result["meetingsConfirmed"] += 1
            session.commit()
        except Exception as exc:  # noqa: BLE001 — 한 건 실패가 나머지를 막지 않게
            session.rollback()
            log.warning("auto-confirm meeting %s failed (%s)", meeting_id, type(exc).__name__)
            result["failed"].append({"kind": "meeting", "id": meeting_id})

    for item_id, kind in items:
        try:
            if _confirm_item(session, item_id, kind, now):
                result["itemsConfirmed"] += 1
            session.commit()
        except Exception as exc:  # noqa: BLE001
            session.rollback()
            log.warning("auto-confirm item %s failed (%s)", item_id, type(exc).__name__)
            result["failed"].append({"kind": "item", "id": item_id})
    return result


def main(argv: Sequence[str] | None = None, session_factory: sessionmaker | None = None) -> int:
    parser = argparse.ArgumentParser(description="자동 확정(백스톱) 1회 실행")
    parser.add_argument("--dry-run", action="store_true", help="대상 건수만 출력하고 아무것도 바꾸지 않음")
    args = parser.parse_args(argv)

    if session_factory is None:
        from app.db import SessionLocal

        session_factory = SessionLocal
    with session_factory() as session:
        result = run_auto_confirm(session, dry_run=args.dry_run)

    # 출력은 건수만(실패는 id 만)
    prefix = "[dry-run] " if args.dry_run else ""
    print(
        f"{prefix}meetingsConfirmed={result['meetingsConfirmed']} itemsConfirmed={result['itemsConfirmed']} "
        f"itemsSkippedIncomplete={result['itemsSkippedIncomplete']} failed={len(result['failed'])}"
    )
    for failure in result["failed"]:
        print(f"failed {failure['kind']} id={failure['id']}")
    return 1 if result["failed"] else 0


if __name__ == "__main__":
    sys.exit(main())
