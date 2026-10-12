"""안건 분할기(작업 75-2): 전사문을 안건(Agenda) 단위 청크로 자른다. 순수 함수만 쓴다(같은 입력은 항상 같은 결과).

1순위: 명시적 안건 표지(AGENDA_MARKERS + 설정으로 더한 문구)로 나누고 제목을 뽑는다. 표지 앞의 발언은 제목 없는 구간이 된다.
2순위: 표지가 없거나 한 안건이 최대 길이를 넘으면 발언 경계에서만 자르는 길이 기반 분할(앞 청크 끝 발언을 overlap 글자 이내로 겹침).
- 한 발언 자체가 최대 길이를 넘으면 문장 경계에서 자르고 Chunk.split_utterance 로 표시한다(그 밖에 발언 중간은 자르지 않는다).
- 청크는 순서와 원문 위치(발언 번호·시작/끝 시각)를 보존한다.
"""
import re
from dataclasses import dataclass
from typing import Sequence

_LINE_TIMED = re.compile(r"^\[\s*(\d{1,3}:\d{2}(?::\d{2})?)\s*\]\s*(.*)$")
_LINE_LABEL = re.compile(r"^([^:：\n]{1,20}?)\s*[:：]\s*(.*)$")
_SENTENCE_END = re.compile(r"(?<=[.!?。！？])\s+|(?<=다\.)\s*|\n")
_TITLE_MAX = 40

_ORDINALS = r"(?:첫|두|세|네|다섯|여섯|일곱|여덟|아홉|열)\s*(?:번째|째)"
# (이름, 정규식): 발언 앞머리에서 안건 표지를 찾는다. 그룹 "rest" 는 표지 뒤 나머지(제목 후보).
AGENDA_MARKERS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("서수 안건", re.compile(rf"^\s*(?:{_ORDINALS}\s*안건)\s*(?:은|는|으로|이|인|:|,)?\s*[,:]?\s*(?P<rest>.*)$", re.DOTALL)),
    ("다음 안건", re.compile(r"^\s*(?:다음|마지막|이번|오늘의?)\s*안건\s*(?:은|는|으로|이|인|:|,)?\s*[,:]?\s*(?P<rest>.*)$", re.DOTALL)),
    ("안건 N", re.compile(r"^\s*안건\s*\d{1,2}\s*번?\s*(?:은|는|으로|:|\.|\)|,)?\s*(?P<rest>.*)$", re.DOTALL)),
    ("안건은", re.compile(r"^\s*안건\s*(?:은|는|으로|:|\.|\)|,)\s*(?P<rest>.*)$", re.DOTALL)),
    ("번호 목록", re.compile(r"^\s*\d{1,2}\s*[.)]\s+(?P<rest>\S.*)$", re.DOTALL)),
    ("서수 나열", re.compile(r"^\s*(?:첫째|둘째|셋째|넷째|다섯째)\s*[,.:]?\s*(?P<rest>.*)$", re.DOTALL)),
)


@dataclass(frozen=True)
class Utterance:
    index: int  # 원문 발언 번호(0부터). 문장 분할 조각은 같은 번호를 가진다
    speaker: str
    start_sec: float | None
    text: str
    split: bool = False  # 너무 긴 발언을 문장 경계에서 자른 조각

    def line(self) -> str:
        if self.start_sec is None:
            return f"{self.speaker}: {self.text}"
        total = int(self.start_sec)
        return f"[{total // 3600:02d}:{total % 3600 // 60:02d}:{total % 60:02d}] {self.speaker}: {self.text}"


@dataclass(frozen=True)
class Chunk:
    index: int  # 청크 순서(0부터)
    title: str  # 안건 제목(없으면 "")
    agenda_index: int  # 같은 안건에서 나온 청크는 같은 값(표지 없는 앞부분·무표지 전체도 하나의 안건 구간)
    part: int  # 안건 안에서의 조각 번호(1부터)
    parts: int
    utterances: tuple[Utterance, ...]
    overlap_count: int  # 앞쪽에서 이전 청크와 겹치는 발언 수
    split_utterance: bool

    @property
    def text(self) -> str:
        return "\n".join(u.line() for u in self.utterances)

    @property
    def first_index(self) -> int:
        return self.utterances[0].index

    @property
    def last_index(self) -> int:
        return self.utterances[-1].index

    @property
    def start_sec(self) -> float | None:
        return next((u.start_sec for u in self.utterances if u.start_sec is not None), None)

    @property
    def end_sec(self) -> float | None:
        return next((u.start_sec for u in reversed(self.utterances) if u.start_sec is not None), None)


def parse_utterances(text: str) -> list[Utterance]:
    """정규화된 전사문("[hh:mm:ss] 화자: 내용" 또는 "화자: 내용" 줄) → 발언 목록. 라벨 없는 줄은 앞 발언에 이어 붙인다."""
    result: list[Utterance] = []
    for raw in (text or "").splitlines():
        line = raw.strip()
        if not line:
            continue
        start: float | None = None
        rest = line
        timed = _LINE_TIMED.match(line)
        if timed:
            parts = [int(p) for p in timed.group(1).split(":")]
            start = float(parts[0] * 60 + parts[1]) if len(parts) == 2 else float(parts[0] * 3600 + parts[1] * 60 + parts[2])
            rest = timed.group(2)
        label = _LINE_LABEL.match(rest)
        if label:
            speaker, content = label.group(1).strip(), label.group(2).strip()
        elif result and not timed:
            last = result[-1]
            result[-1] = Utterance(last.index, last.speaker, last.start_sec, f"{last.text} {line}".strip())
            continue
        else:
            speaker = result[-1].speaker if result else "화자1"
            content = rest.strip()
        result.append(Utterance(len(result), speaker, start, content))
    return result


