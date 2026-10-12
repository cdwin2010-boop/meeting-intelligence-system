"""청크(안건) 단위 추론 조립(작업 75-2). 아직 현행 파이프라인(services/processing.py)에 연결하지 않는다(로컬 엔진 적용 지시 때 연결).

흐름: 전사문 → 안건 분할(agenda_chunker) → 청크마다 프롬프트 렌더(prompts) → LLM 호출 + 3단계 파서(json_parser) → 결과 수집 → 병합.
- 청크 하나가 실패해도 나머지는 계속한다. 예외를 올리지 않는다(상태·경고 목록으로 알린다).
- 업무: 상대 날짜는 기존 정규화(extractor.postprocess → dates.resolve_due_date)를 재사용한다. 겹침 청크 때문에 생긴 중복은 제거한다.
- 환각 방지: 근거 인용문이 그 청크 전사문에 실제로 있어야 "근거 검증"으로 본다(공백 정규화 비교). 없으면 근거 시각을 비운다.
- 담당자: 모델 값 → 인용 발화자 → 인용문이 있는 발언의 화자 순으로 쓰고, 원문 근거로 식별할 수 없으면 빈 문자열("")로 둔다
  (현행 Gemini 경로와 같은 표준: 저장 때 담당자 미지정이 "보완 필요"가 된다. 대체 이름을 만들어 넣지 않는다).
- 5개 항목: 안건별 내용을 안건 제목과 함께 합치고 길이 한도 안에서 자른다. 비면 "내용없음".
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
from app.pipeline.agenda_chunker import Chunk, parse_utterances, split_into_chunks
from app.pipeline.evidence_time import apply_evidence_times
from app.pipeline.extractor import (
    ActionItemCandidate, ActionItemList, ExtractedItem, ExtractionResult, MeetingMinutesDraft, postprocess,
)
from app.pipeline.json_parser import MAX_RETRIES, run_with_repair
from app.pipeline.llm_client import LlmClient
from app.pipeline.prompts import SYSTEM_PROMPT, render_chunk_prompt

STATUS_OK = "success"
STATUS_REPAIRED = "repaired"  # 교정 프롬프트 재시도로 성공
STATUS_FAILED = "failed"

_NON_WORD = re.compile(r"[\W_]+")
_TASK_SIMILAR = 0.9  # 업무명만으로 같은 업무로 보는 유사도
_TASK_SIMILAR_WITH_QUOTE = 0.6  # 근거 인용문이 겹칠 때 같은 업무로 보는 유사도
_QUOTE_SIMILAR = 0.9


class ChunkResponse(BaseModel):
    """청크 응답 스키마: 업무 목록 + 이 구간의 5개 항목. 알 수 없는 필드는 무시, null 은 빈 값으로."""

    items: list[ExtractedItem] = Field(default_factory=list)
    minutes: MeetingMinutesDraft = Field(default_factory=MeetingMinutesDraft)

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
    needle = _ws(quote)
    if not needle:
        return ""
    for u in chunk.utterances:
        if needle in _ws(u.text):
            return u.speaker
    return ""


def _items_of_chunk(chunk: Chunk, raw: list[ExtractedItem], held_at: datetime) -> tuple[list[ChunkedItem], list[str]]:
    warnings: list[str] = []
    result: list[ChunkedItem] = []
    chunk_text_ws = _ws(chunk.text)
    unverified = 0
    for candidate in postprocess(ActionItemList(items=raw), held_at):  # 날짜 정규화·빈 업무 제거는 현행 규칙 그대로
        quote = _ws(candidate.quote_text)
        verified = bool(quote) and quote in chunk_text_ws
        assignee = candidate.assignee.strip() or candidate.quote_speaker.strip()
        if not assignee:
            assignee = _speaker_of_quote(chunk, candidate.quote_text)  # 원문 근거가 없으면 ""
        candidate = replace(candidate, assignee=assignee)
        if verified:
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
    chunks = split_into_chunks(
        parse_utterances(transcript), max_chars=opts.max_chars, overlap_chars=opts.overlap_chars, extra_markers=opts.extra_markers
    )
    reports: list[ChunkReport] = []
    items: list[ChunkedItem] = []
    drafts: list[tuple[Chunk, MeetingMinutesDraft]] = []
    warnings: list[str] = []
    client_error: BaseException | None = None
    total = len(chunks)
    for chunk in chunks:
        prompt = render_chunk_prompt(
            chunk.text, held_at, agenda_title=chunk.title, chunk_index=chunk.index + 1, chunk_total=total,
            participants=opts.participants, meeting_type=opts.meeting_type,
        )
        messages = [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": prompt}]
        outcome = run_with_repair(
            client, messages, ChunkResponse, timeout=opts.timeout, repair_max_chars=opts.repair_max_chars, retry_max=opts.retry_max
        )
        chunk_warnings: list[str] = []
        label = f"구간 {chunk.index + 1}/{total}"
        if outcome.value is None:
            if outcome.client_error is not None:
                client_error = outcome.client_error
            chunk_warnings.append(f"{label} 실패: {'; '.join(outcome.errors[:2]) or '응답을 읽지 못했습니다'}")
            reports.append(ChunkReport(chunk.index, chunk.title, STATUS_FAILED, outcome.calls, 0, outcome.truncated_recovered, tuple(chunk_warnings)))
            warnings.extend(chunk_warnings)
            continue
        if outcome.status == "repaired":
            chunk_warnings.append(f"{label} 교정 재시도로 복구")
        if outcome.truncated_recovered:
            chunk_warnings.append(f"{label} 응답이 잘려 복구")
        if chunk.split_utterance:
            chunk_warnings.append(f"{label} 너무 긴 발언을 문장 경계에서 나눔")
        chunk_items, item_warnings = _items_of_chunk(chunk, outcome.value.items, held_at)
        chunk_warnings.extend(item_warnings)
        items.extend(chunk_items)
        drafts.append((chunk, outcome.value.minutes))
        status = STATUS_REPAIRED if outcome.status == "repaired" else STATUS_OK
        reports.append(ChunkReport(chunk.index, chunk.title, status, outcome.calls, len(chunk_items), outcome.truncated_recovered, tuple(chunk_warnings)))
        warnings.extend(chunk_warnings)

    merged_items, removed = dedupe_items(items)
    if removed:
        warnings.append(f"겹침 구간의 중복 업무 {removed}건 제거")
    minutes = _merge_minutes(drafts, total, opts.minutes_max_chars)
    return ChunkedExtraction(items=merged_items, minutes=minutes, chunks=reports, warnings=warnings, client_error=client_error)


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
