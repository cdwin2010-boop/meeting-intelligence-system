"""내 안내·할 일 API(로그인 직후 화면용). 메일은 보내지 않는다.
모든 목록은 고객사 범위와 회의록 열람 권한(app/auth/access.py)을 따른다. 삭제된 업무는 제외한다.
할 일 목록은 각각 최대 50건 + 전체 건수, 쿼리 수는 데이터 양과 무관하게 고정."""
from datetime import date, datetime
from typing import Any

from fastapi import APIRouter, Depends
from pydantic import Field
from sqlalchemy import exists, func, select, update
from sqlalchemy.orm import Session

from app.api.schemas import CamelModel
from app.auth.access import VIEW_ALL_RANKS, meeting_visibility
from app.auth.deps import get_current_account
from app.db import get_session
from app.models import Account, ActionItem, Meeting, MeetingView, Notice
from app.models.common import utcnow

router = APIRouter(prefix="/api/me", tags=["me"])

TODO_LIMIT = 50
AUTO_CONFIRM_KINDS = ("period_elapsed", "due_reached")


# ---------------- 스키마 ----------------
class NoticeOut(CamelModel):
    id: int
    kind: str
    entity_type: str
    entity_id: int
    meeting_id: int
    payload: dict[str, Any]
    created_at: datetime


class SeenRequest(CamelModel):
    ids: list[int] = Field(default_factory=list, max_length=1000)


class SeenResult(CamelModel):
    updated: int


class AwaitingMeeting(CamelModel):
    id: int
    title: str
    auto_confirm_at: datetime | None


class NeedsCompletionItem(CamelModel):
    meeting_id: int
    item_id: int
    title: str
    missing_fields: list[str]


class MyItem(CamelModel):
    meeting_id: int
    item_id: int
    title: str
    due_date: date | None
    due_undetermined: bool
    status: str


class UnreadAutoConfirmed(CamelModel):
    entity_type: str  # meeting | action_item
    meeting_id: int
    item_id: int | None
    title: str
    confirm_kind: str
    confirmed_at: datetime


class TodoList(CamelModel):
    total: int
    items: list[Any]


class Todos(CamelModel):
    awaiting_confirm_meetings: TodoList
    needs_completion_items: TodoList
    my_items: TodoList
    unread_auto_confirmed: TodoList


# ---------------- 안내 ----------------
@router.get("/notices", response_model=list[NoticeOut], response_model_by_alias=True)
def my_notices(account: Account = Depends(get_current_account), session: Session = Depends(get_session)) -> list[NoticeOut]:
    """내 미확인 안내(최신순)."""
    rows = session.scalars(
        select(Notice)
        .where(Notice.tenant_id == account.tenant_id, Notice.account_id == account.id, Notice.seen_at.is_(None))
        .order_by(Notice.id.desc())
    ).all()
    return [
        NoticeOut(id=n.id, kind=n.kind, entity_type=n.entity_type, entity_id=n.entity_id, meeting_id=n.meeting_id,
                  payload=n.payload or {}, created_at=n.created_at)
        for n in rows
    ]


@router.post("/notices/seen", response_model=SeenResult, response_model_by_alias=True)
def mark_notices_seen(
    body: SeenRequest, account: Account = Depends(get_current_account), session: Session = Depends(get_session)
) -> SeenResult:
    """내 안내만 확인 처리. 남의 id·없는 id 는 조용히 무시, 이미 확인한 것은 그대로(멱등)."""
    if not body.ids:
        return SeenResult(updated=0)
    updated = session.execute(
        update(Notice)
        .where(
            Notice.id.in_(body.ids),
            Notice.tenant_id == account.tenant_id,
            Notice.account_id == account.id,
            Notice.seen_at.is_(None),
        )
        .values(seen_at=utcnow())
        .execution_options(synchronize_session=False)
    ).rowcount
    session.commit()
    return SeenResult(updated=updated)


# ---------------- 할 일 ----------------
def _count(session: Session, stmt) -> int:
    return session.scalar(select(func.count()).select_from(stmt.order_by(None).subquery())) or 0


def _not_viewed_since(account: Account, meeting_id_col, confirmed_at_col):
    """내 열람 기록이 없거나, 마지막 열람이 확정 시각보다 이전이면 True."""
    return ~exists().where(
        MeetingView.meeting_id == meeting_id_col,
        MeetingView.account_id == account.id,
        MeetingView.last_viewed_at >= confirmed_at_col,
    )


