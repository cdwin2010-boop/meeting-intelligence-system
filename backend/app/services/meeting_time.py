"""회의 일시(meetings.held_at) 입력 검증(작업 88-4). 등록(업로드)과 등록 후 수정이 같은 규칙을 쓴다.
- 입력은 사용자 현지 시각을 오프셋이 붙은 ISO 8601 로 받고(예: 2026-10-01T14:00:00+09:00) 저장은 UTC 한 가지다.
  오프셋을 같이 받으므로 변환 과정에서 날짜가 밀리지 않는다.
- 비어 있으면 422("회의 일시를 입력해 주세요"), 형식·오프셋 오류는 기존대로 400, 현재보다 미래(시계 오차 허용 범위 초과)는 422.
- 등록 일시(first_created_at)는 서버가 UTC 로 자동 기록하는 별개 값이다: 입력·수정 경로가 없다.
"""
from datetime import datetime, timedelta, timezone

from fastapi import HTTPException, status

from app.config import settings
from app.models.common import utcnow

MISSING_MESSAGE = "회의 일시를 입력해 주세요(필수 입력입니다)."
FUTURE_MESSAGE = "회의 일시는 현재 시각보다 미래일 수 없습니다. 날짜와 시각을 다시 확인해 주세요."


def parse_meeting_at(value: str | None, now: datetime | None = None) -> datetime:
    """입력 문자열 → UTC 시각. 규칙 위반이면 HTTPException(400 형식·오프셋 / 422 비어 있음·미래)."""
    text = (value or "").strip()
    if not text:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=MISSING_MESSAGE)
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="heldAt 형식이 올바르지 않습니다(ISO 8601).") from None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="heldAt 에는 시간대 오프셋이 필요합니다(예: +09:00).")
    utc = parsed.astimezone(timezone.utc)
    limit = (now or utcnow()) + timedelta(minutes=settings.meeting_at_future_tolerance_minutes)
    if utc > limit:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=FUTURE_MESSAGE)
    return utc
