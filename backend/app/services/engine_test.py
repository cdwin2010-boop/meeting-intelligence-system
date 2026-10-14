"""엔진 시험 모드(작업 91-1, 시험 전용). 규칙은 이 모듈 한 곳에 둔다.

- ENGINE_TEST_MODE 가 꺼져 있으면(기본, 납품 설정) 원격 엔진 선택·설정·로딩 테스트 요청은 서버가 거부한다. 화면 숨김만으로 막지 않는다.
- 원격 엔진 = "OpenAI 호환 HTTP(원격·시험용)". 설정값은 .env 기본값 + Git 제외 로컬 파일(ENGINE_TEST_SETTINGS_PATH)이 덮어쓴 값이다.
  서버 주소는 터널을 열 때마다 바뀌므로 화면에서 바꿀 수 있다. API 키는 응답·로그·화면에 평문으로 내보내지 않는다(마스킹).
- 자동 엔진 전환 없음: 원격 연결 실패는 Gemini 등으로 넘어가지 않고 remote_unavailable 로 실패 처리한다.
- DB 변경 없음: 작업별 엔진 선택은 job.queued 사건 payload 의 engine 으로, 사용 엔진 이름은 회의록 5개 항목의 engine 열에 남긴다.
"""
import json
import logging
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.auth.deps import has_rank

from app.config import settings
from app.models import Account, Event, Job
from app.pipeline.chunked_extract import ChunkOptions, fixed_prompt_chars, profile_chunk_options, safe_chunk_chars
from app.pipeline.engine_profile import EngineProfile
from app.pipeline.json_parser import MAX_RETRIES
from app.pipeline.llm_client import LlmClient
from app.pipeline.local_extractor import LocalLlmExtractor
from app.pipeline.local_llm import NO_KEY, LocalLlmError, OpenAICompatClient

log = logging.getLogger("app.engine_test")

ENGINE_DEFAULT = "default"
ENGINE_REMOTE = "remote"
ENGINES = (ENGINE_DEFAULT, ENGINE_REMOTE)
REMOTE_RETRY_CEILING = 2  # 원격 시험 엔진의 JSON 오류 재시도 상한(지시: 최대 2회)
REMOTE_LABEL_PREFIX = "원격(시험용) · "
ENGINE_COLUMN_MAX = 30  # minutes.engine 열 길이(마이그레이션 없이 맞춘다)
ERROR_REMOTE_UNAVAILABLE = "remote_unavailable"
ERROR_REMOTE_DISABLED = "remote_disabled"
ERROR_REMOTE_NOT_CONFIGURED = "remote_not_configured"
UNAVAILABLE_MESSAGE = "원격 엔진에 연결할 수 없습니다"
TEST_MODE_OFF_MESSAGE = "엔진 시험 모드가 아니어서 사용할 수 없습니다"
_CONNECT_REASONS = {"connect", "http_5xx", "http_429"}


class RemoteEngineError(RuntimeError):
    """원격 시험 엔진을 쓸 수 없음. 메시지에 주소·키는 없다. job_error_code 가 jobs.error_code 에 남는다."""

    kind = "permanent"  # 자동 전환·자동 재시도 없음(사람이 설정을 확인하고 다시 처리한다)

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.job_error_code = code


@dataclass(frozen=True)
class RemoteSettings:
    base_url: str
    model: str
    api_key: str
    timeout_seconds: float
    chunk_max_chars: int  # 0 = EXTRACT_CHUNK_MAX_CHARS

    @property
    def configured(self) -> bool:
        return bool(self.base_url and self.model)


def test_mode() -> bool:
    return settings.engine_test_mode


def normalize_engine(value: str | None) -> str:
    """요청의 engine 값: 비었으면 설정 기본값, 모르는 값은 ValueError."""
    raw = (value or "").strip().lower()
    if not raw:
        return settings.engine_test_default
    if raw not in ENGINES:
        raise ValueError(raw)
    return raw


# ---- 설정 파일(Git 제외). 키는 파일에만 있고 응답·로그로 나가지 않는다 ----
def _settings_file() -> Path:
    path = settings.engine_test_settings_path
    return path if path.is_absolute() else Path(__file__).resolve().parents[2] / path


