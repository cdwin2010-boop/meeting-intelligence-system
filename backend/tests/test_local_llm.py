"""로컬 LLM(OpenAI 호환) 클라이언트·연결 보호·확인 도구(작업 81-1). 127.0.0.1 임시 포트의 가짜 서버만 쓴다(외부 네트워크 없음)."""
import importlib.util
import io
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.config import Settings, settings
from app.pipeline import local_llm
from app.pipeline.errors import error_code
from app.pipeline.llm_client import LlmClientError
from app.pipeline.local_llm import (
    PERMANENT, TRANSIENT, LocalLlmError, OpenAICompatClient, classify_host, external_host_warning, mask_host,
)

ROOT = Path(__file__).resolve().parent.parent
MESSAGES = [{"role": "user", "content": "비밀 전사 본문 XYZ"}]
SECRET_KEY = "sk-test-secret-value-123"


class FakeServer:
    """요청을 기록하고 스크립트된 (상태, 본문, 지연) 목록을 차례로 돌려주는 가짜 서버."""

    def __init__(self, script):
        self.script = list(script)
        self.requests: list[dict] = []
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):  # 조용히
                pass

            def _answer(self):
                length = int(self.headers.get("Content-Length") or 0)
                raw = self.rfile.read(length) if length else b""
                outer.requests.append({
                    "method": self.command, "path": self.path, "headers": {k.lower(): v for k, v in self.headers.items()},
                    "body": json.loads(raw) if raw else None,
                })
                status, body, delay = outer.script.pop(0) if outer.script else (200, ok_body("기본"), 0)
                if delay:
                    time.sleep(delay)
                payload = body if isinstance(body, bytes) else json.dumps(body).encode("utf-8")
                try:
                    self.send_response(status)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(payload)))
                    self.end_headers()
                    self.wfile.write(payload)
                except OSError:
                    pass

            do_POST = _answer
            do_GET = _answer

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.httpd.server_address[1]}/v1"

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()


def ok_body(text: str) -> dict:
    return {"choices": [{"message": {"role": "assistant", "content": text}}]}


@pytest.fixture
def server():
    created: list[FakeServer] = []

    def make(script):
        srv = FakeServer(script)
        created.append(srv)
        return srv

    yield make
    for srv in created:
        srv.close()


def client_for(srv, **kw) -> OpenAICompatClient:
    return OpenAICompatClient(srv.base_url, kw.pop("model", "test-model.gguf"), **kw)


# ---------------- 정상·옵션 ----------------
def test_success_request_shape(server):
    srv = server([(200, ok_body('{"items": []}'), 0)])
    out = client_for(srv, max_output_tokens=777).complete(MESSAGES, json_mode=True, timeout=5)
    assert out == '{"items": []}'
    req = srv.requests[0]
    assert req["method"] == "POST" and req["path"] == "/v1/chat/completions"
    assert req["body"]["model"] == "test-model.gguf" and req["body"]["temperature"] == 0.1
    assert req["body"]["max_tokens"] == 777 and req["body"]["messages"] == MESSAGES
    assert req["body"]["response_format"] == {"type": "json_object"}


def test_json_mode_off_omits_response_format(server):
    srv = server([(200, ok_body("x"), 0)])
    client_for(srv, json_mode_enabled=False).complete(MESSAGES, json_mode=True, timeout=5)
    assert "response_format" not in srv.requests[0]["body"]
    srv2 = server([(200, ok_body("x"), 0)])
    client_for(srv2).complete(MESSAGES, json_mode=False, timeout=5)
    assert "response_format" not in srv2.requests[0]["body"]


def test_json_mode_rejected_retries_once_without_response_format(server):
    srv = server([(400, {"error": "response_format unsupported"}, 0), (200, ok_body("ok"), 0), (200, ok_body("ok2"), 0)])
    client = client_for(srv)
    assert client.complete(MESSAGES, json_mode=True, timeout=5) == "ok"
    assert len(srv.requests) == 2
    assert "response_format" in srv.requests[0]["body"] and "response_format" not in srv.requests[1]["body"]
    # 거부를 기억해 다음 호출은 처음부터 생략한다(요청 1회)
    assert client.complete(MESSAGES, json_mode=True, timeout=5) == "ok2"
    assert len(srv.requests) == 3 and "response_format" not in srv.requests[2]["body"]


def test_double_400_is_permanent_and_capped_at_two_requests(server):
    srv = server([(400, {}, 0), (400, {}, 0), (200, ok_body("never"), 0)])
    with pytest.raises(LocalLlmError) as info:
        client_for(srv).complete(MESSAGES, json_mode=True, timeout=5)
    assert info.value.kind == PERMANENT and len(srv.requests) == 2


# ---------------- 오류 분류 ----------------
@pytest.mark.parametrize("status, kind", [(500, TRANSIENT), (503, TRANSIENT), (429, TRANSIENT), (401, PERMANENT),
                                           (403, PERMANENT), (404, PERMANENT), (422, PERMANENT)])
