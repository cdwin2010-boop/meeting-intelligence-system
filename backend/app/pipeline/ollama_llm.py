"""Ollama 네이티브 API 어댑터(작업 L-2). LLM_PROVIDER=ollama.

- 요청: POST {OLLAMA_BASE_URL}/api/chat, stream=false, options 에 num_ctx 를 반드시 넣는다(OpenAI 호환 /v1 은 num_ctx 를 못 정한다).
  format=json(JSON 모드), temperature 는 기존 값(TEMPERATURE) 재사용. OLLAMA_NUM_GPU 를 지정했을 때만 options.num_gpu 를 보낸다.
- 안건 분할·사전 예산(90%)·응답 잘림 처리·오류 본문 마스킹·제한 재시도는 기존 추출 엔진 부품(local_extractor·chunked_extract·local_llm)을 재사용한다.
- 오류는 ERR-LLM-* 코드(NO-SERVER / NO-MODEL / TIMEOUT / TRUNCATED / FAILED). 주소·키는 메시지·상세에 넣지 않는다. 자동 폴백 없음.
- 외부 주소는 기본 거부(루프백·사설망만, 허용은 LOCAL_LLM_ALLOW_EXTERNAL_HOST 규칙 그대로).
- OLLAMA_UNLOAD_AFTER_EXTRACT 이면 추출 종료 때(성공·예외 모두) keep_alive=0 요청으로 모델을 내려 VRAM 을 반환한다.
  워커는 작업을 직렬 처리하므로 이 해제가 끝나야 다음 작업이 시작된다.
"""
import json
import logging
from datetime import datetime
from typing import Any

import httpx

from app.config import settings
from app.pipeline.engine_profile import EngineProfile
from app.pipeline.extractor import ExtractionResult
from app.pipeline.local_extractor import LocalLlmExtractor
from app.pipeline.local_llm import (
    PERMANENT, TEMPERATURE, TRANSIENT, LocalLlmError, _status_error, _transport_error, check_base_url, classify_bad_request,
    mask_detail, BAD_CONTEXT,
)

log = logging.getLogger("app.ollama")  # 추출 부품(chunked_extract 등)은 services/extraction_engine.py 가 연결한다(기존 구조 가드)

ERR_NO_SERVER = "ERR-LLM-NO-SERVER"
ERR_NO_MODEL = "ERR-LLM-NO-MODEL"
ERR_TIMEOUT = "ERR-LLM-TIMEOUT"
ERR_TRUNCATED = "ERR-LLM-TRUNCATED"
ERR_FAILED = "ERR-LLM-FAILED"

OLLAMA_RETRY_CEILING = 2
_BODY_MAX = 4000


class OllamaError(LocalLlmError):
    """Ollama 호출 실패. reason 은 기존 LocalLlmError 값(timeout·connect·output_truncated …)에 model_missing 을 더한 것이다."""

    @property
    def job_error_code(self) -> str:
        if self.reason == "connect":
            return ERR_NO_SERVER
        if self.reason == "model_missing":
            return ERR_NO_MODEL
        if self.reason == "timeout":
            return ERR_TIMEOUT
        if self.reason in ("output_truncated", "context_limit_reached", "context_exceeded"):
            return ERR_TRUNCATED
        return ERR_FAILED


def _as_ollama(exc: LocalLlmError) -> OllamaError:
    if isinstance(exc, OllamaError):
        return exc
    return OllamaError(str(exc), kind=exc.kind, reason=exc.reason, detail=exc.detail)


def ollama_profile() -> EngineProfile:
    """Ollama 엔진 프로파일: 값은 모두 설정(OLLAMA_*, LOCAL_LLM_MAX_OUTPUT_TOKENS 아님)에서 읽는다. 사양별 값 확정은 L-3."""
    return EngineProfile(
        name="ollama", context_tokens=settings.ollama_num_ctx, max_output_tokens=settings.ollama_num_predict,
        supports_json_format=True, send_context_option=True,
    )


