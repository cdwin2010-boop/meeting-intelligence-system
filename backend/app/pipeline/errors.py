"""예외 → 사람이 읽을 한 줄 요약, API 키 가리기 (stub app/pipeline/service.py 의 redact·describe_error 이식).
요약에는 스택 트레이스와 키 값을 넣지 않는다."""
import re

from app.config import GeminiKeyMissingError, settings
from app.pipeline.extractor import ExtractionError
from app.pipeline.stt import GeminiFileError

MSG_NO_SPEECH = "말소리가 감지되지 않았습니다. 음성이 들어 있는 파일인지 확인해 주세요."

_KEY_PATTERN = re.compile(r"AIza[0-9A-Za-z_\-]{20,}")  # Google API 키 모양


def redact(text: str) -> str:
    """혹시라도 문장에 API 키가 섞이면 가린다 (무료·유료 키 값 모두 + 키 모양 문자열)."""
    for key in settings.all_gemini_keys():
        text = text.replace(key, "***")
    return _KEY_PATTERN.sub("***", text)


def error_code(exc: BaseException) -> str:
    """예외 → DB(jobs.error_code)에 남길 분류 코드. 예외 원문·키·파일 경로는 넣지 않는다."""
    if isinstance(exc, GeminiKeyMissingError):
        return "gemini_key_missing"
    if isinstance(exc, FileNotFoundError):
        return "upload_missing"
    try:
        import httpx

        if isinstance(exc, httpx.TimeoutException):
            return "timeout"
    except ImportError:  # pragma: no cover
        pass
    if isinstance(exc, TimeoutError):
        return "timeout"
    if isinstance(exc, GeminiFileError):
        return "stt_file_failed"
    if isinstance(exc, ExtractionError):
        return "extraction_invalid"
    try:
        from google.genai import errors as genai_errors
    except ImportError:  # pragma: no cover
        genai_errors = None
    if genai_errors is not None and isinstance(exc, genai_errors.APIError):
        code = exc.code if isinstance(exc.code, int) else 0
        return f"gemini_api_{code}"
    return "internal_error"


def describe_error(exc: BaseException) -> str:
    """예외 → 사람이 읽을 한 줄 요약 (스택 트레이스·키 없음)"""
    timeout = f"{settings.gemini_timeout_sec:g}초"
    if isinstance(exc, GeminiKeyMissingError):
        return redact(str(exc))  # 설정 이름만 들어 있다
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
        return "업무 추출 결과의 형식이 올바르지 않습니다. Retry 하세요."

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
