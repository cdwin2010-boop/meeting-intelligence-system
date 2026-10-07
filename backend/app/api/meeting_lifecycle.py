"""회의록 직권 종료·삭제 API. 권한은 회의록 확정과 같은 판정(can_confirm_meeting: 지시자·총괄), 그 밖의 관리자·담당자 403, 볼 수 없으면 404.
- 직권 종료 POST /api/meetings/{id}/end: 확정 대기·확정 회의록만(그 밖 409). 보류 중·삭제됨 409, 이미 종료면 200(멱등, 사건 없음).
  진행 중 업무(확정 대기·확정)를 모두 종결(직권이므로 확정 전 업무도, 업무마다 item.closed via=meeting_ended)한 뒤 회의록 종료(구분 manager)
- 삭제 POST /api/meetings/{id}/delete: 처리 중이 아니면 어떤 상태든(보류·종료 포함) 삭제 표시. 이미 삭제면 200(멱등, 사건 없음).
  회의록·업무·수정 요청 행은 그대로 두고, 딸린 업무·요청은 회의록 단계로 목록·할 일·자동 확정·메일에서 빠진다
- 기록: meeting_closures(종료 구분·처리자·시각, 삭제 처리자·시각) + 사건 meeting.ended·meeting.deleted. 한 번에 커밋한다.
- 직권 종료·삭제는 사유(reason) 필수. 사유는 사건(meeting.ended·meeting.deleted, 직권 종료로 종결한 업무의 item.closed)에 남긴다."""
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.action_schemas import ReasonBody
from app.auth.locks import reject_if_locked
from app.api.meeting_schemas import AccountRef
from app.api.schemas import CamelModel
from app.auth.access import can_confirm_meeting, get_visible_meeting
from app.auth.deps import require_rank
from app.db import get_session
from app.models import Account, ActionItem, Meeting, MeetingClosure, append_event
from app.models.closure import meeting_phase
from app.models.common import utcnow
from app.models.item_conditions import open_item
from app.services.lifecycle import end_meeting, get_or_create_closure
from app.services.reasons import latest_reason

router = APIRouter(prefix="/api/meetings", tags=["meetings"])

ENDABLE_STATUSES = ("awaiting_confirmation", "confirmed")


class MeetingPhaseResult(CamelModel):
    id: int
    phase: str  # active | ended | on_hold | deleted
    end_kind: str | None
    ended_by: AccountRef | None
    ended_at: datetime | None
    deleted_by: AccountRef | None
    deleted_at: datetime | None
    # 직권 종료 사유(자동 종료·사유 도입 전 종료는 None), 삭제 사유
    end_reason: str | None = None
    delete_reason: str | None = None
    # 직권 종료로 종결한 업무 id(그 밖의 응답에서는 빈 목록)
    closed_item_ids: list[int] = []


def _ref(session: Session, account_id: int | None) -> AccountRef | None:
    if account_id is None:
        return None
    found = session.get(Account, account_id)
    return AccountRef(id=found.id, name=found.name) if found else None


def phase_result(session: Session, meeting: Meeting, closed_item_ids: list[int] | None = None) -> MeetingPhaseResult:
    session.refresh(meeting)  # 계산 칸(보류·종료·삭제)을 방금 바꾼 값으로
    closure = session.get(MeetingClosure, meeting.id)
    return MeetingPhaseResult(
        id=meeting.id,
        phase=meeting_phase(meeting),
        end_kind=closure.end_kind if closure else None,
        ended_by=_ref(session, closure.ended_by) if closure else None,
        ended_at=closure.ended_at if closure else None,
        deleted_by=_ref(session, closure.deleted_by) if closure else None,
        deleted_at=closure.deleted_at if closure else None,
        closed_item_ids=closed_item_ids or [],
        end_reason=latest_reason(session, meeting, "meeting.ended") if closure and closure.ended_at else None,
        delete_reason=latest_reason(session, meeting, "meeting.deleted") if closure and closure.deleted_at else None,
    )


def _managed_meeting(session: Session, account: Account, meeting_id: int, action: str) -> Meeting:
    meeting = get_visible_meeting(session, account, meeting_id)
    if meeting is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="회의록을 찾을 수 없습니다")
    if not can_confirm_meeting(session, account, meeting):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=f"회의록을 총괄하는 관리자 또는 지시자만 {action}할 수 있습니다")
    return meeting


@router.post("/{meeting_id}/end", response_model=MeetingPhaseResult, response_model_by_alias=True)
def end_meeting_by_manager(
    meeting_id: int,
    body: ReasonBody,
    account: Account = Depends(require_rank("manager")),
    session: Session = Depends(get_session),
) -> MeetingPhaseResult:
    meeting = _managed_meeting(session, account, meeting_id, "종료")
    if meeting.status not in ENDABLE_STATUSES:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=f"종료할 수 없는 상태입니다({meeting.status})")
    if meeting.ended and not meeting.deleted and not meeting.on_hold:
        return phase_result(session, meeting)  # 멱등: 처음 종료 기록 유지
    reject_if_locked(meeting)  # 삭제·보류 중이면 409

    now = utcnow()
    closed: list[int] = []
    items = session.scalars(
        select(ActionItem)
        .where(ActionItem.meeting_id == meeting.id, open_item())
        .order_by(ActionItem.id)
    ).all()
    for item in items:
        before = item.status
        item.status, item.closed_by, item.closed_at = "closed", account.id, now
        append_event(
            session, tenant_id=item.tenant_id, entity_type="action_item", entity_id=item.id,
            event_type="item.closed", actor_account_id=account.id,
            payload={"before": {"status": before}, "after": {"status": "closed"}, "via": "meeting_ended", "reason": body.reason},
        )
        closed.append(item.id)
    end_meeting(session, meeting, kind="manager", actor_id=account.id, closed_item_ids=closed, reason=body.reason)
    session.commit()
    return phase_result(session, meeting, closed)


@router.post("/{meeting_id}/delete", response_model=MeetingPhaseResult, response_model_by_alias=True)
def delete_meeting(
    meeting_id: int,
    body: ReasonBody,
    account: Account = Depends(require_rank("manager")),
    session: Session = Depends(get_session),
) -> MeetingPhaseResult:
    meeting = _managed_meeting(session, account, meeting_id, "삭제")
    if meeting.deleted:
        return phase_result(session, meeting)  # 멱등: 처음 삭제 기록 유지
    if meeting.status == "processing":
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="처리 중인 회의록은 삭제할 수 없습니다")
    closure = get_or_create_closure(session, meeting)
    closure.deleted_by, closure.deleted_at = account.id, utcnow()
    append_event(
        session, tenant_id=meeting.tenant_id, entity_type="meeting", entity_id=meeting.id,
        event_type="meeting.deleted", actor_account_id=account.id,
        payload={"before": {"phase": meeting_phase(meeting)}, "after": {"phase": "deleted"}, "reason": body.reason},
    )
    session.commit()
    return phase_result(session, meeting)
