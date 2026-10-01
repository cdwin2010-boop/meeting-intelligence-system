"""업무 쓰기 API: 보완(수정)·확정. manager·executive 만(staff 403).
데이터 변경과 이벤트 기록은 같은 세션에서 한 번에 커밋한다(둘 중 하나만 남지 않게).
수정·확정해도 회의록의 first_created_at·auto_confirm_at(자동 확정 시계)은 건드리지 않는다."""
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.action_schemas import ActionItemPatch
from app.api.meeting_schemas import AccountRef, ActionItemOut
from app.auth.access import get_visible_meeting
from app.auth.deps import require_rank
from app.auth.scope import scoped
from app.db import get_session
from app.models import Account, ActionItem, Meeting, append_event
from app.models.common import utcnow
from app.services.notices import create_confirm_notices

router = APIRouter(prefix="/api/action-items", tags=["action-items"])

# str.isspace() 가 공백으로 보지 않는 폭 없는 문자도 빈칸으로 취급한다
_ZERO_WIDTH = "​‌‍⁠﻿"


def _http(code: int, detail) -> HTTPException:
    return HTTPException(status_code=code, detail=detail)


def is_blank(text: str) -> bool:
    """모든 유니코드 공백(탭·줄바꿈·전각 공백 등)과 폭 없는 문자를 빼면 아무것도 없는지."""
    return all(ch.isspace() or ch in _ZERO_WIDTH for ch in text)


def get_visible_item(session: Session, account: Account, item_id: int) -> ActionItem:
    """고객사 범위 + 회의록 열람 권한을 통과한 업무. 아니면 404(존재 여부를 알리지 않음)."""
    item = session.scalar(scoped(select(ActionItem), account).where(ActionItem.id == item_id))
    if item is None or get_visible_meeting(session, account, item.meeting_id) is None:
        raise _http(status.HTTP_404_NOT_FOUND, "업무를 찾을 수 없습니다")
    return item


def item_out(session: Session, item: ActionItem) -> ActionItemOut:
    assignee = session.get(Account, item.assignee_id) if item.assignee_id is not None else None
    return ActionItemOut.from_item(item, AccountRef(id=assignee.id, name=assignee.name) if assignee else None)


def _snapshot(item: ActionItem) -> dict:
    # 이벤트 before/after 용(JSON 저장 가능한 값만)
    return {
        "title": item.title,
        "assigneeId": item.assignee_id,
        "dueDate": item.due_date.isoformat() if item.due_date else None,
        "dueUndetermined": item.due_undetermined,
    }


def confirm_item(session: Session, item: ActionItem, account: Account) -> bool:
    """보완 필요가 아닌 pending 업무를 확정하고 이벤트를 남긴다(커밋은 호출부). 확정했으면 True."""
    if item.status != "pending" or item.needs_supplement:
        return False
    item.status, item.confirm_kind = "confirmed", "manager"
    item.confirmed_by, item.confirmed_at = account.id, utcnow()
    append_event(
        session, tenant_id=item.tenant_id, entity_type="action_item", entity_id=item.id,
        event_type="item.confirmed", actor_account_id=account.id,
        payload={"before": {"status": "pending"}, "after": {"status": "confirmed", "confirmKind": "manager"}},
    )
    create_confirm_notices(
        session, meeting=session.get(Meeting, item.meeting_id), entity_type="action_item", entity_id=item.id,
        confirm_kind="manager", title=item.title, actor_id=account.id,
    )
    return True


@router.patch("/{item_id}", response_model=ActionItemOut, response_model_by_alias=True)
def update_action_item(
    item_id: int,
    body: ActionItemPatch,
    account: Account = Depends(require_rank("manager")),
    session: Session = Depends(get_session),
) -> ActionItemOut:
    item = get_visible_item(session, account, item_id)
    if item.status == "deleted":
        raise _http(status.HTTP_409_CONFLICT, "삭제된 업무는 수정할 수 없습니다")

    sent = body.model_fields_set
    if not sent:
        raise _http(status.HTTP_400_BAD_REQUEST, "바꿀 항목이 없습니다")
    if "due_date" in sent and body.due_date is not None and body.due_undetermined is True:
        raise _http(status.HTTP_400_BAD_REQUEST, "기한 날짜와 '미확정'을 함께 지정할 수 없습니다")

    before = _snapshot(item)

    blank_title = False
    if "title" in sent:
        if body.title is None or is_blank(body.title):
            if item.status != "confirmed":
                raise _http(status.HTTP_400_BAD_REQUEST, "업무명은 비워 둘 수 없습니다")
            blank_title = True  # 확정된 업무: 아래에서 missingFields 형식으로 함께 거부
        else:
            item.title = body.title.strip()

    if "assignee_id" in sent:
        if body.assignee_id is not None:
            assignee = session.get(Account, body.assignee_id)
            if assignee is None or assignee.tenant_id != item.tenant_id or not assignee.is_active:
                # 다른 고객사·없는 계정·비활성 계정을 구분하지 않는다
                raise _http(status.HTTP_400_BAD_REQUEST, "같은 고객사의 활성 계정만 담당자로 지정할 수 있습니다")
        item.assignee_id = body.assignee_id

    # 기한: 날짜를 넣으면 '미확정' 해제, '미확정'을 고르면 날짜 비움
    if "due_date" in sent:
        item.due_date = body.due_date
        if body.due_date is not None:
            item.due_undetermined = False
    if "due_undetermined" in sent:
        if body.due_undetermined is None:
            raise _http(status.HTTP_400_BAD_REQUEST, "dueUndetermined 는 true 또는 false 여야 합니다")
        item.due_undetermined = body.due_undetermined
        if body.due_undetermined:
            item.due_date = None

    # 확정된 업무는 적용 결과가 보완 필요(빈칸)가 되는 변경을 거부한다. 다른 유효한 값으로 바꾸는 것은 허용.
    # 커밋 전에 거부하므로 데이터·이벤트 모두 바뀌지 않는다(세션은 커밋 없이 닫힘)
    if item.status == "confirmed":
        missing = item.missing_fields
        if blank_title and "title" not in missing:
            missing = ["title", *missing]
        if missing:
            raise _http(
                status.HTTP_400_BAD_REQUEST,
                {"message": "확정된 업무는 필수 항목을 비울 수 없습니다", "missingFields": missing},
            )

    after = _snapshot(item)
    changed = {k for k in before if before[k] != after[k]}
    if changed:
        append_event(
            session, tenant_id=item.tenant_id, entity_type="action_item", entity_id=item.id,
            event_type="item.updated", actor_account_id=account.id,
            payload={"before": {k: before[k] for k in changed}, "after": {k: after[k] for k in changed}},
        )
    session.commit()
    return item_out(session, item)


@router.post("/{item_id}/confirm", response_model=ActionItemOut, response_model_by_alias=True)
def confirm_action_item(
    item_id: int,
    account: Account = Depends(require_rank("manager")),
    session: Session = Depends(get_session),
) -> ActionItemOut:
    item = get_visible_item(session, account, item_id)
    if item.status == "confirmed":
        return item_out(session, item)  # 멱등: 이벤트를 다시 남기지 않음
    if item.status != "pending":
        raise _http(status.HTTP_409_CONFLICT, f"확정할 수 없는 상태입니다({item.status})")
    if item.needs_supplement:
        raise _http(
            status.HTTP_409_CONFLICT,
            {"message": "보완 필요 항목이 있어 확정할 수 없습니다", "missingFields": item.missing_fields},
        )
    confirm_item(session, item, account)
    session.commit()
    return item_out(session, item)
