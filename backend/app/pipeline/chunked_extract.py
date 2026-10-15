"""청크(안건) 단위 추론 조립(작업 75-2). 아직 현행 파이프라인(services/processing.py)에 연결하지 않는다(로컬 엔진 적용 지시 때 연결).

흐름: 전사문 → 안건 분할(agenda_chunker) → 청크마다 프롬프트 렌더(prompts) → LLM 호출 + 3단계 파서(json_parser) → 결과 수집 → 병합.
- 청크 하나가 실패해도 나머지는 계속한다. 예외를 올리지 않는다(상태·경고 목록으로 알린다).
- 업무: 상대 날짜는 기존 정규화(extractor.postprocess → dates.resolve_due_date)를 재사용한다. 겹침 청크 때문에 생긴 중복은 제거한다.
- 환각 방지: 근거 인용문이 그 청크 전사문에 실제로 있어야 "근거 검증"으로 본다(공백 정규화 비교). 없으면 근거 시각을 비운다.
- 담당자: 모델 값 → 인용 발화자 → 인용문이 있는 발언의 화자 순으로 쓰고, 원문 근거로 식별할 수 없으면 빈 문자열("")로 둔다
  (현행 Gemini 경로와 같은 표준: 저장 때 담당자 미지정이 "보완 필요"가 된다. 대체 이름을 만들어 넣지 않는다).
- 5개 항목: 안건별 내용을 안건 제목과 함께 합치고 길이 한도 안에서 자른다. 비면 "내용없음".
- C-1: 입력이 예산(max_chars) 이내면 분할 없이 1회 호출한다. 초과할 때만 목표 크기(target_chars)로 라인 단위 분할·중첩한다.
  전체 회의록 기준 줄 번호(Line ID, 발언 번호+1)를 프롬프트 줄 앞에 붙이고, 근거는 Line ID 로 원문과 1:1 검증한다.
  같은 사유로 구간이 연속 fail_stop 회 실패하면 나머지 구간을 중단한다.
- to_extraction_result 로 현행 추출 결과(ExtractionResult)와 같은 모양으로 바꾼다.
"""
import re
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from difflib import SequenceMatcher
from typing import Any

from pydantic import BaseModel, Field, field_validator

from app.config import settings
from app.models.minutes import MINUTES_FIELDS, NO_CONTENT
from app.pipeline.agenda_chunker import Chunk, Utterance, parse_utterances, split_into_chunks
from app.pipeline.evidence_time import apply_evidence_times
from app.pipeline.extractor import (
    ActionItemCandidate, ActionItemList, ExtractedItem, ExtractedQuote, ExtractionResult, MeetingMinutesDraft, postprocess,
)
from app.pipeline.engine_profile import EngineProfile
from app.pipeline.json_parser import MAX_RETRIES, ParseOutcome, run_with_repair
from app.pipeline.llm_client import LlmClient
from app.pipeline.local_llm import PERMANENT, LocalLlmError
from app.pipeline.prompts import SYSTEM_PROMPT, render_chunk_prompt

STATUS_OK = "success"
STATUS_REPAIRED = "repaired"  # 교정 프롬프트 재시도로 성공
STATUS_FAILED = "failed"

_NON_WORD = re.compile(r"[\W_]+")
_TASK_SIMILAR = 0.9  # 업무명만으로 같은 업무로 보는 유사도
_TASK_SIMILAR_WITH_QUOTE = 0.6  # 근거 인용문이 겹칠 때 같은 업무로 보는 유사도
_QUOTE_SIMILAR = 0.9