def _keep_alive_value(value: str) -> Any:
    v = value.strip()
    try:
        return int(v)
    except ValueError:
        return v


class OllamaClient:
    """LlmClient 프로토콜 구현(Ollama /api/chat)."""

    def __init__(
        self, base_url: str, model: str, *, num_ctx: int, num_predict: int, num_gpu: int | None = None,
        keep_alive: str = "", allow_external: bool = False, json_mode_enabled: bool = True, truncation_ratio: float = 0.98,
    ):
        self._base = base_url.rstrip("/")
        self._model = model
        self._num_ctx = num_ctx
        self._num_predict = num_predict
        self._num_gpu = num_gpu
        self._keep_alive = keep_alive.strip()
        self._allow_external = allow_external
        self._json_enabled = json_mode_enabled
        self._ratio = truncation_ratio

    def build_body(self, messages: list[dict[str, str]], json_mode: bool) -> dict:
        options: dict[str, Any] = {"temperature": TEMPERATURE, "num_ctx": self._num_ctx, "num_predict": self._num_predict}
        if self._num_gpu is not None:  # 비우면 Ollama 자동 배치
            options["num_gpu"] = self._num_gpu
        body: dict[str, Any] = {"model": self._model, "messages": messages, "stream": False, "options": options}
        if json_mode and self._json_enabled:
            body["format"] = "json"
        if self._keep_alive:
            body["keep_alive"] = _keep_alive_value(self._keep_alive)
        return body

    def _post(self, path: str, body: dict, timeout: float) -> httpx.Response:
        try:
            with httpx.Client(timeout=timeout, follow_redirects=False, trust_env=False) as http:
                return http.post(f"{self._base}{path}", json=body)
        except httpx.HTTPError as exc:
            raise _as_ollama(_transport_error(exc)) from None

    @staticmethod
    def _text(response: httpx.Response) -> str:
        try:
            raw = response.text[:_BODY_MAX]
        except Exception:  # noqa: BLE001
            return ""
        try:
            return json.dumps(json.loads(raw), ensure_ascii=False)
        except ValueError:
            return raw

    def _detail(self, response: httpx.Response) -> str:
        return mask_detail(self._text(response), base_url=self._base)

    def complete(self, messages: list[dict[str, str]], json_mode: bool, timeout: float) -> str:
        try:
            check_base_url(self._base, self._allow_external)
        except LocalLlmError as exc:
            raise _as_ollama(exc) from None
        response = self._post("/api/chat", self.build_body(messages, json_mode), timeout)
        if response.status_code != 200:
            text = self._text(response)
            if response.status_code == 404 and "not found" in text.lower():
                raise OllamaError("Ollama 에 설정한 모델이 없습니다(404).", kind=PERMANENT, reason="model_missing", detail=self._detail(response))
            category = classify_bad_request(text) if response.status_code == 400 else ""
            raise _as_ollama(_status_error(response.status_code, self._detail(response), category if category == BAD_CONTEXT else ""))
        try:
            data = response.json()
            content = data["message"]["content"]
        except (ValueError, KeyError, TypeError):
            raise OllamaError("Ollama 응답 형식이 올바르지 않습니다.", kind=PERMANENT, reason="format") from None
        if not isinstance(content, str):
            raise OllamaError("Ollama 응답 형식이 올바르지 않습니다.", kind=PERMANENT, reason="format")
        self._check_truncation(data)
        return content

    def _check_truncation(self, data: dict) -> None:
        """done_reason=length 또는 생성 토큰(eval_count)이 num_predict 에 도달하면 출력 잘림, 프롬프트 토큰이 num_ctx×비율 이상이면 맥락 한계."""
        evaluated = data.get("eval_count") if isinstance(data.get("eval_count"), int) else None
        prompt_tokens = data.get("prompt_eval_count") if isinstance(data.get("prompt_eval_count"), int) else None
        info = f"done_reason={data.get('done_reason')}, eval_count={evaluated}, prompt_eval_count={prompt_tokens}, num_ctx={self._num_ctx}, num_predict={self._num_predict}"
        if data.get("done_reason") == "length" or (evaluated is not None and evaluated >= self._num_predict):
            raise OllamaError("Ollama 응답이 출력 길이 한도에서 잘렸습니다.", kind=PERMANENT, reason="output_truncated", detail=info)
        if prompt_tokens is not None and prompt_tokens >= self._num_ctx * self._ratio:
            raise OllamaError(
                "사용 토큰이 모델 맥락 길이의 한계에 도달해 앞부분이 잘렸을 수 있습니다.", kind=PERMANENT, reason="context_limit_reached", detail=info,
            )

    def unload(self, timeout: float = 30.0) -> bool:
        """모델을 VRAM 에서 내린다(keep_alive=0). 실패해도 예외를 올리지 않는다(추출 결과·원래 오류를 가리지 않는다)."""
        try:
            response = self._post("/api/chat", {"model": self._model, "keep_alive": 0}, timeout)
            return response.status_code == 200
        except Exception as exc:  # noqa: BLE001
            log.warning("Ollama 모델 언로드 실패(무시): %s", type(exc).__name__)
            return False


