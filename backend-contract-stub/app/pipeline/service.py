"""
작업 1건 처리: queued → processing(STT) → processing(LLM) → completed / failed

- 단계마다 '조건부 UPDATE'(store.*_if_*)를 쓴다. 관리자가 중간에 Kill 하면 조건이 맞지 않아 결과를 저장하지 않는다.
- 한 번에 한 작업만 처리한다(Semaphore(1)). 나머지는 queued 로 기다린다.
- 로그에는 작업 ID·단계·소요 시간(초)·예외 종류만 남긴다. 전사문과 API 키는 절대 남기지 않는다.
- 실패 이유는 사람이 읽을 한 줄로 errorLog 에 남긴다. 스택 트레이스와 키는 넣지 않는다.
"""
import logging
import re
import threading
import time
from datetime import datetime, timezone
from typing import Callable

from app.config import settings
from app.db import store
from app.fake_worker import run_fake_worker
from app.pipeline.extractor import ExtractionError, Extractor, make_extractor
from app.pipeline.stt import NO_SPEECH, GeminiFileError, SttEngine, make_stt
from app.upload_storage import AUDIO_MIME_TYPES, delete_upload, find_upload
from app.uploads import parse_started_at

log = logging.getLogger("app.pipeline")
if not log.handlers:  # uvicorn 은 app.* 로거를 설정하지 않으므로 단계별 소요 시간이 보이도록 직접 붙인다
    _handler = logging.StreamHandler()
    _handler.setFormatter(logging.Formatter("%(levelname)s:     [pipeline] %(message)s"))
    log.addHandler(_handler)
    log.setLevel(logging.INFO)
    log.propagate = False

_slot = threading.Semaphore(1)  # 동시 처리 1건

MSG_NO_SPEECH = "말소리가 감지되지 않았습니다. 음성이 들어 있는 파일인지 확인해 주세요."
MSG_NO_FILE = "업로드된 음성 파일을 찾을 수 없습니다. 파일을 다시 업로드해 주세요."
MSG_BAD_MEETING = "회의 정보를 읽을 수 없어 처리하지 못했습니다."

_KEY_PATTERN = re.compile(r"AIza[0-9A-Za-z_\-]{20,}")  # Google API 키 모양


def redact(text: str) -> str:
    """혹시라도 문장에 API 키가 섞이면 가린다 (무료·유료 키 값 모두 + 키 모양 문자열)."""
    for key in settings.all_gemini_keys():
        text = text.replace(key, "***")
    return _KEY_PATTERN.sub("***", text)


def describe_error(exc: BaseException) -> str:
    """예외 → 사람이 읽을 한 줄 요약 (스택 트레이스·키 없음)"""
    timeout = f"{settings.gemini_timeout_seconds:g}초"
    try:
        import httpx

        if isinstance(exc, httpx.TimeoutException):
            return f"Gemini API 응답 시간이 초과되었습니다({timeout}). 잠시 후 Retry 하세요."
    except ImportError:  # pragma: no cover
        pass
    if isinstance(exc, TimeoutError):
        return f"Gemini API 응답 시간이 초과되었습니다({timeout}). 잠시 후 Retry 하세요."
    if isinstance(exc, GeminiFileError):
        return "Gemini가 음성 파일을 처리하지 못했습니다. 파일이 손상되지 않았는지 확인해 주세요."
    if isinstance(exc, ExtractionError):
        return "액션아이템 추출 결과의 형식이 올바르지 않습니다. Retry 하세요."

    try:
        from google.genai import errors as genai_errors
    except ImportError:  # pragma: no cover
        genai_errors = None
    if genai_errors is not None and isinstance(exc, genai_errors.APIError):
        code = exc.code
        if code == 429:
            if settings.gemini_key_mode == "free":
                # 자동으로 유료 키로 넘어가지 않는다. 전환은 사람이 설정으로 결정한다.
                return "무료 키 한도(할당량) 초과(429). GEMINI_KEY_MODE=paid로 바꾸면 유료 키를 씁니다."
            return "Gemini API 사용 한도(할당량)를 초과했습니다(429). 잠시 후 Retry 하세요."
        if code in (401, 403):
            return f"Gemini API 인증에 실패했습니다({code}). 서버의 API 키 설정을 확인해 주세요."
        if code is not None and code >= 500:
            return f"Gemini 서버 오류가 발생했습니다({code}). 잠시 후 Retry 하세요."
        # 400/404 등: 파일 형식·모델 이름 문제일 수 있으므로 API 가 준 이유를 그대로(짧게) 보고한다
        reason = redact(str(exc.message or exc.status or ""))[:200]
        return f"Gemini API가 요청을 거절했습니다({code}): {reason}".rstrip(": ")
    return f"처리 중 오류가 발생했습니다({type(exc).__name__}). Retry 하세요."


