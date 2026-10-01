"""마감일 정규화: YYYY-MM-DD 는 그대로, 상대 표현은 회의 기준일로 계산, 그 밖에는 빈 문자열("").

LLM 에게도 프롬프트로 같은 규칙(회의 일시 기준 계산)을 주지만, LLM 이 상대 표현을 그대로 돌려준 경우에 대비해
서버가 확실한 표현만 계산한다. 날짜 하나로 정할 수 없으면 짐작하지 않고 "" 로 둔다.
  "오늘"=기준일, "내일"=+1, "모레"=+2
  "이번 주 X요일" = 기준일이 속한 주(월~일)의 X요일, "다음 주/차주 X요일" = 그다음 주의 X요일
"""
import re
from datetime import date, timedelta

WEEKDAYS_KO = ["월", "화", "수", "목", "금", "토", "일"]
_ISO_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_DAY_OFFSETS = {"오늘": 0, "금일": 0, "내일": 1, "명일": 1, "모레": 2}
_WEEK_RE = re.compile(r"^(이번\s*주|금주|다음\s*주|차주)\s*([월화수목금토일])(?:요일)?$")


def valid_iso_date(value: str) -> str:
    """YYYY-MM-DD 형식이고 실제 있는 날짜면 그대로, 아니면 ""."""
    value = value.strip()
    if not _ISO_RE.match(value):
        return ""
    try:
        date.fromisoformat(value)
    except ValueError:
        return ""
    return value


def resolve_due_date(value: str, base: date) -> str:
    """마감일 문자열을 YYYY-MM-DD 로 정규화한다. 정할 수 없으면 ""."""
    text = (value or "").strip()
    iso = valid_iso_date(text)
    if iso:
        return iso

    # "까지", 공백 정리
    text = re.sub(r"\s*까지$", "", text).strip()
    if text in _DAY_OFFSETS:
        return (base + timedelta(days=_DAY_OFFSETS[text])).isoformat()

    match = _WEEK_RE.match(text)
    if match:
        week, weekday = match.groups()
        monday = base - timedelta(days=base.weekday())
        if week.replace(" ", "") in ("다음주", "차주"):
            monday += timedelta(days=7)
        return (monday + timedelta(days=WEEKDAYS_KO.index(weekday))).isoformat()
    return ""
