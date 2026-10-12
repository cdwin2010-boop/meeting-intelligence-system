"""로컬 LLM(OpenAI 호환) 클라이언트와 연결 보호(작업 81-1).

- 요청: POST {base_url}/chat/completions, 본문 model·messages·temperature·max_tokens(+ JSON 모드면 response_format).
- 보안 원칙: 기본은 루프백·사설망 호스트만 허용한다(LOCAL_LLM_ALLOW_EXTERNAL_HOST 가 참이면 허용). 호스트 판정은 문자열·IP 리터럴 기준이며
  이름이 사설 IP 로 해석되는지 DNS 조회는 하지 않는다(한계). https 인증서 검증은 끄지 않고 리디렉션은 따라가지 않는다.
- 오류는 LocalLlmError(kind: transient | permanent)로 올린다. 메시지에는 주소 전체·키·요청 본문·전사 본문을 넣지 않는다.
"""
import ipaddress
import logging
from urllib.parse import urlsplit

import httpx

from app.pipeline.llm_client import LlmClientError, Message

log = logging.getLogger("app.local_llm")
# httpx 는 INFO 로 요청 주소 전체를 남긴다. 주소를 로그에 남기지 않기 위해 httpx·httpcore 로그는 경고 이상만 남긴다.
for _name in ("httpx", "httpcore"):
    logging.getLogger(_name).setLevel(logging.WARNING)

TRANSIENT = "transient"  # 연결 실패·타임아웃·5xx·429: 재처리하면 될 수 있는 오류
PERMANENT = "permanent"  # 4xx·응답 형식 오류·설정 오류
NO_KEY = "not-needed"
TEMPERATURE = 0.1

HOST_LOOPBACK = "loopback"
HOST_PRIVATE = "private"
HOST_EXTERNAL = "external"
HOST_INVALID = "invalid"

_PRIVATE_NETWORKS = tuple(
    ipaddress.ip_network(n)
    for n in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "169.254.0.0/16", "fc00::/7", "fe80::/10")
)


class LocalLlmError(LlmClientError):
    """로컬 LLM 호출 실패. kind 로 일시/영구를 구분한다."""

    def __init__(self, message: str, *, kind: str, reason: str = ""):
        super().__init__(message)
        self.kind = kind
        self.reason = reason  # timeout | connect | http_5xx | http_429 | http_4xx | format | host_blocked | config

    @property
    def job_error_code(self) -> str:
        """jobs.error_code 에 남길 분류 코드."""
        if self.reason == "timeout":
            return "timeout"
        if self.reason == "host_blocked":
            return "llm_host_blocked"
        return "llm_unavailable" if self.kind == TRANSIENT else "llm_error"


def classify_host(host: str | None) -> str:
    """호스트 문자열 분류: loopback | private | external | invalid. 이름 해석(DNS)은 하지 않는다."""
    value = (host or "").strip().strip("[]").lower().rstrip(".")
    if not value or any(c in value for c in " /\\@"):
        return HOST_INVALID
    try:
        address = ipaddress.ip_address(value)
    except ValueError:
        pass
    else:
        if address.is_loopback:
            return HOST_LOOPBACK
        if any(address in net for net in _PRIVATE_NETWORKS if net.version == address.version):
            return HOST_PRIVATE
        return HOST_EXTERNAL
    if value == "localhost" or value.endswith(".localhost"):
        return HOST_LOOPBACK
    if value.endswith(".local") or "." not in value:
        return HOST_PRIVATE  # mDNS 이름·점 없는 호스트명(사내 이름)
    return HOST_EXTERNAL


def mask_host(host: str | None) -> str:
    """로그·출력용 호스트 일부 마스킹. 주소 전체를 남기지 않는다."""
    value = (host or "").strip().strip("[]")
    if not value:
        return "(없음)"
    try:
        address = ipaddress.ip_address(value)
    except ValueError:
        pass
    else:
        if address.version == 4:
            return ".".join(str(address).split(".")[:2] + ["*", "*"])
        return str(address)[:4] + ":***"
    head, dot, tail = value.partition(".")
    if len(head) <= 3:
        return "***" + (dot + tail.rsplit(".", 1)[-1] if dot else "")
    return head[:3] + "***" + (dot + tail.rsplit(".", 1)[-1] if dot else "")


def host_of(base_url: str) -> str | None:
    try:
        return urlsplit(base_url).hostname
    except ValueError:
        return None


def check_base_url(base_url: str, allow_external: bool) -> None:
    """연결 보호: 허용되지 않은 주소면 LocalLlmError(permanent, host_blocked). 형식 오류는 reason=config."""
    parts = urlsplit(base_url or "")
    host_class = classify_host(parts.hostname) if parts.scheme in ("http", "https") else HOST_INVALID
    if host_class == HOST_INVALID:
        raise LocalLlmError("로컬 LLM 주소 형식이 올바르지 않습니다.", kind=PERMANENT, reason="config")
    if host_class == HOST_EXTERNAL and not allow_external:
        raise LocalLlmError(
            "외부 주소는 허용 설정(LOCAL_LLM_ALLOW_EXTERNAL_HOST)이 필요합니다.", kind=PERMANENT, reason="host_blocked"
        )