# 맥락 길이 초과 400 을 받았을 때 구간을 절반으로 나눠 다시 요청하는 상한(작업 B-1d)
CONTEXT_SPLIT_DEPTH = 2  # 구간 하나당 나누는 깊이(최대 4조각)
CONTEXT_SPLIT_BUDGET = 8  # 전사문 전체에서 나눔 때문에 늘어나는 추가 요청 수 상한(한 번 나눌 때마다 2회 소비)
# 구간을 절반으로 나눠 다시 요청하는 사유(LocalLlmError.reason): 맥락 초과 400 / 사용 토큰이 맥락 한계에 도달(조용한 잘림 의심) / 출력 잘림
SPLIT_REASONS = ("context_exceeded", "context_limit_reached", "output_truncated")
REASON_CONTEXT = "context_exceeded"
ERROR_DETAIL_MAX_COUNT = 5
MIN_AUTO_CHUNK_CHARS = 300
REASON_CONTEXT_TOO_SMALL = "context_too_small"
_LINE_ID_PREFIX = re.compile(r"^\s*#\s*\d+\s*")
_TIME_PREFIX = re.compile(r"^\s*\[\s*\d{1,3}:\d{2}(?::\d{2})?\s*\]\s*")
_ROOM_LINES = 2  # Line ID 의 줄에서 인용문이 이어질 수 있는 뒤쪽 줄 수


def output_reserve_tokens(context_tokens: int, max_output_tokens: int) -> int:
    """(엔진 프로파일 EngineProfile.output_reserve_tokens 와 같은 계산) 설정 출력 토큰을 맥락의 1/4(최소 512) 이하로 줄인 출력 예약."""
    return EngineProfile(context_tokens=context_tokens, max_output_tokens=max_output_tokens).output_reserve_tokens()


def safe_chunk_chars(profile: EngineProfile, fixed_prompt_chars: int) -> int:
    """사전 예산: (고정 지시 + 구간 + 출력 예약) 토큰이 맥락의 90% 안에 들어가는 구간 글자 수 상한(보수적 글자당 토큰, 하한 300자).
    구간 토큰 = 맥락×0.9 − 출력 예약 − 고정 지시 토큰, 글자 수 = 구간 토큰 / 글자당 토큰."""
    available = profile.budget_tokens - profile.output_reserve_tokens() - profile.tokens_of_chars(fixed_prompt_chars)
    return max(MIN_AUTO_CHUNK_CHARS, int(available / profile.tokens_per_char))


def check_context_sufficient(profile: EngineProfile, meeting_type: str = "") -> None:
    """맥락 길이가 구간 처리에 부족하면(고정 지시+출력 예약만으로 예산이 차서 구간을 넣을 자리가 최소 글자 수 미만) 시작 때 오류를 낸다.
    원격 엔진 맥락 길이가 2048 처럼 작은 설정이 대표적이다. 오류 코드는 llm_context_too_small(영구 오류, 재시도 무의미)."""
    available = profile.budget_tokens - profile.output_reserve_tokens() - profile.tokens_of_chars(fixed_prompt_chars(meeting_type))
    if int(available / profile.tokens_per_char) < MIN_AUTO_CHUNK_CHARS:
        raise LocalLlmError(
            f"모델 맥락 길이({profile.context_tokens} 토큰)가 구간 처리에 부족합니다. 맥락 길이를 늘리거나 출력 길이를 줄이는 설정이 필요합니다.",
            kind=PERMANENT, reason=REASON_CONTEXT_TOO_SMALL,
        )


def fixed_prompt_chars(meeting_type: str = "") -> int:
    """구간 본문을 뺀 고정 지시(시스템 + 사용자 프롬프트 틀)의 글자 수."""
    sample = render_chunk_prompt("", datetime(2000, 1, 3, tzinfo=timezone.utc), meeting_type=meeting_type)
    return len(SYSTEM_PROMPT) + len(sample)