def _read_file() -> dict[str, Any]:
    try:
        data = json.loads(_settings_file().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def load_remote_settings() -> RemoteSettings:
    saved = _read_file()
    base = str(saved.get("baseUrl", settings.remote_llm_base_url) or "").strip().rstrip("/")
    try:
        timeout = float(saved.get("timeoutSeconds", settings.remote_llm_timeout_seconds))
    except (TypeError, ValueError):
        timeout = settings.remote_llm_timeout_seconds
    try:
        chunk = int(saved.get("chunkMaxChars", settings.remote_llm_chunk_max_chars))
    except (TypeError, ValueError):
        chunk = settings.remote_llm_chunk_max_chars
    return RemoteSettings(
        base_url=base,
        model=str(saved.get("model", settings.remote_llm_model) or "").strip(),  # 대소문자는 그대로, 앞뒤 공백만 제거
        api_key=str(saved.get("apiKey", settings.remote_llm_api_key.get_secret_value()) or ""),
        timeout_seconds=timeout if timeout > 0 else settings.remote_llm_timeout_seconds,
        chunk_max_chars=max(0, chunk),
    )


def validate_base_url(value: str) -> str:
    """http(s) 주소만. 질의·조각이 있거나 호스트가 없으면 ValueError. 끝의 / 는 뗀다."""
    url = (value or "").strip()
    try:
        parts = urlsplit(url)
        ok = parts.scheme in ("http", "https") and bool(parts.hostname) and not parts.query and not parts.fragment
        parts.port  # noqa: B018 — 잘못된 포트는 ValueError
    except ValueError:
        ok = False
    if not ok:
        raise ValueError("서버 주소는 http:// 또는 https:// 로 시작하는 올바른 주소여야 합니다")
    return url.rstrip("/")


def save_remote_settings(
    *, base_url: str, model: str, api_key: str | None, timeout_seconds: float, chunk_max_chars: int
) -> list[str]:
    """화면에서 저장한 값을 파일에 쓴다(원자적 교체). api_key 가 None·빈 값이면 기존 키를 유지한다. 바뀐 항목 이름 목록을 돌려준다."""
    before = load_remote_settings()
    saved = _read_file()
    saved.update({"baseUrl": base_url, "model": model, "timeoutSeconds": timeout_seconds, "chunkMaxChars": chunk_max_chars})
    if api_key:
        saved["apiKey"] = api_key
    path = _settings_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".engine_test_", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as out:
            json.dump(saved, out, ensure_ascii=False)
        os.replace(tmp, path)
    except OSError:
        Path(tmp).unlink(missing_ok=True)
        raise
    after = load_remote_settings()
    return [
        name
        for name, old, new in (
            ("baseUrl", before.base_url, after.base_url),
            ("model", before.model, after.model),
            ("apiKey", before.api_key, after.api_key),
            ("timeoutSeconds", before.timeout_seconds, after.timeout_seconds),
            ("chunkMaxChars", before.chunk_max_chars, after.chunk_max_chars),
        )
        if old != new
    ]


def mask_key(key: str) -> str:
    """화면 표시용 마스킹: 값이 없으면 "", 있으면 길이와 무관한 고정 형태(앞·뒤 글자를 드러내지 않는다)."""
    return "" if not key else "●●●●●●●●"


# ---- 작업별 엔진 선택(job.queued 사건 payload) ----
def resolve_requested_engine(account: Account, requested: str | None) -> str | None:
    """등록·재처리 요청의 engine 값을 해석한다. 시험 모드가 꺼져 있으면 값이 있는 요청을 403 으로 거부하고(None 이면 현행 동작),
    켜져 있으면 관리자 이상(중간관리자·지시자)만 고를 수 있다(담당자 403, 모르는 값 422). 고르지 않으면 설정 기본값."""
    raw = (requested or "").strip()
    if not raw:
        return None
    if not settings.engine_test_mode:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=TEST_MODE_OFF_MESSAGE)
    if not has_rank(account, "manager"):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="엔진은 관리자 이상만 선택할 수 있습니다")
    try:
        return normalize_engine(raw)
    except ValueError:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="engine 은 default 또는 remote 여야 합니다") from None


def require_test_mode() -> None:
    """원격 엔진 설정·로딩 테스트 요청의 서버 측 거부(시험 모드가 꺼져 있으면 403)."""
    if not settings.engine_test_mode:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=TEST_MODE_OFF_MESSAGE)


def requested_engine_payload(engine: str | None) -> dict[str, Any]:
    """job.queued 사건 payload 에 합칠 값. 시험 모드가 꺼져 있으면 비어 있다(현행 동작 그대로)."""
    if not settings.engine_test_mode:
        return {}
    return {"engine": engine or settings.engine_test_default}


