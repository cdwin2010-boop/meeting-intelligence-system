"""업무 근거 시각 산정: Gemini 가 준 시각에 맡기지 않고, 저장된 전사문의 구간 시각과 근거 인용문을 서버가 대조해서 정한다.
엔진(Gemini·로컬)과 상관없이 같은 방식이고, 인용문이 전사문에 실제로 있는지 확인하는 환각 방어를 겸한다.

규칙
- 구간: 전사문에 구간(segments)이 있으면 그것, 없으면 "[mm:ss] 화자: 내용" 또는 "[hh:mm:ss] 화자: 내용" 줄(이어지는 줄은 앞 구간에 붙임).
- 대조: 공백·구두점을 지운 글자로 인용문을 구간 글자와 맞춘다.
  · 한 구간에 들어 있으면 그 구간의 시작 시각, 여러 구간에 걸치면 인용문이 시작되는 첫 구간의 시작 시각. 같은 말이 여러 곳이면 Gemini 시각이 가장 가까운 쪽(없으면 첫 번째).
  · 완전 일치가 없으면 인용문이 구간(연속 최대 3개)과 얼마나 맞는지(%)를 difflib 로 재서 가장 높은 곳을 고르되 EVIDENCE_MATCH_MIN(기본 80) 미만이면 대조 실패.
- 최종 값: 서버 대조 값이 있으면 그것 > 대조에 실패했고 Gemini 시각이 전사문 안(마지막 구간 시작 이하)이면 그 값 > 둘 다 없으면 None.
- 대조 실패 건수만 로그에 남긴다(인용문 원문·키 금지).
"""
import logging
import re
from dataclasses import dataclass, replace
from difflib import SequenceMatcher
from typing import Any

from app.config import settings

log = logging.getLogger("app.evidence")

_STAMP = re.compile(r"^\s*\[?\s*(\d{1,3}(?::\d{1,2}){1,2})\s*\]?\s*(.*)$", re.DOTALL)
_DROP = re.compile(r"[\W_]+")  # 공백·구두점·기호(한글·영문·숫자는 남는다)
_WINDOW = 3  # 유사도 대조 때 이어 붙여 보는 구간 수 최대
_MAX_SPEAKER_LEN = 20


def parse_seconds(value: str | None) -> float | None:
    """"mm:ss"·"hh:mm:ss"(대괄호·공백 허용) → 초. 형식이 틀리면 None."""
    if not value:
        return None
    match = re.fullmatch(r"\s*\[?\s*(\d{1,3}(?::\d{1,2}){1,2})\s*\]?\s*", value)
    if not match:
        return None
    parts = [int(p) for p in match.group(1).split(":")]
    if len(parts) == 2:
        minutes, seconds = parts
        return float(minutes * 60 + seconds) if seconds < 60 else None
    hours, minutes, seconds = parts
    return float(hours * 3600 + minutes * 60 + seconds) if minutes < 60 and seconds < 60 else None


@dataclass(frozen=True)
class Segment:
    start_sec: float
    text: str  # 발화 내용(화자 표기 제외)


def segments_of(full_text: str | None, segments: list[dict[str, Any]] | None) -> list[Segment]:
    """전사문 → 구간 목록. 구간이 있으면 그것, 없으면 전사 줄에서 읽는다."""
    if segments:
        found = [
            Segment(float(s["start_sec"]), str(s.get("text") or ""))
            for s in segments
            if isinstance(s, dict) and isinstance(s.get("start_sec"), (int, float))
        ]
        if found:
            return found
    result: list[Segment] = []
    for line in (full_text or "").splitlines():
        match = _STAMP.match(line)
        seconds = parse_seconds(line[: line.find("]") + 1]) if "]" in line else None
        if match and seconds is not None and line.lstrip().startswith("["):
            rest = match.group(2)
            colon = rest.find(":")
            content = rest[colon + 1 :] if 0 < colon <= _MAX_SPEAKER_LEN else rest
            result.append(Segment(seconds, content.strip()))
        elif result and line.strip():
            last = result[-1]
            result[-1] = Segment(last.start_sec, f"{last.text} {line.strip()}")  # 이어지는 줄은 앞 구간에 붙인다
    return result


