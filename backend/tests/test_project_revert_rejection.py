"""프로젝트 반려 되돌리기(작업 A-1 e): 처리자·사유 필수, 이력(record_change) 기록, 허용 동작 revert_rejection."""
from sqlalchemy import select

from app.models import Project
from tests.test_org_projects import call, create, env, events, team  # noqa: F401  (env·team 은 픽스처)


def rejected_project(env, team):
    project = create(env, team, "kim").json()
    done = call(env, "POST", team["head"], f"/api/projects/{project['id']}/reject", json={"reason": "예산 부족"})
    assert done.status_code == 200 and done.json()["status"] == "rejected"
    return project["id"]


def test_revert_returns_to_pending_and_records_actor_reason_and_history(env, team):
    pid = rejected_project(env, team)
    res = call(env, "POST", team["head"], f"/api/projects/{pid}/revert-rejection", json={"reason": "예산 확보됨"})
    assert res.status_code == 200
    body = res.json()
    assert body["status"] == "pending_approval"
    last = events(env, "project", pid)[-1]
    assert last.event_type == "project.rejection_reverted" and last.actor_account_id == team["head"].id
    assert last.payload["after"] == {"status": "pending_approval", "reason": "예산 확보됨"}
    assert last.payload["before"]["status"] == "rejected" and last.payload["before"]["decidedBy"] == team["head"].id
    # 반려 사건도 이력에 그대로 남아 있다(지워지지 않는다)
    assert [e.event_type for e in events(env, "project", pid)][-2:] == ["project.rejected", "project.rejection_reverted"]
    with env["factory"]() as s:
        project = s.scalar(select(Project).where(Project.id == pid))
        assert project.decided_by is None and project.decided_at is None  # 승인 대기의 결정 기록은 비어 있다


def test_reverted_project_can_be_approved_again(env, team):
    pid = rejected_project(env, team)
    assert call(env, "POST", team["head"], f"/api/projects/{pid}/revert-rejection", json={"reason": "다시 검토"}).status_code == 200
    approved = call(env, "POST", team["head"], f"/api/projects/{pid}/approve")
    assert approved.status_code == 200 and approved.json()["status"] == "active"


def test_reason_is_required(env, team):
    pid = rejected_project(env, team)
    path = f"/api/projects/{pid}/revert-rejection"
    assert call(env, "POST", team["head"], path, json={}).status_code == 422
    assert call(env, "POST", team["head"], path, json={"reason": "   "}).status_code == 422
    assert call(env, "POST", team["head"], path, json={"reason": "가" * 2001}).status_code == 422
    assert call(env, "GET", team["head"], f"/api/projects/{pid}").json()["status"] == "rejected"  # 거부된 요청은 상태를 바꾸지 않는다


def test_only_the_approver_can_revert(env, team):
    pid = rejected_project(env, team)
    path = f"/api/projects/{pid}/revert-rejection"
    for who in ("head2", "kim", "boss"):
        res = call(env, "POST", team[who], path, json={"reason": "권한 없음"})
        assert res.status_code in (403, 404), who
    assert call(env, "POST", team["outsider"], path, json={"reason": "다른 고객사"}).status_code == 404
    assert call(env, "GET", team["head"], f"/api/projects/{pid}").json()["status"] == "rejected"


def test_only_rejected_projects_can_be_reverted(env, team):
    pending = create(env, team, "kim").json()
    active = create(env, team, "head").json()
    for pid in (pending["id"], active["id"]):
        assert call(env, "POST", team["head"], f"/api/projects/{pid}/revert-rejection", json={"reason": "x"}).status_code == 409
    pid = rejected_project(env, team)
    assert call(env, "POST", team["head"], f"/api/projects/{pid}/revert-rejection", json={"reason": "첫 번째"}).status_code == 200
    # 이미 되돌린(승인 대기) 프로젝트를 다시 되돌릴 수 없다
    assert call(env, "POST", team["head"], f"/api/projects/{pid}/revert-rejection", json={"reason": "두 번째"}).status_code == 409


def test_allowed_actions_expose_revert_only_to_the_approver_on_rejected(env, team):
    pid = rejected_project(env, team)
    assert call(env, "GET", team["head"], f"/api/projects/{pid}").json()["allowedActions"] == ["revert_rejection"]
    assert "revert_rejection" not in call(env, "GET", team["kim"], f"/api/projects/{pid}").json()["allowedActions"]
    assert "revert_rejection" not in call(env, "GET", team["head2"], f"/api/projects/{pid}").json().get("allowedActions", [])
    pending = create(env, team, "kim").json()
    assert "revert_rejection" not in call(env, "GET", team["head"], f"/api/projects/{pending['id']}").json()["allowedActions"]
