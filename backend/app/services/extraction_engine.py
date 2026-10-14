"""추출 엔진 선택(작업 81-1). 서비스 파일(processing.py)을 키우지 않으려고 엔진·회의 유형 조회를 여기에 둔다.
LLM_PROVIDER 가 local 이면 로컬 LLM 추출기(안건 분할·3단계 파서)를, 아니면 기존 추출기 선택(STT_PROVIDER 따름)을 쓴다."""
from collections.abc import Callable

from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from app.config import settings
from app.models import MeetingClassification
from app.pipeline.extractor import Extractor
from app.pipeline.local_extractor import make_local_extractor
from app.pipeline.chunked_extract import profile_chunk_options
from app.pipeline.json_parser import MAX_RETRIES
from app.pipeline.ollama_llm import (
    OLLAMA_RETRY_CEILING, OllamaClient, OllamaExtractor, ollama_profile, preflight,
)
from app.services.engine_test import ENGINE_DEFAULT, ENGINE_REMOTE, make_remote_extractor
from app.services.external_policy import local_external_host_allowed


def make_ollama_extractor(
    meeting_type: str = "", client: OllamaClient | None = None, allow_external: bool | None = None, preflight_check: bool = True
) -> OllamaExtractor:
    """설정(OLLAMA_*)으로 Ollama 추출기를 만든다. client 는 테스트용 주입."""
    allow = settings.local_llm_allow_external_host if allow_external is None else allow_external
    profile = ollama_profile()
    if client is None:
        client = OllamaClient(
            settings.ollama_base_url, settings.ollama_model, num_ctx=profile.context_tokens,
            num_predict=profile.output_reserve_tokens(), num_gpu=settings.ollama_num_gpu_value,
            keep_alive=settings.ollama_keep_alive, allow_external=allow, truncation_ratio=profile.truncation_ratio,
        )
    options = profile_chunk_options(
        profile, meeting_type, configured_chars=0, timeout=settings.ollama_timeout_sec,
        retry_max=min(settings.extract_parse_retry_max, OLLAMA_RETRY_CEILING), retry_ceiling=max(OLLAMA_RETRY_CEILING, MAX_RETRIES),
    )
    check = None
    if preflight_check:
        check = lambda: preflight(settings.ollama_base_url, settings.ollama_model, min(settings.ollama_timeout_sec, 15.0), allow)  # noqa: E731
    return OllamaExtractor(client, settings.ollama_model, meeting_type, options=options, unload_after=settings.ollama_unload_after_extract, preflight_fn=check)


def meeting_type_of(factory: sessionmaker, meeting_id: int) -> str:
    """회의 유형 코드. 분류 기록이 없으면 미지정("")."""
    with factory() as session:
        found = session.scalar(select(MeetingClassification.meeting_type).where(MeetingClassification.meeting_id == meeting_id))
    return found or ""


def select_extractor(
    factory: sessionmaker, meeting_id: int, extractor_factory: Callable[[], Extractor] | None, default: Callable[[], Extractor],
    engine: str = ENGINE_DEFAULT,
) -> Extractor:
    """주입된 추출기가 있으면 그것을, 원격 시험 엔진이 요청됐으면 원격 추출기를, 로컬 엔진이면 로컬 추출기를, 아니면 기본 추출기를 만든다.
    원격이 요청됐는데 쓸 수 없으면 RemoteEngineError 로 실패한다(Gemini 등으로 자동 전환하지 않는다, 작업 91-1)."""
    if extractor_factory is not None:
        return extractor_factory()
    if engine == ENGINE_REMOTE:
        return make_remote_extractor(
            meeting_type_of(factory, meeting_id), allow_external=local_external_host_allowed(factory, meeting_id, remote=True)
        )
    if settings.effective_llm_provider == "ollama":
        return make_ollama_extractor(
            meeting_type_of(factory, meeting_id), allow_external=local_external_host_allowed(factory, meeting_id)
        )
    if settings.effective_llm_provider == "local":
        return make_local_extractor(
            meeting_type_of(factory, meeting_id), allow_external=local_external_host_allowed(factory, meeting_id)
        )
    return default()
