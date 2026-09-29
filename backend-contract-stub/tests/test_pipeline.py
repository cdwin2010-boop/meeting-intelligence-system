"""처리 파이프라인(app/pipeline) 테스트. 네트워크·실제 키 없이 돈다: Gemini 클라이언트는 가짜 객체로 주입한다."""
import json
import threading
from types import SimpleNamespace

import httpx
import pytest
from fastapi.testclient import TestClient
from google.genai import errors as genai_errors
from pydantic import SecretStr

from app.config import settings
from app.db import store
from app.main import app
from app.pipeline import service
from app.pipeline.extractor import ActionItemList, GeminiExtractor, postprocess, response_json_schema
from app.pipeline.stt import NO_SPEECH, GeminiStt
from app.upload_storage import find_upload
from app.uploads import fake_transcript

client = TestClient(app)

M4A = b"\x00\x00\x00\x18ftypM4A \x00\x00\x00\x00" + b"\x00" * 1000
FORM = {"title": "파이프라인 회의", "startedAt": "2026-09-29T14:00:00+09:00"}
FAKE_KEY = "AIzaSyTEST_ONLY_not_a_real_key_0123456789"


@pytest.fixture(autouse=True)
def fresh(monkeypatch):
    store.reset()
    monkeypatch.setattr(settings, "fake_worker_step_seconds", 0)
    monkeypatch.setattr(settings, "fake_worker_enabled", False)  # 업로드 직후 queued 로 붙잡고, 처리는 테스트가 직접 부른다


def upload(**override) -> dict:
    r = client.post("/api/meetings", data={**FORM, **override}, files={"file": ("A.m4a", M4A, "audio/mp4")})
    assert r.status_code == 202, r.text
    return r.json()


def uploaded_files() -> list[str]:
    folder = settings.upload_dir
    return sorted(p.name for p in folder.iterdir()) if folder.exists() else []


def admin_job(job_id: str) -> dict:
    return next(j for j in client.get("/api/admin/jobs").json() if j["id"] == job_id)


# ---------------- 가짜 엔진 ----------------
class StubStt:
    def __init__(self, result=None, error=None, on_transcribe=None):
        self.result, self.error, self.on_transcribe = result, error, on_transcribe
        self.released = False

    def transcribe(self, audio_path, mime_type):
        self.mime_type = mime_type
        if self.on_transcribe:
            self.on_transcribe()
        if self.error:
            raise self.error
        return self.result

    def release(self):
        self.released = True


class StubExtractor:
    def __init__(self, items=None, error=None, on_extract=None):
        self.items, self.error, self.on_extract = items or [], error, on_extract

    def extract(self, transcript, started_at):
        if self.on_extract:
            self.on_extract()
        if self.error:
            raise self.error
        return self.items


ITEM = {"task": "디자인 시안 확정", "assignee": "정민수", "dueDate": "2026-10-01",
        "quote": {"speaker": "김도현", "timestamp": "00:00:20", "text": "디자인 시안은 정민수 님이 이번 주 목요일까지 확정해 주세요."}}


def run(job_id, stt, extractor=None):
    service.process_job(job_id, stt_factory=lambda: stt, extractor_factory=lambda: extractor or StubExtractor([ITEM]))


# ---------------- 추출 후처리 ----------------
def test_postprocess_fixes_dates_drops_empty_tasks_and_fills_empty_strings():
    raw = json.dumps({"items": [
        {"task": "좋은 항목", "assignee": "이서연", "due_date": "2026-10-09",
         "quote": {"speaker": "김도현", "timestamp": "00:00:03", "text": "원문"}},
        {"task": "날짜가 말로 옴", "assignee": "정민수", "due_date": "다음 주 금요일", "quote": None},
        {"task": "없는 날짜", "assignee": None, "due_date": "2026-13-45"},
        {"task": "슬래시 날짜", "due_date": "2026/10/09", "quote": {"speaker": None}},
        {"task": "   ", "assignee": "누군가", "due_date": "2026-10-01"},
        {"assignee": "task 누락"},
    ]}, ensure_ascii=False)
    items = postprocess(ActionItemList.model_validate_json(raw))
    assert [i["task"] for i in items] == ["좋은 항목", "날짜가 말로 옴", "없는 날짜", "슬래시 날짜"]
    assert [i["dueDate"] for i in items] == ["2026-10-09", "", "", ""]
    assert items[2]["assignee"] == ""  # None → ""
    assert items[1]["quote"] == {"speaker": "", "timestamp": "", "text": ""}  # quote=None → 빈 문자열들
    assert items[3]["assignee"] == "" and items[3]["quote"]["speaker"] == ""  # 누락 → ""
    assert all(v is not None for i in items for v in (i["task"], i["assignee"], i["dueDate"], *i["quote"].values()))


