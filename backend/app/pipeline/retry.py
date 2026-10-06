"""Gemini generate_content 일시 오류 자동 재시도(호출 하나하나에 적용).
- 재시도 대상: HTTP 500·502·503·504 와 접속 오류(httpx.ConnectError)뿐. 타임아웃·429(한도 초과)·기타 4xx·키 오류는 바로 올려 보낸다.
- 횟수 GEMINI_RETRY_MAX(0 이면 끔), 대기 GEMINI_RETRY_BACKOFF_SEC 에서 시작해 재시도마다 2배(최대 60초).
- SDK 자체 재시도는 쓰지 않는다(클라이언트에 retry_options 를 주지 않으면 SDK 는 한 번만 시도한다) → 이중 재시도 없음.
- 이미 성공한 단계는 호출하는 쪽이 다시 부르지 않는다(이 함수는 호출 1건만 감싼다).
- 로그에는 예외 종류와 상태 코드·남은 횟수만 남긴다(키·원본 문구 금지).
"""
import logging
import time
from typing import Any

import httpx

from app.config import settings

log = logging.getLogger("app.gemini")

RETRYABLE_STATUS = (500, 502, 503, 504)
MAX_BACKOFF_SEC = 60.0


def _sleep(seconds: float) -> None:
    # 테스트가 가로채 실제로 기다리지 않게 한다
    time.sleep(seconds)


def status_code_of(exc: BaseException) -> int | None:
    code = getattr(exc, "code", None)
    return code if isinstance(code, int) else None


def is_transient(exc: BaseException) -> bool:
    """재시도해도 되는 일시 오류인지."""
    if isinstance(exc, httpx.TimeoutException):
        return False  # 타임아웃은 재시도하지 않는다
    if isinstance(exc, httpx.ConnectError):
        return True
    try:
        from google.genai import errors as genai_errors
    except ImportError:  # pragma: no cover
        return False
    return isinstance(exc, genai_errors.APIError) and status_code_of(exc) in RETRYABLE_STATUS


def generate_with_retry(client: Any, **kwargs: Any) -> Any:
    """client.models.generate_content(**kwargs) 를 일시 오류에 한해 재시도한다. 마지막 실패는 원래 예외 그대로 올린다."""
    attempt = 0
    while True:
        try:
            return client.models.generate_content(**kwargs)
        except Exception as exc:  # noqa: BLE001
            if attempt >= settings.gemini_retry_max or not is_transient(exc):
                raise
            delay = min(MAX_BACKOFF_SEC, settings.gemini_retry_backoff_sec * (2**attempt))
            attempt += 1
            log.warning(
                "gemini transient error %s (status %s): retry %d/%d in %.1fs",
                type(exc).__name__, status_code_of(exc), attempt, settings.gemini_retry_max, delay,
            )
            _sleep(delay)
