"""L-5: 벤치마크 변수 입력·범위 검증·프리셋·맥락 스윕·설정 선기록·연결 방식 분기·측정 품질. 127.0.0.1 임시 포트의 가짜 서버만 쓴다."""
import json
import re
import threading
import time
from datetime import timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from app.config import settings
from app.engine_lab import params as lab_params
from app.services import bench_data, bench_runner, engine_test
from tests.test_l3a_bench import MODEL, PREFIX, SECRET, _DUE, _LINE, add_account, auth, env, wait_done  # noqa: F401  (env 는 픽스처)

CONN_BASE = "/api/admin/engine-test"


class FakeServer:
    """OpenAI 호환(/v1) 과 Ollama 네이티브(/api) 를 함께 흉내 내는 가짜 서버. 받은 요청 본문을 기록한다."""

    def __init__(self, *, prompt_tokens: int = 400, ps_context: int | None = None, due_mode: str = "ok", drop_after: int | None = None):
        self.prompt_tokens = prompt_tokens
        self.ps_context = ps_context
        self.due_mode = due_mode
        self.drop_after = drop_after
        self.chat_bodies: list[dict] = []  # 측정 호출(messages 가 있는 요청)
        self.unloads = 0
        self.order: list[tuple[str, int | None]] = []  # ("chat", num_ctx) / ("unload", None)
        self.loaded = False
        self.chat_calls = 0
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def _send(self, status: int, body: dict) -> None:
                payload = json.dumps(body, ensure_ascii=False).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def do_GET(self):  # noqa: N802
                if self.path.endswith("/models"):
                    return self._send(200, {"data": [{"id": MODEL}]})
                if self.path == "/api/tags":
                    return self._send(200, {"models": [{"name": MODEL}]})
                if self.path == "/api/ps":
                    models = []
                    if outer.loaded:
                        entry = {"name": MODEL, "size": 5000, "size_vram": 4500, "expires_at": "2099-01-01T00:00:00Z"}
                        if outer.ps_context is not None:
                            entry["context_length"] = outer.ps_context
                        models.append(entry)
                    return self._send(200, {"models": models})
                return self._send(404, {"error": "not found"})

            def _items(self, text: str) -> list[dict]:
                items = []
                for line in text.splitlines():
                    m = _LINE.match(line)
                    if not m:
                        continue
                    due_day = _DUE[m["phrase"]](bench_data.HELD_AT.date())
                    due = due_day.isoformat()
                    if outer.due_mode == "blank":
                        due = ""
                    elif outer.due_mode == "bad":
                        due = "다음주 금요일"
                    elif outer.due_mode == "shift":
                        due = (due_day + timedelta(days=3)).isoformat()
                    quote = line.split("] ", 1)[1].split(": ", 1)[1]
                    items.append({"task": f"{m['team']} {m['noun']} {m['ref']} 준비", "assignee": m["person"], "due_date": due,
                                  "quote": {"speaker": "화자1", "timestamp": "", "text": quote, "line_id": m["n"]}})
                return items

            def do_POST(self):  # noqa: N802
                length = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(length) or b"{}")
                native = self.path == "/api/chat"
                if native and "messages" not in body:  # 모델 내리기
                    outer.unloads += 1
                    outer.loaded = False
                    outer.order.append(("unload", None))
                    return self._send(200, {"done": True})
                outer.chat_calls += 1
                if outer.drop_after is not None and outer.chat_calls > outer.drop_after:
                    self.connection.close()
                    return
                outer.chat_bodies.append({"path": self.path, "body": body})
                outer.order.append(("chat", (body.get("options") or {}).get("num_ctx")))
                if body.get("model") != MODEL:
                    return self._send(404, {"error": f"model '{body.get('model')}' not found"} if native else {"error": {"message": "model not found"}})
                outer.loaded = True
                prompt = body["messages"][-1]["content"]
                text = prompt.split("전사문:\n", 1)[1] if "전사문:\n" in prompt else prompt
                content = json.dumps({"minutes": {}, "items": self._items(text)}, ensure_ascii=False)
                if native:
                    return self._send(200, {"message": {"role": "assistant", "content": content}, "done_reason": "stop",
                                            "prompt_eval_count": outer.prompt_tokens, "eval_count": 120})
                self._send(200, {"choices": [{"message": {"role": "assistant", "content": content}, "finish_reason": "stop"}],
                                 "usage": {"prompt_tokens": outer.prompt_tokens, "completion_tokens": 120}})

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    @property
    def origin(self) -> str:
        return f"http://127.0.0.1:{self.httpd.server_address[1]}"

    def close(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()


@pytest.fixture
def rig(monkeypatch, tmp_path):
    servers: list[FakeServer] = []
    monkeypatch.setattr(settings, "engine_test_mode", True)
    monkeypatch.setattr(settings, "engine_test_settings_path", tmp_path / "engine_test.json")
    monkeypatch.setattr(settings, "bench_dir", tmp_path / "bench")
    monkeypatch.setattr(settings, "remote_llm_base_url", "")
    monkeypatch.setattr(settings, "remote_llm_model", "")
    monkeypatch.setattr(settings, "bench_retry_delay_seconds", 0)
    monkeypatch.setattr(settings, "bench_repeat", 2)
    monkeypatch.setattr(settings, "bench_drift_runs", 2)
    monkeypatch.setattr(settings, "extract_chunk_max_chars", 3000)
    monkeypatch.setattr(settings, "extract_chunk_target_tokens", 1500)
    monkeypatch.setattr(settings, "bench_item_timeout_seconds", 20)

    def make(conn: str = "openai", **kwargs) -> FakeServer:
        srv = FakeServer(**kwargs)
        base = srv.origin + ("/v1" if conn == "openai" else "")
        engine_test.save_remote_settings(base_url=base, model=MODEL, api_key=SECRET, timeout_seconds=30, chunk_max_chars=0, connection_type=conn)
        servers.append(srv)
        return srv

    yield make
    bench_runner.stop()
    thread = bench_runner._thread
    if thread is not None:
        thread.join(timeout=60)
    for srv in servers:
        srv.close()


@pytest.fixture
def mgr(env):
    return add_account(env["factory"], "고객사A", "mgr", "manager", name="박관리")


def start(env, account, **body):
    body.setdefault("mode", "quick")
    return env["client"].post(f"{PREFIX}/start", headers=auth(account), json=body)


def result_of(env, account) -> dict:
    res = env["client"].get(f"{PREFIX}/result", headers=auth(account))
    assert res.status_code == 200
    return res.json()


# ---------------- 범위·조합 검증 ----------------
@pytest.mark.parametrize("key", [p["key"] for p in lab_params.spec()["params"]])
def test_range_boundaries_accept_min_and_max_and_reject_outside(key):
    p = next(x for x in lab_params.spec()["params"] if x["key"] == key)
    for ok in (p["min"], p["max"]):
        _values, errors = lab_params.validate({key: ok})
        assert errors == [], (key, ok)
    for bad in (p["min"] - 1, p["max"] + 1):
        if p.get("autoValue") is not None and bad == p["autoValue"]:
            continue
        _values, errors = lab_params.validate({key: bad})
        assert [e.key for e in errors] == [key], (key, bad)
        assert str(int(p["min"])) in errors[0].message and str(int(p["max"])) in errors[0].message  # 허용 범위를 문장에 포함


def test_non_number_and_unknown_keys_are_rejected():
    _v, errors = lab_params.validate({"repeat": "많이", "nope": 1, "driftRuns": True})
    assert {e.key for e in errors} == {"repeat", "nope", "driftRuns"}


def test_chunk_over_cap_is_combination_error_with_cap_in_message():
    values, _ = lab_params.validate({"chunkChars": 12000})
    cap = lab_params.chunk_cap(values)
    errors = lab_params.check_combination(values)
    assert [e.key for e in errors] == ["chunkChars"] and str(cap) in errors[0].message
    values["chunkChars"] = cap
    assert lab_params.check_combination(values) == []


def test_api_rejects_out_of_range_and_combination_and_keeps_nothing_started(env, mgr, rig):
    rig()
    bad = start(env, mgr, values={"repeat": 99})
    assert bad.status_code == 422
    detail = bad.json()["detail"]
    assert detail["errors"][0]["key"] == "repeat" and "1~20" in detail["errors"][0]["message"]
    combo = start(env, mgr, values={"chunkChars": 12000})
    assert combo.status_code == 422 and combo.json()["detail"]["errors"][0]["key"] == "chunkChars"
    assert bench_runner.load_state() is None  # 오류가 있으면 측정을 시작하지 않는다
    assert env["client"].get(f"{PREFIX}/status", headers=auth(mgr)).json()["status"] == "idle"


def test_params_endpoint_comes_from_config_file_and_flags_openai(env, mgr, rig):
    rig("openai")
    body = env["client"].get(f"{PREFIX}/params", headers=auth(mgr)).json()
    keys = [p["key"] for p in body["params"]]
    assert keys[:3] == ["contextTokens", "chunkChars", "outputReserveTokens"]
    ctx = next(p for p in body["params"] if p["key"] == "contextTokens")
    assert (ctx["min"], ctx["max"], ctx["default"]) == (2048, 32768, 8192) and ctx["disabledReason"] == "이 방식은 맥락을 서버에 보낼 수 없음"
    assert [p["id"] for p in body["presets"]] == ["A", "B", "C", "D"] and body["sweepEnabled"] is False and body["sweepDisabledReason"]


# ---------------- 프리셋·수정 표시 ----------------
def test_preset_applied_and_modified_summary():
    values = dict(lab_params.base_values("C"))
    assert values["contextTokens"] == 16384 and values["repeat"] == 5 and values["driftRuns"] == 5
    assert lab_params.describe(values, "C")["summary"] == "프리셋 C 적용됨"
    values["contextTokens"] = 12288
    info = lab_params.describe(values, "C")
    assert info["modified"] is True and info["summary"] == "프리셋 C 기반, 수정됨(맥락 길이: 12288)"
    assert info["sources"]["contextTokens"] == "화면 입력" and info["sources"]["repeat"] == "프리셋" and info["sources"]["retryMax"] == "기본값"
    assert [p["id"] for p in lab_params.presets()] == ["A", "B", "C", "D"]
    assert (lab_params.preset_of("D")["values"]["contextTokens"], lab_params.preset_of("D")["values"]["repeat"]) == (24576, 3)
    assert lab_params.preset_of("A")["values"]["repeat"] == 1 and lab_params.preset_of("A")["values"]["driftRuns"] == 1


# ---------------- settings 선기록 ----------------
def test_settings_block_is_first_key_and_written_before_any_unit(env, mgr, rig, monkeypatch):
    srv = rig("ollama")
    seen: dict = {}

    def probe(state, cfg, sleep):  # 실행 직전에 결과 파일이 이미 있어야 한다
        path = bench_runner._dir() / f"result-{state['runId']}.json"
        seen["keys"] = list(json.loads(path.read_text(encoding="utf-8")))
        seen["units"] = dict(state["units"])

    monkeypatch.setattr(bench_runner, "_run", probe)
    cfg = bench_runner.BenchConfig(base_url=srv.origin, model=MODEL, api_key=SECRET, allow_external=True, item_timeout=10, retry_max=0,
                                   retry_delay=0, repeat=1, drift_runs=1, long_max_chars=80000, connection_type="ollama")
    bench_runner.start("quick", cfg, wait=True)
    assert seen["keys"][0] == "settings" and seen["units"] == {}
    saved = json.loads((bench_runner._dir() / f"result-{bench_runner.load_state()['runId']}.json").read_text(encoding="utf-8"))
    assert saved["settings"]["connectionType"] == "ollama" and saved["settings"]["model"] == MODEL
    assert SECRET not in json.dumps(saved) and srv.origin not in json.dumps(saved)


def test_settings_remain_when_run_is_interrupted(env, mgr, rig):
    rig("openai", drop_after=1)
    assert start(env, mgr, mode="full", preset="B", values={"contextTokens": 8192, "repeat": 2, "driftRuns": 2}).status_code == 202
    done = wait_done(env, mgr, timeout=120)
    assert done["status"] == "interrupted"
    result = result_of(env, mgr)
    block = result["settings"]
    assert list(result)[0] == "settings"
    assert block["preset"] == "B" and block["modified"] is True and [m["key"] for m in block["modifiedItems"]] == ["repeat", "driftRuns"]
    assert block["sources"]["repeat"] == "화면 입력" and block["model"] == MODEL and block["connectionType"] == "openai"
    assert set(block["values"]) >= {"contextTokens", "chunkChars", "outputReserveTokens", "repeat", "driftRuns", "retryMax"}
    assert block["recordedAt"]


# ---------------- 연결 방식 분기 ----------------
def test_ollama_sends_num_ctx_and_openai_does_not(env, mgr, rig):
    srv = rig("ollama")
    assert start(env, mgr, values={"contextTokens": 12288, "outputReserveTokens": 1024}).status_code == 202
    assert wait_done(env, mgr)["status"] == "completed"
    native = [b for b in srv.chat_bodies if b["path"] == "/api/chat"]
    assert native and all(b["body"]["options"]["num_ctx"] == 12288 and b["body"]["options"]["num_predict"] == 1024 for b in native)
    assert all("temperature" in b["body"]["options"] for b in native)

    oa = rig("openai")
    bench_runner._thread.join(timeout=30)
    assert start(env, mgr).status_code == 202
    assert wait_done(env, mgr)["status"] == "completed"
    v1 = [b for b in oa.chat_bodies if b["path"].endswith("/chat/completions")]
    assert v1 and all("num_ctx" not in json.dumps(b["body"]) and "options" not in b["body"] for b in v1)


def test_ollama_diagnosis_uses_tags_and_strips_v1(env, mgr, rig):
    srv = rig("ollama")
    engine_test.save_remote_settings(base_url=srv.origin + "/v1", model=MODEL, api_key=None, timeout_seconds=30, chunk_max_chars=0)
    body = env["client"].post(f"{CONN_BASE}/remote/diagnose", headers=auth(mgr)).json()
    assert body["category"] == "OK"
    engine_test.save_remote_settings(base_url=srv.origin, model="없는모델", api_key=None, timeout_seconds=30, chunk_max_chars=0)
    assert env["client"].post(f"{CONN_BASE}/remote/diagnose", headers=auth(mgr)).json()["category"] == "MODEL_MISSING"


def test_connection_type_saved_and_validated(env, mgr, rig):
    rig("openai")
    ok = env["client"].put(f"{CONN_BASE}/remote", headers=auth(mgr), json={
        "baseUrl": "http://127.0.0.1:11434", "model": MODEL, "timeoutSeconds": 30, "chunkMaxChars": 0, "connectionType": "ollama"})
    assert ok.status_code == 200 and ok.json()["connectionType"] == "ollama"
    bad = env["client"].put(f"{CONN_BASE}/remote", headers=auth(mgr), json={
        "baseUrl": "http://127.0.0.1:11434", "model": MODEL, "timeoutSeconds": 30, "chunkMaxChars": 0, "connectionType": "grpc"})
    assert bad.status_code == 422


# ---------------- 맥락 스윕 ----------------
def test_sweep_runs_b_c_d_in_order_unloading_between(env, mgr, rig):
    srv = rig("ollama", ps_context=None)
    assert start(env, mgr, sweep=True, values={"repeat": 1, "driftRuns": 1}).status_code == 202  # 반복은 프리셋 값이 우선한다
    deadline = time.time() + 300
    while time.time() < deadline:
        st = env["client"].get(f"{PREFIX}/status", headers=auth(mgr)).json()
        if st["status"] != "running" and not (bench_runner._thread and bench_runner._thread.is_alive()):
            break
        time.sleep(0.2)
    assert st["status"] == "completed" and st["sweepTotal"] == 3 and st["sweepIndex"] == 3
    chats = [ctx for kind, ctx in srv.order if kind == "chat"]
    seq = [c for i, c in enumerate(chats) if i == 0 or c != chats[i - 1]]
    assert seq == [8192, 16384, 24576]
    assert srv.unloads == 2  # 맥락 사이마다 한 번씩 모델을 내린다
    kinds = [k for k, _ in srv.order]
    first_unload = kinds.index("unload")
    assert all(c == 8192 for k, c in srv.order[:first_unload] if k == "chat")
    history = env["client"].get(f"{PREFIX}/history", headers=auth(mgr)).json()["items"]
    assert sorted(h["contextTokens"] for h in history) == [8192, 16384, 24576] and len({h["sweepId"] for h in history}) == 1


def test_sweep_rejected_for_openai(env, mgr, rig):
    rig("openai")
    res = start(env, mgr, sweep=True)
    assert res.status_code == 422 and "맥락 스윕" in res.json()["detail"]


# ---------------- 측정 품질 ----------------
def test_input_truncation_suspected_and_context_mismatch_warned(env, mgr, rig):
    # 입력 토큰이 서버 기본 맥락(2048)과 같은데 추정 입력은 훨씬 큼 + 서버가 적재한 맥락(4096)이 설정(8192)과 다름
    rig("ollama", prompt_tokens=2048, ps_context=4096)
    assert start(env, mgr, values={"contextTokens": 8192}).status_code == 202
    wait_done(env, mgr)
    result = result_of(env, mgr)
    b = result["sections"]["B"]
    assert b["truncationSuspectedRuns"] >= 1 and b["inputTokens"]["min"] == b["inputTokens"]["max"] == 2048
    text = " ".join(result["warnings"])
    assert "입력 잘림 의심" in text and "4096" in text and "8192" in text
    assert "경고" in result["summaryMarkdown"]


def test_no_warning_when_tokens_small_and_context_matches(env, mgr, rig):
    rig("ollama", prompt_tokens=400, ps_context=8192)
    start(env, mgr)
    wait_done(env, mgr)
    assert result_of(env, mgr)["warnings"] == []


def test_due_classification_counts_and_day_differences(env, mgr, rig):
    rig("openai", due_mode="shift")
    start(env, mgr)
    wait_done(env, mgr)
    due = result_of(env, mgr)["sections"]["B"]["dueClassification"]
    assert due["dueClass"]["different"] > 0 and due["dueClass"]["blank"] == 0 and due["dueClass"]["formatError"] == 0
    assert set(due["dayDiffDistribution"]) == {"3"}


def test_due_class_function_distinguishes_blank_format_and_different():
    assert bench_runner.due_class("", "2026-10-20") == ("blank", None)
    assert bench_runner.due_class("다음주 금요일", "2026-10-20") == ("formatError", None)
    assert bench_runner.due_class("2026-13-45", "2026-10-20") == ("formatError", None)
    assert bench_runner.due_class("2026-10-18", "2026-10-20") == ("different", -2)
    assert bench_runner.due_class("2026-10-20", "2026-10-20") == ("match", 0)


# ---------------- 결함 수정 ----------------
def test_blocked_attempt_does_not_overwrite_diagnosis(env, mgr, rig):
    srv = rig("openai")
    start(env, mgr)
    wait_done(env, mgr)
    before = bench_runner.load_state()["diagnosis"]
    assert before["category"] == "OK"
    srv.close()  # 서버가 사라진 뒤 다시 시작을 시도한다 → 진단이 막는다
    blocked = start(env, mgr)
    assert blocked.status_code == 409
    state = bench_runner.load_state()
    assert state["diagnosis"] == before  # 측정 직전 값은 그대로
    assert state["blockedAttempt"]["category"] == "UNREACHABLE"
    result = result_of(env, mgr)
    assert result["diagnosis"]["category"] == "OK" and result["blockedAttempt"]["category"] == "UNREACHABLE"


def test_exhausted_retries_are_counted(env, mgr, rig, monkeypatch):
    monkeypatch.setattr(settings, "bench_retry_max", 2)
    rig("openai", drop_after=0)  # 첫 호출부터 연결이 끊긴다
    start(env, mgr)
    done = wait_done(env, mgr, timeout=60)
    assert done["status"] == "interrupted"
    assert done["retries"] == 2 and result_of(env, mgr)["retries"] == 2


# ---------------- 시험 모드 꺼짐/켜짐 ----------------
def test_test_mode_off_hides_menu_flag_and_rejects_all_bench_apis(env, mgr, rig, monkeypatch):
    rig("openai")
    monkeypatch.setattr(settings, "engine_test_mode", False)
    assert env["client"].get("/api/system/engine", headers=auth(mgr)).json()["engineTestMode"] is False  # 화면 메뉴는 이 값으로 숨긴다
    for path in ("params", "models", "history", "status", "result"):
        assert env["client"].get(f"{PREFIX}/{path}", headers=auth(mgr)).status_code == 404, path
    for path in ("start", "resume", "stop"):
        assert env["client"].post(f"{PREFIX}/{path}", headers=auth(mgr), json={"mode": "quick"}).status_code == 404, path
    assert env["client"].get(f"{CONN_BASE}/remote", headers=auth(mgr)).status_code == 404
    assert not settings.bench_dir.exists()


def test_test_mode_on_works(env, mgr, rig):
    rig("openai")
    assert env["client"].get("/api/system/engine", headers=auth(mgr)).json()["engineTestMode"] is True
    assert env["client"].get(f"{PREFIX}/params", headers=auth(mgr)).status_code == 200
    models = env["client"].get(f"{PREFIX}/models", headers=auth(mgr)).json()
    assert models["models"] == [MODEL] and models["error"] == ""
    assert env["client"].get(f"{PREFIX}/history", headers=auth(mgr)).json() == {"items": []}
