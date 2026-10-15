"""로컬 LLM(OpenAI 호환) 클라이언트와 연결 보호(작업 81-1).

- 요청: POST {base_url}/chat/completions, 본문 model·messages·temperature·max_tokens(+ JSON 모드면 response_format).
- 보안 원칙: 기본은 루프백·사설망 호스트만 허용한다(LOCAL_LLM_ALLOW_EXTERNAL_HOST 가 참이면 허용). 호스트 판정은 문자열·IP 리터럴 기준이며
  이름이 사설 IP 로 해석되는지 DNS 조회는 하지 않는다(한계). https 인증서 검증은 끄지 않고 리디렉션은 따라가지 않는다.
- 오류는 LocalLlmError(kind: transient | permanent)로 올린다. 메시지에는 주소 전체·키·요청 본문·전사 본문을 넣지 않는다.
- HTTP 오류(4xx·5xx) 때 서버가 준 응답 본문은 앞 500자만, 키·인증 헤더·주소를 가린 뒤 LocalLlmError.detail 에만 담는다
  (메시지·로그에는 넣지 않는다. 이력 저장·관리자 화면 표시는 호출부가 한다, 작업 B-1d).
"""
import ipaddress
import json
import logging
import re
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

DETAIL_MAX_CHARS = 500
_BODY_READ_MAX = 4000  # 마스킹 전에 읽는 응답 본문 상한(너무 긴 본문 방어)
_URL = re.compile(r"https?://[^\s\"'<>]+", re.IGNORECASE)
_BEARER = re.compile(r"(?i)bearer\s+[A-Za-z0-9._~+/=\-]+")
_KEY_LIKE = re.compile(r"\b(?:sk-|AIza)[A-Za-z0-9_\-]{8,}")
_IPV4 = re.compile(r"\b\d{1,3}(?:\.\d{1,3}){3}(?::\d{1,5})?\b")

# 400 응답 본문 분류(작업 B-1d)
BAD_CONTEXT = "context"  # 맥락 길이·토큰 초과
BAD_OPTION = "option"  # response_format 등 요청 옵션 미지원
BAD_EMPTY = "empty"  # 사유를 읽을 수 없는 본문(비어 있거나 JSON 의 빈 객체)
BAD_OTHER = "other"
_LIMIT_WORDS = ("exceed", "too many", "too long", "greater than", "larger than", "maximum", "limit", "overflow")

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

    def __init__(self, message: str, *, kind: str, reason: str = "", detail: str = ""):
        super().__init__(message)
        self.kind = kind
        # timeout | connect | http_5xx | http_429 | http_4xx | context_exceeded(맥락 초과 400) | context_limit_reached(사용 토큰이
        # 맥락 한계 도달) | output_truncated(finish_reason=length) | format | host_blocked | config
        self.reason = reason
        self.detail = detail  # 서버 응답 본문 앞 500자(마스킹됨, 없으면 ""). 메시지·로그에는 넣지 않는다

    @property
    def job_error_code(self) -> str:
        """jobs.error_code 에 남길 분류 코드."""
        if self.reason == "timeout":
            return "timeout"
        if self.reason == "host_blocked":
            return "llm_host_blocked"
        if self.reason == "context_too_small":
            return "llm_context_too_small"
        if self.reason in ("context_exceeded", "context_limit_reached"):
            return "llm_context_exceeded"
        if self.reason == "output_truncated":
            return "llm_output_truncated"
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


def mask_detail(text: str | None, *, base_url: str = "", api_key: str = "") -> str:
    """응답 본문 일부를 이력·화면용으로 가린다: 키 값·인증 헤더 값·서버 주소(URL·호스트·IPv4)를 지우고 앞 500자만 남긴다."""
    value = (text or "")[:_BODY_READ_MAX]
    key = (api_key or "").strip()
    if key and key != NO_KEY:
        value = value.replace(key, "***")
    host = host_of(base_url) if base_url else None
    for literal in (base_url.strip().rstrip("/") if base_url else "", host or ""):
        if literal:
            value = value.replace(literal, "***")
    value = _URL.sub("***", value)
    value = _BEARER.sub("Bearer ***", value)
    value = _KEY_LIKE.sub("***", value)
    value = _IPV4.sub("***", value)
    return " ".join(value.split())[:DETAIL_MAX_CHARS]


def classify_bad_request(body_text: str | None) -> str:
    """400 응답 본문 분류: context(맥락·토큰 초과) | option(response_format 등 미지원) | empty | other."""
    low = (body_text or "").strip().lower()
    if low in ("", "{}", "null", "[]", "{\"error\": {}}"):
        return BAD_EMPTY
    if "n_ctx" in low or "context" in low or ("token" in low and any(w in low for w in _LIMIT_WORDS)):
        return BAD_CONTEXT
    if any(word in low for word in ("response_format", "json_object", "json_schema")):
        return BAD_OPTION
    return BAD_OTHER


