"""가짜 전사·추출 결과 (테스트 가이드 A파일 대본과 정답표). stub app/uploads.py 의 _SCRIPT·fake_transcript·fake_action_items 이식."""
from datetime import date, datetime, timedelta

FAKE_SCRIPT = [
    ("00:00:03", "김도현", "화자 분리가 자꾸 틀려요. 이서연 님, 개선안을 다음 주 금요일까지 정리해 주세요."),
    ("00:00:12", "이서연", "네, 제가 다음 주 금요일까지 정리해서 공유하겠습니다."),
    ("00:00:20", "김도현", "디자인 시안은 정민수 님이 이번 주 목요일까지 확정해 주세요."),
    ("00:00:28", "정민수", "알겠습니다. 목요일까지 확정하겠습니다."),
    ("00:00:35", "김도현", "그리고 점심 메뉴는 다음에 정합시다."),
]


def fake_transcript() -> str:
    return "\n".join(f"[{ts}] {speaker}: {text}" for ts, speaker, text in FAKE_SCRIPT)


def fake_items_raw(held_at: datetime) -> list[dict]:
    """회의 일시를 기준으로 '이번 주 목요일', '다음 주 금요일'을 계산해 업무 2건(LLM 응답과 같은 모양)을 만든다.
    (점심 메뉴 잡담은 업무가 아니므로 일부러 제외. 계산식은 stub 과 동일)"""
    d: date = held_at.date()
    this_thursday = d + timedelta(days=(3 - d.weekday()) % 7)  # 월=0 … 목=3
    next_friday = d + timedelta(days=(7 - d.weekday()) + 4)  # 다음 주 월요일 + 4일
    s0, s2 = FAKE_SCRIPT[0], FAKE_SCRIPT[2]
    return [
        {"task": "STT 화자 분리 정확도 개선안 정리", "assignee": "이서연", "due_date": next_friday.isoformat(),
         "quote": {"speaker": s0[1], "timestamp": s0[0], "text": s0[2]}},
        {"task": "디자인 시안 확정", "assignee": "정민수", "due_date": this_thursday.isoformat(),
         "quote": {"speaker": s2[1], "timestamp": s2[0], "text": s2[2]}},
    ]


def fake_minutes_raw() -> dict:
    """회의록 5개 항목 가짜 응답(LLM 응답과 같은 모양). 리스크는 일부러 비워 "내용없음" 처리를 확인할 수 있게 한다."""
    return {
        "purpose": "STT 화자 분리 정확도 개선과 디자인 시안 확정 일정 점검",
        "discussion": "화자 분리 오류 개선안 논의\n디자인 시안 확정 일정 논의",
        "decisions": "개선안은 다음 주 금요일까지 정리\n시안은 이번 주 목요일까지 확정",
        "risks": "",
        "next_agenda": "점심 메뉴 선정",
    }
