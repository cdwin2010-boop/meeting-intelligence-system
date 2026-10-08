"""회의록 조회 API(읽기 전용): 목록·상세·전사문.
열람 권한은 app/auth/access.py 한 곳에서 판정하고, 권한 없음·다른 고객사·없음은 모두 같은 404로 응답한다.
전사문 내용은 로그에 남기지 않는다."""
import re
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import Integer, and_, case, cast, func, or_, select
from sqlalchemy.orm import Session, aliased

from app.api.meeting_schemas import (
    AccountRef,
    ActionItemOut,
    EventOut,
    MeetingDetail,
    ProjectRef,
    SupersessionRef,
    MeetingListItem,
    MeetingListPage,
    MinutesOut,
    ProcessingOut,
    SpeakerOut,
    TranscriptOut,
)
from app.auth.actions import available_phases, item_allowed_actions, meeting_allowed_actions, phase_allowed
from app.auth.access import VIEW_ALL_RANKS, can_confirm_meeting, get_visible_meeting, has_assigned_item, meeting_visibility
from app.auth.deps import get_current_account
from app.auth.scope import scoped
from app.db import get_session
from app.models import (
    Account, ActionItem, Event, Job, Meeting, MeetingClassification, MeetingClosure, MeetingGuestParticipant, MeetingHold, MeetingMinutes, MeetingParticipant, SourceDocument,
    Project, Transcript,
)
from app.models.minutes import MINUTES_FIELDS
from app.models.closure import meeting_phase, phase_condition
from app.models.common import MEETING_STATUSES
from app.services.item_closure import closure_kinds
from app.services.source_kind import SOURCE_AUDIO, source_kind_of_path
from app.services.supersession import project_of_meeting, superseded_ids, supersession_refs
from app.models.item_conditions import live_item, not_deleted_item
from app.services.reasons import event_reason, latest_reason
from app.services.speakers import SpeakerView, display_text, load_speakers
from app.services.history import record_meeting_view
from app.services.reprocess import latest_job, safe_error_code

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
            # 합계는 엔진마다 반환형이 다르다(PostgreSQL 은 Decimal). 한 곳에서 coalesce + 정수 변환으로 int 를 보장한다
            cast(func.coalesce(func.sum(case((and_(ActionItem.needs_supplement, live_item()), 1), else_=0)), 0), Integer).label("needs_count"),
        )
        .where(not_deleted_item())
        .group_by(ActionItem.meeting_id)
        .subquery()
    )


@router.get("", response_model=MeetingListPage, response_model_by_alias=True)
def list_meetings(
    status_filter: MeetingStatus | None = Query(None, alias="status"),
    # 회의록 단계(기본 진행중). 담당자는 진행중·종료만(보류·삭제는 403)
    phase: Literal["active", "ended", "on_hold", "deleted"] = Query("active"),
    mine: Literal["registered", "assigned"] | None = Query(None),
    needs_completion: bool | None = Query(None, alias="needsCompletion"),
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=100),
    account: Account = Depends(get_current_account),
    session: Session = Depends(get_session),
) -> MeetingListPage:
    return build_meeting_page(
        session, account, status_filter=status_filter, phase=phase, mine=mine, needs_completion=needs_completion, page=page, size=size,
    )


def build_meeting_page(
    session: Session,
    account: Account,
    *,
    status_filter: str | None,
    phase: str,
    mine: str | None,
    needs_completion: bool | None,
    page: int,
    size: int,
    extra_conditions: tuple = (),
) -> MeetingListPage:
    """회의록 목록 쪽(열람 가능한 것만, 최신 회의 일시순). 회의록 목록 API 와 프로젝트별 회의록 조회(extra_conditions 로 범위를 좁힘)가 함께 쓴다."""
    if not phase_allowed(account, phase):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="보류·삭제된 회의록은 관리자 이상만 볼 수 있습니다")
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
        .where(meeting_visibility(account), phase_condition(phase), *extra_conditions)
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
            phase=meeting_phase(row.Meeting),
        )
        for row in rows
    ]
    return MeetingListPage(items=items, total=total, page=page, size=size, available_phases=available_phases(account))


def _account_ref(session: Session, account_id: int | None) -> AccountRef | None:
    if account_id is None:
        return None
    found = session.get(Account, account_id)
    return AccountRef(id=found.id, name=found.name) if found else None


def processing_out(session: Session, meeting: Meeting) -> ProcessingOut | None:
    """가장 최근 처리 작업(상태·분류된 오류 코드·시작·종료 시각). 코드가 분류 코드 모양이 아니면 internal_error 로 바꿔 내려준다."""
    job = latest_job(session, meeting.id)
    if job is None:
        return None
    code = safe_error_code(job.error_code)
    return ProcessingOut(status=job.status, error_code=code, started_at=job.started_at, finished_at=job.finished_at)


def minutes_out(session: Session, meeting: Meeting) -> MinutesOut:
    """저장된 5개 항목. 아직 없으면(처리 전·실패·이 기능 이전 회의록) 모두 "내용없음"."""
    row = session.get(MeetingMinutes, meeting.id)
    if row is None:
        return MinutesOut()
    return MinutesOut(
        **{key: getattr(row, key) for key in MINUTES_FIELDS},
        engine=row.engine or None,
        updated_by=_account_ref(session, row.updated_by),
        updated_at=row.updated_at,
    )


