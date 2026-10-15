"""L-4a: 엔진 연결 진단·모델 이름 비교. 127.0.0.1 임시 포트의 가짜 서버만 쓴다(외부 접속 없음)."""
import io
import json
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from app.config import settings
from app.services import bench_runner, engine_diag, engine_test
from tests.test_upload_processing import add_account, auth, env  # noqa: F401  (env 는 픽스처)

SECRET = "sk-diag-secret-value-4242"
MODEL = "qwen2.5:7b"
LATEST = "Qwen2.5-7B-Instruct-Q4_K_M.gguf:latest"


class Fake:
    """GET /v1/models 에 설정한 상태·본문으로 응답하는 가짜 서버. mode='hang' 이면 연결만 받고 응답하지 않는다."""

    def __init__(self, status=200, body=None, content_type="application/json", mode="http"):
        self.status = status
        self.body = body if body is not None else {"data": [{"id": MODEL}, {"id": LATEST}]}
        self.content_type = content_type
        self.mode = mode
        self.auth_seen: list[str] = []
        outer = self
        if mode == "hang":
            self.sock = socket.socket()
            self.sock.bind(("127.0.0.1", 0))
            self.sock.listen(5)
            self.held: list[socket.socket] = []
            self.port = self.sock.getsockname()[1]

            def accept_loop():
                while True:
                    try:
                        conn, _ = self.sock.accept()
                    except OSError:
                        return
                    self.held.append(conn)

            threading.Thread(target=accept_loop, daemon=True).start()
            return

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):  # noqa: N802
                outer.auth_seen.append(self.headers.get("Authorization", ""))
                raw = outer.body if isinstance(outer.body, (bytes, str)) else json.dumps(outer.body, ensure_ascii=False)
                payload = raw if isinstance(raw, bytes) else raw.encode("utf-8")
                self.send_response(outer.status)
                self.send_header("Content-Type", outer.content_type)
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.port = self.httpd.server_address[1]
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.port}/v1"

    def close(self):
        if self.mode == "hang":
            self.sock.close()
            for c in self.held:
                c.close()
        else:
            self.httpd.shutdown()
            self.httpd.server_close()


@pytest.fixture
def fake():
    made: list[Fake] = []

    def make(**kw) -> Fake:
        f = Fake(**kw)
        made.append(f)
        return f

    yield make
    for f in made:
        f.close()


def diag(f: Fake, model=MODEL, key=SECRET):
    return engine_diag.diagnose(f.base_url, key, model, allow_external=True)


# ---------------- 분류 ----------------
def test_ok(fake):
    f = fake()
    d = diag(f)
    assert d.category == "OK" and d.http_status == 200 and d.candidates == []
    assert f.auth_seen == [f"Bearer {SECRET}"]


def test_unreachable_connection_refused():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()  # 닫힌 포트: 연결 거부
    d = engine_diag.diagnose(f"http://127.0.0.1:{port}/v1", SECRET, MODEL, allow_external=True)
    assert d.category == "UNREACHABLE" and d.http_status is None and d.elapsed_ms < 3500


@pytest.mark.parametrize("status,body,ctype", [
    (404, {"error": "no"}, "application/json"),
    (405, "", "text/plain"),
    (302, "", "text/plain"),
    (200, "<html><body>안내 페이지</body></html>", "text/html"),
    (200, {"models": []}, "application/json"),
])
def test_path_mismatch(fake, status, body, ctype):
    assert diag(fake(status=status, body=body, content_type=ctype)).category == "PATH_MISMATCH"


@pytest.mark.parametrize("status", [401, 403])
def test_auth_failed(fake, status):
    d = diag(fake(status=status, body={"error": "x"}))
    assert d.category == "AUTH_FAILED" and d.http_status == status


@pytest.mark.parametrize("status", [500, 502, 503, 504])
def test_server_error(fake, status):
    d = diag(fake(status=status, body={"error": "x"}))
    assert d.category == "SERVER_ERROR" and d.http_status == status


def test_model_missing_with_candidates(fake):
    d = diag(fake(), model="qwen2.5:14b")
    assert d.category == "MODEL_MISSING" and d.candidates and d.candidates[0] == "qwen2.5:7b"
    assert len(d.candidates) <= 3


def test_hang_after_connect_ends_within_time_limit(fake):
    f = fake(mode="hang")
    started = time.monotonic()
    d = diag(f)
    assert time.monotonic() - started < 5.5
    assert d.category == "SERVER_ERROR" and d.http_status is None


def test_host_blocked_is_reported_without_calling():
    d = engine_diag.diagnose("http://example.invalid/v1", SECRET, MODEL, allow_external=False)
    assert d.category == "UNREACHABLE" and "허용" in d.hint


def test_result_never_contains_address_or_key(fake):
    for kw in ({}, {"status": 401}, {"status": 502}, {"status": 404}):
        f = fake(**kw)
        blob = json.dumps(diag(f).as_dict(), ensure_ascii=False)
        assert SECRET not in blob and str(f.port) not in blob and "127.0.0.1" not in blob


