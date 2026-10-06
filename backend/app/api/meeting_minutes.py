"""회의록 5개 항목(목적·주요 논의사항·결정사항·리스크·다음 안건) 직권 수정, 공통 변경 이력 조회, 엑셀 내려받기.

- 조회·내려받기: 회의록 열람 판정(get_visible_meeting) 그대로. 볼 수 없으면 404.
- 직권 수정(PATCH /{id}/minutes): 열람 + 회의록 확정 권한(can_confirm_meeting: 지시자·총괄 관리자). 확정 이후에도 가능, 사유는 받지 않는다.
  보류·종료·삭제된 회의록은 409(reject_if_locked). 바뀐 항목만 변경 이력(구분 "직권 수정")에 남기고, 바뀐 것이 없으면 이력도 남기지 않는다.
  비운 항목은 "내용없음"으로 저장한다(AI 생성과 같은 규칙).
- 변경 이력은 공통 구조(app/services/history.py, events 표 재사용)."""
from datetime import datetime
from typing import Any
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Response, status
from pydantic import Field
from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session, aliased

from app.api.meeting_hold import reject_if_locked
from app.api.meeting_queries import minutes_out
from app.api.meeting_schemas import AccountRef, MinutesOut
from app.api.schemas import CamelModel
from app.auth.access import can_confirm_meeting, get_visible_meeting
from app.auth.deps import get_current_account
from app.db import get_session
from app.models import Account, ActionItem, Event, Meeting, MeetingGuestParticipant, MeetingParticipant, SourceDocument
from app.models.minutes import MINUTES_FIELDS
from app.services import export_sheets as sheets
from app.services.history import HISTORY_KINDS, history_fields
from app.services.minutes_update import apply_minutes_changes

router = APIRouter(prefix="/api/meetings", tags=["meeting-minutes"])

MINUTES_MAX = 5000
HISTORY_LIMIT = 200


class MinutesPatch(CamelModel):
    purpose: str | None = Field(None, max_length=MINUTES_MAX)
    discussion: str | None = Field(None, max_length=MINUTES_MAX)
    decisions: str | None = Field(None, max_length=MINUTES_MAX)
    risks: str | None = Field(None, max_length=MINUTES_MAX)
    next_agenda: str | None = Field(None, max_length=MINUTES_MAX)


class HistoryOut(CamelModel):
    id: int
    target_type: str
    target_id: int
    kind: str
    # 구분 코드(예: minutes.overridden)와 같은 업로드 묶음 식별자(업로드로 생긴 이력만)
    kind_code: str = ""
    batch_id: str | None = None
    before: dict[str, Any]
    after: dict[str, Any]
    # 변경 전 값이 기록되지 않은 옛 사건(before 는 빈 값)
    before_missing: bool = False
    # 참석자 변경 때 빠진 사람의 열람 영향([{accountId, name, loginId, viewImpact}]), 그 밖에는 None
    view_impact: list[dict[str, Any]] | None = None
    changed_by: AccountRef | None
    changed_at: datetime


def _http(code: int, detail: str) -> HTTPException:
    return HTTPException(status_code=code, detail=detail)


def _visible(session: Session, account: Account, meeting_id: int) -> Meeting:
    meeting = get_visible_meeting(session, account, meeting_id)
    if meeting is None:
        raise _http(status.HTTP_404_NOT_FOUND, "회의록을 찾을 수 없습니다")
    return meeting


@router.patch("/{meeting_id}/minutes", response_model=MinutesOut, response_model_by_alias=True)
def override_minutes(
    meeting_id: int,
    body: MinutesPatch,
    account: Account = Depends(get_current_account),
    session: Session = Depends(get_session),
) -> MinutesOut:
    meeting = _visible(session, account, meeting_id)
    if not can_confirm_meeting(session, account, meeting):
        raise _http(status.HTTP_403_FORBIDDEN, "회의록 항목은 이 회의록을 확정할 수 있는 사람만 수정할 수 있습니다")
    reject_if_locked(meeting)

    sent = {key: getattr(body, key) for key in body.model_fields_set if key in MINUTES_FIELDS}
    if not sent or any(value is None for value in sent.values()):
        raise _http(status.HTTP_400_BAD_REQUEST, "수정할 항목을 글자로 보내세요")

    apply_minutes_changes(session, meeting, account, sent)
    session.commit()
    return minutes_out(session, meeting)


@router.get("/{meeting_id}/history", response_model=list[HistoryOut], response_model_by_alias=True)
def get_history(
    meeting_id: int,
    account: Account = Depends(get_current_account),
    session: Session = Depends(get_session),
) -> list[HistoryOut]:
    """이 회의록의 변경 이력(최신순, 최대 200건): 5개 항목·참석자 직권 수정, 업로드 업무 갱신, 직권 등록 업무. 업무 대상 이력은 targetType=action_item."""
    meeting = _visible(session, account, meeting_id)
    actor = aliased(Account)
    rows = session.execute(
        select(Event, actor.id.label("actor_id"), actor.name.label("actor_name"))
        .outerjoin(actor, actor.id == Event.actor_account_id)
        .where(
            Event.tenant_id == meeting.tenant_id,
            or_(
                and_(Event.entity_type == "meeting", Event.entity_id == meeting.id),
                and_(Event.entity_type == "action_item", Event.entity_id.in_(select(ActionItem.id).where(ActionItem.meeting_id == meeting.id))),
            ),
            Event.event_type.in_(list(HISTORY_KINDS)),
        )
        .order_by(Event.id.desc())
        .limit(HISTORY_LIMIT)
    ).all()
    return [
        HistoryOut(
            id=row.Event.id,
            target_type=row.Event.entity_type,
            target_id=row.Event.entity_id,
            kind=HISTORY_KINDS[row.Event.event_type],
            kind_code=row.Event.event_type,
            **history_fields(row.Event),
            changed_by=AccountRef(id=row.actor_id, name=row.actor_name) if row.actor_id is not None else None,
            changed_at=row.Event.created_at,
        )
        for row in rows
    ]


