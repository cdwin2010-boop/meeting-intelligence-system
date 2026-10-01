"""회의록 조회 API(읽기 전용): 목록·상세·전사문.
열람 권한은 app/auth/access.py 한 곳에서 판정하고, 권한 없음·다른 고객사·없음은 모두 같은 404로 응답한다.
전사문 내용은 로그에 남기지 않는다."""
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import and_, case, func, or_, select
from sqlalchemy.orm import Session, aliased

from app.api.meeting_schemas import (
    AccountRef,
    ActionItemOut,
    EventOut,
    MeetingDetail,
    MeetingListItem,
    MeetingListPage,
    TranscriptOut,
)
from app.auth.access import get_visible_meeting, has_assigned_item, meeting_visibility
from app.auth.deps import get_current_account
from app.auth.scope import scoped
from app.db import get_session
from app.models import Account, ActionItem, Event, Job, Meeting, MeetingParticipant, SourceDocument, Transcript
from app.models.common import MEETING_STATUSES
from app.services.views import record_meeting_view

router = APIRouter(prefix="/api/meetings", tags=["meetings"])

RECENT_EVENT_LIMIT = 20
MeetingStatus = Literal[MEETING_STATUSES]  # type: ignore[valid-type]


def _not_found(message: str = "회의록을 찾을 수 없습니다") -> HTTPException:
    return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=message)


def _item_counts():
    """회의록별 업무 수·보완 필요 수(삭제된 업무 제외)를 한 번에 세는 집계 서브쿼리(목록 N+1 방지)."""
    return (
        select(
            ActionItem.meeting_id.label("meeting_id"),
            func.count(ActionItem.id).label("item_count"),
            func.sum(case((ActionItem.needs_supplement, 1), else_=0)).label("needs_count"),
        )
        .where(ActionItem.status != "deleted")
        .group_by(ActionItem.meeting_id)
        .subquery()
    )


@router.get("", response_model=MeetingListPage, response_model_by_alias=True)
def list_meetings(
    status_filter: MeetingStatus | None = Query(None, alias="status"),
    mine: Literal["registered", "assigned"] | None = Query(None),
    needs_completion: bool | None = Query(None, alias="needsCompletion"),
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=100),
    account: Account = Depends(get_current_account),
    session: Session = Depends(get_session),
) -> MeetingListPage:
    counts = _item_counts()
    registrant = aliased(Account)
    item_count = func.coalesce(counts.c.item_count, 0)
    needs_count = func.coalesce(counts.c.needs_count, 0)

    stmt = (
        select(
            Meeting,
            SourceDocument.origin,
            registrant.id.label("registrant_id"),
            registrant.name.label("registrant_name"),
            item_count.label("item_count"),
            needs_count.label("needs_count"),
        )
        .join(SourceDocument, SourceDocument.id == Meeting.source_document_id)
        .join(registrant, registrant.id == SourceDocument.registered_by)
        .outerjoin(counts, counts.c.meeting_id == Meeting.id)
        .where(meeting_visibility(account))
    )
    if status_filter is not None:
        stmt = stmt.where(Meeting.status == status_filter)
    if mine == "registered":
        stmt = stmt.where(SourceDocument.registered_by == account.id)
    elif mine == "assigned":
        stmt = stmt.where(has_assigned_item(account))
    if needs_completion is True:
        stmt = stmt.where(needs_count > 0)
    elif needs_completion is False:
        stmt = stmt.where(needs_count == 0)

    total = session.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = session.execute(
        stmt.order_by(Meeting.held_at.desc(), Meeting.id.desc()).limit(size).offset((page - 1) * size)
    ).all()

    items = [
        MeetingListItem(
            id=row.Meeting.id,
            title=row.Meeting.title,
            held_at=row.Meeting.held_at,
            registered_by=AccountRef(id=row.registrant_id, name=row.registrant_name),
            origin=row.origin,
            status=row.Meeting.status,
            confirm_kind=row.Meeting.confirm_kind,
            item_count=row.item_count,
            needs_completion_count=row.needs_count,
            auto_confirm_at=row.Meeting.auto_confirm_at,
        )
        for row in rows
    ]
    return MeetingListPage(items=items, total=total, page=page, size=size)


def _account_ref(session: Session, account_id: int | None) -> AccountRef | None:
    if account_id is None:
        return None
    found = session.get(Account, account_id)
    return AccountRef(id=found.id, name=found.name) if found else None