def _fail(job_id: str, message: str) -> None:
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    store.fail_if_processing(job_id, f"{now} [ERROR] {redact(message)}")


def process_job(
    job_id: str,
    stt_factory: Callable[[], SttEngine] | None = None,
    extractor_factory: Callable[[], Extractor] | None = None,
) -> None:
    """작업 1건을 STT → LLM 으로 처리한다. 팩토리는 테스트에서 가짜 엔진을 주입할 때 쓴다(기본: 설정에 따른 make_*)."""
    stt_factory = stt_factory or make_stt
    extractor_factory = extractor_factory or make_extractor
    with _slot:
        if not store.start_if_queued(job_id):
            return  # 이미 시작됐거나 취소됨 → 중복 실행 방지
        log.info("job=%s 키 모드: %s", job_id, settings.gemini_key_mode)  # 모드만. 키 값·일부 글자도 남기지 않는다

        audio = find_upload(job_id)
        if audio is None:
            _fail(job_id, MSG_NO_FILE)
            return
        meeting = store.get_meeting(store.job_meeting_id(job_id)) or {}
        started_at = parse_started_at(meeting.get("startedAt", ""))
        if started_at is None:
            _fail(job_id, MSG_BAD_MEETING)
            return

        # ---- STT ----
        t0 = time.monotonic()
        stt = None
        try:
            stt = stt_factory()
            transcript = stt.transcribe(audio, AUDIO_MIME_TYPES[audio.suffix.lower()]).strip()
        except Exception as exc:  # noqa: BLE001
            log.warning("job=%s stage=STT failed after %.1fs (%s)", job_id, time.monotonic() - t0, type(exc).__name__)
            _fail(job_id, describe_error(exc))
            return
        finally:
            if stt is not None:
                stt.release()  # 다음 단계(LLM) 전에 STT 자원을 먼저 정리 (GPU 순차 실행 원칙)
        log.info("job=%s stage=STT seconds=%.1f", job_id, time.monotonic() - t0)

        if not transcript or transcript == NO_SPEECH:
            _fail(job_id, MSG_NO_SPEECH)
            return

        if not store.set_stage_if_processing(job_id, "LLM"):
            return  # STT 도중 Kill 됨 → 결과를 저장하지 않는다

        # ---- LLM ----
        t1 = time.monotonic()
        extractor = None
        try:
            extractor = extractor_factory()
            items = extractor.extract(transcript, started_at)
        except Exception as exc:  # noqa: BLE001
            log.warning("job=%s stage=LLM failed after %.1fs (%s)", job_id, time.monotonic() - t1, type(exc).__name__)
            _fail(job_id, describe_error(exc))
            return
        log.info("job=%s stage=LLM seconds=%.1f items=%d", job_id, time.monotonic() - t1, len(items))

        # 추출 결과 출처를 함께 저장한다. 출처 속성이 없는 추출기(테스트용 가짜 등)는 NULL(알 수 없음)
        extracted_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
        if store.complete_if_processing(
            job_id, transcript, items,
            extract_model=getattr(extractor, "model_name", None),
            prompt_version=getattr(extractor, "prompt_version", None),
            extracted_at=extracted_at,
        ):
            delete_upload(job_id)  # 완료되면 음성은 지운다 (실패면 Retry 를 위해 남김)


MSG_INTERRUPTED = "서버 재시작으로 중단됨"


def recover_interrupted_jobs() -> list[str]:
    """서버 시작 때(app.main lifespan) 한 번만 부른다. 업로드 작업 중 처리 중·대기로 남은 것을 failed 로 바꿔 Retry 할 수 있게 한다.
    스크립트(smoke_gemini·reset_db)나 Store 를 여는 것만으로는 실행되지 않는다(다른 프로세스가 처리 중인 작업을 건드리지 않게).
    로그에는 작업 ID와 건수만 남긴다."""
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    ids = store.fail_interrupted_uploads(f"{now} [ERROR] {MSG_INTERRUPTED}")
    if ids:
        log.warning("startup recovery: %d interrupted job(s) marked failed: %s", len(ids), ", ".join(ids))
    return ids


def run_job(job_id: str) -> None:
    """업로드·Retry 뒤 백그라운드에서 부르는 진입점. 둘 다 fake 이면 기존 가짜 처리기를 그대로 쓴다."""
    if settings.stt_provider == "fake" and settings.llm_provider == "fake":
        run_fake_worker(job_id)
        status = store.get_job_status(job_id)
        if status and status["status"] == "completed":
            delete_upload(job_id)
        return
    process_job(job_id)
