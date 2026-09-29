"""
업로드 검사와 가짜 결과 만들기 (순수 함수 모음).

FastAPI를 몰라도 되는 파일입니다. 그래서 테스트하기 쉽고, 운영 백엔드로 옮길 때도 그대로 참고할 수 있습니다.
비유: 우편물 접수 때 "봉투 겉면(확장자)"만 보지 않고 "안에 든 내용물(파일 앞부분)"도 확인하는 검수대.
"""
import math
import re
from datetime import date, datetime, timedelta

ALLOWED_EXTENSIONS = (".mp3", ".m4a", ".wav")

# 스텁은 음성을 실제로 읽지 않으므로 "파일 크기로 길이를 어림"한다. (128kbps 음성 ≈ 16,000 바이트/초)
# 운영 백엔드에서는 ffprobe 등으로 실제 길이를 잰다.
BYTES_PER_AUDIO_SECOND = 16_000


def extension_of(filename: str) -> str:
    """'A.M4A' -> '.m4a' (소문자). 확장자가 없으면 빈 문자열."""
    match = re.search(r"\.[A-Za-z0-9]+$", filename or "")
    return match.group(0).lower() if match else ""


def detect_audio_kind(head: bytes) -> str | None:
    """파일 앞부분(시그니처)으로 종류를 판별한다. 음성이 아니면 None."""
    if head[:3] == b"ID3":  # mp3 (태그가 있는 경우)
        return ".mp3"
    if len(head) >= 2 and head[0] == 0xFF and (head[1] & 0xE0) == 0xE0:  # mp3 프레임 동기 신호
        return ".mp3"
    if head[4:8] == b"ftyp":  # m4a (MP4 컨테이너)
        return ".m4a"
    if head[:4] == b"RIFF" and head[8:12] == b"WAVE":
        return ".wav"
    return None


def estimate_audio_seconds(size_bytes: int) -> int:
    return max(1, size_bytes // BYTES_PER_AUDIO_SECOND)


def expected_local_minutes(audio_seconds: int) -> int:
    """로컬 Faster-Whisper 예상 처리 시간(분, 올림). 스텁은 '녹음 길이 = 처리 시간'으로 가정한다.
    운영 백엔드에서는 gpu_guard.py가 가용 VRAM과 오디오 길이로 계산한 값을 쓴다."""
    return math.ceil(audio_seconds / 60)


def parse_started_at(value: str) -> datetime | None:
    """ISO 8601 문자열 -> datetime. 형식이 틀리면 None."""
    try:
        return datetime.fromisoformat(value.strip())
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# 가짜 전사·추출 결과 (테스트 가이드의 A파일 대본과 정답표와 같은 내용)
# ---------------------------------------------------------------------------
_SCRIPT = [
    ("00:00:03", "김도현", "화자 분리가 자꾸 틀려요. 이서연 님, 개선안을 다음 주 금요일까지 정리해 주세요."),
    ("00:00:12", "이서연", "네, 제가 다음 주 금요일까지 정리해서 공유하겠습니다."),
    ("00:00:20", "김도현", "디자인 시안은 정민수 님이 이번 주 목요일까지 확정해 주세요."),
    ("00:00:28", "정민수", "알겠습니다. 목요일까지 확정하겠습니다."),
    ("00:00:35", "김도현", "그리고 점심 메뉴는 다음에 정합시다."),
]


def fake_transcript() -> str:
    return "\n".join(f"[{ts}] {speaker}: {text}" for ts, speaker, text in _SCRIPT)


def fake_action_items(started_at: datetime) -> list[dict]:
    """회의 일시를 기준으로 '이번 주 목요일', '다음 주 금요일'을 계산해 액션아이템 2건을 만든다.
    (점심 메뉴 잡담은 액션아이템이 아니므로 일부러 제외)"""
    d: date = started_at.date()
    this_thursday = d + timedelta(days=(3 - d.weekday()) % 7)  # 월=0 … 목=3
    next_friday = d + timedelta(days=(7 - d.weekday()) + 4)  # 다음 주 월요일 + 4일
    return [
        {"task": "STT 화자 분리 정확도 개선안 정리", "assignee": "이서연", "dueDate": next_friday.isoformat(),
         "quote": {"speaker": _SCRIPT[0][1], "timestamp": _SCRIPT[0][0], "text": _SCRIPT[0][2]}},
        {"task": "디자인 시안 확정", "assignee": "정민수", "dueDate": this_thursday.isoformat(),
         "quote": {"speaker": _SCRIPT[2][1], "timestamp": _SCRIPT[2][0], "text": _SCRIPT[2][2]}},
    ]