def split_chunk_in_half(chunk: Chunk) -> tuple[Chunk, Chunk] | None:
    """구간을 글자 수 기준 절반으로 나눈다. 발언이 둘 이상이면 발언 경계에서, 하나뿐이면 그 발언 글을 가운데 근처에서 자른다.
    더 나눌 수 없으면 None."""
    utterances = chunk.utterances
    if len(utterances) >= 2:
        total = sum(len(u.text) for u in utterances)
        running, cut = 0, 1
        for position, u in enumerate(utterances[:-1], start=1):
            running += len(u.text)
            cut = position
            if running * 2 >= total:
                break
        return (
            replace(chunk, utterances=utterances[:cut], overlap_count=min(chunk.overlap_count, cut)),
            replace(chunk, utterances=utterances[cut:], overlap_count=0),
        )
    if not utterances or len(utterances[0].text) < 2:
        return None
    only = utterances[0]
    mid = len(only.text) // 2
    window = only.text[max(mid - 40, 1): mid + 40]
    offset = max(mid - 40, 1)
    cut = mid
    for marker in ("\n", ". ", "? ", "! ", "。", ", ", " "):  # 문장 경계 → 쉼표 → 공백 순으로 가운데에 가까운 곳
        at = window.rfind(marker)
        if at != -1:
            cut = offset + at + len(marker)
            break
    first, second = only.text[:cut].rstrip(), only.text[cut:].lstrip()
    if not first or not second:
        return None
    return (
        replace(chunk, utterances=(replace(only, text=first, split=True),), split_utterance=True, overlap_count=0),
        replace(chunk, utterances=(replace(only, text=second, split=True),), split_utterance=True, overlap_count=0),
    )


def _as_text(value: Any) -> Any:
    """모델이 문자열 대신 목록·객체·숫자로 준 값을 글자로 바꾼다(검증 실패로 구간 전체를 잃지 않게)."""
    if isinstance(value, (list, tuple)):
        return "\n".join(str(_as_text(v)).strip() for v in value if _as_text(v) not in (None, ""))
    if isinstance(value, dict):
        return "\n".join(f"{k}: {_as_text(v)}".strip() for k, v in value.items() if _as_text(v) not in (None, ""))
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return str(value)
    return value


class ChunkMinutes(MeetingMinutesDraft):
    """5개 항목: 값이 목록·객체여도 글자로 합쳐 받는다(C-1: 목록형 값 때문에 구간이 통째로 실패하던 결함)."""

    @field_validator("*", mode="before")
    @classmethod
    def _coerce_text(cls, value: Any) -> Any:
        return "" if value is None else _as_text(value)


class ChunkQuote(ExtractedQuote):
    line_id: str = Field("", description="근거가 시작되는 줄 번호(Line ID)")

    @field_validator("*", mode="before")
    @classmethod
    def _coerce_text(cls, value: Any) -> Any:
        return "" if value is None else _as_text(value)


class ChunkItem(ExtractedItem):
    quote: ChunkQuote = Field(default_factory=ChunkQuote)


class ChunkResponse(BaseModel):
    """청크 응답 스키마: 이 구간의 5개 항목 + 업무 목록. 알 수 없는 필드는 무시, null 은 빈 값으로."""

    minutes: ChunkMinutes = Field(default_factory=ChunkMinutes)
    items: list[ChunkItem] = Field(default_factory=list)

    @field_validator("items", mode="before")
    @classmethod
    def _items_none(cls, value: Any) -> Any:
        return [] if value is None or value == "" else value

    @field_validator("minutes", mode="before")
    @classmethod
    def _minutes_none(cls, value: Any) -> Any:
        return {} if value is None or value == "" else value


