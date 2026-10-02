"""화자 매핑 조회·저장 API: GET·PUT /api/meetings/{id}/speakers.

- 조회 권한: 회의록을 볼 수 있어야 하고(아니면 404), 그중 등록자 또는 관리자 이상만(아니면 403).
- 저장 권한: 위에 더해 회의록 확정 권한(can_confirm_meeting: 지시자, 또는 총괄 관리자)이 있어야 한다(아니면 403).
  등록하지 않은 관리자와 담당자 직급 등록자는 저장할 수 없다.
- 저장은 전체 교체. 화자 표기는 그 회의록 전사문에 나오는 것만, 각 표기는 같은 고객사 활성 계정(accountId)
  또는 미등록 이름 글자(name) 중 정확히 하나. 어긋나면 400 이고 아무것도 저장하지 않는다.
- 직급 우선: 지금 매핑을 저장한 사람보다 직급이 낮으면 409(같거나 높으면 덮어씀). 수정 요청 해결과 같은 규칙.
- 저장 후 계정으로 매핑된 화자와 추출 담당자 글자가 맞는 빈 담당자 업무를 채운다(app/services/speakers.py).
- 매핑 변경·자동 채움·사건 기록은 한 트랜잭션으로 커밋한다."""
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import Field
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.api.meeting_hold import reject_if_on_hold
from app.api.meeting_queries import speaker_out
from app.api.meeting_schemas import SpeakerOut
from app.api.schemas import CamelModel
from app.auth.access import VIEW_ALL_RANKS, can_confirm_meeting, get_visible_meeting
from app.auth.deps import RANK_ORDER, get_current_account
from app.db import get_session
from app.models import Account, Meeting, MeetingSpeaker, SourceDocument, Transcript, append_event
from app.models.common import utcnow
from app.services.speakers import auto_assign_from_speakers, load_speakers, transcript_labels

router = APIRouter(prefix="/api/meetings", tags=["meetings"])

NAME_MAX = 50
LABEL_MAX = 100


class SpeakerIn(CamelModel):
    label: str
    account_id: int | None = None
    name: str | None = None


class SpeakersPut(CamelModel):
    speakers: list[SpeakerIn] = Field(default_factory=list, max_length=200)


class SpeakersOut(CamelModel):
    # 전사문에 나오는 화자 표기(처음 나온 순서)와 현재 매핑
    labels: list[str]
    speakers: list[SpeakerOut]
    # 이번 저장으로 담당자가 자동으로 채워진 업무 id(조회에서는 빈 목록)
    auto_assigned_item_ids: list[int] = []


def _http(code: int, detail: str) -> HTTPException:
    return HTTPException(status_code=code, detail=detail)


def _editable_meeting(session: Session, account: Account, meeting_id: int) -> Meeting:
    """볼 수 없으면 404, 볼 수는 있어도 등록자·관리자 이상이 아니면 403."""
    meeting = get_visible_meeting(session, account, meeting_id)
    if meeting is None:
        raise _http(status.HTTP_404_NOT_FOUND, "회의록을 찾을 수 없습니다")
    if account.rank not in VIEW_ALL_RANKS:
        registrant = session.scalar(select(SourceDocument.registered_by).where(SourceDocument.id == meeting.source_document_id))
        if registrant != account.id:
            raise _http(status.HTTP_403_FORBIDDEN, "권한이 없습니다")
    return meeting


def _transcript(session: Session, meeting: Meeting) -> Transcript | None:
    return session.scalar(
        select(Transcript).where(Transcript.meeting_id == meeting.id, Transcript.tenant_id == meeting.tenant_id)
    )


def _out(session: Session, meeting: Meeting, transcript: Transcript | None, auto_assigned: list[int] | None = None) -> SpeakersOut:
    return SpeakersOut(
        labels=transcript_labels(transcript),
        speakers=[speaker_out(s) for s in load_speakers(session, meeting.id)],
        auto_assigned_item_ids=auto_assigned or [],
    )


