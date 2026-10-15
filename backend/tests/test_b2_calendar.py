"""B-2: 개인별 할 일 캘린더 조회(GET /api/me/calendar). 읽기 전용, 열람 범위는 할 일과 같다."""
from datetime import date

import pytest

from tests.test_meeting_queries import make_meeting
from tests.test_notices_todos import call, set_item, team  # noqa: F401  (team 은 픽스처)
from tests.test_upload_processing import env  # noqa: F401  (env 는 픽스처)

RANGE = {"from": "2026-10-01", "to": "2026-10-31"}


def seed(env, team):
    f = env["factory"]
    staff, mgr = team["staff"], team["mgr"]
    meeting_id = make_meeting(f, mgr, status="confirmed", participants=[staff], title="주간 회의", items=[
        {"title": "내 업무 10/05", "assignee_id": staff.id, "due_date": date(2026, 10, 5), "status": "confirmed"},
        {"title": "내 업무 10/31", "assignee_id": staff.id, "due_date": date(2026, 10, 31), "status": "pending"},
        {"title": "범위 밖 11/01", "assignee_id": staff.id, "due_date": date(2026, 11, 1), "status": "pending"},
        {"title": "기한 미확정", "assignee_id": staff.id, "due_date": None, "due_undetermined": True, "status": "pending"},
        {"title": "삭제됨", "assignee_id": staff.id, "due_date": date(2026, 10, 6), "status": "deleted"},
        {"title": "종결됨", "assignee_id": staff.id, "due_date": date(2026, 10, 7), "status": "closed"},
        {"title": "남의 업무", "assignee_id": team["staff2"].id, "due_date": date(2026, 10, 8), "status": "confirmed"},
    ])
    return meeting_id


def titles(body, key="dated"):
    return [i["title"] for i in body[key]["items"]]


def test_requires_login(env):
    assert env["client"].get("/api/me/calendar", params=RANGE).status_code == 401


def test_self_scope_has_own_dated_items_and_separate_undetermined(env, team):
    seed(env, team)
    res = call(env, "GET", team["staff"], "/api/me/calendar", params=RANGE)
    assert res.status_code == 200
    body = res.json()
    assert titles(body) == ["내 업무 10/05", "종결됨", "내 업무 10/31"]  # 삭제됨·범위 밖·남의 업무는 없다
    assert [i["status"] for i in body["dated"]["items"]] == ["confirmed", "closed", "pending"]
    assert body["dated"]["items"][0]["dueDate"] == "2026-10-05"  # 날짜 문자열 그대로
    assert titles(body, "undetermined") == ["기한 미확정"] and body["scope"] == "self"
    assert body["dated"]["items"][0]["meetingTitle"] == "주간 회의"


def test_all_scope_only_for_manager_and_executive(env, team):
    seed(env, team)
    assert call(env, "GET", team["staff"], "/api/me/calendar", params={**RANGE, "scope": "all"}).status_code == 403
    for key in ("mgr", "exe"):
        res = call(env, "GET", team[key], "/api/me/calendar", params={**RANGE, "scope": "all"})
        assert res.status_code == 200
        assert "남의 업무" in titles(res.json()) and "삭제됨" not in titles(res.json())


def test_manager_self_scope_is_only_own_assignments(env, team):
    seed(env, team)
    body = call(env, "GET", team["mgr"], "/api/me/calendar", params=RANGE).json()
    assert body["dated"]["total"] == 0 and body["undetermined"]["total"] == 0


def test_staff_without_visibility_sees_nothing_and_other_tenant_is_isolated(env, team):
    seed(env, team)
    assert call(env, "GET", team["staff2"], "/api/me/calendar", params={**RANGE, "scope": "self"}).json()["dated"]["items"][0]["title"] == "남의 업무"
    body = call(env, "GET", team["outsider"], "/api/me/calendar", params={**RANGE, "scope": "all"}).json()
    assert body["dated"]["total"] == 0 and body["undetermined"]["total"] == 0


def test_on_hold_meeting_items_are_hidden_from_staff(env, team):
    meeting_id = seed(env, team)
    assert call(env, "POST", team["exe"], f"/api/meetings/{meeting_id}/hold", json={"reason": "검토"}).status_code == 200
    body = call(env, "GET", team["staff"], "/api/me/calendar", params=RANGE).json()
    assert body["dated"]["total"] == 0


@pytest.mark.parametrize("params", [{"from": "2026-10-31", "to": "2026-10-01"}, {"from": "2026-01-01", "to": "2026-12-31"}, {"from": "x", "to": "y"}])
def test_invalid_range_is_rejected(env, team, params):
    assert call(env, "GET", team["staff"], "/api/me/calendar", params=params).status_code == 422