@dataclass(frozen=True)
class ChunkOptions:
    max_chars: int
    overlap_chars: int
    repair_max_chars: int
    timeout: float
    retry_max: int
    minutes_max_chars: int
    extra_markers: tuple[str, ...] = ()
    participants: tuple[str, ...] = ()
    meeting_type: str = ""
    retry_ceiling: int = MAX_RETRIES  # 원격 시험 엔진은 2(작업 91-1)
    target_chars: int = 0  # 분할 때 구간 목표 글자 수(0 이면 max_chars). 입력이 max_chars 이내면 분할하지 않는다(C-1)
    overlap_ratio: float = 0.0  # >0 이면 겹침을 목표 글자 수의 이 비율로 계산(0 이면 overlap_chars)
    fail_stop: int = 3  # 같은 사유로 구간이 연속 실패하면 나머지를 중단하는 횟수(C-1)

    @classmethod
    def from_settings(cls, participants: tuple[str, ...] = (), meeting_type: str = "") -> "ChunkOptions":
        return cls(
            max_chars=settings.extract_chunk_max_chars,
            overlap_chars=settings.extract_chunk_overlap_chars,
            repair_max_chars=settings.extract_response_max_chars_for_repair,
            timeout=settings.extract_llm_timeout_seconds,
            retry_max=min(settings.extract_parse_retry_max, MAX_RETRIES),
            minutes_max_chars=settings.extract_minutes_field_max_chars,
            extra_markers=tuple(settings.extract_agenda_marker_list),
            participants=participants,
            meeting_type=meeting_type,
            target_chars=_target_chars(settings.extract_chunk_max_chars),
            overlap_ratio=settings.extract_chunk_overlap_ratio,
            fail_stop=settings.extract_chunk_fail_stop,
        )


def _target_chars(max_chars: int) -> int:
    """목표 토큰(설정)을 보수적 글자당 토큰으로 글자 수로 바꾸되 구간 상한을 넘지 않게 한다."""
    return max(MIN_AUTO_CHUNK_CHARS, min(max_chars, int(settings.extract_chunk_target_tokens / EngineProfile().tokens_per_char)))


def profile_chunk_options(
    profile: "EngineProfile", meeting_type: str, *, configured_chars: int, timeout: float, retry_max: int, retry_ceiling: int,
) -> ChunkOptions:
    """엔진 프로파일(사전 예산 90%)로 구간 글자 수 상한을 정한 ChunkOptions. 원격(B-1d)·Ollama(L-2) 엔진이 같이 쓴다.
    구간 글자 수 = 설정(configured_chars, 0 이면 기본 청크 한도)과 맥락 길이 자동 계산값 중 작은 값(맥락 초과 방어)."""
    base = ChunkOptions.from_settings(meeting_type=meeting_type)
    check_context_sufficient(profile, meeting_type)  # 맥락이 너무 작으면 처리 전에 오류 코드와 안내로 끝낸다
    configured = configured_chars or base.max_chars
    auto_chars = safe_chunk_chars(profile, fixed_prompt_chars(meeting_type))
    max_chars = min(configured, auto_chars)
    # 자동 계산값이 줄였을 때는 겹침도 구간의 1/4 이하로 줄인다(겹침이 구간과 비슷해지면 구간 수만 늘어난다)
    overlap = min(base.overlap_chars, max(max_chars - 1, 1)) if max_chars == configured else min(base.overlap_chars, max_chars // 4)
    return ChunkOptions(
        max_chars=max_chars, overlap_chars=overlap, repair_max_chars=base.repair_max_chars, timeout=timeout, retry_max=retry_max,
        minutes_max_chars=base.minutes_max_chars, extra_markers=base.extra_markers, participants=base.participants,
        meeting_type=meeting_type, retry_ceiling=retry_ceiling,
        target_chars=_target_chars(max_chars), overlap_ratio=base.overlap_ratio, fail_stop=base.fail_stop,
    )


@dataclass(frozen=True)
class ChunkedItem:
    candidate: ActionItemCandidate
    evidence_verified: bool  # 근거 인용문이 청크 전사문에 실제로 있는가
    chunk_index: int
    agenda_title: str


@dataclass(frozen=True)
class ChunkReport:
    index: int
    title: str
    status: str  # success | repaired | failed
    calls: int
    item_count: int
    truncated_recovered: bool = False
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True)
class ChunkedExtraction:
    items: list[ChunkedItem]
    minutes: dict[str, str]  # MINUTES_FIELDS 키 전부, 비면 "내용없음"
    chunks: list[ChunkReport]
    warnings: list[str] = field(default_factory=list)
    # 청크 호출 실패의 마지막 원인 예외(응답 형식 문제로만 실패했으면 None). 전부 실패했을 때 오류 분류에 쓴다
    client_error: BaseException | None = None
    # 서버가 준 오류 응답 본문 일부(마스킹됨, 최대 5건). 이력에 남기고 관리자 이상에게만 보여 준다
    error_details: list[str] = field(default_factory=list)

    @property
    def all_failed(self) -> bool:
        return bool(self.chunks) and all(c.status == STATUS_FAILED for c in self.chunks)

    @property
    def unverified_count(self) -> int:
        return sum(1 for i in self.items if not i.evidence_verified)


