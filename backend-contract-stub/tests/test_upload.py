"""음성 등록 계약 테스트: docs/API-CONTRACT.md "음성 등록 API" 절이 지켜지는지 확인합니다.
실제 음성 대신 '앞부분(시그니처)만 진짜처럼 만든 가짜 바이트'를 씁니다. 스텁은 음성을 해석하지 않기 때문입니다."""
import pytest
from fastapi.testclient import TestClient

from app.config import settings
from app.db import store
from app.fake_worker import run_fake_worker
from app.main import app

client = TestClient(app)

# 종류별 최소 가짜 파일 (앞부분만 진짜 모양)
MP3 = b"ID3\x04\x00\x00\x00\x00\x00\x00" + b"\x00" * 1000
M4A = b"\x00\x00\x00\x18ftypM4A \x00\x00\x00\x00" + b"\x00" * 1000
WAV = b"RIFF\x00\x00\x00\x00WAVEfmt " + b"\x00" * 1000
FORM = {"title": "테스트 회의", "startedAt": "2026-09-29T14:00:00+09:00"}


@pytest.fixture(autouse=True)
def fresh(monkeypatch):
    store.reset()
    monkeypatch.setattr(settings, "fake_worker_step_seconds", 0)  # 테스트에서는 기다리지 않는다
    monkeypatch.setattr(settings, "fake_worker_enabled", True)


def upload(name="A.m4a", content=M4A, data=None, **override):
    form = {**FORM, **(data or {})}
    form.update(override)
    return client.post("/api/meetings", data=form, files={"file": (name, content, "application/octet-stream")})


def job_count() -> int:
    return len(client.get("/api/admin/jobs").json())


@pytest.mark.parametrize("name,content", [("A.m4a", M4A), ("B.MP3", MP3), ("C.wav", WAV)])
def test_upload_accepts_three_formats_and_flows_to_completed(name, content):
    r = upload(name, content)
    assert r.status_code == 202
    receipt = r.json()
    assert set(receipt) == {"meetingId", "jobId", "status"} and receipt["status"] == "queued"
    # 테스트에서는 대기 0초라 응답 직후 이미 처리가 끝나 있다
    job = client.get(f"/api/jobs/{receipt['jobId']}").json()
    assert job["status"] == "completed" and job["meetingId"] == receipt["meetingId"] and job["errorMessage"] == ""
    assert job_count() == 10  # 시드 9건 + 새 작업 1건 (/admin 큐에 보인다)


def test_completed_upload_has_transcript_and_two_action_items():
    receipt = upload().json()
    meeting = client.get(f"/api/meetings/{receipt['meetingId']}").json()
    assert meeting["title"] == "테스트 회의" and "김도현" in meeting["transcriptText"]
    items = client.get(f"/api/meetings/{receipt['meetingId']}/action-items").json()
    got = {(i["assignee"], i["dueDate"]) for i in items}
    assert got == {("이서연", "2026-10-09"), ("정민수", "2026-10-01")}  # 테스트 가이드 A파일 정답표
    assert all(i["status"] == "open" and i["quote"]["text"] for i in items)


def test_seed_meeting_has_null_transcript():
    assert client.get("/api/meetings/mtg-2026-0925").json()["transcriptText"] is None


def test_upload_returns_before_processing_and_job_is_visible_as_queued(monkeypatch):
    monkeypatch.setattr(settings, "fake_worker_enabled", False)  # 처리기를 꺼서 '접수 직후' 상태를 붙잡는다
    receipt = upload().json()
    assert client.get(f"/api/jobs/{receipt['jobId']}").json()["status"] == "queued"
    assert client.get(f"/api/meetings/{receipt['meetingId']}").json()["transcriptText"] is None
    assert client.get(f"/api/meetings/{receipt['meetingId']}/action-items").json() == []
    assert any(j["id"] == receipt["jobId"] and j["status"] == "queued" for j in client.get("/api/admin/jobs").json())


@pytest.mark.parametrize("override", [
    {"title": ""}, {"title": "   "}, {"title": "가" * 201},
    {"startedAt": "어제 오후"}, {"engine": "whisper-cloud"},
])
def test_bad_fields_are_400_and_nothing_is_saved(override):
    assert upload(**override).status_code == 400
    assert job_count() == 9


