"""회의록 보류·재개 API. 보류는 삭제와 달리 기록을 그대로 두고 목록에서도 숨기지 않는다.
- 권한: 회의록 확정과 같은 판정(can_confirm_meeting: 지시자·총괄). 그 밖의 관리자 403, 담당자 직급 403, 볼 수 없으면 404
- 대상: 확정 대기·확정된 회의록만(처리 중·실패·내용 없음은 409)
- 보류: meeting_holds 행(회의록당 1행, 처음 보류 때 생성)의 on_hold=true + 보류한 사람·시각. Meeting.on_hold 는 이 행에서 계산. 딸린 업무는 상태를 바꾸지 않고 "보류된 회의록의 업무"로 본다
  (할 일·자동 확정·메일·안내에서 빠지고, 수정·확정·종결·삭제·화자 저장·회의록 확정은 409). 이미 보류면 200(멱등, 사건 없음)
- 재개: 같은 행의 on_hold=false + 재개한 사람·시각. 진행 중 업무(확정 대기·확정)의 기한을 모두 비워(미확정 표시도 해제) 보완 필요로 만든다.
  확정된 업무도 비운다(PATCH 의 "확정 업무 빈칸 금지" 규칙은 재개 때만 예외). 종결·삭제 업무는 그대로.
  확정 대기 회의록의 자동 확정 시각은 보류했던 기간만큼 뒤로 미룬다(남은 기간 유지). 보류 중이 아니면 200(멱등, 변경 없음)
- 데이터 변경과 사건(meeting.on_hold·meeting.resumed, 기한을 비운 업무마다 item.updated via=meeting_resumed)은 한 번에 커밋한다."""
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.meeting_schemas import AccountRef
from app.api.schemas import CamelModel
from app.auth.access import can_confirm_meeting, get_visible_meeting
from app.auth.deps import require_rank
from app.db import get_session
from app.models import Account, ActionItem, Meeting, MeetingHold, append_event
from app.models.common import utcnow

router = APIRouter(prefix="/api/meetings", tags=["meetings"])

HOLDABLE_STATUSES = ("awaiting_confirmation", "confirmed")
# 재개 때 기한을 비우는 업무(진행 중인 것만)
ACTIVE_ITEM_STATUSES = ("pending", "confirmed")
ON_HOLD_MESSAGE = "보류 중인 회의록입니다. 재개한 뒤에 다시 시도하세요"
ENDED_MESSAGE = "종료된 회의록입니다. 수정할 수 없습니다"
DELETED_MESSAGE = "삭제된 회의록입니다. 수정할 수 없습니다"


def reject_if_locked(meeting: Meeting | None, *, allow_on_hold: bool = False) -> None:
    """보류·종료·삭제된 회의록(과 그 업무)에 대한 변경 요청을 409 로 거부한다. 권한 판정(403) 뒤에 부른다.
    allow_on_hold: 보류·재개 API 처럼 보류 상태 자체를 다루는 곳에서만 True."""
    if meeting is None:
        return
    if meeting.deleted:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=DELETED_MESSAGE)
    if meeting.on_hold and not allow_on_hold:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=ON_HOLD_MESSAGE)
    if meeting.ended:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=ENDED_MESSAGE)


class MeetingHoldResult(CamelModel):
    id: int
    on_hold: bool
    on_hold_by: AccountRef | None
    on_hold_at: datetime | None
    resumed_by: AccountRef | None
    resumed_at: datetime | None
    auto_confirm_at: datetime | None
    # 재개로 기한을 비운 업무 id(보류·멱등 응답에서는 빈 목록)
    cleared_due_item_ids: list[int] = []


def _ref(session: Session, account_id: int | None) -> AccountRef | None:
    if account_id is None:
        return None
    found = session.get(Account, account_id)
    return AccountRef(id=found.id, name=found.name) if found else None


def _result(session: Session, meeting: Meeting, hold: MeetingHold | None, cleared: list[int] | None = None) -> MeetingHoldResult:
    return MeetingHoldResult(
        id=meeting.id, on_hold=bool(hold and hold.on_hold),
        on_hold_by=_ref(session, hold.on_hold_by if hold else None), on_hold_at=hold.on_hold_at if hold else None,
        resumed_by=_ref(session, hold.resumed_by if hold else None), resumed_at=hold.resumed_at if hold else None,
        auto_confirm_at=meeting.auto_confirm_at, cleared_due_item_ids=cleared or [],
    )


