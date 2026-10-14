"""작업 L-2: Ollama 어댑터. 실제 Ollama 없이 httpx MockTransport·가짜 클라이언트를 주입한다."""
import json
from datetime import datetime, timezone

import httpx
import pytest
from pydantic import ValidationError

from app.config import Settings
from app.pipeline import ollama_llm
from app.pipeline.errors import error_code
from app.pipeline.ollama_llm import (
    ERR_FAILED, ERR_NO_MODEL, ERR_NO_SERVER, ERR_TIMEOUT, ERR_TRUNCATED, OllamaClient, OllamaError, OllamaExtractor,
    list_ollama_models, model_installed, preflight,
)

MSGS = [{"role": "user", "content": "안녕"}]
_REAL_CLIENT = httpx.Client  # install() 를 여러 번 불러도 진짜 클래스를 기준으로 한다


def install(monkeypatch, handler):
    """ollama_llm 이 만드는 httpx.Client 를 MockTransport 로 바꾸고, 보낸 요청을 기록한다."""
    sent: list[httpx.Request] = []
    real = _REAL_CLIENT

    def recording(request: httpx.Request) -> httpx.Response:
        sent.append(request)
        return handler(request)

    monkeypatch.setattr(ollama_llm.httpx, "Client", lambda **kw: real(transport=httpx.MockTransport(recording), **{k: v for k, v in kw.items() if k in ("timeout",)}))
    return sent


def chat_ok(content="{}", **extra):
    body = {"message": {"role": "assistant", "content": content}, "done": True, "done_reason": "stop", "eval_count": 10, "prompt_eval_count": 100}
    body.update(extra)
    return httpx.Response(200, json=body)


def client(**kw) -> OllamaClient:
    base = dict(num_ctx=8192, num_predict=2048, keep_alive="5m")
    base.update(kw)
    return OllamaClient("http://127.0.0.1:11434", "m1", **base)


def test_request_body_has_num_ctx_format_model_and_no_num_gpu(monkeypatch):
    sent = install(monkeypatch, lambda r: chat_ok("{\"items\": []}"))
    assert client().complete(MSGS, True, 5) == "{\"items\": []}"
    assert sent[0].url.path == "/api/chat"
    body = json.loads(sent[0].content)
    assert body["model"] == "m1" and body["stream"] is False and body["format"] == "json"
    assert body["options"]["num_ctx"] == 8192 and body["options"]["num_predict"] == 2048
    assert "num_gpu" not in body["options"] and body["keep_alive"] == "5m"
    assert body["messages"] == MSGS and "temperature" in body["options"]


def test_num_gpu_sent_only_when_set_and_no_format_when_json_off(monkeypatch):
    sent = install(monkeypatch, lambda r: chat_ok())
    client(num_gpu=20).complete(MSGS, False, 5)
    body = json.loads(sent[0].content)
    assert body["options"]["num_gpu"] == 20 and "format" not in body
    client(num_gpu=0).complete(MSGS, True, 5)
    assert json.loads(sent[1].content)["options"]["num_gpu"] == 0  # 0 도 지정값


@pytest.mark.parametrize(
    "extra", [{"done_reason": "length"}, {"eval_count": 2048}, {"prompt_eval_count": 8100}],
)
def test_truncation_detected(monkeypatch, extra):
    install(monkeypatch, lambda r: chat_ok(**extra))
    with pytest.raises(OllamaError) as ei:
        client().complete(MSGS, True, 5)
    assert ei.value.job_error_code == ERR_TRUNCATED and error_code(ei.value) == ERR_TRUNCATED


def test_model_missing_server_missing_timeout_failed_codes(monkeypatch):
    install(monkeypatch, lambda r: httpx.Response(404, json={"error": "model 'm1' not found"}))
    with pytest.raises(OllamaError) as ei:
        client().complete(MSGS, True, 5)
    assert ei.value.job_error_code == ERR_NO_MODEL

    def refuse(r):
        raise httpx.ConnectError("refused http://127.0.0.1:11434")

    install(monkeypatch, refuse)
    with pytest.raises(OllamaError) as ei:
        client().complete(MSGS, True, 5)
    assert ei.value.job_error_code == ERR_NO_SERVER
    assert "127.0.0.1" not in str(ei.value) and "11434" not in str(ei.value)

    def slow(r):
        raise httpx.ReadTimeout("t")

    install(monkeypatch, slow)
    with pytest.raises(OllamaError) as ei:
        client().complete(MSGS, True, 5)
    assert ei.value.job_error_code == ERR_TIMEOUT

    install(monkeypatch, lambda r: httpx.Response(500, text="boom 10.1.2.3"))
    with pytest.raises(OllamaError) as ei:
        client().complete(MSGS, True, 5)
    assert ei.value.job_error_code == ERR_FAILED and "10.1.2.3" not in ei.value.detail  # 본문 마스킹


def test_bad_response_shape_is_failed(monkeypatch):
    install(monkeypatch, lambda r: httpx.Response(200, json={"nope": 1}))
    with pytest.raises(OllamaError) as ei:
        client().complete(MSGS, True, 5)
    assert ei.value.job_error_code == ERR_FAILED