def external_host_warning(base_url: str, allow_external: bool) -> str | None:
    """외부 호스트를 허용해 쓰는 중이면 시작 시 남길 경고 문구(호스트 일부 마스킹), 아니면 None."""
    host = host_of(base_url)
    if allow_external and base_url and classify_host(host) == HOST_EXTERNAL:
        return (
            f"로컬 LLM 주소가 외부 호스트({mask_host(host)})입니다. LOCAL_LLM_ALLOW_EXTERNAL_HOST 로 허용된 상태이며 "
            "전사 내용이 이 호스트로 전송됩니다."
        )
    return None


def log_startup_warning(provider: str, base_url: str, allow_external: bool) -> None:
    """서버·워커 시작 때 호출: 로컬 엔진이 외부 호스트를 쓰도록 허용돼 있으면 경고 로그(호스트 일부 마스킹)."""
    if provider != "local":
        return
    message = external_host_warning(base_url, allow_external)
    if message:
        log.warning(message)


def _status_error(status: int) -> LocalLlmError:
    if status == 429:
        return LocalLlmError("로컬 LLM 이 요청 한도를 초과했다고 응답했습니다(429).", kind=TRANSIENT, reason="http_429")
    if status >= 500:
        return LocalLlmError(f"로컬 LLM 서버 오류({status}).", kind=TRANSIENT, reason="http_5xx")
    return LocalLlmError(f"로컬 LLM 이 요청을 거절했습니다({status}).", kind=PERMANENT, reason="http_4xx")


def _transport_error(exc: BaseException) -> LocalLlmError:
    if isinstance(exc, httpx.TimeoutException):
        return LocalLlmError("로컬 LLM 응답 시간이 초과되었습니다.", kind=TRANSIENT, reason="timeout")
    return LocalLlmError(f"로컬 LLM 에 연결하지 못했습니다({type(exc).__name__}).", kind=TRANSIENT, reason="connect")


def _headers(api_key: str) -> dict:
    headers = {"Content-Type": "application/json"}
    key = (api_key or "").strip()
    if key and key != NO_KEY:
        headers["Authorization"] = f"Bearer {key}"
    return headers


class OpenAICompatClient:
    """LlmClient 프로토콜 구현. complete() 한 번의 HTTP 요청은 최대 2회(JSON 모드 거부 시 response_format 없이 1회 재요청)."""

    def __init__(
        self, base_url: str, model: str, api_key: str = NO_KEY, *, max_output_tokens: int = 4096,
        allow_external: bool = False, json_mode_enabled: bool = True,
    ):
        self._base = base_url.rstrip("/")
        self._model = model
        self._api_key = api_key
        self._max_tokens = max_output_tokens
        self._allow_external = allow_external
        self._json_enabled = json_mode_enabled
        self._json_rejected = False  # 서버가 response_format 을 거부한 적이 있으면 이후 요청에서 생략한다

    def _post(self, body: dict, timeout: float) -> httpx.Response:
        try:
            with httpx.Client(timeout=timeout, follow_redirects=False, trust_env=False) as http:
                return http.post(f"{self._base}/chat/completions", json=body, headers=_headers(self._api_key))
        except httpx.HTTPError as exc:
            raise _transport_error(exc) from None

    def complete(self, messages: list[Message], json_mode: bool, timeout: float) -> str:
        check_base_url(self._base, self._allow_external)
        body = {"model": self._model, "messages": messages, "temperature": TEMPERATURE, "max_tokens": self._max_tokens}
        use_json = json_mode and self._json_enabled and not self._json_rejected
        if use_json:
            body["response_format"] = {"type": "json_object"}
        response = self._post(body, timeout)
        if use_json and response.status_code == 400:
            # 서버가 JSON 모드를 지원하지 않는 경우: response_format 없이 1회만 다시 요청한다(프롬프트 유도에 의존)
            self._json_rejected = True
            body.pop("response_format")
            response = self._post(body, timeout)
        if response.status_code != 200:
            raise _status_error(response.status_code)
        return _content_of(response)


def _content_of(response: httpx.Response) -> str:
    try:
        content = response.json()["choices"][0]["message"]["content"]
    except (ValueError, KeyError, IndexError, TypeError):
        raise LocalLlmError("로컬 LLM 응답 형식이 올바르지 않습니다.", kind=PERMANENT, reason="format") from None
    if not isinstance(content, str):
        raise LocalLlmError("로컬 LLM 응답 형식이 올바르지 않습니다.", kind=PERMANENT, reason="format")
    return content


def list_models(base_url: str, api_key: str, timeout: float, allow_external: bool) -> list[str]:
    """GET {base_url}/models 로 모델 id 목록을 읽는다(프롬프트·회의 내용을 보내지 않는다)."""
    check_base_url(base_url, allow_external)
    try:
        with httpx.Client(timeout=timeout, follow_redirects=False, trust_env=False) as http:
            response = http.get(f"{base_url.rstrip('/')}/models", headers=_headers(api_key))
    except httpx.HTTPError as exc:
        raise _transport_error(exc) from None
    if response.status_code != 200:
        raise _status_error(response.status_code)
    try:
        data = response.json()["data"]
        return [str(entry["id"]) for entry in data]
    except (ValueError, KeyError, TypeError):
        raise LocalLlmError("로컬 LLM 응답 형식이 올바르지 않습니다.", kind=PERMANENT, reason="format") from None