def _ws(text: str) -> str:
    return " ".join((text or "").split())


def _key(text: str) -> str:
    return _NON_WORD.sub("", text or "").lower()


def _speaker_of_quote(chunk: Chunk, quote: str) -> str:
    needle = _norm(quote)
    if not needle:
        return ""
    for u in chunk.utterances:
        if needle in _norm(u.text):
            return u.speaker
    return ""


def _norm(text: str) -> str:
    """공백·구두점·따옴표를 지운 비교용 글자(evidence_time 의 대조 규칙과 같다)."""
    return _NON_WORD.sub("", text or "").lower()


def _clean_quote(text: str, speakers: set[str]) -> str:
    """인용문 앞에 모델이 붙인 #줄번호·[시각]·화자 표기를 걷어 낸다(발화 내용만 남긴다)."""
    cleaned = _LINE_ID_PREFIX.sub("", text or "")
    cleaned = _TIME_PREFIX.sub("", cleaned)
    for speaker in speakers:
        for colon in (":", "："):
            if speaker and cleaned.lstrip().startswith(speaker + colon):
                return cleaned.lstrip()[len(speaker) + 1:].strip()
    return cleaned.strip()


def _parse_line_id(raw: str) -> int | None:
    found = re.search(r"\d+", raw or "")
    return int(found.group()) if found else None


def _items_of_chunk(
    chunk: Chunk, raw: list[ExtractedItem], held_at: datetime, lines: dict[int, list[Utterance]] | None = None,
) -> tuple[list[ChunkedItem], list[str]]:
    """lines: 전체 회의록 기준 줄 번호(발언 번호) → 그 번호의 발언 조각들. 있으면 Line ID 로 근거를 원문과 1:1 검증한다."""
    warnings: list[str] = []
    result: list[ChunkedItem] = []
    chunk_norm = _norm(chunk.text)
    speakers = {u.speaker for u in chunk.utterances}
    unverified = 0
    for raw_item in raw:
        quote_obj = raw_item.quote
        line_id = _parse_line_id(getattr(quote_obj, "line_id", ""))
        cleaned = _clean_quote(quote_obj.text, speakers)
        item = raw_item.model_copy(update={"quote": ExtractedQuote(speaker=quote_obj.speaker, timestamp=quote_obj.timestamp, text=cleaned)})
        candidates = postprocess(ActionItemList(items=[item]), held_at)  # 날짜 정규화·빈 업무 제거는 현행 규칙 그대로
        if not candidates:
            continue
        candidate = candidates[0]
        needle = _norm(candidate.quote_text)
        located: float | None = None
        if not needle:
            verified = False
        elif line_id is not None and lines is not None and (line_id - 1) in lines:
            # Line ID 검증: 그 줄(과 이어지는 뒤쪽 줄)에 인용문이 실제로 있어야 한다. 줄 번호가 틀리면 미검증
            span = [u for n in range(line_id - 1, line_id + _ROOM_LINES) for u in lines.get(n, [])]
            verified = needle in _norm(" ".join(u.text for u in span))
            if verified:
                located = lines[line_id - 1][0].start_sec
        else:
            verified = needle in chunk_norm  # Line ID 가 없거나 범위 밖이면 구간 안 대조(공백·기호 차이는 무시)
        assignee = candidate.assignee.strip() or candidate.quote_speaker.strip()
        if not assignee:
            assignee = _speaker_of_quote(chunk, candidate.quote_text)  # 원문 근거가 없으면 ""
        candidate = replace(candidate, assignee=assignee)
        if verified and located is not None:
            candidate = replace(candidate, evidence_start_sec=located)
        elif verified:
            candidate = apply_evidence_times([candidate], chunk.text, None)[0]  # 서버 대조로 근거 시각 산정
        else:
            candidate = replace(candidate, evidence_start_sec=None)  # 근거 미검증: 시각을 비운다
            unverified += 1
        result.append(ChunkedItem(candidate, verified, chunk.index, chunk.title))
    if unverified:
        warnings.append(f"구간 {chunk.index + 1}: 근거 미검증 업무 {unverified}건")
    return result, warnings