def job_engine(factory: sessionmaker, job_id: int) -> str:
    """이 작업이 요청한 엔진. 기록이 없으면 default."""
    with factory() as session:
        events = session.scalars(
            select(Event)
            .where(Event.entity_type == "job", Event.entity_id == job_id, Event.event_type == "job.queued")
            .order_by(Event.id)
        ).all()
    for event in reversed(events):
        value = (event.payload or {}).get("engine")
        if value in ENGINES:
            return value
    return ENGINE_DEFAULT


def remote_label(model: str) -> str:
    """회의록에 남기고 화면에 보이는 엔진 값: "원격(시험용) · 모델명"(열 길이 30자에 맞춰 모델명만 줄인다)."""
    room = ENGINE_COLUMN_MAX - len(REMOTE_LABEL_PREFIX)
    name = model if len(model) <= room else model[: max(room - 1, 1)] + "…"
    return REMOTE_LABEL_PREFIX + name


def remote_profile() -> EngineProfile:
    """원격(시험용) 엔진 프로파일: 값은 모두 설정(REMOTE_LLM_*, LOCAL_LLM_MAX_OUTPUT_TOKENS)에서 읽는다(작업 B-1d)."""
    return EngineProfile(
        name="remote-openai-compat",
        context_tokens=settings.remote_llm_context_tokens,
        max_output_tokens=settings.local_llm_max_output_tokens,
        supports_json_format=settings.remote_llm_json_format,
        tokens_per_char=settings.remote_llm_tokens_per_char,
        use_server_tokenizer=settings.remote_llm_use_server_tokenizer,
        send_context_option=settings.remote_llm_send_context_option,
    )


def remote_output_tokens() -> int:
    """원격 요청의 max_tokens: 설정 출력 토큰과 맥락 길이의 1/4 중 작은 값(작업 B-1d)."""
    return remote_profile().output_reserve_tokens()


def build_remote_client(remote: RemoteSettings, allow_external: bool) -> LlmClient:
    return OpenAICompatClient(
        remote.base_url,
        remote.model,
        remote.api_key or NO_KEY,
        max_output_tokens=remote_output_tokens(),
        allow_external=allow_external,
        json_mode_enabled=remote_profile().supports_json_format,
        context_tokens=remote_profile().context_tokens,
        truncation_ratio=remote_profile().truncation_ratio,
    )


class RemoteExtractor(LocalLlmExtractor):
    """원격 시험 엔진 추출기: 추출 흐름은 로컬 엔진과 같고, 연결 실패는 remote_unavailable 로 올린다(자동 전환 없음)."""

    def extract(self, transcript, held_at):
        try:
            return super().extract(transcript, held_at)
        except LocalLlmError as exc:
            if exc.reason in _CONNECT_REASONS:
                raise RemoteEngineError(ERROR_REMOTE_UNAVAILABLE, UNAVAILABLE_MESSAGE) from None
            raise


def make_remote_extractor(meeting_type: str = "", client: LlmClient | None = None, allow_external: bool = True) -> RemoteExtractor:
    """시험 모드 확인 + 원격 설정 확인 후 원격 추출기를 만든다. 쓸 수 없으면 RemoteEngineError(자동 전환 없음).
    allow_external 은 외부 주소 허용 판단(시스템 관리자 정책, services/external_policy.py)이다."""
    if not settings.engine_test_mode:
        raise RemoteEngineError(ERROR_REMOTE_DISABLED, "엔진 시험 모드가 꺼져 있어 원격 엔진을 쓸 수 없습니다")
    remote = load_remote_settings()
    if not remote.configured:
        raise RemoteEngineError(ERROR_REMOTE_NOT_CONFIGURED, "원격 엔진 주소·모델이 설정되지 않았습니다")
    if client is None:
        client = build_remote_client(remote, allow_external)
    # 구간 글자 수: 설정(원격 청크 한도 또는 기본)과 맥락 길이 자동 계산값 중 작은 값(맥락 초과 400 방어, 작업 B-1d). 계산은 공용 helper
    options = profile_chunk_options(
        remote_profile(), meeting_type, configured_chars=remote.chunk_max_chars, timeout=remote.timeout_seconds,
        retry_max=REMOTE_RETRY_CEILING, retry_ceiling=max(REMOTE_RETRY_CEILING, MAX_RETRIES),
    )
    return RemoteExtractor(client, remote.model, meeting_type, options=options, label=remote_label(remote.model))


def processing_in_progress(session: Session) -> bool:
    """음성 인식·추출 작업이 진행 중인가(로딩 테스트가 GPU 를 겹쳐 쓰지 않도록 막는 판단)."""
    return session.scalar(select(Job.id).where(Job.status == "running").limit(1)) is not None