def test_status_classification(server, status, kind):
    srv = server([(status, {"error": "x"}, 0)])
    with pytest.raises(LocalLlmError) as info:
        client_for(srv).complete(MESSAGES, json_mode=False, timeout=5)
    assert info.value.kind == kind and isinstance(info.value, LlmClientError)
    assert len(srv.requests) == 1  # 400 외에는 재요청 없음


def test_timeout_is_transient_with_timeout_code(server):
    srv = server([(200, ok_body("late"), 1.5)])
    with pytest.raises(LocalLlmError) as info:
        client_for(srv).complete(MESSAGES, json_mode=False, timeout=0.3)
    assert info.value.kind == TRANSIENT and info.value.reason == "timeout" and error_code(info.value) == "timeout"


def test_connection_refused_is_transient():
    # 닫힌 포트: 서버를 띄웠다가 닫아 포트를 얻는다
    srv = FakeServer([])
    url = srv.base_url
    srv.close()
    with pytest.raises(LocalLlmError) as info:
        OpenAICompatClient(url, "m").complete(MESSAGES, json_mode=False, timeout=15)
    assert info.value.kind == TRANSIENT and info.value.reason == "connect" and error_code(info.value) == "llm_unavailable"


@pytest.mark.parametrize("body", [b"not json", json.dumps({"choices": []}).encode(), json.dumps({"choices": [{"message": {}}]}).encode(),
                                  json.dumps({"choices": [{"message": {"content": 5}}]}).encode(), b"[]"])
def test_bad_response_shape_is_permanent(server, body):
    srv = server([(200, body, 0)])
    with pytest.raises(LocalLlmError) as info:
        client_for(srv).complete(MESSAGES, json_mode=False, timeout=5)
    assert info.value.kind == PERMANENT and info.value.reason == "format" and error_code(info.value) == "llm_error"


def test_no_redirect_following(server):
    # 3xx 는 따라가지 않고 영구 오류(4xx 외 상태)로 본다
    srv = server([(302, {}, 0)])
    with pytest.raises(LocalLlmError):
        client_for(srv).complete(MESSAGES, json_mode=False, timeout=5)
    assert len(srv.requests) == 1


# ---------------- 인증 헤더 ----------------
@pytest.mark.parametrize("key, sent", [("not-needed", False), ("", False), ("  ", False), (SECRET_KEY, True)])
def test_authorization_header(server, key, sent):
    srv = server([(200, ok_body("x"), 0)])
    client_for(srv, api_key=key).complete(MESSAGES, json_mode=False, timeout=5)
    header = srv.requests[0]["headers"].get("authorization")
    assert (header == f"Bearer {SECRET_KEY}") if sent else header is None


# ---------------- 메시지에 주소·키·본문 없음 ----------------
def test_error_messages_do_not_leak_address_key_or_body(server):
    srv = server([(503, {"error": "비밀 전사 본문 XYZ"}, 0)])
    with pytest.raises(LocalLlmError) as info:
        client_for(srv, api_key=SECRET_KEY).complete(MESSAGES, json_mode=False, timeout=5)
    text = str(info.value) + repr(info.value)
    assert srv.base_url not in text and "127.0.0.1" not in text and SECRET_KEY not in text and "XYZ" not in text
    blocked = OpenAICompatClient("https://llm.example.com/v1", "m", api_key=SECRET_KEY)
    with pytest.raises(LocalLlmError) as info2:
        blocked.complete(MESSAGES, json_mode=False, timeout=1)
    text2 = str(info2.value)
    assert "example.com" not in text2 and SECRET_KEY not in text2 and "XYZ" not in text2
    assert info2.value.kind == PERMANENT and info2.value.reason == "host_blocked"
    assert error_code(info2.value) == "llm_host_blocked" and "허용 설정" in text2


# ---------------- 호스트 판정 ----------------
@pytest.mark.parametrize(
    "host, expected",
    [
        ("localhost", "loopback"), ("127.0.0.1", "loopback"), ("127.5.6.7", "loopback"), ("::1", "loopback"), ("[::1]", "loopback"),
        ("10.1.2.3", "private"), ("172.16.0.1", "private"), ("172.31.255.255", "private"), ("192.168.0.10", "private"),
        ("169.254.1.1", "private"), ("fd00::1", "private"), ("fe80::1", "private"), ("gpu-server.local", "private"),
        ("gpuserver", "private"),
        ("172.32.0.1", "external"), ("8.8.8.8", "external"), ("11.0.0.1", "external"), ("llm.example.com", "external"),
        ("abc.trycloudflare.com", "external"), ("2001:db8::1", "external"),
        ("", "invalid"), (None, "invalid"), ("a b", "invalid"),
    ],
)
def test_host_classification_table(host, expected):
    assert classify_host(host) == expected