def test_null_items_becomes_empty_list():
    assert postprocess(ActionItemList.model_validate_json('{"items": null}')) == []


def test_response_schema_is_inlined_and_requires_every_field():
    schema = response_json_schema()
    assert "$ref" not in json.dumps(schema) and "$defs" not in schema
    item = schema["properties"]["items"]["items"]
    assert set(item["required"]) == {"task", "assignee", "due_date", "quote"}
    assert set(item["properties"]["quote"]["required"]) == {"speaker", "timestamp", "text"}


# ---------------- 전사 결과가 비었을 때 ----------------
@pytest.mark.parametrize("transcript", [NO_SPEECH, "", "   \n"])
def test_no_speech_fails_job_with_reason_and_keeps_file(transcript):
    receipt = upload()
    job_id = receipt["jobId"]
    stt = StubStt(result=transcript)
    run(job_id, stt)
    status = client.get(f"/api/jobs/{job_id}").json()
    assert status["status"] == "failed" and "말소리가 감지되지 않았습니다" in status["errorMessage"]
    assert "말소리가 감지되지 않았습니다" in admin_job(job_id)["errorLog"][-1]
    assert find_upload(job_id) is not None  # Retry 를 위해 남는다
    assert stt.released is True
    assert client.get(f"/api/meetings/{receipt['meetingId']}").json()["transcriptText"] is None


# ---------------- Gemini 호출 예외 ----------------
def _api_error(code: int, status: str, message: str):
    return genai_errors.ClientError(code, {"error": {"code": code, "status": status, "message": message}})


@pytest.mark.parametrize("error,expected", [
    (_api_error(429, "RESOURCE_EXHAUSTED", f"quota exceeded for key {FAKE_KEY}"), "할당량"),
    (_api_error(401, "UNAUTHENTICATED", f"API key not valid: {FAKE_KEY}"), "인증에 실패"),
    (_api_error(403, "PERMISSION_DENIED", "denied"), "인증에 실패"),
    (httpx.ReadTimeout(f"timed out calling ...?key={FAKE_KEY}"), "응답 시간이 초과"),
    (TimeoutError("file never became ACTIVE"), "응답 시간이 초과"),
    (_api_error(400, "INVALID_ARGUMENT", f"Unsupported MIME type: audio/mp4 (key={FAKE_KEY})"), "Unsupported MIME type"),
    (RuntimeError(f"boom {FAKE_KEY}"), "RuntimeError"),
])
def test_gemini_errors_fail_job_with_summary_and_no_key(monkeypatch, error, expected):
    monkeypatch.setattr(settings, "gemini_api_key", SecretStr(FAKE_KEY))
    job_id = upload()["jobId"]
    run(job_id, StubStt(error=error))
    job = admin_job(job_id)
    status = client.get(f"/api/jobs/{job_id}").json()
    assert job["status"] == "failed" and len(job["errorLog"]) == 1
    assert expected in job["errorLog"][0] and status["errorMessage"] == job["errorLog"][0]
    body = json.dumps([job, status], ensure_ascii=False)
    assert FAKE_KEY not in body and "AIza" not in body
    assert "Traceback" not in body and "File \"" not in body  # 스택 트레이스 없음
    assert find_upload(job_id) is not None


