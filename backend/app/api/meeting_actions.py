"""회의록 쓰기 API: 회의록 확정, 수정 요청(기록·조회·해결).

- 수정 요청은 상태가 아니라 사건 원장에 남는 기록이다. 회의록·업무 상태와 자동 확정 시계에 영향을 주지 않는다(부록 C-4).
- 해결 결정은 상위 직급 우선: 이미 결정된 요청을 더 낮은 직급이 다시 결정하면 409, 같거나 높은 직급은 덮어쓴다
  (이전 결정 사건은 지우지 않고 그대로 남는다).
- 데이터 변경과 이벤트 기록은 같은 세션에서 한 번에 커밋한다.
"""
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.action_items import confirm_item
from app.api.action_schemas import (
    ChangeRequestCreate,
    ChangeRequestCreated,
    ChangeRequestOut,
    ChangeRequestResolve,
    MeetingConfirmResult,
    Resolution,
)
from app.api.meeting_hold import reject_if_on_hold
from app.api.meeting_schemas import AccountRef
from app.auth.access import can_confirm_meeting, get_visible_meeting
from app.auth.deps import RANK_ORDER, get_current_account, require_rank
from app.db import get_session
from app.models import Account, ActionItem, Event, Meeting, append_event
from app.models.common import utcnow
from app.services.notices import create_confirm_notices

router = APIRouter(prefix="/api/meetings", tags=["meetings"])

CR_CREATED = "change_request.created"
CR_RESOLVED = "change_request.resolved"
# 수정 요청 해결(수락·반려) 최소 직급. 해결 API 와 할 일 "수정 요청 대기"(app/api/me.py)가 같은 판정을 쓴다:
# 이 직급 이상 + 회의록 열람 가능(meeting_visibility). 직급 우선(409)은 이미 해결된 요청을 다시 결정할 때만 해당
RESOLVE_MIN_RANK = "manager"


def _http(code: int, detail) -> HTTPException:
    return HTTPException(status_code=code, detail=detail)


def _visible_meeting_or_404(session: Session, account: Account, meeting_id: int) -> Meeting:
    meeting = get_visible_meeting(session, account, meeting_id)
    if meeting is None:
        raise _http(status.HTTP_404_NOT_FOUND, "회의록을 찾을 수 없습니다")
    return meeting


def _ref(session: Session, account_id: int | None) -> AccountRef | None:
    if account_id is None:
        return None
    found = session.get(Account, account_id)
    return AccountRef(id=found.id, name=found.name) if found else None


# ---------------- 회의록 확정 ----------------
@router.post("/{meeting_id}/confirm", response_model=MeetingConfirmResult, response_model_by_alias=True)
def confirm_meeting(
    meeting_id: int,
    with_items: bool = Query(False, alias="withItems"),
    account: Account = Depends(require_rank("manager")),
    session: Session = Depends(get_session),
) -> MeetingConfirmResult:
    meeting = _visible_meeting_or_404(session, account, meeting_id)
    # 회의록 확정은 총괄 관리자(등록자, 담당자 등록이면 참석 관리자)와 지시자만(그 밖의 관리자는 403). 상태 검사보다 먼저
    if not can_confirm_meeting(session, account, meeting):
        raise _http(status.HTTP_403_FORBIDDEN, "회의록을 총괄하는 관리자 또는 지시자만 확정할 수 있습니다")
    reject_if_on_hold(meeting)
    if meeting.status not in ("awaiting_confirmation", "confirmed"):
        raise _http(status.HTTP_409_CONFLICT, f"확정할 수 없는 상태입니다({meeting.status})")

    if meeting.status == "awaiting_confirmation":
        # 업무 보완 여부와 무관하게 회의록은 확정한다(회의록 확정과 업무 확정은 분리, 부록 C-5)
        meeting.status, meeting.confirm_kind = "confirmed", "manager"
        meeting.confirmed_by, meeting.confirmed_at = account.id, utcnow()
        append_event(
            session, tenant_id=meeting.tenant_id, entity_type="meeting", entity_id=meeting.id,
            event_type="meeting.confirmed", actor_account_id=account.id,
            payload={"before": {"status": "awaiting_confirmation"}, "after": {"status": "confirmed", "confirmKind": "manager"}},
        )
        create_confirm_notices(
            session, meeting=meeting, entity_type="meeting", entity_id=meeting.id,
            confirm_kind="manager", title=meeting.title, actor_id=account.id,
        )
    # 이미 confirmed 면 회의록은 그대로(멱등). withItems 는 아래에서 그대로 처리한다

    confirmed_ids: list[int] = []
    skipped_ids: list[int] = []
    if with_items:
        pending = session.scalars(
            select(ActionItem)
            .where(ActionItem.meeting_id == meeting.id, ActionItem.status == "pending")
            .order_by(ActionItem.id)
        ).all()
        for item in pending:
            if item.needs_supplement:
                skipped_ids.append(item.id)
            elif confirm_item(session, item, account):
                confirmed_ids.append(item.id)

    session.commit()
    return MeetingConfirmResult(
        id=meeting.id,
        status=meeting.status,
        confirm_kind=meeting.confirm_kind,
        confirmed_by=_ref(session, meeting.confirmed_by),
        confirmed_at=meeting.confirmed_at,
        confirmed_item_ids=confirmed_ids,
        skipped_item_ids=skipped_ids,
    )


