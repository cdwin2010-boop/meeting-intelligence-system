"""등록 후 회의 일시 수정: PATCH /api/meetings/{id}/held-at (작업 88-4).
- 권한: 관리자(manager)·지시자(executive)만(담당자 403). 볼 수 없는 회의록(다른 고객사 등)은 404. 보류·종료·삭제된 회의록은 409(기존 잠금 규칙).
- 입력: heldAt(오프셋 붙은 ISO, 필수·미래 거부·비어 있으면 422, 등록 때와 같은 규칙) + reason(사유 필수, 공백뿐이면 422).
- 이미 같은 일시면 200 으로 그대로 돌려주고 이력을 남기지 않는다(멱등).
- 이력: 공통 변경 이력(구분 "회의 일시 변경")에 변경자·시각·사유·변경 전후 값을 남긴다.
- 등록 일시(first_created_at)·자동 확정 시각·이미 계산된 업무 기한은 바꾸지 않는다(다시 계산하지 않음).
"""
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.api.action_schemas import ReasonBody
from app.api.schemas import CamelModel
from app.auth.access import get_visible_meeting
from app.auth.deps import get_current_account, has_rank
from app.auth.locks import reject_if_locked
from app.db import get_session
from app.models import Account
from app.services.history import KIND_MEETING_HELD_AT, record_change
from app.services.meeting_time import parse_meeting_at

router = APIRouter(prefix="/api/meetings", tags=["meeting-held-at"])


class HeldAtBody(ReasonBody):
    held_at: str = ""


class HeldAtOut(CamelModel):
    id: int
    held_at: datetime
    registered_at: datetime
    changed: bool


@router.patch("/{meeting_id}/held-at", response_model=HeldAtOut, response_model_by_alias=True)
def change_held_at(
    meeting_id: int,
    body: HeldAtBody,
    account: Account = Depends(get_current_account),
    session: Session = Depends(get_session),
) -> HeldAtOut:
    meeting = get_visible_meeting(session, account, meeting_id)
    if meeting is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="회의록을 찾을 수 없습니다")
    if not has_rank(account, "manager"):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="관리자 또는 지시자만 회의 일시를 변경할 수 있습니다")
    reject_if_locked(meeting)
    new_value = parse_meeting_at(body.held_at)
    old_value = meeting.held_at
    if new_value == old_value:
        return HeldAtOut(id=meeting.id, held_at=meeting.held_at, registered_at=meeting.first_created_at, changed=False)
    meeting.held_at = new_value
    record_change(
        session, tenant_id=meeting.tenant_id, target_type="meeting", target_id=meeting.id, kind=KIND_MEETING_HELD_AT, actor_id=account.id,
        before={"heldAt": old_value.isoformat()}, after={"heldAt": new_value.isoformat()}, extra={"reason": body.reason},
    )
    session.commit()
    return HeldAtOut(id=meeting.id, held_at=meeting.held_at, registered_at=meeting.first_created_at, changed=True)