def test_external_host_denied_by_default_and_allowed_by_setting(monkeypatch):
    sent = install(monkeypatch, lambda r: chat_ok())
    ext = OllamaClient("http://ollama.example.com:11434", "m1", num_ctx=8192, num_predict=1024)
    with pytest.raises(OllamaError) as ei:
        ext.complete(MSGS, True, 5)
    assert ei.value.reason == "host_blocked" and ei.value.job_error_code == ERR_FAILED and not sent
    ok = OllamaClient("http://ollama.example.com:11434", "m1", num_ctx=8192, num_predict=1024, allow_external=True)
    assert ok.complete(MSGS, True, 5) == "{}"
    private = OllamaClient("http://192.168.0.5:11434", "m1", num_ctx=8192, num_predict=1024)
    assert private.complete(MSGS, True, 5) == "{}"  # 사설망은 기본 허용


def test_tags_preflight(monkeypatch):
    sent = install(monkeypatch, lambda r: httpx.Response(200, json={"models": [{"name": "qwen:7b"}, {"name": "llama3:latest"}]}))
    assert list_ollama_models("http://127.0.0.1:11434", 5, False) == ["qwen:7b", "llama3:latest"]
    assert sent[0].url.path == "/api/tags" and sent[0].method == "GET"
    assert model_installed("llama3", ["llama3:latest"]) and not model_installed("qwen", ["qwen:7b"])
    preflight("http://127.0.0.1:11434", "qwen:7b", 5, False)
    with pytest.raises(OllamaError) as ei:
        preflight("http://127.0.0.1:11434", "other", 5, False)
    assert ei.value.job_error_code == ERR_NO_MODEL


def test_unload_sends_keep_alive_zero_and_never_raises(monkeypatch):
    sent = install(monkeypatch, lambda r: httpx.Response(200, json={"done": True}))
    assert client().unload() is True
    assert json.loads(sent[0].content) == {"model": "m1", "keep_alive": 0}

    def down(r):
        raise httpx.ConnectError("x")

    install(monkeypatch, down)
    assert client().unload() is False


class StubClient:
    """OllamaClient 자리: 호출 순서를 events 에 남긴다."""

    def __init__(self, events, fail=None):
        self.events, self.fail = events, fail

    def complete(self, messages, json_mode, timeout):
        self.events.append("complete")
        if self.fail:
            raise self.fail
        return json.dumps({"items": [], "minutes": {}}, ensure_ascii=False)

    def unload(self, timeout=30.0):
        self.events.append("unload")
        return True


HELD = datetime(2026, 10, 14, 1, 0, tzinfo=timezone.utc)


def test_unload_after_success_and_after_exception():
    ev: list[str] = []
    ex = OllamaExtractor(StubClient(ev), "m1", unload_after=True, preflight_fn=lambda: ev.append("preflight"))
    ex.extract("[00:00:01] 화자1: 안녕하세요", HELD)
    assert ev[0] == "preflight" and ev[-1] == "unload" and "complete" in ev  # 언로드가 마지막(반환 전)

    ev2: list[str] = []
    err = OllamaError("x", kind="permanent", reason="timeout")
    ex2 = OllamaExtractor(StubClient(ev2, fail=err), "m1", unload_after=True)
    with pytest.raises(OllamaError):
        ex2.extract("[00:00:01] 화자1: 안녕하세요", HELD)
    assert ev2[-1] == "unload"


def test_unload_disabled_and_preflight_failure_still_unloads_when_enabled():
    ev: list[str] = []
    OllamaExtractor(StubClient(ev), "m1", unload_after=False).extract("[00:00:01] 화자1: 가", HELD)
    assert "unload" not in ev

    def no_model():
        raise OllamaError("x", kind="permanent", reason="model_missing")

    ev2: list[str] = []
    with pytest.raises(OllamaError) as ei:
        OllamaExtractor(StubClient(ev2), "m1", unload_after=True, preflight_fn=no_model).extract("[00:00:01] 화자1: 가", HELD)
    assert ei.value.job_error_code == ERR_NO_MODEL and "complete" not in ev2


def test_settings_defaults_and_validation():
    s = Settings(_env_file=None)
    assert s.llm_provider == "auto" and s.effective_llm_provider == "fake"  # 기본 불변
    assert (s.ollama_base_url, s.ollama_num_ctx, s.ollama_num_gpu_value, s.ollama_unload_after_extract) == ("http://127.0.0.1:11434", 8192, None, True)
    for bad in ({"ollama_num_gpu": "abc"}, {"ollama_num_gpu": "-1"}, {"ollama_keep_alive": "soon"}, {"ollama_num_ctx": 100},
                {"ollama_num_predict": 9000}, {"llm_provider": "ollama"}, {"llm_provider": "ollama", "ollama_model": "m", "ollama_base_url": "ftp://x"}):
        with pytest.raises(ValidationError):
            Settings(_env_file=None, **bad)
    ok = Settings(_env_file=None, llm_provider="ollama", ollama_model="m", ollama_base_url=" http://127.0.0.1:11434/ ", ollama_num_gpu="20")
    assert ok.ollama_base_url == "http://127.0.0.1:11434" and ok.ollama_num_gpu_value == 20


def test_extraction_engine_selects_ollama(monkeypatch):
    from app.config import settings as live
    from app.services.extraction_engine import select_extractor

    monkeypatch.setattr(live, "llm_provider", "ollama")
    monkeypatch.setattr(live, "ollama_model", "m1")
    monkeypatch.setattr(live, "external_policy_enforced", False)
    monkeypatch.setattr("app.services.extraction_engine.meeting_type_of", lambda f, m: "")
    ex = select_extractor(None, 1, None, lambda: pytest.fail("default 를 쓰면 안 된다"))  # type: ignore[arg-type]
    assert isinstance(ex, OllamaExtractor)