def _status_error(status: int, detail: str = "", category: str = "") -> LocalLlmError:
    if status == 429:
        return LocalLlmError("로컬 LLM 이 요청 한도를 초과했다고 응답했습니다(429).", kind=TRANSIENT, reason="http_429", detail=detail)
    if status >= 500:
        return LocalLlmError(f"로컬 LLM 서버 오류({status}).", kind=TRANSIENT, reason="http_5xx", detail=detail)
    if status == 400 and category == BAD_CONTEXT:
        return LocalLlmError(
            "로컬 LLM 이 요청이 모델의 맥락 길이(토큰 한도)를 넘는다고 응답했습니다(400).",
            kind=PERMANENT, reason="context_exceeded", detail=detail,
        )
    return LocalLlmError(f"로컬 LLM 이 요청을 거절했습니다({status}).", kind=PERMANENT, reason="http_4xx", detail=detail)


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
        allow_external: bool = False, json_mode_enabled: bool = True, context_tokens: int = 0, truncation_ratio: float = 0.98,
    ):
        self._base = base_url.rstrip("/")
        self._model = model
        self._api_key = api_key
        self._max_tokens = max_output_tokens
        self._allow_external = allow_external
        self._json_enabled = json_mode_enabled
        self._context_tokens = context_tokens  # 0 이면 잘림 검사(사용 토큰 ≥ 맥락×비율)를 건너뛴다
        self._truncation_ratio = truncation_ratio
        self.usage_missing = 0  # 응답에 사용 토큰 정보가 없던 횟수(잘림 검사를 건너뛴 기록)
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
        category = ""
        if response.status_code == 400:
            category = classify_bad_request(self._body_text(response))
            # 맥락 길이 초과는 옵션 문제가 아니므로 다시 보내지 않는다(호출부가 구간을 나눠 다시 요청한다)
            # 옵션 미지원(또는 사유를 읽을 수 없는 400)이고 JSON 모드였으면 response_format 없이 1회만 다시 요청한다(프롬프트 유도에 의존)
            if use_json and category in (BAD_OPTION, BAD_EMPTY):
                self._json_rejected = True
                body.pop("response_format")
                response = self._post(body, timeout)
                category = classify_bad_request(self._body_text(response)) if response.status_code == 400 else ""
        if response.status_code != 200:
            raise _status_error(response.status_code, self._detail(response), category)
        self._check_truncation(response)
        return _content_of(response)

    def _check_truncation(self, response: httpx.Response) -> None:
        """조용한 잘림 감지: finish_reason=length 이면 출력 잘림, 사용 토큰이 맥락×비율 이상이면 맥락 한계 도달(잘림 의심).
        사용 토큰 정보가 없으면 검사를 건너뛰고 건수만 센다. 본문 모양이 다르면(JSON 아님 등) 여기서는 아무것도 하지 않는다."""
        try:
            data = response.json()
            finish = data["choices"][0].get("finish_reason")
            usage = data.get("usage") if isinstance(data.get("usage"), dict) else {}
        except (ValueError, KeyError, IndexError, TypeError, AttributeError):
            return
        used = usage.get("prompt_tokens") if isinstance(usage.get("prompt_tokens"), int) else usage.get("total_tokens")
        info = f"finish_reason={finish}, prompt_tokens={used}, n_ctx={self._context_tokens or '미설정'}"
        if finish == "length":
            raise LocalLlmError("로컬 LLM 응답이 출력 길이 한도에서 잘렸습니다(finish_reason=length).", kind=PERMANENT, reason="output_truncated", detail=info)
        if not self._context_tokens:
            return
        if not isinstance(used, int):
            self.usage_missing += 1
            log.info("response has no usage token info; truncation check skipped")
            return
        if used >= self._context_tokens * self._truncation_ratio:
            raise LocalLlmError(
                "사용 토큰이 모델 맥락 길이의 한계에 도달해 앞부분이 잘렸을 수 있습니다.", kind=PERMANENT, reason="context_limit_reached", detail=info,
            )

    @staticmethod
    def _body_text(response: httpx.Response) -> str:
        try:
            raw = response.text[:_BODY_READ_MAX]
        except Exception:  # noqa: BLE001 — 본문을 못 읽어도 오류 분류는 계속한다
            return ""
        try:  # JSON 이면 한글이 \uXXXX 로 읽히지 않게 다시 직렬화한다(관리자가 읽을 문구)
            return json.dumps(json.loads(raw), ensure_ascii=False)
        except ValueError:
            return raw

    def _detail(self, response: httpx.Response) -> str:
        return mask_detail(self._body_text(response), base_url=self._base, api_key=self._api_key)


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