# ---------------- 수정 요청 ----------------
def _request_event(session: Session, meeting: Meeting, request_id: int) -> Event:
    event = session.scalar(
        select(Event).where(
            Event.id == request_id,
            Event.tenant_id == meeting.tenant_id,
            Event.entity_type == "meeting",
            Event.entity_id == meeting.id,
            Event.event_type == CR_CREATED,
        )
    )
    if event is None:
        raise _http(status.HTTP_404_NOT_FOUND, "수정 요청을 찾을 수 없습니다")
    return event


def _latest_resolutions(session: Session, tenant_id: int, request_ids: list[int]) -> dict[int, Event]:
    """요청별 가장 최근 해결 사건(이전 결정 사건은 원장에 그대로 남아 있다)."""
    if not request_ids:
        return {}
    events = session.scalars(
        select(Event)
        .where(
            Event.tenant_id == tenant_id,
            Event.entity_type == "change_request",
            Event.entity_id.in_(request_ids),
            Event.event_type == CR_RESOLVED,
        )
        .order_by(Event.id)
    ).all()
    latest: dict[int, Event] = {}
    for event in events:
        latest[event.entity_id] = event
    return latest


def _resolution(session: Session, event: Event | None) -> Resolution | None:
    if event is None:
        return None
    return Resolution(
        decision=event.payload["after"],
        reason=event.payload.get("reason"),
        resolved_by=_ref(session, event.actor_account_id),
        resolved_at=event.created_at,
    )


def _request_out(session: Session, request: Event, resolution: Event | None) -> ChangeRequestOut:
    return ChangeRequestOut(
        request_id=request.id,
        requester=_ref(session, request.actor_account_id),
        comment=request.payload.get("comment", ""),
        item_id=request.payload.get("itemId"),
        created_at=request.created_at,
        resolution=_resolution(session, resolution),
    )


@router.post(
    "/{meeting_id}/change-requests",
    status_code=status.HTTP_201_CREATED,
    response_model=ChangeRequestCreated,
    response_model_by_alias=True,
)
def create_change_request(
    meeting_id: int,
    body: ChangeRequestCreate,
    account: Account = Depends(get_current_account),
    session: Session = Depends(get_session),
) -> ChangeRequestCreated:
    meeting = _visible_meeting_or_404(session, account, meeting_id)
    if body.item_id is not None:
        item = session.get(ActionItem, body.item_id)
        if item is None or item.meeting_id != meeting.id or item.status == "deleted":
            raise _http(status.HTTP_400_BAD_REQUEST, "같은 회의록의 업무만 지정할 수 있습니다")
    # 상태 변경 없음: 사건 기록만 남긴다(자동 확정 시계도 그대로)
    event = append_event(
        session, tenant_id=meeting.tenant_id, entity_type="meeting", entity_id=meeting.id,
        event_type=CR_CREATED, actor_account_id=account.id,
        payload={"comment": body.comment, "itemId": body.item_id},
    )
    session.commit()
    return ChangeRequestCreated(request_id=event.id)


@router.get("/{meeting_id}/change-requests", response_model=list[ChangeRequestOut], response_model_by_alias=True)
def list_change_requests(
    meeting_id: int,
    account: Account = Depends(get_current_account),
    session: Session = Depends(get_session),
) -> list[ChangeRequestOut]:
    meeting = _visible_meeting_or_404(session, account, meeting_id)
    requests = session.scalars(
        select(Event)
        .where(
            Event.tenant_id == meeting.tenant_id,
            Event.entity_type == "meeting",
            Event.entity_id == meeting.id,
            Event.event_type == CR_CREATED,
        )
        .order_by(Event.id)
    ).all()
    latest = _latest_resolutions(session, meeting.tenant_id, [r.id for r in requests])
    return [_request_out(session, r, latest.get(r.id)) for r in requests]


@router.post(
    "/{meeting_id}/change-requests/{request_id}/resolve",
    response_model=ChangeRequestOut,
    response_model_by_alias=True,
)
def resolve_change_request(
    meeting_id: int,
    request_id: int,
    body: ChangeRequestResolve,
    account: Account = Depends(require_rank(RESOLVE_MIN_RANK)),
    session: Session = Depends(get_session),
) -> ChangeRequestOut:
    meeting = _visible_meeting_or_404(session, account, meeting_id)
    request = _request_event(session, meeting, request_id)
    previous = _latest_resolutions(session, meeting.tenant_id, [request.id]).get(request.id)

    if previous is not None:
        # 상위 직급 우선: 결정 당시 직급(payload.rank)보다 낮으면 덮어쓸 수 없다
        previous_rank = previous.payload.get("rank", "staff")
        if RANK_ORDER[account.rank] < RANK_ORDER.get(previous_rank, 0):
            raise _http(status.HTTP_409_CONFLICT, "더 높은 직급이 이미 결정한 요청입니다")

    event = append_event(
        session, tenant_id=meeting.tenant_id, entity_type="change_request", entity_id=request.id,
        event_type=CR_RESOLVED, actor_account_id=account.id,
        payload={
            "before": previous.payload["after"] if previous is not None else None,
            "after": body.decision,
            "reason": body.reason,
            "rank": account.rank,
        },
    )
    session.commit()
    return _request_out(session, request, event)
