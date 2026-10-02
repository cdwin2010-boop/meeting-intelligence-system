"""
일 1회 안내 메일을 발송함에 쌓는다(실제 발송은 app/jobs/send_mail.py).
운영 시 OS 스케줄러로 하루 1회 실행한다:  python -m app.jobs.daily_mail

- 수신자별 하루 1통(dedupe_key 'daily:{accountId}:{KST 날짜}'). 같은 날 다시 돌려도 새로 쌓이지 않는다.
- manager·executive: 확정 대기 회의록 수 + 보완 필요 업무 수(고객사 전체).
- 담당자: 본인 담당 pending 업무가 있는 회의록 중 아직 열어 보지 않은(meeting_views 없음) 회의록 수.
- 확정·종결·삭제된 것과 이미 열어 본 것은 세지 않는다. 합계가 0 이면 쌓지 않는다.
- 본문은 건수와 로그인 링크만(제목 나열 금지). 출력도 건수만.
"""
import argparse
import sys
from collections import defaultdict
from collections.abc import Sequence
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import distinct, exists, func, select
from sqlalchemy.orm import Session, sessionmaker

from app.auth.access import VIEW_ALL_RANKS
from app.config import settings
from app.models import Account, ActionItem, Meeting, MeetingView
from app.services.mail import SUBJECT_PREFIX, login_link, queue_mail

# 이 상태의 회의록에 속한 업무는 세지 않는다
EXCLUDED_MEETING_STATUSES = ("processing", "failed", "no_content")


def _counts(session: Session) -> tuple[dict[int, int], dict[int, int], dict[int, int]]:
    """(고객사별 확정 대기 수, 고객사별 보완 필요 수, 계정별 미열람 담당 회의록 수). 쿼리 3번."""
    awaiting = dict(
        session.execute(
            select(Meeting.tenant_id, func.count(Meeting.id))
            .where(Meeting.status == "awaiting_confirmation", Meeting.on_hold.is_(False))
            .group_by(Meeting.tenant_id)
        ).all()
    )
    needs = dict(
        session.execute(
            select(ActionItem.tenant_id, func.count(ActionItem.id))
            .join(Meeting, Meeting.id == ActionItem.meeting_id)
            .where(
                ActionItem.status == "pending",
                ActionItem.needs_supplement,
                Meeting.status.not_in(EXCLUDED_MEETING_STATUSES),
                Meeting.on_hold.is_(False),
            )
            .group_by(ActionItem.tenant_id)
        ).all()
    )
    unviewed = dict(
        session.execute(
            select(ActionItem.assignee_id, func.count(distinct(ActionItem.meeting_id)))
            .join(Meeting, Meeting.id == ActionItem.meeting_id)
            .where(
                ActionItem.assignee_id.is_not(None),
                ActionItem.status == "pending",
                Meeting.tenant_id == ActionItem.tenant_id,
                Meeting.status.not_in(EXCLUDED_MEETING_STATUSES),
                Meeting.on_hold.is_(False),
                ~exists().where(
                    MeetingView.meeting_id == ActionItem.meeting_id,
                    MeetingView.account_id == ActionItem.assignee_id,
                ),
            )
            .group_by(ActionItem.assignee_id)
        ).all()
    )
    return awaiting, needs, unviewed


def _body(lines: list[str]) -> str:
    return "오늘 확인할 일이 있습니다.\n\n" + "\n".join(lines) + f"\n\n로그인해서 확인해 주세요.\n{login_link()}\n"


def run_daily_mail(session: Session, now: datetime | None = None) -> dict:
    """오늘(KST) 안내 메일을 쌓고 건수를 돌려준다."""
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    today = now.astimezone(ZoneInfo(settings.app_timezone)).date()
    awaiting, needs, unviewed = _counts(session)

    accounts = session.scalars(select(Account).where(Account.is_active.is_(True)).order_by(Account.id)).all()
    result = {"created": 0, "alreadyQueued": 0}
    per_account: dict[int, list[str]] = defaultdict(list)
    for account in accounts:
        if not (account.email or "").strip():
            continue
        if account.rank in VIEW_ALL_RANKS:
            a, n = awaiting.get(account.tenant_id, 0), needs.get(account.tenant_id, 0)
            if a + n > 0:
                per_account[account.id].append(f"- 확정 대기 회의록 {a}건, 보완 필요 업무 {n}건")
        u = unviewed.get(account.id, 0)
        if u > 0:
            per_account[account.id].append(f"- 아직 열어 보지 않은 담당 회의록 {u}건")

    by_id = {a.id: a for a in accounts}
    for account_id, lines in per_account.items():
        created = queue_mail(
            session,
            account=by_id[account_id],
            kind="daily_reminder",
            subject=f"{SUBJECT_PREFIX} 오늘 확인할 일이 있습니다",
            body=_body(lines),
            dedupe_key=f"daily:{account_id}:{today.isoformat()}",
        )
        result["created" if created else "alreadyQueued"] += 1
    session.commit()
    return result


def main(argv: Sequence[str] | None = None, session_factory: sessionmaker | None = None) -> int:
    argparse.ArgumentParser(description="일 1회 안내 메일을 발송함에 쌓기").parse_args(argv)
    if session_factory is None:
        from app.db import SessionLocal

        session_factory = SessionLocal
    with session_factory() as session:
        result = run_daily_mail(session)
    print(f"created={result['created']} alreadyQueued={result['alreadyQueued']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