def test_missing_fields_are_400():
    assert client.post("/api/meetings", data={"startedAt": FORM["startedAt"]}, files={"file": ("A.m4a", M4A)}).status_code == 400
    assert client.post("/api/meetings", data={"title": "x"}, files={"file": ("A.m4a", M4A)}).status_code == 400
    assert client.post("/api/meetings", data=FORM).status_code == 400  # 파일 없음
    assert upload(content=b"").status_code == 400  # 빈 파일
    assert job_count() == 9


@pytest.mark.parametrize("name,content", [
    ("notes.txt", b"hello"),                       # 허용하지 않는 확장자
    ("fake.mp3", "메모장 파일입니다".encode()),     # 이름만 mp3 (T4의 D파일)
    ("wrong.mp3", M4A),                             # 확장자와 실제 종류가 다름
    ("noext", MP3),                                 # 확장자 없음
])
def test_wrong_type_is_415_and_nothing_is_saved(name, content):
    assert upload(name, content).status_code == 415
    assert job_count() == 9


def test_too_large_is_413(monkeypatch):
    monkeypatch.setattr(settings, "max_upload_mb", 1)
    assert upload("B.mp3", MP3 + b"\x00" * (1024 * 1024)).status_code == 413
    assert upload("B.mp3", MP3).status_code == 202  # 한도 이하는 통과 (내용이 MP3이므로 이름도 .mp3)
    assert job_count() == 10


def test_gpu_guard_409_then_force_or_switch_engine(monkeypatch):
    monkeypatch.setattr(settings, "gpu_guard_threshold_minutes", 1)
    big = MP3 + b"\x00" * 2_000_000  # 약 125초로 어림 -> 3분 (내용이 MP3이므로 파일 이름도 B.mp3로 보낸다)
    r = upload("B.mp3", big, engine="faster-whisper")
    assert r.status_code == 409 and r.json()["detail"] == {"code": "gpu_guard", "expectedMinutes": 3}
    assert job_count() == 9  # 경고 단계에서는 작업이 만들어지지 않는다
    assert upload("B.mp3", big, engine="faster-whisper", forceLocal="true").status_code == 202  # 알고도 로컬로
    assert upload("B.mp3", big, engine="gemini-api").status_code == 202  # Gemini로 전환
    assert job_count() == 11


def test_short_file_on_local_engine_passes_default_guard():
    assert upload(engine="faster-whisper").status_code == 202


def test_job_status_404_and_seed_job_has_empty_meeting_id():
    assert client.get("/api/jobs/NOPE").status_code == 404
    seed = client.get("/api/jobs/JOB-20260929-008").json()
    assert seed["meetingId"] == "" and seed["stage"] in ("STT", "LLM", None)


def test_kill_during_processing_stops_worker_and_saves_no_result(monkeypatch):
    monkeypatch.setattr(settings, "fake_worker_enabled", False)
    receipt = upload().json()
    job_id, meeting_id = receipt["jobId"], receipt["meetingId"]
    assert store.start_if_queued(job_id) is True          # 처리기가 시작했다고 가정
    assert store.start_if_queued(job_id) is False          # 중복 시작은 거절
    assert client.post(f"/api/admin/jobs/{job_id}/kill").status_code == 200
    assert store.complete_if_processing(job_id, "전사", []) is False  # 이미 취소됨 -> 결과를 저장하지 않는다
    assert client.get(f"/api/meetings/{meeting_id}").json()["transcriptText"] is None
    assert client.get(f"/api/jobs/{job_id}").json()["status"] == "failed"
    assert client.get(f"/api/jobs/{job_id}").json()["errorMessage"]  # 사람이 읽을 실패 이유


def test_retry_of_uploaded_job_runs_worker_again(monkeypatch):
    monkeypatch.setattr(settings, "fake_worker_enabled", False)
    receipt = upload().json()
    job_id, meeting_id = receipt["jobId"], receipt["meetingId"]
    store.start_if_queued(job_id)
    client.post(f"/api/admin/jobs/{job_id}/kill")
    assert client.post(f"/api/admin/jobs/{job_id}/retry").json()["attempt"] == 2  # 처리기는 꺼져 있어 queued 로 남는다
    monkeypatch.setattr(settings, "fake_worker_enabled", True)
    run_fake_worker(job_id)  # 켜고 다시 돌리면 끝까지 간다
    assert client.get(f"/api/jobs/{job_id}").json()["status"] == "completed"
    assert len(client.get(f"/api/meetings/{meeting_id}/action-items").json()) == 2  # 중복 저장 없음