def _norm(text: str) -> str:
    return _DROP.sub("", text or "")


def _coverage(quote: str, candidate: str) -> tuple[float, float]:
    """(인용문이 후보와 맞는 비율 %, 전체 유사도 %)"""
    matcher = SequenceMatcher(None, quote, candidate, autojunk=False)
    matched = sum(block.size for block in matcher.get_matching_blocks())
    return 100.0 * matched / len(quote), 100.0 * matcher.ratio()


def locate(segments: list[Segment], quote: str, hint_sec: float | None = None) -> float | None:
    """인용문이 시작되는 구간의 시작 시각. 대조에 실패하면 None."""
    target = _norm(quote)
    if not target or not segments:
        return None
    texts = [_norm(s.text) for s in segments]
    joined = "".join(texts)
    starts: list[int] = []
    cursor = 0
    for text in texts:
        starts.append(cursor)
        cursor += len(text)

    def segment_at(index: int) -> int:
        position = 0
        for i, begin in enumerate(starts):
            if begin <= index:
                position = i
            else:
                break
        # 비어 있는 구간(글자가 모두 기호)은 건너뛰어 글자가 실제로 있는 구간을 고른다
        while position < len(texts) - 1 and texts[position] == "" :
            position += 1
        return position

    hits: list[int] = []
    at = joined.find(target)
    while at != -1 and len(hits) < 50:
        hits.append(at)
        at = joined.find(target, at + 1)
    if hits:
        positions = [segment_at(h) for h in hits]
        if hint_sec is not None and len(positions) > 1:
            positions.sort(key=lambda p: abs(segments[p].start_sec - hint_sec))
        return segments[positions[0]].start_sec

    # 완전 일치가 없으면 유사도(인용문이 구간 안에 얼마나 들어 있는지)로 가장 가까운 곳
    best: tuple[float, float, int] | None = None  # (일치율, 유사도, -위치) 가 클수록 좋다
    best_index = -1
    for i in range(len(segments)):
        for size in range(1, _WINDOW + 1):
            if i + size > len(segments):
                break
            candidate = "".join(texts[i : i + size])
            if not candidate:
                continue
            cover, ratio = _coverage(target, candidate)
            key = (cover, ratio, -i)
            if best is None or key > best:
                best, best_index = key, i
    if best is not None and best[0] >= settings.evidence_match_min:
        return segments[best_index].start_sec
    return None


def resolve_evidence_sec(segments: list[Segment], quote: str | None, gemini_sec: float | None) -> tuple[float | None, bool]:
    """(근거 시각, 서버 대조 성공 여부). 서버 대조 > (대조 실패 시) 전사문 안의 Gemini 시각 > None."""
    if not (quote or "").strip():
        return gemini_sec, False
    found = locate(segments, quote or "", gemini_sec)
    if found is not None:
        return found, True
    if gemini_sec is None:
        return None, False
    if not segments:
        return gemini_sec, False  # 비교할 전사문 구간이 없으면 기존 값 그대로
    inside = 0 <= gemini_sec <= max(s.start_sec for s in segments)
    return (gemini_sec if inside else None), False


def apply_evidence_times(candidates: list[Any], full_text: str | None, segments: list[dict[str, Any]] | None) -> list[Any]:
    """업무 후보(ActionItemCandidate)마다 근거 시각을 서버 대조로 다시 정한다. 후보 자체는 항상 그대로 돌려준다(시각만 바뀜).
    대조 실패 건수만 로그에 남긴다."""
    parsed = segments_of(full_text, segments)
    result: list[Any] = []
    failed = 0
    for candidate in candidates:
        hint = candidate.evidence_start_sec
        if hint is None:
            hint = parse_seconds(getattr(candidate, "quote_timestamp", ""))  # Gemini 가 mm:ss 로 준 시각도 보조로 쓴다
        seconds, matched = resolve_evidence_sec(parsed, candidate.quote_text, hint)
        if not matched and (candidate.quote_text or "").strip():
            failed += 1
        result.append(replace(candidate, evidence_start_sec=seconds) if seconds != candidate.evidence_start_sec else candidate)
    if failed:
        log.warning("evidence time match failed for %d of %d items", failed, len(candidates))
    return result
