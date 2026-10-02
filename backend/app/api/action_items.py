"""업무 쓰기 API: 보완(수정)·확정·종결·삭제. staff 는 403, manager 는 본인이 등록한 회의록의 업무 또는 본인이 담당자인 업무만(아니면 403),
executive 는 모든 업무(판정은 app/auth/access.py 의 can_write_item). 종결도 같은 판정을 쓴다.
삭제는 회의록 확정 권한(can_confirm_meeting: 지시자·총괄)만. 삭제는 행을 지우지 않고 status=deleted 로 표시한다.
종결·삭제한 사람·시각은 closed_by/at·deleted_by/at 칸과 사건(item.closed·item.deleted)에 남긴다.
데이터 변경과 이벤트 기록은 같은 세션에서 한 번에 커밋한다(둘 중 하나만 남지 않게).
수정·확정해도 회의록의 first_created_at·auto_confirm_at(자동 확정 시계)은 건드리지 않는다."""
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.action_schemas import ActionItemPatch
from app.api.meeting_schemas import AccountRef, ActionItemOut
from app.auth.access import can_confirm_meeting, can_write_item, get_visible_meeting
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


def get_writable_item(session: Session, account: Account, item_id: int) -> ActionItem:
    """볼 수 없으면 404, 볼 수는 있어도 수정·확정 권한이 없으면 403."""
    item = get_visible_item(session, account, item_id)
    if not can_write_item(session, account, item, session.get(Meeting, item.meeting_id)):
        raise _http(status.HTTP_403_FORBIDDEN, "이 업무를 수정·확정할 권한이 없습니다")
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
    item = get_writable_item(session, account, item_id)
    if item.status == "deleted":
        raise _http(status.HTTP_409_CONFLICT, "삭제된 업무는 수정할 수 없습니다")
    if item.status == "closed":
        raise _http(status.HTTP_409_CONFLICT, "종결된 업무는 수정할 수 없습니다")

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
    item = get_writable_item(session, account, item_id)
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


@router.post("/{item_id}/close", response_model=ActionItemOut, response_model_by_alias=True)
def close_action_item(
    item_id: int,
    account: Account = Depends(require_rank("manager")),
    session: Session = Depends(get_session),
) -> ActionItemOut:
    """업무 종결: 확정된 업무만(확정 전은 409). 이미 종결이면 그대로 200(멱등, 사건 없음). 삭제된 업무는 409."""
    item = get_writable_item(session, account, item_id)
    if item.status == "closed":
        return item_out(session, item)
    if item.status == "deleted":
        raise _http(status.HTTP_409_CONFLICT, "삭제된 업무는 종결할 수 없습니다")
    if item.status != "confirmed":
        raise _http(status.HTTP_409_CONFLICT, "확정된 업무만 종결할 수 있습니다")
    item.status, item.closed_by, item.closed_at = "closed", account.id, utcnow()
    append_event(
        session, tenant_id=item.tenant_id, entity_type="action_item", entity_id=item.id,
        event_type="item.closed", actor_account_id=account.id,
        payload={"before": {"status": "confirmed"}, "after": {"status": "closed"}},
    )
    session.commit()
    return item_out(session, item)


@router.post("/{item_id}/delete", response_model=ActionItemOut, response_model_by_alias=True)
def delete_action_item(
    item_id: int,
    account: Account = Depends(require_rank("manager")),
    session: Session = Depends(get_session),
) -> ActionItemOut:
    """업무 삭제(표시만, 행은 남김): 지시자·총괄만(아니면 403). 어떤 상태든 삭제할 수 있고, 이미 삭제면 그대로 200(멱등, 사건 없음)."""
    item = get_visible_item(session, account, item_id)
    if not can_confirm_meeting(session, account, session.get(Meeting, item.meeting_id)):
        raise _http(status.HTTP_403_FORBIDDEN, "이 업무를 삭제할 권한이 없습니다")
    if item.status == "deleted":
        return item_out(session, item)
    before = item.status
    item.status, item.deleted_by, item.deleted_at = "deleted", account.id, utcnow()
    append_event(
        session, tenant_id=item.tenant_id, entity_type="action_item", entity_id=item.id,
        event_type="item.deleted", actor_account_id=account.id,
        payload={"before": {"status": before}, "after": {"status": "deleted"}},
    )
    session.commit()
    return item_out(session, item)