def test_stage_moves_from_stt_to_llm(monkeypatch):
    monkeypatch.setattr(settings, "fake_worker_enabled", False)
    job_id = upload().json()["jobId"]
    store.start_if_queued(job_id)
    assert client.get(f"/api/jobs/{job_id}").json()["stage"] == "STT"
    assert store.set_stage_if_processing(job_id, "LLM") is True
    assert client.get(f"/api/jobs/{job_id}").json()["stage"] == "LLM"


# ---------------- 즉시 변환 (작업 88-1, docs/contracts/process-now-v1.json) ----------------
def test_process_now_queued_job_returns_contract_shape(monkeypatch):
    monkeypatch.setattr(settings, "fake_worker_enabled", False)  # 작업이 queued 로 남는다
    first = upload("A.m4a").json()
    second = upload("B.m4a").json()
    r = client.post(f"/api/meetings/{second['meetingId']}/process-now")
    assert r.status_code == 200
    body = r.json()
    assert set(body) == {
        "meetingId", "jobId", "status", "queueAhead", "prioritized", "alreadyPrioritized", "waitingForPrior", "message", "workerAlive",
    }
    assert body["jobId"] == second["jobId"] and body["status"] == "queued" and body["queueAhead"] == 1
    assert body["waitingForPrior"] is True and body["message"] == "시스템의 선행 작업완료 후 처리하겠습니다."
    head = client.post(f"/api/meetings/{first['meetingId']}/process-now").json()
    assert head["queueAhead"] == 0 and head["waitingForPrior"] is False and head["message"] == "곧 변환을 시작합니다."


def test_process_now_conflicts_and_not_found(monkeypatch):
    done = upload("A.m4a").json()  # 가짜 처리기가 바로 완료까지 흘려보낸다(step 0)
    assert client.get(f"/api/jobs/{done['jobId']}").json()["status"] == "completed"
    r = client.post(f"/api/meetings/{done['meetingId']}/process-now")
    assert r.status_code == 409 and "완료" in r.json()["detail"]
    assert client.post("/api/meetings/mtg-none/process-now").status_code == 404


# ---------------- 회의 일시 (작업 88-4) ----------------
def test_future_started_at_is_rejected_with_422_and_nothing_is_saved():
    from datetime import datetime, timedelta, timezone

    before = len(client.get("/api/meetings").json())
    future = (datetime.now(timezone.utc) + timedelta(days=2)).isoformat(timespec="seconds")
    r = upload(startedAt=future)
    assert r.status_code == 422 and "미래" in r.json()["detail"]
    assert len(client.get("/api/meetings").json()) == before


def test_registered_at_is_recorded_by_server_and_returned_in_list_and_detail():
    r = upload(data={"registeredAt": "2020-01-01T00:00:00+00:00"})  # 클라이언트가 보내도 무시된다
    assert r.status_code == 202
    meeting_id = r.json()["meetingId"]
    detail = client.get(f"/api/meetings/{meeting_id}").json()
    listed = next(m for m in client.get("/api/meetings").json() if m["id"] == meeting_id)
    assert detail["registeredAt"] == listed["registeredAt"] and not detail["registeredAt"].startswith("2020")
    assert detail["startedAt"] == FORM["startedAt"]  # 회의 일시와 별개로 보관


# ---------------- 엔진 시험 모드 (작업 91-1, docs/contracts/engine-test-v1.json) ----------------
def test_engine_test_endpoints_are_rejected_like_a_server_with_test_mode_off():
    expected = "엔진 시험 모드가 아니어서 사용할 수 없습니다"
    for call in (
        lambda: client.get("/api/admin/engine-test/remote"),
        lambda: client.put("/api/admin/engine-test/remote", json={"baseUrl": "http://x.test/v1", "model": "m", "timeoutSeconds": 30}),
        lambda: client.post("/api/admin/engine-test/remote/load-test"),
    ):
        r = call()
        assert r.status_code == 403 and r.json()["detail"] == expected