def list_ollama_models(base_url: str, timeout: float, allow_external: bool) -> list[str]:
    """GET {base_url}/api/tags 로 설치된 모델 이름 목록을 읽는다(프롬프트·회의 내용을 보내지 않는다)."""
    try:
        check_base_url(base_url, allow_external)
    except LocalLlmError as exc:
        raise _as_ollama(exc) from None
    try:
        with httpx.Client(timeout=timeout, follow_redirects=False, trust_env=False) as http:
            response = http.get(f"{base_url.rstrip('/')}/api/tags")
    except httpx.HTTPError as exc:
        raise _as_ollama(_transport_error(exc)) from None
    if response.status_code != 200:
        raise _as_ollama(_status_error(response.status_code))
    try:
        return [str(m["name"]) for m in response.json()["models"]]
    except (ValueError, KeyError, TypeError):
        raise OllamaError("Ollama 응답 형식이 올바르지 않습니다.", kind=PERMANENT, reason="format") from None


def model_installed(model: str, installed: list[str]) -> bool:
    """이름이 정확히 같거나, 태그를 생략한 이름이 :latest 와 같으면 설치된 것으로 본다."""
    wanted = model.strip()
    names = set(installed)
    return wanted in names or (":" not in wanted and f"{wanted}:latest" in names)


def preflight(base_url: str, model: str, timeout: float, allow_external: bool) -> None:
    """서버 연결과 모델 존재 확인. 문제는 OllamaError(ERR-LLM-NO-SERVER / NO-MODEL ...)."""
    installed = list_ollama_models(base_url, timeout, allow_external)
    if not model_installed(model, installed):
        raise OllamaError("Ollama 에 설정한 모델이 설치되어 있지 않습니다.", kind=PERMANENT, reason="model_missing")


class OllamaExtractor(LocalLlmExtractor):
    """추출 흐름은 로컬 엔진과 같다. 시작 전 점검(tags), 종료 때 모델 언로드를 더한다."""

    def __init__(
        self, client: OllamaClient, model: str, meeting_type: str = "", *, options: Any = None,
        unload_after: bool = True, preflight_fn=None,
    ):
        super().__init__(client, model, meeting_type, options=options, label=f"Ollama({model})")
        self._ollama = client
        self._unload_after = unload_after
        self._preflight = preflight_fn

    def extract(self, transcript: str, held_at: datetime) -> ExtractionResult:
        try:
            if self._preflight is not None:
                self._preflight()
            return super().extract(transcript, held_at)
        except LocalLlmError as exc:
            raise _as_ollama(exc) from None
        finally:
            if self._unload_after:
                self._ollama.unload()  # 성공·예외 모두. 끝난 뒤에야 반환 → 다음 작업(워커 직렬)이 시작된다