def _recent_events(session: Session, meeting: Meeting) -> list[EventOut]:
    """이 회의록과 그 작업·업무·전사문에 기록된 사건 최근 20건(최신순). payload 는 내보내지 않는다."""
    job_ids = select(Job.id).where(Job.meeting_id == meeting.id).scalar_subquery()
    item_ids = select(ActionItem.id).where(ActionItem.meeting_id == meeting.id).scalar_subquery()
    transcript_ids = select(Transcript.id).where(Transcript.meeting_id == meeting.id).scalar_subquery()
    actor = aliased(Account)
    rows = session.execute(
        select(Event.event_type, Event.created_at, actor.id.label("actor_id"), actor.name.label("actor_name"))
        .outerjoin(actor, actor.id == Event.actor_account_id)
        .where(
            Event.tenant_id == meeting.tenant_id,
            or_(
                and_(Event.entity_type == "meeting", Event.entity_id == meeting.id),
                and_(Event.entity_type == "job", Event.entity_id.in_(job_ids)),
                and_(Event.entity_type == "action_item", Event.entity_id.in_(item_ids)),
                and_(Event.entity_type == "transcript", Event.entity_id.in_(transcript_ids)),
            ),
        )
        .order_by(Event.id.desc())
        .limit(RECENT_EVENT_LIMIT)
    ).all()
    return [
        EventOut(
            event_type=row.event_type,
            actor=AccountRef(id=row.actor_id, name=row.actor_name) if row.actor_id is not None else None,
            created_at=row.created_at,
        )
        for row in rows
    ]


@router.get("/{meeting_id}", response_model=MeetingDetail, response_model_by_alias=True)
def get_meeting(
    meeting_id: int,
    account: Account = Depends(get_current_account),
    session: Session = Depends(get_session),
) -> MeetingDetail:
    meeting = get_visible_meeting(session, account, meeting_id)
    if meeting is None:
        raise _not_found()
    document = session.get(SourceDocument, meeting.source_document_id)

    participants = session.execute(
        select(Account.id, Account.name)
        .join(MeetingParticipant, MeetingParticipant.account_id == Account.id)
        .where(MeetingParticipant.meeting_id == meeting.id)
        .order_by(Account.id)
    ).all()

    assignee = aliased(Account)
    item_rows = session.execute(
        select(ActionItem, assignee.id.label("assignee_id"), assignee.name.label("assignee_name"))
        .outerjoin(assignee, assignee.id == ActionItem.assignee_id)
        .where(ActionItem.meeting_id == meeting.id, ActionItem.status != "deleted")
        .order_by(ActionItem.id)
    ).all()
    action_items = [
        ActionItemOut.from_item(
            row.ActionItem,
            AccountRef(id=row.assignee_id, name=row.assignee_name) if row.assignee_id is not None else None,
        )
        for row in item_rows
    ]

    detail = MeetingDetail(
        id=meeting.id,
        title=meeting.title,
        held_at=meeting.held_at,
        summary=meeting.summary,
        decisions=meeting.decisions or [],
        status=meeting.status,
        confirm_kind=meeting.confirm_kind,
        confirmed_by=_account_ref(session, meeting.confirmed_by),
        confirmed_at=meeting.confirmed_at,
        first_created_at=meeting.first_created_at,
        auto_confirm_at=meeting.auto_confirm_at,
        registered_by=_account_ref(session, document.registered_by),
        origin=document.origin,
        participants=[AccountRef(id=p.id, name=p.name) for p in participants],
        action_items=action_items,
        recent_events=_recent_events(session, meeting),
    )
    # 상세 조회가 성공한 경우에만 열람 기록(첫 열람 유지, 마지막 열람 갱신)
    record_meeting_view(session, account, meeting)
    return detail


@router.get("/{meeting_id}/transcript", response_model=TranscriptOut, response_model_by_alias=True)
def get_transcript(
    meeting_id: int,
    account: Account = Depends(get_current_account),
    session: Session = Depends(get_session),
) -> TranscriptOut:
    meeting = get_visible_meeting(session, account, meeting_id)
    if meeting is None:
        raise _not_found()
    transcript = session.scalar(scoped(select(Transcript), account).where(Transcript.meeting_id == meeting.id))
    if transcript is None:
        raise _not_found("전사문이 없습니다")
    return TranscriptOut(full_text=transcript.full_text, segments=transcript.segments, stt_provider=transcript.stt_provider)
