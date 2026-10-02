"""메일 발송함(mail_outbox)에 쌓기. 실제 발송은 app/jobs/send_mail.py 가 따로 한다.

- 본문에는 회의록 제목·건수·로그인 링크(APP_BASE_URL)만 넣는다. 업무 내용·전사문·담당자 이름은 넣지 않는다.
- dedupe_key 로 같은 메일은 한 번만 쌓인다(재처리·재실행해도 1회).
- 수신자는 같은 고객사의 활성 계정 중 이메일이 있는 계정만.
"""
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.models import Account, ActionItem, MailOutbox, Meeting, MeetingParticipant, SourceDocument
from app.models.common import INACTIVE_ITEM_STATUSES

SUBJECT_PREFIX = "[회의록]"


def login_link() -> str:
    return settings.app_base_url.rstrip("/") or "/"


def mailable_accounts(session: Session, tenant_id: int, account_ids) -> list[Account]:
    """같은 고객사·활성·이메일 있음. account_ids 는 id 목록 또는 id 서브쿼리."""
    accounts = session.scalars(
        select(Account)
        .where(Account.tenant_id == tenant_id, Account.is_active.is_(True), Account.id.in_(account_ids))
        .order_by(Account.id)
    ).all()
    return [a for a in accounts if (a.email or "").strip()]


def queue_mail(session: Session, *, account: Account, kind: str, subject: str, body: str, dedupe_key: str) -> bool:
    """dedupe_key 가 없을 때만 쌓는다(커밋은 호출부 트랜잭션). 쌓았으면 True."""
    if session.scalar(select(MailOutbox.id).where(MailOutbox.dedupe_key == dedupe_key)) is not None:
        return False
    session.add(
        MailOutbox(
            tenant_id=account.tenant_id,
            account_id=account.id,
            to_email=account.email.strip(),
            kind=kind,
            subject=subject[:300],
            body=body,
            status="queued",
            dedupe_key=dedupe_key,
        )
    )
    session.flush()
    return True


def queue_immediate_new_minutes(session: Session, meeting: Meeting) -> int:
    """처리 완료로 회의록이 확정 대기/확정이 된 순간의 즉시 메일(같은 트랜잭션에서 호출).
    수신자 = 참석자 + 업무 담당자 + 등록자 중 메일 가능한 계정, 등록자 본인 제외, 중복 제거."""
    if meeting.status not in ("awaiting_confirmation", "confirmed") or meeting.on_hold or meeting.ended or meeting.deleted:
        return 0
    registrant_id = session.scalar(
        select(SourceDocument.registered_by).where(SourceDocument.id == meeting.source_document_id)
    )
    participants = select(MeetingParticipant.account_id).where(MeetingParticipant.meeting_id == meeting.id)
    assignees = select(ActionItem.assignee_id).where(
        ActionItem.meeting_id == meeting.id, ActionItem.assignee_id.is_not(None),
        ActionItem.status.not_in(INACTIVE_ITEM_STATUSES),
    )
    candidates = participants.union(assignees)
    created = 0
    for account in mailable_accounts(session, meeting.tenant_id, candidates):
        if account.id == registrant_id:
            continue
        subject = f"{SUBJECT_PREFIX} 새 회의록: {meeting.title}"
        body = (
            f"새 회의록이 등록되었습니다: {meeting.title}\n\n"
            f"로그인해서 내용을 확인해 주세요.\n{login_link()}\n"
        )
        if queue_mail(
            session, account=account, kind="immediate_new_minutes", subject=subject, body=body,
            dedupe_key=f"meeting:{meeting.id}:immediate:{account.id}",
        ):
            created += 1
    return created
