"""질의응답용 LLM 스트리밍 클라이언트(작업 89-1). 공급자는 설정(QA_LLM_PROVIDER, auto 면 LLM_PROVIDER)으로 전환한다.

- local: OpenAI 호환 /chat/completions (stream=true, SSE "data:" 줄). ollama: 네이티브 /api/chat (stream=true, 줄 단위 JSON).
- fake: 컨텍스트의 첫 근거 ID 를 인용하는 고정 응답(개발·테스트용, 외부 호출 없음).
- 호출부가 제너레이터를 닫으면(연결 끊김·시간 초과) HTTP 스트림도 닫혀 추론이 취소된다.
- 주소·키·질문·답변은 예외 문구·로그에 넣지 않는다. 오류는 분류 코드만 올린다.
"""
import json
import re
import time
from collections.abc import Iterator
from typing import Any, Protocol

import httpx

from app.config import settings
from app.pipeline.local_llm import NO_KEY, LocalLlmError, _headers, check_base_url

CODE_UNAVAILABLE = "llm_unavailable"
CODE_TIMEOUT = "llm_timeout"
CODE_FAILED = "llm_error"
_ID = re.compile(r"\[(S\d+)\]")


class QaLlmError(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


class QaLlm(Protocol):
    uses_gpu: bool

    def stream(self, messages: list[dict[str, str]], *, max_tokens: int, timeout: float) -> Iterator[str]: ...


def _timeout(total: float) -> httpx.Timeout:
    return httpx.Timeout(total, connect=min(5.0, total))


def _classify(exc: BaseException) -> QaLlmError:
    if isinstance(exc, httpx.TimeoutException):
        return QaLlmError(CODE_TIMEOUT, "응답 시간이 초과되었습니다")
    if isinstance(exc, httpx.ConnectError):
        return QaLlmError(CODE_UNAVAILABLE, "답변 서버에 연결할 수 없습니다")
    return QaLlmError(CODE_FAILED, "답변을 만들지 못했습니다")


class OpenAIStreamLlm:
    uses_gpu = True

    def __init__(self, base_url: str, model: str, api_key: str, allow_external: bool):
        self._base, self._model, self._key, self._allow = base_url.rstrip("/"), model, api_key, allow_external
        self.model_label = model

    def stream(self, messages, *, max_tokens, timeout):
        try:
            check_base_url(self._base, self._allow)
        except LocalLlmError:
            raise QaLlmError(CODE_UNAVAILABLE, "답변 서버 주소가 허용되지 않습니다") from None
        body = {"model": self._model, "messages": messages, "temperature": 0.2, "max_tokens": max_tokens, "stream": True}
        try:
            with httpx.Client(timeout=_timeout(timeout), follow_redirects=False, trust_env=False) as http:
                with http.stream("POST", f"{self._base}/chat/completions", json=body, headers=_headers(self._key)) as response:
                    if response.status_code != 200:
                        raise QaLlmError(CODE_FAILED, "답변 서버가 요청을 거절했습니다")
                    for line in response.iter_lines():
                        if not line.startswith("data:"):
                            continue
                        payload = line[5:].strip()
                        if payload == "[DONE]":
                            return
                        try:
                            delta = json.loads(payload)["choices"][0].get("delta", {}).get("content")
                        except (ValueError, KeyError, IndexError, TypeError):
                            continue
                        if isinstance(delta, str) and delta:
                            yield delta
        except httpx.HTTPError as exc:
            raise _classify(exc) from None


class OllamaStreamLlm:
    uses_gpu = True

    def __init__(self, base_url: str, model: str, allow_external: bool, num_ctx: int):
        self._base, self._model, self._allow, self._num_ctx = base_url.rstrip("/"), model, allow_external, num_ctx
        self.model_label = model

    def stream(self, messages, *, max_tokens, timeout):
        try:
            check_base_url(self._base, self._allow)
        except LocalLlmError:
            raise QaLlmError(CODE_UNAVAILABLE, "답변 서버 주소가 허용되지 않습니다") from None
        body = {
            "model": self._model, "messages": messages, "stream": True,
            "options": {"temperature": 0.2, "num_ctx": self._num_ctx, "num_predict": max_tokens},
        }
        try:
            with httpx.Client(timeout=_timeout(timeout), follow_redirects=False, trust_env=False) as http:
                with http.stream("POST", f"{self._base}/api/chat", json=body) as response:
                    if response.status_code != 200:
                        raise QaLlmError(CODE_FAILED, "답변 서버가 요청을 거절했습니다")
                    for line in response.iter_lines():
                        if not line.strip():
                            continue
                        try:
                            data: Any = json.loads(line)
                        except ValueError:
                            continue
                        piece = (data.get("message") or {}).get("content") if isinstance(data, dict) else None
                        if isinstance(piece, str) and piece:
                            yield piece
                        if isinstance(data, dict) and data.get("done"):
                            return
        except httpx.HTTPError as exc:
            raise _classify(exc) from None


class FakeQaLlm:
    """외부 호출 없는 응답: 자료의 첫 ID 를 인용한다(자료가 없으면 근거 없는 문장). responder 가 있으면 그것이 우선한다."""

    uses_gpu = False
    model_label = "fake"

    def __init__(self, responder=None, delay: float = 0.0, chunk: int = 12):
        self.responder = responder
        self.delay = delay
        self.chunk = chunk
        self.calls: list[list[dict[str, str]]] = []
        self.closed = False

    def stream(self, messages, *, max_tokens, timeout):
        self.calls.append([dict(m) for m in messages])
        try:
            if self.responder is not None:
                text = self.responder(messages)
            else:
                ids = _ID.findall(messages[-1]["content"])
                text = f"자료에 따르면 해당 내용이 있습니다. [{ids[0]}]" if ids else "알 수 없습니다."
            for i in range(0, len(text), self.chunk):
                if self.delay:
                    time.sleep(self.delay)
                yield text[i : i + self.chunk]
        finally:
            self.closed = True  # 제너레이터가 닫히면(연결 끊김·시간 초과) 추론이 취소된 것


def make_qa_llm() -> QaLlm:
    """설정에서 공급자를 골라 만든다. 쓸 수 없으면 QaLlmError(llm_unavailable)."""
    provider = settings.qa_llm_provider
    if provider == "auto":
        provider = settings.effective_llm_provider
    if provider == "fake":
        return FakeQaLlm()
    if provider == "local":
        if not (settings.local_llm_base_url and settings.local_llm_model):
            raise QaLlmError(CODE_UNAVAILABLE, "답변 엔진이 설정되지 않았습니다")
        key = settings.local_llm_api_key.get_secret_value() or NO_KEY
        return OpenAIStreamLlm(settings.local_llm_base_url, settings.local_llm_model, key, settings.local_llm_allow_external_host)
    if provider == "ollama":
        if not (settings.ollama_base_url and settings.ollama_model):
            raise QaLlmError(CODE_UNAVAILABLE, "답변 엔진이 설정되지 않았습니다")
        return OllamaStreamLlm(settings.ollama_base_url, settings.ollama_model, settings.local_llm_allow_external_host, settings.ollama_num_ctx)
    raise QaLlmError(CODE_UNAVAILABLE, "이 답변 엔진은 질의응답에 지원되지 않습니다")
