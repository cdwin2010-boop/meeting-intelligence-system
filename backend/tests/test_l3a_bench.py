"""L-3a: 원격(시험용) 엔진 연결 점검·벤치마크 측정. 127.0.0.1 임시 포트의 가짜 서버만 쓴다(실제 원격·Gemini·Ollama 호출 없음)."""
import json
import re
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from app.config import settings
from app.services import bench_data, bench_runner, engine_test
from tests.test_upload_processing import add_account, auth, env  # noqa: F401  (env 는 픽스처)

SECRET = "sk-bench-secret-value-7777"
MODEL = "bench-model-q4"
PREFIX = "/api/admin/engine-test/bench"
_LINE = re.compile(
    r"^#(?P<n>\d+) \[[^\]]+\] 화자\d+: (?P<person>\S+) 님, (?P<team>\S+) (?P<noun>.+?) (?P<ref>제\d+호)를 (?P<phrase>.+?까지) "
)
_DUE = {phrase: calc for phrase, calc in bench_data._DUES}


class FakeLlm:
    """OpenAI 호환 /v1 + (선택) Ollama /api/ps 를 흉내 내는 가짜 서버. 전사 줄을 읽어 정답에 가까운 JSON 을 돌려준다."""

    def __init__(self, *, ps: bool = True, delay: float = 0.0, truncated: bool = False, accept_any_model: bool = False,
                 drop_after: int | None = None):
        self.ps = ps
        self.delay = delay
        self.truncated = truncated
        self.accept_any_model = accept_any_model
        self.drop_after = drop_after  # 이 횟수만큼 chat 호출을 받은 뒤에는 연결을 끊는다
        self.chat_calls = 0
        self.requests: list[tuple[str, str]] = []
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
                outer.requests.append(("GET", self.path))
                if self.path.endswith("/models"):
                    return self._send(200, {"data": [{"id": MODEL}]})
                if self.path == "/api/ps" and outer.ps:
                    return self._send(200, {"models": [{"name": MODEL, "size": 5000, "size_vram": 4500, "expires_at": "2099-01-01T00:00:00Z"}]})
                return self._send(404, {"error": "not found"})

            def do_POST(self):  # noqa: N802
                length = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(length) or b"{}")
                outer.requests.append(("POST", self.path))
                outer.chat_calls += 1
                if outer.drop_after is not None and outer.chat_calls > outer.drop_after:
                    self.connection.close()  # 응답 없이 끊김
                    return
                if outer.delay:
                    time.sleep(outer.delay)
                if body.get("model") != MODEL and not outer.accept_any_model:
                    return self._send(404, {"error": {"message": "model not found"}})
                prompt = body["messages"][-1]["content"]
                text = prompt.split("전사문:\n", 1)[1] if "전사문:\n" in prompt else prompt
                items = []
                for line in text.splitlines():
                    m = _LINE.match(line)
                    if not m:
                        continue
                    due = _DUE[m["phrase"]](bench_data.HELD_AT.date()).isoformat()
                    quote = line.split("] ", 1)[1].split(": ", 1)[1]
                    items.append({
                        "task": f"{m['team']} {m['noun']} {m['ref']} 준비", "assignee": m["person"], "due_date": due,
                        "quote": {"speaker": "화자1", "timestamp": "", "text": quote, "line_id": m["n"]},
                    })
                content = json.dumps({"minutes": {}, "items": items}, ensure_ascii=False)
                if outer.truncated:
                    content = content[: max(len(content) // 3, 10)]
                self._send(200, {
                    "choices": [{"message": {"role": "assistant", "content": content}, "finish_reason": "stop"}],
                    "usage": {"prompt_tokens": 400, "completion_tokens": 120},
                })

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.httpd.server_address[1]}/v1"

    def close(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()


@pytest.fixture
def rig(monkeypatch, tmp_path):
    servers: list[FakeLlm] = []
    monkeypatch.setattr(settings, "engine_test_mode", True)
    monkeypatch.setattr(settings, "engine_test_settings_path", tmp_path / "engine_test.json")
    monkeypatch.setattr(settings, "bench_dir", tmp_path / "bench")
    monkeypatch.setattr(settings, "remote_llm_base_url", "")
    monkeypatch.setattr(settings, "remote_llm_model", "")
    monkeypatch.setattr(settings, "bench_retry_delay_seconds", 0)
    monkeypatch.setattr(settings, "bench_repeat", 2)
    monkeypatch.setattr(settings, "bench_drift_runs", 3)
    monkeypatch.setattr(settings, "extract_chunk_max_chars", 3000)
    monkeypatch.setattr(settings, "extract_chunk_target_tokens", 1500)
    monkeypatch.setattr(settings, "bench_item_timeout_seconds", 20)

    def make(**kwargs) -> FakeLlm:
        srv = FakeLlm(**kwargs)
        servers.append(srv)
        engine_test.save_remote_settings(base_url=srv.base_url, model=MODEL, api_key=SECRET, timeout_seconds=30, chunk_max_chars=0)
        return srv

    yield make
    bench_runner.stop()
    thread = bench_runner._thread
    if thread is not None:
        thread.join(timeout=30)
    for srv in servers:
        srv.close()


@pytest.fixture
def mgr(env):
    return add_account(env["factory"], "고객사A", "mgr", "manager", name="박관리")


@pytest.fixture
def staff(env):
    return add_account(env["factory"], "고객사A", "stf", "staff", name="김담당")


def wait_done(env, account, *, timeout=90.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        body = env["client"].get(f"{PREFIX}/status", headers=auth(account)).json()
        if body["status"] != "running":
            return body
        time.sleep(0.1)
    raise AssertionError("측정이 제시간에 끝나지 않았다")


def start(env, account, mode="quick"):
    return env["client"].post(f"{PREFIX}/start", headers=auth(account), json={"mode": mode})


def result_of(env, account) -> dict:
    res = env["client"].get(f"{PREFIX}/result", headers=auth(account))
    assert res.status_code == 200
    assert "attachment" in res.headers["content-disposition"]
    return res.json()


# ---------------- 빠른 점검 ----------------
def test_quick_run_completes_with_expected_sections(env, mgr, rig):
    rig()
    assert start(env, mgr).status_code == 202
    done = wait_done(env, mgr)
    assert done["status"] == "completed" and done["doneUnits"] == 4 and done["totalUnits"] == 4
    result = result_of(env, mgr)
    s = result["sections"]
    assert s["A"]["status"] == "완료" and s["A"]["modelFoundAll"] is True
    assert s["B"]["jsonParsedRuns"] == 1 and s["B"]["missRate"]["mean"] == 0
    assert s["B"]["assigneeMatchRate"]["mean"] == 1 and s["B"]["dueMatchRate"]["mean"] == 1
    assert s["B"]["tokensPerSecond"]["mean"] is not None
    assert s["E"]["rejectedRuns"] == 1 and "http_4xx" in s["E"]["errorCodes"]
    assert s["F"]["measurable"] is True and s["F"]["vramBytes"] == 4500
    assert s["C"]["status"] == "미측정" and s["G"]["status"] == "미측정"  # 빠른 점검은 C·G 를 하지 않는다(사유와 함께 미측정)
    assert "| B 단건 추출 |" in result["summaryMarkdown"]


def test_result_files_never_contain_address_key_host_or_transcript(env, mgr, rig, tmp_path):
    srv = rig()
    start(env, mgr)
    wait_done(env, mgr)
    result_of(env, mgr)  # 결과 파일도 기록된다
    blob = ""
    for path in (tmp_path / "bench").iterdir():
        blob += path.read_text(encoding="utf-8")
    blob += env["client"].get(f"{PREFIX}/result", headers=auth(mgr)).text + env["client"].get(f"{PREFIX}/status", headers=auth(mgr)).text
    for secret in (SECRET, srv.base_url, "127.0.0.1", str(srv.httpd.server_address[1]), "김도윤", "제1호", "주간 업무 점검"):
        assert secret not in blob


def test_f_is_unmeasurable_when_status_api_is_missing(env, mgr, rig):
    rig(ps=False)
    start(env, mgr)
    wait_done(env, mgr)
    f = result_of(env, mgr)["sections"]["F"]
    assert f["measurable"] is False and "측정 불가" in f["reason"]


def test_server_that_accepts_invalid_model_is_reported(env, mgr, rig):
    rig(accept_any_model=True)
    start(env, mgr)
    wait_done(env, mgr)
    e = result_of(env, mgr)["sections"]["E"]
    assert e["rejectedRuns"] == 0 and "거절하지 않고" in e["guidance"]


def test_truncated_json_is_recorded_as_parse_failure_not_crash(env, mgr, rig):
    rig(truncated=True)
    start(env, mgr)
    done = wait_done(env, mgr)
    assert done["status"] == "completed"
    b = result_of(env, mgr)["sections"]["B"]
    assert b["cleanParsedRuns"] == 0 and (b["jsonParsedRuns"] == 0 or b["truncatedRecoveredRuns"] + b["repairedRuns"] >= 1)


# ---------------- 전체 측정 ----------------
def test_full_run_covers_all_sections_and_split_path(env, mgr, rig):
    rig()
    start(env, mgr, "full")
    done = wait_done(env, mgr, timeout=180)
    assert done["status"] == "completed"
    assert done["doneUnits"] == done["totalUnits"] == 2 * 5 + 3  # A,B,C,E,F 각 2회 + G 3회
    s = result_of(env, mgr)["sections"]
    split = s["C"]["split"]
    assert split["measured"] is True and split["splitTriggeredRuns"] == 2 and split["chunkCount"]["mean"] >= 3
    assert split["perChunkMs"]["n"] >= 6 and split["itemsAfterDedupe"]["mean"] <= split["itemsBeforeDedupe"]["mean"]
    assert s["C"]["single"]["chunkCount"]["mean"] == 1  # 예산 이내는 분할 없이 1회
    d = s["D"]
    assert d["total"] > 0 and d["verified"] == d["total"] and d["verifiedRate"] == 1.0 and d["lineIdMismatch"] == 0
    assert len(s["G"]["elapsedMsSeries"]) == 3 and s["G"]["failedRuns"] == 0
    assert s["A"]["runs"] == 2 and s["A"]["status"] == "완료"


# ---------------- 연결 끊김 → 이어하기(주소 변경) ----------------
def test_disconnect_marks_interrupted_and_resume_with_new_address_finishes(env, mgr, rig, tmp_path):
    first = rig(drop_after=3)  # A·B(1회차 호출 1)·... 몇 건 처리한 뒤 끊김
    start(env, mgr, "full")
    stopped = wait_done(env, mgr, timeout=120)
    assert stopped["status"] == "interrupted"
    assert stopped["message"] == "연결이 끊겼습니다. 새 주소를 입력하고 이어하기를 눌러 주세요"
    assert 0 < stopped["doneUnits"] < stopped["totalUnits"] and stopped["canResume"] is True
    kept = bench_runner.load_state()["units"]["A#1"]["elapsedMs"]
    first.close()
    # 새 주소(다른 포트)의 서버를 저장하고 이어하기
    second = FakeLlm()
    try:
        engine_test.save_remote_settings(base_url=second.base_url, model=MODEL, api_key=SECRET, timeout_seconds=30, chunk_max_chars=0)
        assert env["client"].post(f"{PREFIX}/resume", headers=auth(mgr)).status_code == 202
        done = wait_done(env, mgr, timeout=180)
        assert done["status"] == "completed" and done["doneUnits"] == done["totalUnits"]
        state = bench_runner.load_state()
        assert state["units"]["A#1"]["elapsedMs"] == kept  # 완료 항목은 다시 실행하지 않는다
        assert state["resumeCount"] == 1
        assert result_of(env, mgr)["resumeCount"] == 1
    finally:
        second.close()


def test_resume_without_state_is_conflict_and_result_is_404(env, mgr, rig):
    rig()
    assert env["client"].post(f"{PREFIX}/resume", headers=auth(mgr)).status_code == 409
    assert env["client"].get(f"{PREFIX}/result", headers=auth(mgr)).status_code == 404
    assert env["client"].get(f"{PREFIX}/status", headers=auth(mgr)).json()["status"] == "idle"


# ---------------- 중복 시작·정지 ----------------
def test_second_start_is_rejected_while_running_and_stop_allows_resume(env, mgr, rig):
    rig(delay=0.4)
    assert start(env, mgr, "full").status_code == 202
    again = start(env, mgr, "quick")
    assert again.status_code == 409
    stop = env["client"].post(f"{PREFIX}/stop", headers=auth(mgr))
    assert stop.status_code == 200
    done = wait_done(env, mgr)
    assert done["status"] == "stopped" and done["canResume"] is True


def test_start_requires_saved_remote_settings(env, mgr, rig, monkeypatch):
    res = start(env, mgr)
    assert res.status_code == 422


# ---------------- 권한 ----------------
def test_permissions_and_test_mode(env, mgr, staff, rig, monkeypatch):
    rig()
    assert start(env, staff).status_code == 403
    assert env["client"].get(f"{PREFIX}/status", headers=auth(staff)).status_code == 403
    assert env["client"].get(f"{PREFIX}/result", headers=auth(staff)).status_code == 403
    monkeypatch.setattr(settings, "engine_test_mode", False)
    assert start(env, mgr).status_code == 404  # 시험 모드가 꺼져 있으면 없는 API 처럼 404 로 거부(L-5)
    assert not (settings.bench_dir).exists()


def test_invalid_mode_is_422(env, mgr, rig):
    rig()
    assert start(env, mgr, "huge").status_code == 422
