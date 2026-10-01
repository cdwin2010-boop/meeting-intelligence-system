"""회의록 열람 권한. 판정은 이 모듈 한 곳에서만 한다(목록·상세·전사문과 이후 단계에서 재사용).

- 모든 조회는 고객사 범위(tenant_id) 안에서만. 다른 고객사 회의록은 존재 여부도 알리지 않는다(404).
- manager·executive: 고객사의 모든 회의록.
- staff: 참석자이거나, 담당자로 지정된 업무(삭제 제외)가 있거나, 등록자인 회의록만.
"""
from sqlalchemy import ColumnElement, and_, exists, or_, select, true
from sqlalchemy.orm import Session, aliased

from app.auth.scope import scoped
from app.models import Account, ActionItem, Meeting, MeetingParticipant, SourceDocument

# 고객사 전체 회의록을 볼 수 있는 직급
VIEW_ALL_RANKS = ("manager", "executive")


def is_participant(account: Account) -> ColumnElement[bool]:
    return exists().where(MeetingParticipant.meeting_id == Meeting.id, MeetingParticipant.account_id == account.id)


def has_assigned_item(account: Account) -> ColumnElement[bool]:
    return exists().where(
        ActionItem.meeting_id == Meeting.id,
        ActionItem.assignee_id == account.id,
        ActionItem.status != "deleted",
    )


def is_registrant(account: Account) -> ColumnElement[bool]:
    # 별칭: 목록 조회가 SourceDocument 를 이미 조인해도 서브쿼리가 바깥 표에 흡수(자동 상관)되지 않게
    document = aliased(SourceDocument)
    return exists().where(document.id == Meeting.source_document_id, document.registered_by == account.id)


def meeting_visibility(account: Account) -> ColumnElement[bool]:
    """Meeting 조회에 붙일 열람 조건(고객사 범위 포함)."""
    same_tenant = Meeting.tenant_id == account.tenant_id
    if account.rank in VIEW_ALL_RANKS:
        return and_(same_tenant, true())
    return and_(same_tenant, or_(is_participant(account), has_assigned_item(account), is_registrant(account)))


def get_visible_meeting(session: Session, account: Account, meeting_id: int) -> Meeting | None:
    """열람 가능한 회의록이면 돌려주고, 없거나 다른 고객사·권한 없음이면 None(호출부는 모두 404)."""
    return session.scalar(scoped(select(Meeting), account).where(Meeting.id == meeting_id, meeting_visibility(account)))