def _same_task(a: ActionItemCandidate, b: ActionItemCandidate) -> bool:
    """겹침 청크 때문에 나온 같은 업무인가: 담당자가 같고 (업무명이 매우 비슷하거나, 근거 인용문이 겹치면서 업무명이 어느 정도 비슷)."""
    if _key(a.assignee) != _key(b.assignee):
        return False
    task_ratio = SequenceMatcher(None, _key(a.task), _key(b.task)).ratio()
    if task_ratio >= _TASK_SIMILAR:
        return True
    qa, qb = _key(a.quote_text), _key(b.quote_text)
    if qa and qb:
        overlap = qa in qb or qb in qa or SequenceMatcher(None, qa, qb).ratio() >= _QUOTE_SIMILAR
        if overlap and task_ratio >= _TASK_SIMILAR_WITH_QUOTE:
            return True
    return False


def dedupe_items(items: list[ChunkedItem]) -> tuple[list[ChunkedItem], int]:
    """앞선 항목을 남기되, 뒤에 나온 같은 업무가 근거 검증을 통과했고 앞의 것은 아니면 뒤의 것으로 바꾼다. (결과, 제거 건수)."""
    kept: list[ChunkedItem] = []
    removed = 0
    for item in items:
        for position, existing in enumerate(kept):
            if _same_task(existing.candidate, item.candidate):
                if item.evidence_verified and not existing.evidence_verified:
                    kept[position] = item
                removed += 1
                break
        else:
            kept.append(item)
    return kept, removed


def _merge_minutes(per_chunk: list[tuple[Chunk, MeetingMinutesDraft]], total_chunks: int, max_chars: int) -> dict[str, str]:
    merged: dict[str, str] = {}
    for key in MINUTES_FIELDS:
        lines: list[str] = []
        seen: set[str] = set()
        contributing = [(c, getattr(d, key).strip()) for c, d in per_chunk if getattr(d, key).strip()]
        for chunk, value in contributing:
            fresh = [ln for ln in value.splitlines() if ln.strip() and ln.strip() not in seen]  # 겹침 구간의 같은 줄은 한 번만
            seen.update(ln.strip() for ln in fresh)
            if not fresh:
                continue
            if len(contributing) > 1 or total_chunks > 1:
                label = chunk.title or f"구간 {chunk.index + 1}/{total_chunks}"
                lines.append(f"[{label}]")
            lines.extend(fresh)
        text = "\n".join(lines).strip()
        if len(text) > max_chars:
            text = text[: max_chars - 1].rstrip() + "…"
        merged[key] = text or NO_CONTENT
    return merged