def _recent_events(session: Session, meeting: Meeting) -> list[EventOut]:
    """이 회의록과 그 작업·업무·전사문에 기록된 사건 최근 20건(최신순). payload 는 내보내지 않고 처리 사유만 꺼내 준다."""
    job_ids = select(Job.id).where(Job.meeting_id == meeting.id).scalar_subquery()
    item_ids = select(ActionItem.id).where(ActionItem.meeting_id == meeting.id).scalar_subquery()
    transcript_ids = select(Transcript.id).where(Transcript.meeting_id == meeting.id).scalar_subquery()
    actor = aliased(Account)
    rows = session.execute(
        select(Event.event_type, Event.created_at, Event.payload, actor.id.label("actor_id"), actor.name.label("actor_name"))
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
            reason=event_reason(row.payload),
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
        .where(ActionItem.meeting_id == meeting.id, not_deleted_item())
        .order_by(ActionItem.id)
    ).all()
    action_items = [
        ActionItemOut.from_item(
            row.ActionItem,
            AccountRef(id=row.assignee_id, name=row.assignee_name) if row.assignee_id is not None else None,
        )
        for row in item_rows
    ]
    # 허용 동작(화면 표시용): 회의록 단위 한 번 + 업무마다. 판정 규칙은 app/auth/actions.py 가 기존 함수로 계산한다
    lead = can_confirm_meeting(session, account, meeting)
    item_ids = [row.ActionItem.id for row in item_rows]
    superseded_set = superseded_ids(session, item_ids)
    kinds = closure_kinds(session, item_ids)
    superseded_by, supersedes = supersession_refs(session, account, item_ids)
    linked = project_of_meeting(session, meeting.id)
    for out, row in zip(action_items, item_rows):
        out.allowed_actions = item_allowed_actions(
            session, account, row.ActionItem, meeting, lead=lead, project=linked, superseded=row.ActionItem.id in superseded_set,
        )
        out.closure_kind = kinds.get(row.ActionItem.id)
        ref = superseded_by.get(row.ActionItem.id)
        if ref is not None:
            out.superseded_by = SupersessionRef(item_id=ref["itemId"], meeting_id=ref["meetingId"], meeting_title=ref["meetingTitle"])
        out.supersedes = [
            SupersessionRef(item_id=r["itemId"], meeting_id=r["meetingId"], meeting_title=r["meetingTitle"]) for r in supersedes.get(row.ActionItem.id, [])
        ]

    hold = session.get(MeetingHold, meeting.id)
    closure = session.get(MeetingClosure, meeting.id)
    classification = session.scalar(select(MeetingClassification).where(MeetingClassification.meeting_id == meeting.id))
    linked_project = session.get(Project, classification.project_id) if classification and classification.project_id is not None else None
    kind = source_kind_of_path(document.file_path if document is not None else None)
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
        guest_participants=list(session.scalars(
            select(MeetingGuestParticipant.name).where(MeetingGuestParticipant.meeting_id == meeting.id).order_by(MeetingGuestParticipant.id)
        )),
        action_items=action_items,
        recent_events=_recent_events(session, meeting),
        on_hold=bool(hold and hold.on_hold),
        on_hold_by=_account_ref(session, hold.on_hold_by) if hold else None,
        on_hold_at=hold.on_hold_at if hold else None,
        resumed_by=_account_ref(session, hold.resumed_by) if hold else None,
        resumed_at=hold.resumed_at if hold else None,
        phase=meeting_phase(meeting),
        end_kind=closure.end_kind if closure else None,
        ended_by=_account_ref(session, closure.ended_by) if closure else None,
        ended_at=closure.ended_at if closure else None,
        deleted_by=_account_ref(session, closure.deleted_by) if closure else None,
        deleted_at=closure.deleted_at if closure else None,
        on_hold_reason=latest_reason(session, meeting, "meeting.on_hold") if hold else None,
        end_reason=latest_reason(session, meeting, "meeting.ended") if closure and closure.ended_at else None,
        delete_reason=latest_reason(session, meeting, "meeting.deleted") if closure and closure.deleted_at else None,
        minutes=minutes_out(session, meeting),
        meeting_type=classification.meeting_type if classification else None,
        # 프로젝트 이름은 열람 가능한 회의록에서만 나간다(프로젝트를 볼 수 없어도 회의록을 볼 수 있으면 나감)
        project=ProjectRef(id=linked_project.id, name=linked_project.name) if linked_project else None,
        processing=processing_out(session, meeting),
        source_kind=kind,
        has_audio=kind == SOURCE_AUDIO and bool(document is not None and document.file_path),
        allowed_actions=meeting_allowed_actions(session, account, meeting),
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
    speakers = load_speakers(session, meeting.id)
    return TranscriptOut(
        full_text=transcript.full_text,
        segments=transcript.segments,
        stt_provider=transcript.stt_provider,
        display_text=display_text(transcript.full_text, speakers),
        speakers=[speaker_out(s) for s in speakers],
    )


def speaker_out(speaker: SpeakerView) -> SpeakerOut:
    """화자 매핑 → 응답(미등록이면 이름 글자와 "이름(미등록)" 표시)."""
    return SpeakerOut(
        label=speaker.label,
        account_id=speaker.account_id,
        account_name=speaker.account_name,
        name=speaker.display_name if speaker.unregistered else None,
        display_name=speaker.display,
        unregistered=speaker.unregistered,
    )
