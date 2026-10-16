"""질의 가능 여부(허용 동작 ask_meeting 의 서버 판정). 열람 권한은 get_visible_meeting 이 먼저 판정한다."""
from sqlalchemy.orm import Session

from app.models import Meeting, MeetingMinutes

ASKABLE_STATUSES = ("awaiting_confirmation", "confirmed")  # 정리된 회의록이 있는 상태
STATE_OK, STATE_DELETED, STATE_NO_MINUTES = "ok", "deleted", "no_minutes"


def ask_state(session: Session, meeting: Meeting) -> str:
    """ok / deleted(삭제된 회의록: 403) / no_minutes(정리된 회의록 없음: 409)."""
    if meeting.deleted:
        return STATE_DELETED
    if meeting.status not in ASKABLE_STATUSES or session.get(MeetingMinutes, meeting.id) is None:
        return STATE_NO_MINUTES
    return STATE_OK
