"""확정 안내(시스템 안, 메일 없음).

회의록이나 업무가 확정되면(수동·등록 시·자동 모두) 같은 트랜잭션에서 안내를 만든다.
수신자 = 그 회의록의 '관리자 이상' 관련자: 참석자 중 manager·executive + 등록자가 manager·executive 면 등록자.
활성 계정만, 확정을 직접 수행한 본인은 제외, 중복 제거. payload 에는 confirmKind·제목 같은 짧은 값만 넣는다.
"""
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.access import VIEW_ALL_RANKS
from app.models import Account, Meeting, MeetingParticipant, Notice, SourceDocument

TITLE_MAX = 100


def confirm_notice_recipients(session: Session, meeting: Meeting, actor_id: int | None) -> list[int]:
    participant_ids = select(MeetingParticipant.account_id).where(MeetingParticipant.meeting_id == meeting.id)
    registrant_id = select(SourceDocument.registered_by).where(SourceDocument.id == meeting.source_document_id)
    ids = session.scalars(
        select(Account.id)
        .where(
            Account.tenant_id == meeting.tenant_id,
            Account.is_active.is_(True),
            Account.rank.in_(VIEW_ALL_RANKS),
            Account.id.in_(participant_ids.union(registrant_id)),
        )
        .order_by(Account.id)
    ).all()
    return [account_id for account_id in dict.fromkeys(ids) if account_id != actor_id]


def create_confirm_notices(
    session: Session,
    *,
    meeting: Meeting,
    entity_type: str,
    entity_id: int,
    confirm_kind: str,
    title: str,
    actor_id: int | None,
) -> int:
    """확정 안내를 만들고 건수를 돌려준다(커밋은 호출부 트랜잭션)."""
    recipients = confirm_notice_recipients(session, meeting, actor_id)
    for account_id in recipients:
        session.add(
            Notice(
                tenant_id=meeting.tenant_id,
                account_id=account_id,
                kind="confirmed_notice",
                entity_type=entity_type,
                entity_id=entity_id,
                meeting_id=meeting.id,
                payload={"confirmKind": confirm_kind, "title": (title or "")[:TITLE_MAX]},
            )
        )
    session.flush()
    return len(recipients)