@router.get("/todos", response_model=Todos, response_model_by_alias=True)
def my_todos(account: Account = Depends(get_current_account), session: Session = Depends(get_session)) -> Todos:
    visible = meeting_visibility(account)
    is_manager = account.rank in VIEW_ALL_RANKS

    # 1) 확정 대기 회의록(관리자 이상)
    awaiting = TodoList(total=0, items=[])
    if is_manager:
        stmt = select(Meeting.id, Meeting.title, Meeting.auto_confirm_at).where(
            visible, Meeting.status == "awaiting_confirmation"
        )
        awaiting = TodoList(
            total=_count(session, stmt),
            items=[
                AwaitingMeeting(id=r.id, title=r.title, auto_confirm_at=r.auto_confirm_at)
                for r in session.execute(stmt.order_by(Meeting.auto_confirm_at, Meeting.id).limit(TODO_LIMIT))
            ],
        )

    # 2) 보완 필요 업무(관리자 이상). 확정 전(pending) 업무만
    needs = TodoList(total=0, items=[])
    if is_manager:
        stmt = (
            select(ActionItem)
            .join(Meeting, Meeting.id == ActionItem.meeting_id)
            .where(visible, ActionItem.status == "pending", ActionItem.needs_supplement)
        )
        needs = TodoList(
            total=_count(session, stmt),
            items=[
                NeedsCompletionItem(meeting_id=i.meeting_id, item_id=i.id, title=i.title, missing_fields=i.missing_fields)
                for i in session.scalars(stmt.order_by(ActionItem.id).limit(TODO_LIMIT))
            ],
        )

    # 3) 내 담당 업무(pending·confirmed). 기한 있는 것 먼저(가까운 순), 없는 것 뒤
    stmt = (
        select(ActionItem)
        .join(Meeting, Meeting.id == ActionItem.meeting_id)
        .where(visible, ActionItem.assignee_id == account.id, ActionItem.status.in_(("pending", "confirmed")))
    )
    my_items = TodoList(
        total=_count(session, stmt),
        items=[
            MyItem(meeting_id=i.meeting_id, item_id=i.id, title=i.title, due_date=i.due_date,
                   due_undetermined=i.due_undetermined, status=i.status)
            for i in session.scalars(
                stmt.order_by(ActionItem.due_date.is_(None), ActionItem.due_date, ActionItem.id).limit(TODO_LIMIT)
            )
        ],
    )

    # 4) 자동 확정됐는데 그 뒤로 내가 열어 보지 않은 회의록·업무(최신 확정순)
    meeting_stmt = select(Meeting.id, Meeting.title, Meeting.confirm_kind, Meeting.confirmed_at).where(
        visible,
        Meeting.status == "confirmed",
        Meeting.confirm_kind.in_(AUTO_CONFIRM_KINDS),
        Meeting.confirmed_at.is_not(None),
        _not_viewed_since(account, Meeting.id, Meeting.confirmed_at),
    )
    item_stmt = (
        select(ActionItem.id, ActionItem.meeting_id, ActionItem.title, ActionItem.confirm_kind, ActionItem.confirmed_at)
        .join(Meeting, Meeting.id == ActionItem.meeting_id)
        .where(
            visible,
            ActionItem.status == "confirmed",
            ActionItem.confirm_kind.in_(AUTO_CONFIRM_KINDS),
            ActionItem.confirmed_at.is_not(None),
            _not_viewed_since(account, ActionItem.meeting_id, ActionItem.confirmed_at),
        )
    )
    unread_entries = [
        UnreadAutoConfirmed(entity_type="meeting", meeting_id=r.id, item_id=None, title=r.title,
                            confirm_kind=r.confirm_kind, confirmed_at=r.confirmed_at)
        for r in session.execute(meeting_stmt.order_by(Meeting.confirmed_at.desc(), Meeting.id.desc()).limit(TODO_LIMIT))
    ] + [
        UnreadAutoConfirmed(entity_type="action_item", meeting_id=r.meeting_id, item_id=r.id, title=r.title,
                            confirm_kind=r.confirm_kind, confirmed_at=r.confirmed_at)
        for r in session.execute(
            item_stmt.order_by(ActionItem.confirmed_at.desc(), ActionItem.id.desc()).limit(TODO_LIMIT)
        )
    ]
    unread_entries.sort(key=lambda e: e.confirmed_at, reverse=True)
    unread = TodoList(
        total=_count(session, meeting_stmt) + _count(session, item_stmt),
        items=unread_entries[:TODO_LIMIT],
    )

    return Todos(
        awaiting_confirm_meetings=awaiting,
        needs_completion_items=needs,
        my_items=my_items,
        unread_auto_confirmed=unread,
    )