def _holdable_meeting(session: Session, account: Account, meeting_id: int) -> Meeting:
    meeting = get_visible_meeting(session, account, meeting_id)
    if meeting is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="회의록을 찾을 수 없습니다")
    if not can_confirm_meeting(session, account, meeting):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="회의록을 총괄하는 관리자 또는 지시자만 보류·재개할 수 있습니다")
    if meeting.status not in HOLDABLE_STATUSES:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=f"보류·재개할 수 없는 상태입니다({meeting.status})")
    reject_if_locked(meeting, allow_on_hold=True)  # 종료·삭제된 회의록은 보류·재개 불가
    return meeting


@router.post("/{meeting_id}/hold", response_model=MeetingHoldResult, response_model_by_alias=True)
def hold_meeting(
    meeting_id: int,
    account: Account = Depends(require_rank("manager")),
    session: Session = Depends(get_session),
) -> MeetingHoldResult:
    meeting = _holdable_meeting(session, account, meeting_id)
    hold = session.get(MeetingHold, meeting.id)
    if hold is not None and hold.on_hold:
        return _result(session, meeting, hold)  # 멱등: 처음 보류 기록 유지
    if hold is None:
        hold = MeetingHold(meeting_id=meeting.id, tenant_id=meeting.tenant_id)
        session.add(hold)
    hold.on_hold, hold.on_hold_by, hold.on_hold_at = True, account.id, utcnow()
    append_event(
        session, tenant_id=meeting.tenant_id, entity_type="meeting", entity_id=meeting.id,
        event_type="meeting.on_hold", actor_account_id=account.id,
        payload={"before": {"onHold": False}, "after": {"onHold": True}},
    )
    session.commit()
    return _result(session, meeting, hold)


@router.post("/{meeting_id}/resume", response_model=MeetingHoldResult, response_model_by_alias=True)
def resume_meeting(
    meeting_id: int,
    account: Account = Depends(require_rank("manager")),
    session: Session = Depends(get_session),
) -> MeetingHoldResult:
    meeting = _holdable_meeting(session, account, meeting_id)
    hold = session.get(MeetingHold, meeting.id)
    if hold is None or not hold.on_hold:
        return _result(session, meeting, hold)  # 멱등: 보류 중이 아니면 아무것도 바꾸지 않는다
    now = utcnow()

    # 자동 확정 시계: 보류했던 기간만큼 미룬다(확정 대기 회의록만, 남은 기간을 그대로 유지)
    before_auto = meeting.auto_confirm_at
    if meeting.status == "awaiting_confirmation" and meeting.auto_confirm_at is not None and hold.on_hold_at is not None:
        meeting.auto_confirm_at = meeting.auto_confirm_at + (now - hold.on_hold_at)

    # 진행 중 업무의 기한을 비워 보완 필요로(확정된 업무도 재개 때만 예외로 비운다)
    cleared: list[int] = []
    items = session.scalars(
        select(ActionItem)
        .where(ActionItem.meeting_id == meeting.id, ActionItem.status.in_(ACTIVE_ITEM_STATUSES))
        .order_by(ActionItem.id)
    ).all()
    for item in items:
        if item.due_date is None and not item.due_undetermined:
            continue  # 이미 비어 있음
        before = {"dueDate": item.due_date.isoformat() if item.due_date else None, "dueUndetermined": item.due_undetermined}
        item.due_date, item.due_undetermined = None, False
        append_event(
            session, tenant_id=item.tenant_id, entity_type="action_item", entity_id=item.id,
            event_type="item.updated", actor_account_id=account.id,
            payload={"before": before, "after": {"dueDate": None, "dueUndetermined": False}, "via": "meeting_resumed"},
        )
        cleared.append(item.id)

    hold.on_hold, hold.resumed_by, hold.resumed_at = False, account.id, now
    append_event(
        session, tenant_id=meeting.tenant_id, entity_type="meeting", entity_id=meeting.id,
        event_type="meeting.resumed", actor_account_id=account.id,
        payload={
            "before": {"onHold": True}, "after": {"onHold": False}, "clearedDueItemIds": cleared,
            "autoConfirmAt": {
                "before": before_auto.isoformat() if before_auto else None,
                "after": meeting.auto_confirm_at.isoformat() if meeting.auto_confirm_at else None,
            },
        },
    )
    session.commit()
    return _result(session, meeting, hold, cleared)