def _download_name(meeting: Meeting) -> str:
    """파일명: 제목_회의일.xlsx (경로·제어 문자는 '_' 로 바꾼다)"""
    bad = '\\/:*?"<>|\r\n\t'
    title = "".join("_" if ch in bad else ch for ch in meeting.title).strip(" .") or f"회의록-{meeting.id}"
    return f"{title[:80]}_{sheets.local_text(meeting.held_at)[:10].replace('-', '')}.xlsx"


@router.get("/{meeting_id}/export")
def export_meeting(
    meeting_id: int,
    account: Account = Depends(get_current_account),
    session: Session = Depends(get_session),
) -> Response:
    """회의록을 엑셀(.xlsx, 읽기 전용 내려받기)로. 시트 "회의 정보"·"업무". 열람 가능한 사람 전원."""
    meeting = _visible(session, account, meeting_id)
    document = session.get(SourceDocument, meeting.source_document_id)
    registrant = session.get(Account, document.registered_by)
    confirmer = session.get(Account, meeting.confirmed_by) if meeting.confirmed_by is not None else None

    participants = session.execute(
        select(Account.name, Account.login_id)
        .join(MeetingParticipant, MeetingParticipant.account_id == Account.id)
        .where(MeetingParticipant.meeting_id == meeting.id)
        .order_by(Account.id)
    ).all()

    guests = session.scalars(
        select(MeetingGuestParticipant.name).where(MeetingGuestParticipant.meeting_id == meeting.id).order_by(MeetingGuestParticipant.id)
    ).all()
    minutes = minutes_out(session, meeting)
    confirm_parts = []
    if meeting.status == "confirmed":
        confirm_parts = [sheets.CONFIRM_KIND_LABEL.get(meeting.confirm_kind or "", "확정")]
        if confirmer is not None:
            confirm_parts.append(confirmer.name)
        if meeting.confirmed_at is not None:
            confirm_parts.append(sheets.local_text(meeting.confirmed_at))
    info = {
        sheets.INFO_TITLE: meeting.title,
        sheets.INFO_HELD_AT: sheets.local_text(meeting.held_at),
        sheets.INFO_STATUS: sheets.MEETING_STATUS_LABEL.get(meeting.status, meeting.status),
        sheets.INFO_REGISTRANT: f"{registrant.name}({registrant.login_id})" if registrant else "",
        sheets.INFO_CONFIRM: " · ".join(confirm_parts),
        sheets.INFO_PARTICIPANTS: ", ".join([f"{p.name}({p.login_id})" for p in participants] + [f"{g}({sheets.UNREGISTERED})" for g in guests]),
        **{label: getattr(minutes, key) for key, label in MINUTES_FIELDS.items()},
    }

    assignee = aliased(Account)
    item_rows = session.execute(
        select(ActionItem, assignee.name.label("assignee_name"), assignee.login_id.label("assignee_login"))
        .outerjoin(assignee, assignee.id == ActionItem.assignee_id)
        .where(ActionItem.meeting_id == meeting.id, ActionItem.status != "deleted")
        .order_by(ActionItem.id)
    ).all()
    tasks = [
        {
            sheets.COL_ID: row.ActionItem.id,
            sheets.COL_TITLE: row.ActionItem.title,
            sheets.COL_ASSIGNEE: row.assignee_name or "",
            sheets.COL_ASSIGNEE_LOGIN: row.assignee_login or "",
            sheets.COL_DUE: sheets.due_text(row.ActionItem.due_date, row.ActionItem.due_undetermined),
            sheets.COL_STATUS: sheets.ITEM_STATUS_LABEL.get(row.ActionItem.status, row.ActionItem.status),
            sheets.COL_EVIDENCE_TIME: sheets.offset_text(row.ActionItem.evidence_start_sec),
            sheets.COL_EVIDENCE_QUOTE: row.ActionItem.evidence_quote or "",
        }
        for row in item_rows
    ]

    name = _download_name(meeting)
    return Response(
        content=sheets.build_workbook(info, tasks),
        media_type=sheets.XLSX_MIME,
        headers={
            # 한글 파일명은 RFC 5987(filename*=UTF-8'')로, 구형 클라이언트용 ASCII 이름을 함께 준다
            "Content-Disposition": f"attachment; filename=\"meeting-{meeting.id}.xlsx\"; filename*=UTF-8''{quote(name)}",
            "Cache-Control": "private, no-store",
        },
    )
