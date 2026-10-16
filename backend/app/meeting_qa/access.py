"""질의 가능 여부(허용 동작 ask_meeting 의 서버 판정). 열람 권한은 get_visible_meeting 이 먼저 판정한다."""
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.access import meeting_visibility
from app.auth.scope import scoped
from app.models import Account, Meeting, MeetingClassification, MeetingMinutes, Project

ASKABLE_STATUSES = ("awaiting_confirmation", "confirmed")  # 정리된 회의록이 있는 상태
STATE_OK, STATE_DELETED, STATE_NO_MINUTES = "ok", "deleted", "no_minutes"


def ask_state(session: Session, meeting: Meeting) -> str:
    """ok / deleted(삭제된 회의록: 403) / no_minutes(정리된 회의록 없음: 409)."""
    if meeting.deleted:
        return STATE_DELETED
    if meeting.status not in ASKABLE_STATUSES or session.get(MeetingMinutes, meeting.id) is None:
        return STATE_NO_MINUTES
    return STATE_OK


def project_askable_meetings(session: Session, account: Account, project: Project, limit: int | None = None) -> list[Meeting]:
    """프로젝트 질의의 자료가 되는 회의록(작업 89-2b): 그 프로젝트에 연결되고, 이 계정이 열람할 수 있고, 삭제되지 않았고, 정리된 회의록이 있는 것.
    범위는 서버가 프로젝트 ID 로 정한다(요청이 회의록을 고르지 못한다). 최근 회의 순."""
    query = (
        scoped(select(Meeting), account)
        .join(MeetingMinutes, MeetingMinutes.meeting_id == Meeting.id)
        .where(
            Meeting.id.in_(select(MeetingClassification.meeting_id).where(MeetingClassification.project_id == project.id)),
            meeting_visibility(account),
            ~Meeting.deleted,
            Meeting.status.in_(ASKABLE_STATUSES),
        )
        .order_by(Meeting.held_at.desc(), Meeting.id.desc())
    )
    if limit is not None:
        query = query.limit(limit)
    return list(session.scalars(query))
