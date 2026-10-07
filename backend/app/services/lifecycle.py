"""회의록 종료·삭제 처리(커밋은 호출부). 기록은 meeting_closures 1행과 사건(meeting.ended·meeting.deleted)에 남긴다.
- 자동 종료: 업무 종결 API 가 업무를 종결한 직후 auto_end_if_all_closed 를 부른다.
  삭제되지 않은 업무가 1건 이상이고 모두 종결이면 종료(구분 auto, 처리자 없음). 업무가 하나도 없으면 종료하지 않는다
- 직권 종료·삭제: app/api/meeting_lifecycle.py 가 부른다"""
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import ActionItem, Meeting, MeetingClosure, append_event
from app.models.common import utcnow
from app.models.item_conditions import live_item, not_deleted_item


def get_or_create_closure(session: Session, meeting: Meeting) -> MeetingClosure:
    closure = session.get(MeetingClosure, meeting.id)
    if closure is None:
        closure = MeetingClosure(meeting_id=meeting.id, tenant_id=meeting.tenant_id)
        session.add(closure)
    return closure


def end_meeting(
    session: Session, meeting: Meeting, *, kind: str, actor_id: int | None, closed_item_ids: list[int], reason: str | None = None
) -> MeetingClosure:
    """종료 기록. 직권 종료는 사유를 사건에 남기고, 자동 종료는 사유 없음(구분 auto 로 구별)."""
    closure = get_or_create_closure(session, meeting)
    closure.end_kind, closure.ended_by, closure.ended_at = kind, actor_id, utcnow()
    payload = {"endKind": kind, "closedItemIds": closed_item_ids}
    if reason is not None:
        payload["reason"] = reason
    append_event(
        session, tenant_id=meeting.tenant_id, entity_type="meeting", entity_id=meeting.id,
        event_type="meeting.ended", actor_account_id=actor_id, payload=payload,
    )
    return closure


def auto_end_if_all_closed(session: Session, meeting: Meeting) -> bool:
    """삭제되지 않은(대체된 업무는 제외) 업무가 있고 모두 종결이면 회의록을 자동 종료한다. 종료했으면 True."""
    session.flush()  # 방금 바꾼 업무 상태를 조회에 반영(세션 autoflush 꺼짐)
    closure = session.get(MeetingClosure, meeting.id)
    if closure is not None and (closure.ended_at is not None or closure.deleted_at is not None):
        return False
    statuses = session.scalars(
        select(ActionItem.status).where(ActionItem.meeting_id == meeting.id, not_deleted_item(), live_item())
    ).all()
    if not statuses or any(s != "closed" for s in statuses):
        return False
    end_meeting(session, meeting, kind="auto", actor_id=None, closed_item_ids=[])
    return True