def match_agenda_marker(text: str, extra_markers: Sequence[str] = ()) -> str | None:
    """발언 앞머리가 안건 표지면 제목(없으면 표지 이름)을 돌려주고, 아니면 None."""
    for name, pattern in AGENDA_MARKERS:
        found = pattern.match(text)
        if found:
            return _title_from(found.group("rest")) or name
    stripped = text.lstrip()
    for marker in extra_markers:
        marker = marker.strip()
        if marker and stripped.startswith(marker):
            return _title_from(stripped[len(marker):].lstrip(" :,.)은는으로")) or marker
    return None


_TRANSITION = re.compile(r"(?:넘어가|시작|진행|말씀|살펴|다루|논의|들어가).{0,12}(?:겠습니다|ㅂ시다|볼까요|겠습니다만)$")
_TITLE_TAIL = re.compile(r"\s*(?:입니다|이에요|예요|이고요|인데요|입니다만|입니다요)$")


def _title_from(rest: str) -> str:
    """표지 뒤 첫 문장을 제목으로 쓴다. "넘어가겠습니다" 같은 전환 문장이면 다음 문장을 쓴다."""
    sentences = [s.strip(" .,:;-") for s in _SENTENCE_END.split(rest.strip()) if s and s.strip(" .,:;-")]
    if not sentences:
        return ""
    chosen = sentences[0]
    if _TRANSITION.search(chosen) and len(sentences) > 1:
        chosen = sentences[1]
    chosen = _TITLE_TAIL.sub("", chosen).strip()
    return chosen[:_TITLE_MAX].strip()


def _split_sentences(text: str, max_chars: int) -> list[str]:
    """문장 경계에서 max_chars 이하 조각으로 나눈다. 문장 하나가 한도를 넘으면 한도 길이로 자른다(마지막 수단)."""
    sentences = [s for s in _SENTENCE_END.split(text) if s and s.strip()]
    pieces: list[str] = []
    current = ""
    for sentence in sentences or [text]:
        while len(sentence) > max_chars:
            if current:
                pieces.append(current)
                current = ""
            pieces.append(sentence[:max_chars])
            sentence = sentence[max_chars:]
        candidate = f"{current} {sentence}".strip() if current else sentence
        if len(candidate) > max_chars:
            pieces.append(current)
            current = sentence
        else:
            current = candidate
    if current:
        pieces.append(current)
    return pieces


def _fit(utterances: Sequence[Utterance], max_chars: int) -> list[Utterance]:
    """줄 길이가 max_chars 를 넘는 발언을 문장 경계에서 잘라 조각(split=True)으로 바꾼다."""
    fitted: list[Utterance] = []
    for u in utterances:
        if len(u.line()) <= max_chars:
            fitted.append(u)
            continue
        overhead = len(u.line()) - len(u.text)
        room = max(1, max_chars - overhead)
        for piece in _split_sentences(u.text, room):
            fitted.append(Utterance(u.index, u.speaker, u.start_sec, piece, split=True))
    return fitted


def _length(utterances: Sequence[Utterance]) -> int:
    return sum(len(u.line()) + 1 for u in utterances)


def _windows(utterances: Sequence[Utterance], max_chars: int, overlap_chars: int) -> list[tuple[list[Utterance], int]]:
    """발언 경계에서만 자르는 길이 기반 분할. (발언들, 앞쪽 겹침 발언 수) 목록. 각 창의 글자 수는 max_chars 이하."""
    windows: list[tuple[list[Utterance], int]] = []
    carry: list[Utterance] = []
    i, total = 0, len(utterances)
    while i < total:
        first_len = len(utterances[i].line()) + 1
        while carry and _length(carry) + first_len > max_chars:
            carry = carry[1:]  # 겹침 때문에 한도를 넘으면 오래된 발언부터 뺀다
        items = list(carry)
        size = _length(items)
        j = i
        while j < total:
            add = len(utterances[j].line()) + 1
            if j > i and size + add > max_chars:
                break
            items.append(utterances[j])
            size += add
            j += 1
        windows.append((items, len(carry)))
        if j >= total:
            break
        carry, used = [], 0
        for item in reversed(utterances[i:j]):
            line_len = len(item.line()) + 1
            if used + line_len > overlap_chars:
                break
            carry.insert(0, item)
            used += line_len
        i = j
    return windows


def split_into_chunks(
    utterances: Sequence[Utterance],
    *,
    max_chars: int,
    overlap_chars: int,
    extra_markers: Sequence[str] = (),
) -> list[Chunk]:
    if max_chars <= 0:
        raise ValueError("max_chars 는 1 이상이어야 합니다.")
    overlap_chars = max(0, min(overlap_chars, max_chars // 2))
    if not utterances:
        return []

    # 1순위: 안건 표지로 구간을 나눈다
    sections: list[tuple[str, list[Utterance]]] = []
    for u in utterances:
        title = match_agenda_marker(u.text, extra_markers)
        if title is not None or not sections:
            sections.append((title or "", [u]))
        else:
            sections[-1][1].append(u)

    chunks: list[Chunk] = []
    for agenda_index, (title, members) in enumerate(sections):
        fitted = _fit(members, max(1, max_chars - 1))  # 줄바꿈 1글자를 포함해 max_chars 이하
        windows = _windows(fitted, max_chars, overlap_chars)
        for part, (items, overlap_count) in enumerate(windows, start=1):
            chunks.append(
                Chunk(
                    index=len(chunks), title=title, agenda_index=agenda_index, part=part, parts=len(windows),
                    utterances=tuple(items), overlap_count=overlap_count,
                    split_utterance=any(u.split for u in items),
                )
            )
    return chunks
