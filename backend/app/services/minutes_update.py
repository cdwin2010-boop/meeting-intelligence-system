"""회의록 5개 항목 직권 수정의 공통 처리(직접 수정 API 와 업로드 갱신이 같이 쓴다)."""
from typing import Any

from sqlalchemy.orm import Session

from app.models import Account, Meeting, MeetingMinutes
from app.models.common import utcnow
from app.models.minutes import MINUTES_FIELDS, NO_CONTENT
from app.services.history import KIND_MINUTES_OVERRIDE, record_change


def current_minutes(session: Session, meeting: Meeting) -> dict[str, str]:
    """저장된 5개 항목(행이 없으면 모두 "내용없음")."""
    row = session.get(MeetingMinutes, meeting.id)
    return {key: (getattr(row, key) if row else NO_CONTENT) or NO_CONTENT for key in MINUTES_FIELDS}


def apply_minutes_changes(
    session: Session, meeting: Meeting, account: Account, sent: dict[str, str], extra: dict[str, Any] | None = None
) -> dict[str, dict[str, str]]:
    """보낸 항목만 새 값으로 바꾸고, 실제로 바뀐 항목만 이력(구분 "직권 수정")에 남긴다. 바뀐 것이 없으면 이력도 없다.
    비운 항목은 "내용없음". 커밋은 호출부. 반환: {"before": {...}, "after": {...}}(바뀐 항목만)."""
    row = session.get(MeetingMinutes, meeting.id)
    if row is None:
        # 5개 항목이 아직 없던 회의록: 모두 "내용없음"인 행을 만든 뒤 수정한다(엔진 기록은 비움)
        row = MeetingMinutes(meeting_id=meeting.id, tenant_id=meeting.tenant_id, generated_at=utcnow())
        session.add(row)
        for key in MINUTES_FIELDS:
            setattr(row, key, NO_CONTENT)

    before: dict[str, str] = {}
    after: dict[str, str] = {}
    for key, value in sent.items():
        new = value.strip() or NO_CONTENT
        old = getattr(row, key) or NO_CONTENT
        if new != old:
            before[key], after[key] = old, new
            setattr(row, key, new)
    if after:
        row.updated_by, row.updated_at = account.id, utcnow()
        record_change(
            session, tenant_id=meeting.tenant_id, target_type="meeting", target_id=meeting.id,
            kind=KIND_MINUTES_OVERRIDE, actor_id=account.id, before=before, after=after, extra=extra,
        )
    return {"before": before, "after": after}
