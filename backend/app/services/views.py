"""회의록 열람 기록: 상세 조회가 성공했을 때만 남긴다(목록·전사문·404 는 기록하지 않음)."""
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import Account, Meeting, MeetingView
from app.models.common import utcnow


def record_meeting_view(session: Session, account: Account, meeting: Meeting) -> None:
    """첫 열람 시각은 유지하고 마지막 열람 시각만 갱신한다(없으면 새로 만듦). 커밋까지 한다."""
    now = utcnow()
    view = session.get(MeetingView, (meeting.id, account.id))
    if view is None:
        session.add(
            MeetingView(meeting_id=meeting.id, account_id=account.id, tenant_id=meeting.tenant_id,
                        first_viewed_at=now, last_viewed_at=now)
        )
        try:
            session.commit()
            return
        except IntegrityError:
            # 같은 계정이 동시에 처음 열어 다른 요청이 먼저 만든 경우: 마지막 열람만 갱신
            session.rollback()
            view = session.get(MeetingView, (meeting.id, account.id))
            if view is None:
                raise
    view.last_viewed_at = now
    session.commit()
