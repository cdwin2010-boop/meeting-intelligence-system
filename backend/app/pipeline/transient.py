"""일시 오류 판별(작업 81-1). 재시도를 소진한 뒤에도 남은 실패가 "일시 오류(연결·시간 초과·5xx·429)"인지 가른다.
일시 오류 때문에 비어 버린 결과는 처리 완료가 아니라 실패로 표시해야 재처리할 수 있다. 정상 응답의 0건과 구분하기 위한 규칙이다."""
import httpx

TRANSIENT_STATUS_MIN = 500


def is_transient_error(exc: BaseException) -> bool:
    """로컬 LLM 오류(kind=transient), httpx 연결·시간 초과, TimeoutError, Gemini 429·5xx 는 일시 오류."""
    if getattr(exc, "kind", None) == "transient":
        return True
    if isinstance(exc, (httpx.TimeoutException, httpx.ConnectError, TimeoutError, ConnectionError)):
        return True
    try:
        from google.genai import errors as genai_errors
    except ImportError:  # pragma: no cover
        return False
    if isinstance(exc, genai_errors.APIError):
        code = exc.code if isinstance(exc.code, int) else 0
        return code == 429 or code >= TRANSIENT_STATUS_MIN
    return False