def test_external_allowed_by_setting(server):
    assert local_llm.check_base_url("http://127.0.0.1:1/v1", False) is None
    with pytest.raises(LocalLlmError):
        local_llm.check_base_url("https://llm.example.com/v1", False)
    assert local_llm.check_base_url("https://llm.example.com/v1", True) is None
    for bad in ("", "ftp://host/v1", "not a url", "//host/v1"):
        with pytest.raises(LocalLlmError) as info:
            local_llm.check_base_url(bad, True)
        assert info.value.reason == "config"


def test_external_warning_masks_host(caplog):
    message = external_host_warning("https://secret-tunnel-abc.example.com/v1", True)
    assert message and "secret-tunnel-abc" not in message and "sec***" in message
    assert external_host_warning("https://secret-tunnel-abc.example.com/v1", False) is None
    assert external_host_warning("http://127.0.0.1:8080/v1", True) is None
    with caplog.at_level("WARNING", logger="app.local_llm"):
        local_llm.log_startup_warning("local", "https://secret-tunnel-abc.example.com/v1", True)
        local_llm.log_startup_warning("gemini", "https://secret-tunnel-abc.example.com/v1", True)
    assert len(caplog.records) == 1 and "secret-tunnel" not in caplog.text


@pytest.mark.parametrize("host, masked", [("192.168.12.34", "192.168.*.*"), ("tunnel.example.com", "tun***.com"), ("gpu", "***")])
def test_mask_host(host, masked):
    assert mask_host(host) == masked


# ---------------- 확인 도구 ----------------
def _tool():
    spec = importlib.util.spec_from_file_location("check_llm_tool", ROOT / "scripts" / "check_llm.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_check_tool_calls_only_models_and_masks_output(server, monkeypatch):
    srv = server([(200, {"data": [{"id": "test-model.gguf"}, {"id": "other"}]}, 0)])
    monkeypatch.setattr(settings, "local_llm_base_url", srv.base_url)
    monkeypatch.setattr(settings, "local_llm_model", "test-model.gguf")
    out = io.StringIO()
    assert _tool().main(out) == 0
    text = out.getvalue()
    assert [r["method"] + " " + r["path"] for r in srv.requests] == ["GET /v1/models"]
    assert srv.requests[0]["body"] is None  # 프롬프트·내용 전송 없음
    assert srv.base_url not in text and "127.0.0.1" not in text and "127.0.*.*" in text
    assert "설정 모델 존재: 예" in text


def test_check_tool_reports_error_class_missing_model_and_external(server, monkeypatch):
    tool = _tool()
    srv = server([(401, {}, 0)])
    monkeypatch.setattr(settings, "local_llm_base_url", srv.base_url)
    monkeypatch.setattr(settings, "local_llm_model", "m")
    out = io.StringIO()
    assert tool.main(out) == 1 and "영구 오류" in out.getvalue() and "http_4xx" in out.getvalue()
    srv2 = server([(200, {"data": [{"id": "other"}]}, 0)])
    monkeypatch.setattr(settings, "local_llm_base_url", srv2.base_url)
    out = io.StringIO()
    assert tool.main(out) == 1 and "서버 모델 목록에 없음" in out.getvalue()
    monkeypatch.setattr(settings, "local_llm_base_url", "https://llm.example.com/v1")
    out = io.StringIO()
    assert tool.main(out) == 1 and "host_blocked" in out.getvalue() and "example.com" not in out.getvalue().replace("ex***", "")
    monkeypatch.setattr(settings, "local_llm_base_url", "")
    assert tool.main(io.StringIO()) == 2


# ---------------- 설정 ----------------
def test_settings_defaults_keep_current_behavior():
    s = Settings(_env_file=None)
    assert s.llm_provider == "auto" and s.effective_llm_provider == "fake"
    assert Settings(_env_file=None, stt_provider="gemini").effective_llm_provider == "gemini"
    assert s.local_llm_api_key.get_secret_value() == "not-needed" and s.local_llm_json_mode is True
    assert s.local_llm_allow_external_host is False and s.local_llm_max_output_tokens == 4096
    assert s.local_llm_base_url == "" and s.local_llm_model == ""


@pytest.mark.parametrize(
    "kwargs",
    [
        {"local_llm_base_url": "ftp://host/v1"}, {"local_llm_base_url": "not a url"}, {"local_llm_base_url": "http://"},
        {"local_llm_base_url": "http://host:99999/v1"}, {"local_llm_max_output_tokens": 0}, {"local_llm_max_output_tokens": -5},
        {"llm_provider": "local"},  # 주소·모델 없음
        {"llm_provider": "local", "local_llm_base_url": "http://127.0.0.1:8080/v1"},  # 모델 없음
        {"llm_provider": "other"},
    ],
)
def test_settings_validation(kwargs):
    with pytest.raises(ValidationError):
        Settings(_env_file=None, **kwargs)


def test_settings_local_ok_and_trailing_slash_trimmed():
    s = Settings(_env_file=None, llm_provider="local", local_llm_base_url="http://127.0.0.1:8080/v1/", local_llm_model="m")
    assert s.effective_llm_provider == "local" and s.local_llm_base_url == "http://127.0.0.1:8080/v1"