@router.get("/{meeting_id}/speakers", response_model=SpeakersOut, response_model_by_alias=True)
def get_speakers(
    meeting_id: int, account: Account = Depends(get_current_account), session: Session = Depends(get_session)
) -> SpeakersOut:
    meeting = _editable_meeting(session, account, meeting_id)
    return _out(session, meeting, _transcript(session, meeting))


@router.put("/{meeting_id}/speakers", response_model=SpeakersOut, response_model_by_alias=True)
def save_speakers(
    meeting_id: int,
    body: SpeakersPut,
    account: Account = Depends(get_current_account),
    session: Session = Depends(get_session),
) -> SpeakersOut:
    meeting = _editable_meeting(session, account, meeting_id)
    if not can_confirm_meeting(session, account, meeting):
        raise _http(status.HTTP_403_FORBIDDEN, "화자 매핑은 이 회의록을 확정할 수 있는 사람만 저장할 수 있습니다")
    reject_if_on_hold(meeting)
    transcript = _transcript(session, meeting)
    if transcript is None:
        raise _http(status.HTTP_404_NOT_FOUND, "전사문이 없습니다")

    # 직급 우선: 기존 매핑을 저장한 사람 중 가장 높은 직급보다 낮으면 거부
    previous_ranks = session.scalars(
        select(Account.rank)
        .join(MeetingSpeaker, MeetingSpeaker.updated_by == Account.id)
        .where(MeetingSpeaker.meeting_id == meeting.id)
    ).all()
    if previous_ranks and RANK_ORDER[account.rank] < max(RANK_ORDER.get(r, 0) for r in previous_ranks):
        raise _http(status.HTTP_409_CONFLICT, "더 높은 직급이 정한 화자 매핑은 바꿀 수 없습니다")

    # 입력 검사(하나라도 어긋나면 아무것도 저장하지 않음)
    known = set(transcript_labels(transcript))
    seen: set[str] = set()
    rows: list[tuple[str, int | None, str]] = []
    for entry in body.speakers:
        label = entry.label.strip()
        if not label or len(label) > LABEL_MAX or label not in known:
            raise _http(status.HTTP_400_BAD_REQUEST, "전사문에 없는 화자 표기입니다")
        if label in seen:
            raise _http(status.HTTP_400_BAD_REQUEST, "같은 화자 표기를 두 번 지정할 수 없습니다")
        seen.add(label)
        name = (entry.name or "").strip()
        if (entry.account_id is None) == (not name):
            raise _http(status.HTTP_400_BAD_REQUEST, "화자마다 계정 또는 미등록 이름 중 하나만 지정하세요")
        if entry.account_id is not None:
            target = session.get(Account, entry.account_id)
            if target is None or target.tenant_id != meeting.tenant_id or not target.is_active:
                # 다른 고객사·없는 계정·비활성 계정을 구분하지 않는다
                raise _http(status.HTTP_400_BAD_REQUEST, "같은 고객사의 활성 계정만 지정할 수 있습니다")
            rows.append((label, target.id, ""))
        else:
            if len(name) > NAME_MAX:
                raise _http(status.HTTP_400_BAD_REQUEST, f"미등록 이름은 {NAME_MAX}자 이하로 입력하세요")
            rows.append((label, None, name))

    # 전체 교체
    session.execute(delete(MeetingSpeaker).where(MeetingSpeaker.meeting_id == meeting.id))
    now = utcnow()
    for label, account_id, name in rows:
        session.add(
            MeetingSpeaker(tenant_id=meeting.tenant_id, meeting_id=meeting.id, label=label,
                           account_id=account_id, display_name=name, updated_by=account.id, updated_at=now)
        )
    session.flush()

    filled = auto_assign_from_speakers(session, meeting_id=meeting.id, tenant_id=meeting.tenant_id, actor=account)
    append_event(
        session, tenant_id=meeting.tenant_id, entity_type="meeting", entity_id=meeting.id,
        event_type="meeting.speakers_updated", actor_account_id=account.id,
        payload={
            "accounts": sum(1 for r in rows if r[1] is not None),
            "unregistered": sum(1 for r in rows if r[1] is None),
            "autoAssignedItemIds": filled,
        },
    )
    session.commit()
    return _out(session, meeting, transcript, filled)
