"""API 계약 테스트: docs/API-CONTRACT.md 의 표가 실제로 지켜지는지 확인합니다."""
import pytest
from fastapi.testclient import TestClient

from app.db import store
from app.main import app

client = TestClient(app)
MEETING = "mtg-2026-0925"


@pytest.fixture(autouse=True)
def fresh_db():
    store.reset()  # 테스트마다 시드 데이터로 초기화


def test_health():
    assert client.get("/health").json() == {"status": "ok"}


def test_cors_allows_both_localhost_origins():
    for origin in ("http://localhost:3000", "http://127.0.0.1:3000"):
        r = client.get("/health", headers={"Origin": origin})
        assert r.headers["access-control-allow-origin"] == origin
    r = client.get("/health", headers={"Origin": "http://evil.example"})
    assert "access-control-allow-origin" not in r.headers


def test_meeting_is_camel_case_and_404():
    body = client.get(f"/api/meetings/{MEETING}").json()
    assert "startedAt" in body and "started_at" not in body
    assert client.get("/api/meetings/nope").status_code == 404


def test_action_items_shape_and_sort():
    items = client.get(f"/api/meetings/{MEETING}/action-items").json()
    assert len(items) == 6 and "dueDate" in items[0] and items[0]["quote"]["speaker"] is not None
    asc = client.get(f"/api/meetings/{MEETING}/action-items?sortKey=dueDate&direction=asc").json()
    desc = client.get(f"/api/meetings/{MEETING}/action-items?sortKey=dueDate&direction=desc").json()
    dates = [i["dueDate"] for i in asc]
    assert dates == sorted(dates) and [i["dueDate"] for i in desc] == sorted(dates, reverse=True)


@pytest.mark.parametrize("url", [
    f"/api/meetings/{MEETING}/action-items?sortKey=due_date",       # snake_case 는 허용 목록에 없음
    f"/api/meetings/{MEETING}/action-items?sortKey=id;DROP TABLE jobs",
    "/api/admin/jobs?sortKey=password",
])
def test_bad_sort_key_is_400(url):
    assert client.get(url).status_code == 400


def test_bad_direction_is_422():
    assert client.get("/api/admin/jobs?sortKey=id&direction=sideways").status_code == 422


def test_delete_action_item_204_then_404():
    assert client.delete("/api/action-items/AI-001").status_code == 204
    assert client.delete("/api/action-items/AI-001").status_code == 404
    assert len(client.get(f"/api/meetings/{MEETING}/action-items").json()) == 5


def test_jobs_shape_and_metrics_match_mock():
    jobs = client.get("/api/admin/jobs").json()
    assert len(jobs) == 9 and "meetingTitle" in jobs[0] and "errorLog" in jobs[0]
    by = lambda s: len([j for j in jobs if j["status"] == s])
    assert by("queued") + by("processing") == 3 and by("failed") == 2


def test_kill_flow_and_409_race():
    processing = next(j for j in client.get("/api/admin/jobs").json() if j["status"] == "processing")
    r = client.post(f"/api/admin/jobs/{processing['id']}/kill")
    assert r.status_code == 200 and r.json()["status"] == "failed" and r.json()["errorLog"]
    # 같은 작업을 다시 kill → 이미 끝났으므로 409 (경쟁 상태)
    assert client.post(f"/api/admin/jobs/{processing['id']}/kill").status_code == 409
    assert client.post("/api/admin/jobs/NOPE/kill").status_code == 404


def test_retry_flow_and_409():
    failed = next(j for j in client.get("/api/admin/jobs").json() if j["status"] == "failed")
    r = client.post(f"/api/admin/jobs/{failed['id']}/retry")
    body = r.json()
    assert r.status_code == 200 and body["status"] == "queued" and body["attempt"] == failed["attempt"] + 1
    assert body["worker"] is None and body["errorLog"] == []
    assert client.post(f"/api/admin/jobs/{failed['id']}/retry").status_code == 409
    assert client.post("/api/admin/jobs/NOPE/retry").status_code == 404