# ---------------- 모델 이름 비교 ----------------
@pytest.mark.parametrize("configured,listed,expected", [
    ("Qwen2.5-7B-Instruct-Q4_K_M.gguf", [LATEST, "qwen2.5:7b"], True),  # 현장 사례
    ("  qwen2.5:7b  ", ["qwen2.5:7b"], True),
    ("qwen2.5:7b:latest", ["qwen2.5:7b"], True),
    ("Qwen2.5:7b", ["qwen2.5:7b"], False),  # 대소문자는 구분
    ("qwen2.5", ["qwen2.5:7b"], False),
    ("", ["qwen2.5:7b"], False),
])
def test_model_in_list_rules(configured, listed, expected):
    assert engine_diag.model_in_list(configured, listed) is expected


def test_closest_models_suggests_without_changing():
    listed = ["llama3:8b", "qwen2.5:7b", LATEST]
    assert engine_diag.closest_models("Qwen2.5:7b", listed)[0] == "qwen2.5:7b"
    assert engine_diag.closest_models("x", []) == []


# ---------------- 명령줄 ----------------
def test_cli_exit_codes_and_no_secrets(fake, monkeypatch, tmp_path):
    import importlib.util
    import pathlib

    monkeypatch.setattr(settings, "engine_test_settings_path", tmp_path / "engine_test.json")
    monkeypatch.setattr(settings, "remote_llm_base_url", "")
    monkeypatch.setattr(settings, "remote_llm_model", "")
    path = pathlib.Path(__file__).resolve().parent.parent / "scripts" / "engine_check.py"
    spec = importlib.util.spec_from_file_location("engine_check", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    out = io.StringIO()
    assert mod.main(out) == 2  # 설정 없음

    good, bad = fake(), fake(status=401, body={"error": "x"})
    engine_test.save_remote_settings(base_url=good.base_url, model=MODEL, api_key=SECRET, timeout_seconds=30, chunk_max_chars=0)
    out = io.StringIO()
    assert mod.main(out) == 0
    text = out.getvalue()
    assert text.count("\n") == 1 and text.startswith("OK")
    engine_test.save_remote_settings(base_url=bad.base_url, model=MODEL, api_key=None, timeout_seconds=30, chunk_max_chars=0)
    out = io.StringIO()
    assert mod.main(out) == 1 and out.getvalue().startswith("AUTH_FAILED")
    for blob in (text, out.getvalue()):
        assert SECRET not in blob and "127.0.0.1" not in blob


# ---------------- API·측정 시작 전 점검 ----------------
PREFIX = "/api/admin/engine-test"


@pytest.fixture
def rig(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "engine_test_mode", True)
    monkeypatch.setattr(settings, "engine_test_settings_path", tmp_path / "engine_test.json")
    monkeypatch.setattr(settings, "bench_dir", tmp_path / "bench")
    monkeypatch.setattr(settings, "remote_llm_base_url", "")
    monkeypatch.setattr(settings, "remote_llm_model", "")
    yield
    bench_runner.stop()


@pytest.fixture
def mgr(env):
    return add_account(env["factory"], "고객사A", "mgr", "manager", name="박관리")


@pytest.fixture
def staff(env):
    return add_account(env["factory"], "고객사A", "stf", "staff", name="김담당")


def save(f: Fake, model=MODEL):
    engine_test.save_remote_settings(base_url=f.base_url, model=model, api_key=SECRET, timeout_seconds=30, chunk_max_chars=0)


def test_diagnose_api_permission_and_result(env, rig, mgr, staff, fake):
    save(fake())
    assert env["client"].post(f"{PREFIX}/remote/diagnose", headers=auth(staff)).status_code == 403
    body = env["client"].post(f"{PREFIX}/remote/diagnose", headers=auth(mgr)).json()
    assert body["category"] == "OK" and body["httpStatus"] == 200 and set(body) >= {"elapsedMs", "hint", "checkedAt", "candidates"}
    assert SECRET not in json.dumps(body) and "127.0.0.1" not in json.dumps(body)


def test_bench_start_is_blocked_when_diagnosis_is_not_ok(env, rig, mgr, fake):
    save(fake(status=502, body={"error": "x"}))
    res = env["client"].post(f"{PREFIX}/bench/start", headers=auth(mgr), json={"mode": "quick"})
    assert res.status_code == 409
    detail = res.json()["detail"]
    assert detail["category"] == "SERVER_ERROR" and "시작하지 않았습니다" in detail["message"]
    assert SECRET not in res.text and "127.0.0.1" not in res.text
    assert env["client"].get(f"{PREFIX}/bench/status", headers=auth(mgr)).json()["status"] == "idle"


def test_bench_start_blocked_for_missing_model_with_candidates(env, rig, mgr, fake):
    save(fake(), model="qwen2.5:14b")
    res = env["client"].post(f"{PREFIX}/bench/start", headers=auth(mgr), json={"mode": "quick"})
    assert res.status_code == 409
    detail = res.json()["detail"]
    assert detail["category"] == "MODEL_MISSING" and detail["candidates"][0] == "qwen2.5:7b"


def test_load_test_model_found_ignores_latest_suffix(env, rig, mgr, fake):
    save(fake(body={"data": [{"id": LATEST}]}), model="Qwen2.5-7B-Instruct-Q4_K_M.gguf")
    body = env["client"].post(f"{PREFIX}/remote/load-test", headers=auth(mgr)).json()
    assert body["modelListed"] is True and body["modelFound"] is True
