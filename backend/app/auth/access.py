"""회의록 열람 권한. 판정은 이 모듈 한 곳에서만 한다(목록·상세·전사문과 이후 단계에서 재사용).

- 모든 조회는 고객사 범위(tenant_id) 안에서만. 다른 고객사 회의록은 존재 여부도 알리지 않는다(404).
- manager·executive: 고객사의 모든 회의록.
- staff: 참석자이거나, 담당자로 지정된 업무(삭제 제외)가 있거나, 등록자인 회의록만.
확정·수정 권한(can_confirm_meeting·can_write_item)도 이 모듈에 둔다(맨 아래).
"""
from sqlalchemy import ColumnElement, and_, exists, false, or_, select, true
from sqlalchemy.orm import Session, aliased

from app.auth.scope import scoped
from app.models import Account, ActionItem, Meeting, MeetingParticipant, SourceDocument

# 고객사 전체 회의록을 볼 수 있는 직급
VIEW_ALL_RANKS = ("manager", "executive")


# 아래 EXISTS 서브쿼리는 모두 별칭을 쓴다: 바깥 조회가 같은 표(업무·참석자·원천 문서)를 이미 조인해도
# 서브쿼리가 바깥 표에 흡수(자동 상관)되지 않게 하기 위해서다.
def is_participant(account: Account) -> ColumnElement[bool]:
    participant = aliased(MeetingParticipant)
    return exists().where(participant.meeting_id == Meeting.id, participant.account_id == account.id)


def has_assigned_item(account: Account) -> ColumnElement[bool]:
    item = aliased(ActionItem)
    return exists().where(
        item.meeting_id == Meeting.id,
        item.assignee_id == account.id,
        item.status != "deleted",
    )


def is_registrant(account: Account) -> ColumnElement[bool]:
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


# ---------------- 확정·수정 권한(쓰기) ----------------
# 열람 권한(위)과 별개다. 판정은 아래 함수에서만 한다.
#  - 지시자(executive): 모든 회의록·업무를 확정·수정
#  - 관리자(manager)가 그 회의록의 "총괄"이면 회의록 확정과 모든 업무 수정·확정.
#    총괄 = 회의록을 등록한 관리자. 담당자(staff)가 등록한 회의록이면 그 회의에 참석한 관리자가 총괄
#  - 총괄이 아닌 관리자: 본인이 담당자인 업무만 수정·확정(회의록 확정은 불가)
#  - 담당자(staff): 확정·수정 불가
def registrant_id(session: Session, meeting: Meeting) -> int | None:
    return session.scalar(select(SourceDocument.registered_by).where(SourceDocument.id == meeting.source_document_id))


def meeting_lead_condition(account: Account) -> ColumnElement[bool]:
    """Meeting 조회에 붙일 '이 관리자가 총괄' 조건(등록한 관리자, 또는 담당자 등록 회의록의 참석 관리자).
    총괄 규칙은 여기 한 곳에만 둔다(is_meeting_lead·할 일 목록이 함께 쓴다)."""
    if account.rank != "manager":
        return false()
    document = aliased(SourceDocument)
    registrant = aliased(Account)
    staff_registered = exists().where(
        document.id == Meeting.source_document_id,
        registrant.id == document.registered_by,
        registrant.rank == "staff",
    )
    return or_(is_registrant(account), and_(staff_registered, is_participant(account)))


def can_confirm_condition(account: Account) -> ColumnElement[bool]:
    """Meeting 조회에 붙일 회의록 확정 권한 조건(지시자 또는 총괄)."""
    return true() if account.rank == "executive" else meeting_lead_condition(account)


def is_meeting_lead(session: Session, account: Account, meeting: Meeting) -> bool:
    """관리자가 이 회의록의 총괄인지(meeting_lead_condition 과 같은 규칙)."""
    if account.rank != "manager":
        return False
    return session.scalar(select(Meeting.id).where(Meeting.id == meeting.id, meeting_lead_condition(account))) is not None


def can_confirm_meeting(session: Session, account: Account, meeting: Meeting) -> bool:
    """회의록 확정 권한. 화자 매핑 저장도 같은 판정을 쓴다."""
    return account.rank == "executive" or is_meeting_lead(session, account, meeting)


def can_write_item(session: Session, account: Account, item: ActionItem, meeting: Meeting) -> bool:
    """업무 수정·확정 권한. 담당자 여부는 '지금(변경 전)' 담당자 기준."""
    if account.rank == "executive":
        return True
    if account.rank != "manager":
        return False
    return item.assignee_id == account.id or is_meeting_lead(session, account, meeting)