def test_llm_error_fails_job_too():
    job_id = upload()["jobId"]
    run(job_id, StubStt(result=fake_transcript()), StubExtractor(error=_api_error(429, "RESOURCE_EXHAUSTED", "x")))
    assert client.get(f"/api/jobs/{job_id}").json()["status"] == "failed"


# ---------------- Kill ----------------
def test_kill_during_stt_saves_nothing():
    receipt = upload()
    job_id = receipt["jobId"]
    kill = lambda: client.post(f"/api/admin/jobs/{job_id}/kill")  # noqa: E731
    run(job_id, StubStt(result=fake_transcript(), on_transcribe=kill))
    job = admin_job(job_id)
    assert job["status"] == "failed" and "killed by administrator" in job["errorLog"][0]
    assert client.get(f"/api/meetings/{receipt['meetingId']}").json()["transcriptText"] is None
    assert client.get(f"/api/meetings/{receipt['meetingId']}/action-items").json() == []


def test_kill_during_llm_saves_nothing_and_does_not_overwrite_kill_log():
    receipt = upload()
    job_id = receipt["jobId"]
    kill = lambda: client.post(f"/api/admin/jobs/{job_id}/kill")  # noqa: E731
    run(job_id, StubStt(result=fake_transcript()), StubExtractor([ITEM], on_extract=kill))
    assert client.get(f"/api/meetings/{receipt['meetingId']}").json()["transcriptText"] is None
    assert client.get(f"/api/meetings/{receipt['meetingId']}/action-items").json() == []
    assert "killed by administrator" in admin_job(job_id)["errorLog"][0]
    assert find_upload(job_id) is not None


def test_fail_if_processing_is_conditional():
    job_id = upload()["jobId"]
    assert store.fail_if_processing(job_id, "x") is False  # queued → 바꾸지 않음
    store.start_if_queued(job_id)
    assert store.fail_if_processing(job_id, "이유") is True
    assert store.fail_if_processing(job_id, "덮어쓰기") is False  # 이미 failed
    assert admin_job(job_id)["errorLog"] == ["이유"]


# ---------------- 동시 처리 1건 ----------------
def test_only_one_job_is_processed_at_a_time():
    first, second = upload()["jobId"], upload()["jobId"]
    release = threading.Event()
    lock = threading.Lock()
    active, peak = [0], [0]

    def blocking_stt():
        with lock:
            active[0] += 1
            peak[0] = max(peak[0], active[0])
        release.wait(5)
        with lock:
            active[0] -= 1

    threads = [threading.Thread(target=run, args=(j, StubStt(result=fake_transcript(), on_transcribe=blocking_stt)))
               for j in (first, second)]
    for t in threads:
        t.start()
    # 첫 작업이 STT 에서 붙잡혀 있는 동안 두 번째는 queued 로 기다린다
    for _ in range(200):
        if active[0] == 1:
            break
        threading.Event().wait(0.01)
    statuses = sorted(client.get(f"/api/jobs/{j}").json()["status"] for j in (first, second))
    assert statuses == ["processing", "queued"]
    release.set()
    for t in threads:
        t.join(5)
    assert peak[0] == 1
    assert all(client.get(f"/api/jobs/{j}").json()["status"] == "completed" for j in (first, second))


# ---------------- 업로드 파일 보관·삭제 ----------------
def test_completed_deletes_file_and_saves_results():
    receipt = upload()
    job_id = receipt["jobId"]
    assert uploaded_files() == [f"{job_id}.m4a"]  # 접수되면 <jobId>.<확장자> 로 보관
    stt = StubStt(result=fake_transcript())
    run(job_id, stt)
    assert stt.mime_type == "audio/mp4"
    assert client.get(f"/api/jobs/{job_id}").json()["status"] == "completed"
    assert uploaded_files() == []
    assert client.get(f"/api/meetings/{receipt['meetingId']}").json()["transcriptText"] == fake_transcript()
    assert len(client.get(f"/api/meetings/{receipt['meetingId']}/action-items").json()) == 1


