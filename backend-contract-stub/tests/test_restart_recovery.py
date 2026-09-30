"""(v1.9.10) 서버 재시작 복구: 업로드 작업 중 processing/queued 로 남은 것만 failed("서버 재시작으로 중단됨")로 바꾸고,
시드·완료 작업은 그대로. 복구된 작업은 음성 파일이 있으면 Retry 로 다시 처리되고, 없으면 409 로 분명히 거절된다.
서버 시작은 `with TestClient(app)`(lifespan 실행)로 흉내 낸다. DB는 conftest 의 테스트 전용 임시 파일."""
import pytest
from fastapi.testclient import TestClient

from app.config import settings
from app.db import store
from app.main import app
from app.pipeline.service import MSG_INTERRUPTED, recover_interrupted_jobs
from app.upload_storage import job_path

M4A = b"\x00\x00\x00\x18ftypM4A \x00\x00\x00\x00" + b"\x00" * 1000


@pytest.fixture(autouse=True)
def fresh(monkeypatch):
    store.reset()
    monkeypatch.setattr(settings, "fake_worker_step_seconds", 0)
    monkeypatch.setattr(settings, "fake_worker_enabled", True)


def make_upload(title: str) -> str:
    """업로드로 만든 작업(queued). 처리기는 돌리지 않는다(재시작 직전 상태를 흉내 내기 위해)."""
    _, job_id = store.create_upload(title, "2026-09-30T10:00:00+09:00", "gemini-api", 60)
    return job_id


def jobs_by_id() -> dict[str, dict]:
    return {j["id"]: j for j in store.list_jobs(None, "asc")}


def restart_server() -> None:
    with TestClient(app):  # lifespan(시작 복구) 실행
        pass


def test_restart_fails_only_interrupted_upload_jobs():
    queued = make_upload("대기 중이던 회의")
    processing = make_upload("처리 중이던 회의")
    assert store.start_if_queued(processing)
    done = make_upload("끝난 회의")
    assert store.start_if_queued(done)
    assert store.complete_if_processing(done, "[00:00:01] 화자1: 끝", [])
    seed_before = {k: v for k, v in jobs_by_id().items() if k.startswith("JOB-20260929-")}

    restart_server()

    after = jobs_by_id()
    for job_id in (queued, processing):
        assert after[job_id]["status"] == "failed"
        assert after[job_id]["errorLog"][-1].endswith(f"[ERROR] {MSG_INTERRUPTED}")
        assert store.get_job_status(job_id)["errorMessage"].endswith(MSG_INTERRUPTED)
    assert after[done]["status"] == "completed"  # 완료 작업은 그대로
    # 시드 작업(processing·queued 포함)은 한 글자도 바뀌지 않는다
    assert {k: v for k, v in after.items() if k in seed_before} == seed_before
    assert {v["status"] for v in seed_before.values()} >= {"processing", "queued"}


def test_recovery_is_idempotent():
    job_id = make_upload("한 번만 복구")
    assert recover_interrupted_jobs() == [job_id]
    assert recover_interrupted_jobs() == []  # 다시 시작해도 이미 failed 라 대상 없음


def test_recovered_job_is_retried_when_audio_file_exists():
    job_id = make_upload("음성 남아 있음")
    path = job_path(job_id, ".m4a")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(M4A)
    restart_server()

    with TestClient(app) as client:
        r = client.post(f"/api/admin/jobs/{job_id}/retry")
        assert r.status_code == 200 and r.json()["status"] == "queued" and r.json()["attempt"] == 2
    # 가짜 처리기가 다시 처리해 완료 → 음성은 지워진다
    assert store.get_job_status(job_id)["status"] == "completed"
    assert not path.exists()


def test_recovered_job_retry_without_audio_is_a_clear_409():
    job_id = make_upload("음성 없어짐")
    restart_server()

    with TestClient(app) as client:
        r = client.post(f"/api/admin/jobs/{job_id}/retry")
    assert r.status_code == 409
    assert "missing" in r.json()["detail"]  # "uploaded audio file is missing; upload it again"
    assert store.get_job_status(job_id)["status"] == "failed"  # 조용히 queued 로 바뀌지 않는다


def test_tests_still_use_temporary_db():
    assert "stub-pytest-db-" in str(store.path)
