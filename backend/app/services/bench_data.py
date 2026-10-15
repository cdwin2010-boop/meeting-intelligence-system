"""연결 점검·벤치마크(작업 L-3a)에 쓰는 가상 한국어 회의 전사본과 기대 업무 목록(정답지).

- 실제 회의 데이터는 쓰지 않는다. 사람·팀·품목 이름은 모두 가상이며 결정적으로(같은 입력 → 같은 결과) 만든다.
- 짧은 전사본: 예산 이내라 분할 없이 1회 호출되는 길이. 긴 전사본: 엔진의 구간 글자 수 한도를 넘겨 분할 경로를 일으키는 길이.
- 기한 표현(내일·모레·이번 주 금요일·다음 주 월요일)은 회의 일시(HELD_AT) 기준으로 정답 날짜를 계산한다.
"""
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone

HELD_AT = datetime(2026, 10, 12, 14, 0, tzinfo=timezone(timedelta(hours=9)))  # 월요일

PEOPLE = ("김도윤", "박서연", "이준호", "최하은", "정민재", "한소율")
TEAMS = ("영업1팀", "생산2팀", "품질3팀", "구매4팀", "물류5팀", "설계6팀", "총무7팀", "기획8팀")
NOUNS = ("견적서", "점검표", "교육 자료", "재고 실사표", "답변서", "시험 성적서", "일정표", "조정안")
VERBS = ("작성해", "정리해", "검토해", "발송해")
FILLERS = (
    "지난주 진행 상황은 전반적으로 계획대로 진행되고 있습니다.",
    "관련 부서와는 이미 한 번 이야기를 나눈 적이 있습니다.",
    "일단 현재까지 확인된 내용만 간단히 공유드리겠습니다.",
    "이 부분은 다음 회의에서 한 번 더 같이 보시죠.",
    "다른 의견 있으시면 편하게 말씀해 주세요.",
    "전반적인 분위기는 나쁘지 않았다고 생각합니다.",
)


def _friday(held: date) -> date:
    return held + timedelta(days=(4 - held.weekday()) % 7)


def _next_monday(held: date) -> date:
    return held + timedelta(days=7 - held.weekday())


# (회의에서 쓰는 표현, 정답 날짜 계산)
_DUES = (
    ("내일까지", lambda d: d + timedelta(days=1)),
    ("모레까지", lambda d: d + timedelta(days=2)),
    ("이번 주 금요일까지", _friday),
    ("다음 주 월요일까지", _next_monday),
)


@dataclass(frozen=True)
class GoldItem:
    team: str
    noun: str
    ref: str  # 업무 고유 표지(예: 제12호). 모델이 업무명에서 빼도 근거 인용문에는 남는다
    assignee: str
    due: str  # YYYY-MM-DD


@dataclass(frozen=True)
class BenchTranscript:
    text: str
    gold: tuple[GoldItem, ...]
    lines: int
    chars: int


def _clock(seconds: int) -> str:
    return f"[{seconds // 3600:02d}:{seconds % 3600 // 60:02d}:{seconds % 60:02d}]"


def _build(task_count: int, *, agenda_blocks: bool) -> BenchTranscript:
    """task_count 건의 업무가 들어 있는 전사본. 업무마다 지시 발언 + 수락 발언 + 잡담 한 줄을 둔다."""
    held = HELD_AT.date()
    lines: list[str] = []
    gold: list[GoldItem] = []
    clock = 20
    lines.append(f"{_clock(clock)} 화자1: 주간 업무 점검 회의를 시작하겠습니다.")
    for n in range(task_count):
        if agenda_blocks and n % 4 == 0:
            clock += 35
            lines.append(f"{_clock(clock)} 화자1: {['첫', '두', '세', '네', '다섯', '여섯', '일곱', '여덟', '아홉', '열'][min(n // 4, 9)]} 번째 안건은 {TEAMS[n % len(TEAMS)]} 업무 현황입니다.")
        team = TEAMS[n % len(TEAMS)]
        noun = NOUNS[(n // len(TEAMS) + n) % len(NOUNS)]
        person = PEOPLE[n % len(PEOPLE)]
        phrase, calc = _DUES[n % len(_DUES)]
        verb = VERBS[n % len(VERBS)]
        ref = f"제{n + 1}호"
        clock += 28
        lines.append(f"{_clock(clock)} 화자1: {person} 님, {team} {noun} {ref}를 {phrase} {verb} 주세요.")
        clock += 9
        lines.append(f"{_clock(clock)} 화자{n % 4 + 2}: 네, 알겠습니다. {phrase} 맞춰 드리겠습니다.")
        clock += 14
        lines.append(f"{_clock(clock)} 화자1: {FILLERS[n % len(FILLERS)]}")
        gold.append(GoldItem(team, noun, ref, person, calc(held).isoformat()))
    text = "\n".join(lines)
    return BenchTranscript(text=text, gold=tuple(gold), lines=len(lines), chars=len(text))


def short_transcript() -> BenchTranscript:
    """예산 이내 단일 호출 경로용(업무 4건)."""
    return _build(4, agenda_blocks=False)


def long_transcript(min_chars: int, max_chars: int) -> BenchTranscript | None:
    """min_chars 를 넘는 가장 짧은 전사본(안건 표지 포함). max_chars 안에서 만들 수 없으면 None(분할을 일으킬 수 없음)."""
    for count in range(8, 2000, 4):
        candidate = _build(count, agenda_blocks=True)
        if candidate.chars > max_chars:
            return None
        if candidate.chars >= min_chars:
            return candidate
    return None
