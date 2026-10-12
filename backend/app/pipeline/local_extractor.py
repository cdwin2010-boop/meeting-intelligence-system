"""로컬 LLM 추출기(작업 81-1): [전사문 → 안건 분할 → 청크별 LLM 호출·3단계 파서 → 병합] 을 현행 Extractor 프로토콜로 감싼다.
이후 후처리(근거 시각·담당자 매핑·저장)는 services/processing.py 의 기존 흐름을 그대로 쓴다.

실패 규칙: 모든 청크가 실패하면 예외를 올려 처리를 failed 로 마친다(일시 오류면 그 예외를, 응답 형식 문제면 ExtractionError).
일부 청크만 실패하면 결과를 돌려주고 처리 경고(processing_warnings)에 남긴다. 정상 응답의 업무 0건은 완료다.
"""
import hashlib
from dataclasses import replace
from datetime import datetime, timezone

from app.config import settings
from app.pipeline.chunked_extract import ChunkOptions, extract_chunked, to_extraction_result
from app.pipeline.extractor import ExtractionError, ExtractionResult
from app.pipeline.llm_client import LlmClient
from app.pipeline.local_llm import OpenAICompatClient
from app.pipeline.prompts import render_chunk_prompt

WARNING_MAX_CHARS = 200
WARNING_MAX_COUNT = 20


def engine_label(model: str) -> str:
    """처리 배너·회의록에 표시할 엔진 값(서버가 만든다)."""
    return f"로컬 LLM({model})"


def prompt_version_of(meeting_type: str) -> str:
    """고정 지시 부분의 SHA-256 앞 12자(전사문·회의 일시 제외). 지시문이나 유형 문단이 바뀌면 값이 바뀐다."""
    sample = render_chunk_prompt("", datetime(2000, 1, 3, tzinfo=timezone.utc), meeting_type=meeting_type)
    return hashlib.sha256(sample.encode("utf-8")).hexdigest()[:12]


class LocalLlmExtractor:
    def __init__(self, client: LlmClient, model: str, meeting_type: str = ""):
        self._client = client
        self._model = model
        self._meeting_type = meeting_type

    def extract(self, transcript: str, held_at: datetime) -> ExtractionResult:
        result = extract_chunked(transcript, held_at, self._client, ChunkOptions.from_settings(meeting_type=self._meeting_type))
        if result.all_failed:
            if result.client_error is not None:
                raise result.client_error  # 일시/영구 오류 그대로(오류 코드는 error_code 가 분류)
            raise ExtractionError("LLM response did not match the chunk schema")
        warnings = tuple(w[:WARNING_MAX_CHARS] for w in result.warnings[:WARNING_MAX_COUNT])
        converted = to_extraction_result(
            result,
            extract_model=self._model,
            prompt_version=prompt_version_of(self._meeting_type),
            minutes_prompt_version=prompt_version_of(self._meeting_type),
        )
        return _with(converted, engine_label(self._model), warnings)


def _with(result: ExtractionResult, label: str, warnings: tuple) -> ExtractionResult:
    return replace(result, engine_label=label, processing_warnings=warnings)


def make_local_extractor(meeting_type: str = "", client: LlmClient | None = None) -> LocalLlmExtractor:
    """설정(LOCAL_LLM_*)으로 로컬 추출기를 만든다. client 는 테스트용 주입."""
    if client is None:
        client = OpenAICompatClient(
            settings.local_llm_base_url,
            settings.local_llm_model,
            settings.local_llm_api_key.get_secret_value(),
            max_output_tokens=settings.local_llm_max_output_tokens,
            allow_external=settings.local_llm_allow_external_host,
            json_mode_enabled=settings.local_llm_json_mode,
        )
    return LocalLlmExtractor(client, settings.local_llm_model, meeting_type)
