"""회의 목록 계약 테스트: docs/API-CONTRACT.md "GET /meetings" (MeetingSummary[]).
처리기는 fake(네트워크 없음), 대기 0초라 업로드 응답 직후 처리가 끝나 있다."""
import pytest
from fastapi.testclient import TestClient

from app.config import settings
from app.db import store
from app.main import app

client = TestClient(app)

M4A = b"\x00\x00\x00\x18ftypM4A \x00\x00\x00\x00" + b"\x00" * 1000
SEED_ID = "mtg-2026-0925"


@pytest.fixture(autouse=True)
def fresh(monkeypatch):
    store.reset()
    monkeypatch.setattr(settings, "fake_worker_step_seconds", 0)
    monkeypatch.setattr(settings, "fake_worker_enabled", True)


def upload(title="목록 테스트 회의", started_at="2026-09-29T14:00:00+09:00"):
    return client.post(
        "/api/meetings",
        data={"title": title, "startedAt": started_at},
        files={"file": ("A.m4a", M4A, "application/octet-stream")},
    )


def meetings() -> list[dict]:
    r = client.get("/api/meetings")
    assert r.status_code == 200
    return r.json()


def test_seed_meeting_is_listed_with_null_job_status():
    rows = meetings()
    assert [m["id"] for m in rows] == [SEED_ID]
    # camelCase 필드 4개만, 시드 회의는 업로드 작업이 없어 jobStatus가 null
    assert rows[0] == {"id": SEED_ID, "title": rows[0]["title"], "startedAt": "2026-09-25T14:00:00+09:00", "jobStatus": None}


def test_uploaded_meeting_appears_with_completed_status():
    receipt = upload().json()
    row = next(m for m in meetings() if m["id"] == receipt["meetingId"])
    assert row == {
        "id": receipt["meetingId"], "title": "목록 테스트 회의",
        "startedAt": "2026-09-29T14:00:00+09:00", "jobStatus": "completed",
    }


def test_uploaded_meeting_shows_queued_before_processing(monkeypatch):
    monkeypatch.setattr(settings, "fake_worker_enabled", False)  # 처리기를 꺼서 '접수 직후' 상태를 붙잡는다
    receipt = upload().json()
    row = next(m for m in meetings() if m["id"] == receipt["meetingId"])
    assert row["jobStatus"] == "queued"


def test_job_status_comes_from_the_latest_job():
    receipt = upload().json()
    # 같은 회의에 더 나중 작업을 직접 넣는다 (API로는 한 회의에 작업이 하나뿐이라 저장소에 직접 기록)
    store._db.execute(
        "INSERT INTO jobs (id, meeting_title, audio_seconds, status, attempt, worker, error_log, meeting_id, engine)"
        " VALUES ('JOB-LATER', 't', 1, 'failed', 1, 'null', '[]', ?, 'gemini-api')",
        (receipt["meetingId"],),
    )
    store._db.commit()
    row = next(m for m in meetings() if m["id"] == receipt["meetingId"])
    assert row["jobStatus"] == "failed"


def test_sorted_by_started_at_desc_then_id_desc():
    older = upload(started_at="2026-09-28T09:00:00+09:00").json()["meetingId"]
    # 01:00Z = 10:00 KST → 아래 09:00+09:00보다 늦다 (문자열이 아니라 시각으로 비교하는지 확인)
    utc = upload(started_at="2026-09-30T01:00:00Z").json()["meetingId"]
    kst = upload(started_at="2026-09-30T09:00:00+09:00").json()["meetingId"]
    same_a = upload(started_at="2026-09-29T14:00:00+09:00").json()["meetingId"]
    same_b = upload(started_at="2026-09-29T14:00:00+09:00").json()["meetingId"]
    assert same_a < same_b  # 발급 순서대로 id가 커진다
    assert [m["id"] for m in meetings()] == [utc, kst, same_b, same_a, older, SEED_ID]


def test_upload_post_on_same_path_still_returns_202():
    r = upload()
    assert r.status_code == 202
    assert set(r.json()) == {"meetingId", "jobId", "status"} and r.json()["status"] == "queued"
    # 단건 조회도 그대로
    assert client.get(f"/api/meetings/{r.json()['meetingId']}").status_code == 200
    assert client.get(f"/api/meetings/{SEED_ID}").status_code == 200
