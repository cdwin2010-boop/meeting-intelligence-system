"""캘린더 파일(.ics, RFC 5545) 생성(작업 E-1). 업무명·날짜만 쓰고 회의 내용·근거·내부 주소는 담지 않는다."""
from datetime import date, datetime, timedelta, timezone

PRODID = "-//AI DOTS//Tasks//KO"
MAX_OCTETS = 75


def escape_text(value: str) -> str:
    """TEXT 값 이스케이프: 역슬래시·세미콜론·쉼표·줄바꿈. 그 밖의 제어문자는 공백으로 바꾼다."""
    value = value.replace("\r\n", "\n").replace("\r", "\n")
    out = []
    for ch in value:
        if ch == "\\":
            out.append("\\\\")
        elif ch == ";":
            out.append("\\;")
        elif ch == ",":
            out.append("\\,")
        elif ch == "\n":
            out.append("\\n")
        elif ord(ch) < 0x20 or ord(ch) == 0x7F:
            out.append(" ")
        else:
            out.append(ch)
    return "".join(out)


def fold_line(line: str) -> str:
    """75옥텟(UTF-8 바이트)마다 CRLF+공백으로 접는다. 문자 중간에서는 자르지 않는다(이어지는 줄은 공백 1바이트 포함)."""
    if len(line.encode("utf-8")) <= MAX_OCTETS:
        return line
    parts: list[str] = []
    current: list[str] = []
    size = 0
    limit = MAX_OCTETS
    for ch in line:
        n = len(ch.encode("utf-8"))
        if size + n > limit:
            parts.append("".join(current))
            current, size, limit = [], 0, MAX_OCTETS - 1  # 접힌 줄은 앞의 공백이 1옥텟
        current.append(ch)
        size += n
    parts.append("".join(current))
    return "\r\n ".join(parts)


def build_ics(items: list[tuple[int, str, date]], now: datetime) -> str:
    """items: (업무 id, 업무명, 기한 날짜). 날짜는 시간대 변환 없이 그대로 종일 일정으로 쓴다."""
    stamp = (now.astimezone(timezone.utc) if now.tzinfo else now).strftime("%Y%m%dT%H%M%SZ")
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", f"PRODID:{PRODID}", "CALSCALE:GREGORIAN"]
    for item_id, title, due in items:
        lines += [
            "BEGIN:VEVENT",
            f"UID:task-{item_id}@aidots",
            f"DTSTAMP:{stamp}",
            f"DTSTART;VALUE=DATE:{due.strftime('%Y%m%d')}",
            f"DTEND;VALUE=DATE:{(due + timedelta(days=1)).strftime('%Y%m%d')}",
            f"SUMMARY:{escape_text(title)}",
            f"DESCRIPTION:{escape_text('AI DOTS에서 확인')}",
            "END:VEVENT",
        ]
    lines.append("END:VCALENDAR")
    return "".join(fold_line(line) + "\r\n" for line in lines)
