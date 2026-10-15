"""원격(시험용) 엔진 연결 진단과 모델 이름 비교(작업 L-4a). 화면·API·명령줄·측정 시작 전 점검이 함께 쓴다.

- GET {base}/models 한 번만 호출한다(프롬프트·회의 내용 없음). 연결 3초·전체 5초를 넘지 않고 리다이렉트·재시도 없음.
- 결과·로그·예외 메시지에 주소 전체와 키를 넣지 않는다(분류와 한국어 힌트만).
- 원인을 단정하지 않는다: 힌트는 "가능성"을 안내하는 문구다.
"""
import concurrent.futures
import difflib
from dataclasses import dataclass, field
from datetime import datetime, timezone

import httpx

from app.pipeline.local_llm import LocalLlmError, check_base_url

OK = "OK"
UNREACHABLE = "UNREACHABLE"
PATH_MISMATCH = "PATH_MISMATCH"
AUTH_FAILED = "AUTH_FAILED"
SERVER_ERROR = "SERVER_ERROR"
MODEL_MISSING = "MODEL_MISSING"

CONNECT_TIMEOUT = 3.0
TOTAL_TIMEOUT = 5.0
CANDIDATE_LIMIT = 3

HINTS = {
    OK: "서버와 모델이 정상입니다",
    UNREACHABLE: "서버에 연결할 수 없습니다. 주소·포트, 서버 실행 여부, 네트워크 연결을 확인하세요",
    PATH_MISMATCH: "서버는 응답했지만 모델 목록 경로가 아닙니다. 주소 끝의 /v1 경로와 포트가 맞는지 확인하세요",
    AUTH_FAILED: "서버가 인증을 거절했습니다. API 키를 확인하세요",
    SERVER_ERROR: "서버 내부 오류이거나 응답이 없습니다. 서버 상태를 확인하고 잠시 뒤 다시 시도하세요",
    MODEL_MISSING: "서버에 설정한 모델이 없습니다. 아래 후보에서 모델명을 선택하거나 서버의 모델 목록을 확인하세요",
}
_HOST_BLOCKED_HINT = "주소가 허용되지 않았거나 형식이 올바르지 않습니다(외부 주소 허용 설정과 주소 형식을 확인하세요)"


@dataclass
class Diagnosis:
    category: str
    http_status: int | None
    elapsed_ms: int
    hint: str
    checked_at: str
    candidates: list[str] = field(default_factory=list)  # MODEL_MISSING 일 때 서버 목록에서 가장 가까운 이름

    @property
    def ok(self) -> bool:
        return self.category == OK

    def as_dict(self) -> dict:
        return {"category": self.category, "httpStatus": self.http_status, "elapsedMs": self.elapsed_ms, "hint": self.hint,
                "checkedAt": self.checked_at, "candidates": list(self.candidates)}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ------------------------- 모델 이름 비교 -------------------------
def _canon(name: str) -> str:
    """앞뒤 공백 제거, 대소문자는 구분, 끝의 ':latest' 는 없는 것과 같게 본다."""
    value = (name or "").strip()
    return value[: -len(":latest")] if value.endswith(":latest") else value


def model_matches(configured: str, listed: str) -> bool:
    configured_c = _canon(configured)
    return bool(configured_c) and configured_c == _canon(listed)


def model_in_list(configured: str, listed: list[str]) -> bool:
    return any(model_matches(configured, name) for name in listed)


def closest_models(configured: str, listed: list[str], limit: int = CANDIDATE_LIMIT) -> list[str]:
    """서버 목록에서 설정 이름과 가장 가까운 후보(자동 변경은 하지 않는다). 대소문자 차이는 가까운 것으로 본다."""
    target = _canon(configured).lower()
    if not target or not listed:
        return []
    scored = sorted(((difflib.SequenceMatcher(None, target, _canon(n).lower()).ratio(), n) for n in listed), key=lambda x: (-x[0], x[1]))
    return [name for _score, name in scored[:limit]]


# ------------------------- 진단 -------------------------
def _result(category: str, started: float, http_status: int | None = None, hint: str | None = None, candidates: list[str] | None = None) -> Diagnosis:
    import time

    return Diagnosis(category=category, http_status=http_status, elapsed_ms=int((time.monotonic() - started) * 1000),
                     hint=hint or HINTS[category], checked_at=_now(), candidates=candidates or [])


def _fetch(url: str, headers: dict[str, str]) -> tuple[str, int | None, bytes]:
    """작업 스레드에서 한 번만 요청한다. 반환: (분류 힌트, 상태 코드, 본문). 예외 문자열은 밖으로 내보내지 않는다."""
    timeout = httpx.Timeout(TOTAL_TIMEOUT, connect=CONNECT_TIMEOUT)
    try:
        with httpx.Client(timeout=timeout, follow_redirects=False, trust_env=False) as http:
            response = http.get(url, headers=headers)
        return "response", response.status_code, response.content[:1_000_000]
    except (httpx.ConnectError, httpx.ConnectTimeout, httpx.UnsupportedProtocol, httpx.InvalidURL):
        return UNREACHABLE, None, b""
    except httpx.TimeoutException:  # 연결 뒤 읽기·쓰기 시간 초과
        return SERVER_ERROR, None, b""
    except httpx.HTTPError:
        # 연결이 끊김·프로토콜 오류: 연결은 됐으나 정상 응답이 아님
        return SERVER_ERROR, None, b""


def diagnose(base_url: str, api_key: str, model: str, allow_external: bool) -> Diagnosis:
    import json
    import time

    started = time.monotonic()
    try:
        check_base_url(base_url, allow_external)
    except LocalLlmError:
        return _result(UNREACHABLE, started, hint=_HOST_BLOCKED_HINT)
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
    url = f"{base_url.rstrip('/')}/models"
    pool = concurrent.futures.ThreadPoolExecutor(max_workers=1)
    future = pool.submit(_fetch, url, headers)
    try:
        kind, status_code, body = future.result(timeout=TOTAL_TIMEOUT)
    except concurrent.futures.TimeoutError:
        pool.shutdown(wait=False)
        return _result(SERVER_ERROR, started)  # 전체 시한 가드: 연결 뒤 응답이 없음
    pool.shutdown(wait=False)
    if kind != "response":
        return _result(kind, started)
    assert status_code is not None
    if status_code in (401, 403):
        return _result(AUTH_FAILED, started, status_code)
    if status_code >= 500 or status_code == 429:
        return _result(SERVER_ERROR, started, status_code)
    if status_code != 200:  # 404·405·3xx 와 그 밖의 4xx
        return _result(PATH_MISMATCH, started, status_code)
    try:
        data = json.loads(body.decode("utf-8"))["data"]
        models = [str(entry["id"]) for entry in data]
    except (ValueError, KeyError, TypeError, UnicodeDecodeError):
        return _result(PATH_MISMATCH, started, status_code)  # JSON 이 아니거나(HTML 안내 페이지 등) 목록 형식이 아님
    if model_in_list(model, models):
        return _result(OK, started, status_code)
    return _result(MODEL_MISSING, started, status_code, candidates=closest_models(model, models))