def extract_chunked(
    transcript: str,
    held_at: datetime,
    client: LlmClient,
    options: ChunkOptions | None = None,
) -> ChunkedExtraction:
    """전사문 전체를 안건 단위로 나눠 추론하고 병합한다. held_at 은 회의 현지 시각(extractor.extract 와 같다)."""
    opts = options or ChunkOptions.from_settings()
    utterances = parse_utterances(transcript)
    # 전체 회의록 기준 줄 번호(Line ID = 발언 번호 + 1) → 그 발언 조각들. 근거 검증과 프롬프트 줄 번호가 이 값을 쓴다
    lines: dict[int, list[Utterance]] = {}
    for u in utterances:
        lines.setdefault(u.index, []).append(u)
    total_chars = sum(len(u.line()) + 1 for u in utterances)
    if utterances and total_chars <= opts.max_chars:
        # 예산 이내: 분할·안건 나눔 없이 1회 호출(지연 추가 없음)
        chunks = [Chunk(0, "", 0, 1, 1, tuple(utterances), 0, False)]
    else:
        split_max = min(opts.target_chars or opts.max_chars, opts.max_chars)
        overlap = int(split_max * opts.overlap_ratio) if opts.overlap_ratio > 0 else opts.overlap_chars
        chunks = split_into_chunks(utterances, max_chars=split_max, overlap_chars=overlap, extra_markers=opts.extra_markers)
    reports: list[ChunkReport] = []
    items: list[ChunkedItem] = []
    drafts: list[tuple[Chunk, MeetingMinutesDraft]] = []
    warnings: list[str] = []
    client_error: BaseException | None = None
    total = len(chunks)
    budget = [CONTEXT_SPLIT_BUDGET]  # 맥락 초과로 나눠 다시 요청할 수 있는 남은 횟수(전사문 전체 공유)
    error_details: list[str] = []

    def call(piece: Chunk) -> ParseOutcome:
        numbered = "\n".join(f"#{u.index + 1} {u.line()}" for u in piece.utterances)  # 줄 앞에 전체 기준 줄 번호(Line ID)
        prompt = render_chunk_prompt(
            numbered, held_at, agenda_title=piece.title, chunk_index=piece.index + 1, chunk_total=total,
            participants=opts.participants, meeting_type=opts.meeting_type,
        )
        messages = [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": prompt}]
        return run_with_repair(
            client, messages, ChunkResponse, timeout=opts.timeout, repair_max_chars=opts.repair_max_chars, retry_max=opts.retry_max,
            retry_ceiling=opts.retry_ceiling,
        )

    def run_piece(piece: Chunk, depth: int, splits: list[int]) -> list[tuple[Chunk, ParseOutcome]]:
        """한 조각을 호출한다. 맥락 길이 초과 400 이면 절반으로 나눠(구간당 깊이·전체 횟수 상한) 각각 다시 호출한다.
        splits = [나눈 횟수, 이 구간에 쓴 요청 수]."""
        outcome = call(piece)
        splits[1] += outcome.calls
        error = outcome.client_error
        detail = getattr(error, "detail", "") if outcome.value is None else ""
        if detail and len(error_details) < ERROR_DETAIL_MAX_COUNT and detail not in error_details:
            error_details.append(detail)
        if outcome.value is None and getattr(error, "reason", "") in SPLIT_REASONS and depth < CONTEXT_SPLIT_DEPTH and budget[0] >= 2:
            halves = split_chunk_in_half(piece)
            if halves is not None:
                budget[0] -= 2  # 두 조각을 다시 요청하므로 추가 요청 2회
                splits[0] += 1
                return run_piece(halves[0], depth + 1, splits) + run_piece(halves[1], depth + 1, splits)
        return [(piece, outcome)]

    fail_streak = 0  # 같은 사유로 연속 실패한 구간 수
    fail_reason = ""
    stopped = False
    for chunk in chunks:
        label = f"구간 {chunk.index + 1}/{total}"
        if stopped:
            skipped = f"{label} 건너뜀: 같은 사유({fail_reason})로 연속 {opts.fail_stop}회 실패해 나머지 구간을 중단했습니다"
            reports.append(ChunkReport(chunk.index, chunk.title, STATUS_FAILED, 0, 0, False, (skipped,)))
            warnings.append(skipped)
            continue
        splits = [0, 0]
        pieces = run_piece(chunk, 0, splits)
        calls = splits[1]  # 실패해서 나눈 요청까지 포함
        chunk_warnings: list[str] = []
        succeeded = [(c, o) for c, o in pieces if o.value is not None]
        failed = [(c, o) for c, o in pieces if o.value is None]
        truncated = any(o.truncated_recovered for _, o in pieces)
        for _, outcome in failed:
            if outcome.client_error is not None:
                client_error = outcome.client_error
        if splits[0]:
            chunk_warnings.append(f"{label} 맥락 한계(초과·잘림 의심)로 구간을 나눠 다시 요청({len(pieces)}조각)")
        if not succeeded:
            last = failed[-1][1]
            reason = getattr(last.client_error, "reason", "")
            if reason == REASON_CONTEXT:
                why = "모델 맥락 길이를 초과했습니다(구간을 나눠도 처리하지 못함)"
            elif reason in SPLIT_REASONS:
                why = "응답이 맥락 한계에서 잘렸을 수 있어 신뢰할 수 없습니다(구간을 나눠도 해소되지 않음)"
            else:
                why = "; ".join(last.errors[:2]) or "응답을 읽지 못했습니다"
            chunk_warnings.append(f"{label} 실패: {why}")
            reports.append(ChunkReport(chunk.index, chunk.title, STATUS_FAILED, calls, 0, truncated, tuple(chunk_warnings)))
            warnings.extend(chunk_warnings)
            reason_now = reason or "format"
            fail_streak = fail_streak + 1 if reason_now == fail_reason else 1
            fail_reason = reason_now
            if fail_streak >= opts.fail_stop and chunk.index + 1 < total:
                stopped = True
            continue
        fail_streak, fail_reason = 0, ""
        if failed:
            chunk_warnings.append(f"{label} 일부 조각 처리 실패({len(failed)}/{len(pieces)})")
        chunk_items_all: list[ChunkedItem] = []
        for piece, outcome in succeeded:
            if outcome.status == "repaired":
                chunk_warnings.append(f"{label} 교정 재시도로 복구")
            if outcome.truncated_recovered:
                chunk_warnings.append(f"{label} 응답이 잘려 복구")
            if piece.split_utterance and not splits[0]:
                chunk_warnings.append(f"{label} 너무 긴 발언을 문장 경계에서 나눔")
            piece_items, item_warnings = _items_of_chunk(piece, outcome.value.items, held_at, lines)
            chunk_warnings.extend(item_warnings)
            chunk_items_all.extend(piece_items)
            drafts.append((piece, outcome.value.minutes))
        items.extend(chunk_items_all)
        status = STATUS_REPAIRED if any(o.status == "repaired" for _, o in succeeded) else STATUS_OK
        reports.append(ChunkReport(chunk.index, chunk.title, status, calls, len(chunk_items_all), truncated, tuple(chunk_warnings)))
        warnings.extend(chunk_warnings)

    merged_items, removed = dedupe_items(items)
    if removed:
        warnings.append(f"겹침 구간의 중복 업무 {removed}건 제거")
    minutes = _merge_minutes(drafts, total, opts.minutes_max_chars)
    return ChunkedExtraction(
        items=merged_items, minutes=minutes, chunks=reports, warnings=warnings, client_error=client_error, error_details=error_details,
    )


def to_extraction_result(
    result: ChunkedExtraction,
    *,
    extract_model: str,
    prompt_version: str,
    minutes_prompt_version: str = "",
    extracted_at: datetime | None = None,
) -> ExtractionResult:
    """현행 추출 결과(ExtractionResult)와 같은 모양으로 바꾼다. 청크 상태·경고·근거 검증 여부는 ChunkedExtraction 쪽에 남는다."""
    return ExtractionResult(
        items=[i.candidate for i in result.items],
        extract_model=extract_model,
        prompt_version=prompt_version,
        extracted_at=extracted_at or datetime.now(timezone.utc),
        minutes=dict(result.minutes),
        minutes_prompt_version=minutes_prompt_version,
    )