def test_fake_mode_upload_also_deletes_file_when_completed(monkeypatch):
    monkeypatch.setattr(settings, "fake_worker_enabled", True)
    job_id = upload()["jobId"]
    assert client.get(f"/api/jobs/{job_id}").json()["status"] == "completed"
    assert uploaded_files() == []


def test_rejected_upload_leaves_no_file():
    r = client.post("/api/meetings", data=FORM, files={"file": ("fake.mp3", "텍스트".encode(), "audio/mp3")})
    assert r.status_code == 415
    assert uploaded_files() == []


def test_upload_response_does_not_expose_file_path():
    receipt = upload()
    assert set(receipt) == {"meetingId", "jobId", "status"}
    body = json.dumps([receipt, client.get(f"/api/jobs/{receipt['jobId']}").json(), admin_job(receipt["jobId"])])
    assert str(settings.upload_dir) not in body and ".m4a" not in body


def test_retry_needs_the_uploaded_file():
    job_id = upload()["jobId"]
    run(job_id, StubStt(result=NO_SPEECH))  # failed, 파일은 남음
    assert client.post(f"/api/admin/jobs/{job_id}/retry").status_code == 200  # 파일이 있으면 Retry 가능

    store.start_if_queued(job_id)
    store.fail_if_processing(job_id, "다시 실패")
    find_upload(job_id).unlink()
    r = client.post(f"/api/admin/jobs/{job_id}/retry")
    assert r.status_code == 409 and "missing" in r.json()["detail"]
    assert client.get(f"/api/jobs/{job_id}").json()["status"] == "failed"  # 상태는 그대로


def test_process_job_fails_when_file_is_gone():
    job_id = upload()["jobId"]
    find_upload(job_id).unlink()
    run(job_id, StubStt(result=fake_transcript()))
    assert "음성 파일을 찾을 수 없습니다" in client.get(f"/api/jobs/{job_id}").json()["errorMessage"]


# ---------------- gemini 모드 업로드 거절 ----------------
def _job_count() -> int:
    return len(client.get("/api/admin/jobs").json())


def test_gemini_mode_rejects_local_engine_with_501(monkeypatch):
    monkeypatch.setattr(settings, "stt_provider", "gemini")
    monkeypatch.setattr(settings, "gemini_api_key", SecretStr(FAKE_KEY))
    r = client.post("/api/meetings", data={**FORM, "engine": "faster-whisper"}, files={"file": ("A.m4a", M4A)})
    assert r.status_code == 501 and FAKE_KEY not in r.text
    assert _job_count() == 9 and uploaded_files() == []


def test_gpu_guard_is_checked_before_501(monkeypatch):
    monkeypatch.setattr(settings, "stt_provider", "gemini")
    monkeypatch.setattr(settings, "gpu_guard_threshold_minutes", 1)
    big = M4A + b"\x00" * 2_000_000
    r = client.post("/api/meetings", data={**FORM, "engine": "faster-whisper"}, files={"file": ("A.m4a", big)})
    assert r.status_code == 409 and r.json()["detail"]["code"] == "gpu_guard"


@pytest.mark.parametrize("provider", ["stt_provider", "llm_provider"])
def test_gemini_mode_without_key_is_503(monkeypatch, provider):
    monkeypatch.setattr(settings, provider, "gemini")
    r = client.post("/api/meetings", data=FORM, files={"file": ("A.m4a", M4A)})
    assert r.status_code == 503
    assert _job_count() == 9 and uploaded_files() == []


def test_gemini_mode_with_key_runs_pipeline_in_background(monkeypatch):
    monkeypatch.setattr(settings, "stt_provider", "gemini")
    monkeypatch.setattr(settings, "llm_provider", "gemini")
    monkeypatch.setattr(settings, "gemini_api_key", SecretStr(FAKE_KEY))
    monkeypatch.setattr(service, "make_stt", lambda: StubStt(result=fake_transcript()))
    monkeypatch.setattr(service, "make_extractor", lambda: StubExtractor([ITEM]))
    receipt = upload()
    assert client.get(f"/api/jobs/{receipt['jobId']}").json()["status"] == "completed"
    assert uploaded_files() == []


# ---------------- Gemini 어댑터 (가짜 클라이언트) ----------------
class FakeFiles:
    def __init__(self, states):
        self.states = list(states)
        self.deleted: list[str] = []
        self.upload_args = None

    def upload(self, *, file, config=None):
        self.upload_args = (file, config)
        return SimpleNamespace(name="files/abc", state=self.states.pop(0))

    def get(self, *, name):
        return SimpleNamespace(name=name, state=self.states.pop(0))

    def delete(self, *, name):
        self.deleted.append(name)


class FakeModels:
    def __init__(self, text=None, error=None):
        self.text, self.error, self.calls = text, error, []

    def generate_content(self, *, model, contents, config=None):
        self.calls.append({"model": model, "contents": contents, "config": config})
        if self.error:
            raise self.error
        return SimpleNamespace(text=self.text)


def test_gemini_stt_waits_for_active_and_deletes_remote_file(tmp_path):
    files, models = FakeFiles(["PROCESSING", "PROCESSING", "ACTIVE"]), FakeModels(text="[00:00:01] 화자1: 안녕하세요\n")
    stt = GeminiStt(SimpleNamespace(files=files, models=models), "model-x", poll_seconds=0)
    audio = tmp_path / "a.m4a"
    audio.write_bytes(M4A)
    assert stt.transcribe(audio, "audio/mp4") == "[00:00:01] 화자1: 안녕하세요"
    assert files.upload_args[1].mime_type == "audio/mp4"
    assert models.calls[0]["model"] == "model-x" and "[NO_SPEECH]" in models.calls[0]["contents"][1]
    assert files.deleted == ["files/abc"]


def test_gemini_stt_deletes_remote_file_even_when_generation_fails(tmp_path):
    files = FakeFiles(["ACTIVE"])
    stt = GeminiStt(SimpleNamespace(files=files, models=FakeModels(error=_api_error(429, "RESOURCE_EXHAUSTED", "q"))), "m")
    with pytest.raises(genai_errors.ClientError):
        stt.transcribe(tmp_path / "a.m4a", "audio/mp4")
    assert files.deleted == ["files/abc"]


def test_gemini_stt_failed_file_state_raises(tmp_path):
    files = FakeFiles(["FAILED"])
    stt = GeminiStt(SimpleNamespace(files=files, models=FakeModels(text="x")), "m")
    with pytest.raises(Exception, match="could not process"):
        stt.transcribe(tmp_path / "a.m4a", "audio/mp4")
    assert files.deleted == ["files/abc"]


def test_gemini_extractor_uses_json_schema_and_postprocesses():
    text = json.dumps({"items": [
        {"task": "개선안 정리", "assignee": "이서연", "due_date": "2026-10-09",
         "quote": {"speaker": "김도현", "timestamp": "00:00:03", "text": "원문"}},
        {"task": "", "assignee": "x", "due_date": "", "quote": {"speaker": "", "timestamp": "", "text": ""}},
    ]}, ensure_ascii=False)
    models = FakeModels(text=text)
    from datetime import datetime

    items = GeminiExtractor(SimpleNamespace(models=models), "m").extract("전사", datetime.fromisoformat(FORM["startedAt"]))
    assert items == [{"task": "개선안 정리", "assignee": "이서연", "dueDate": "2026-10-09",
                      "quote": {"speaker": "김도현", "timestamp": "00:00:03", "text": "원문"}}]
    config = models.calls[0]["config"]
    assert config.response_mime_type == "application/json" and config.response_json_schema == response_json_schema()
    assert "2026-09-29T14:00:00+09:00 (화요일)" in models.calls[0]["contents"]


def test_gemini_extractor_invalid_json_fails_job():
    job_id = upload()["jobId"]
    extractor = GeminiExtractor(SimpleNamespace(models=FakeModels(text="not json")), "m")
    run(job_id, StubStt(result=fake_transcript()), extractor)
    assert "형식이 올바르지 않습니다" in client.get(f"/api/jobs/{job_id}").json()["errorMessage"]
