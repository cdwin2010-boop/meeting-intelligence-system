"""작업 66-3c: 부서장의 프로젝트 총괄 기능(회의록 총괄 권한 전체), 총괄 변경 권한, 서버 문구 "사용권한" 정리."""
import pytest
from sqlalchemy import delete

from app.models import Account, Department, Project, ProjectMember
from app.services import org
from tests.test_meeting_classification import project  # noqa: F401  (project 는 픽스처)
from tests.test_org_projects import call, create, team  # noqa: F401  (team 은 픽스처)
from tests.test_project_lead import DENIED, REASON, change, fresh, powers, roles, run_all
from tests.test_upload_processing import env  # noqa: F401  (env 는 픽스처)


@pytest.fixture
def hproject(env, team):
    """개발팀 등록 활성 프로젝트. 총괄은 지시자(boss), 부서장 head 는 참여자가 아니다(별도 총괄이 있어도 부서장 권한이 따로 적용되는지 본다)."""
    pid = create(env, team, "head", members=[team["kim"]]).json()["id"]
    assert change(env, team["head"], pid, team["boss"]).status_code == 200
    with env["factory"]() as s:
        s.execute(delete(ProjectMember).where(ProjectMember.project_id == pid, ProjectMember.account_id == team["head"].id))
        s.commit()
    assert team["head"].id not in roles(env, pid)
    return pid


# ================= 회의록 총괄 권한 =================
def test_dept_head_has_all_lead_powers_without_membership(env, team, hproject):
    codes = run_all(env, team, hproject, "head")
    for name in ("confirm_meeting", "confirm_item", "edit_item", "hold", "resume", "end", "delete", "minutes", "speakers", "upload_update", "resolve_request"):
        assert codes[name] == 200, (name, codes[name])
    assert codes["add_item"] == 201
    assert codes["reprocess"] not in DENIED, codes  # 실패한 회의록만 가능해 409 일 수 있다


def test_dept_head_can_reprocess_failed_meeting(env, team, hproject):
    mid, _ = fresh(env, team, hproject, status="failed")
    assert call(env, "POST", team["head"], f"/api/meetings/{mid}/reprocess").status_code not in DENIED


@pytest.mark.parametrize("who", ["head2", "kim", "mgr", "sales", "outsider"])
def test_others_have_no_dept_head_powers(env, team, hproject, who):
    """다른 부서 부서장, 같은 부서 부서원(staff·manager), 다른 부서 담당자, 다른 고객사."""
    codes = run_all(env, team, hproject, who)
    for name, code in codes.items():
        if name == "resolve_request" and who in ("head2", "mgr"):
            continue  # 관리자 이상이면 열람 가능한 요청을 해결하는 기존 규칙(총괄 판정과 무관)
        assert code in DENIED, f"{who} {name}: {code}"


def test_demoted_head_loses_powers(env, team, hproject):
    with env["factory"]() as s:
        s.get(Account, team["head"].id).rank = "staff"
        s.commit()
    assert all(code in DENIED for code in run_all(env, team, hproject, "head").values())


def test_pending_rejected_and_unlinked_give_no_powers(env, team, hproject):
    pending = create(env, team, "kim", name="대기").json()["id"]
    assert all(c in DENIED for c in powers(run_all(env, team, pending, "head")))
    rejected = create(env, team, "kim", name="반려").json()["id"]
    assert call(env, "POST", team["head"], f"/api/projects/{rejected}/reject", json=REASON).status_code == 200
    assert all(c in DENIED for c in powers(run_all(env, team, rejected, "head")))
    assert all(c in DENIED for c in powers(run_all(env, team, hproject, "head", linked=False)))


def test_power_moves_with_department_head(env, team, hproject):
    mid, _ = fresh(env, team, hproject)
    with env["factory"]() as s:
        org.set_head(s, s.get(Department, team["dept"]["dev"]), s.get(Account, team["mgr"].id))
        s.commit()
    assert call(env, "POST", team["head"], f"/api/meetings/{mid}/confirm").status_code in DENIED  # 이전 부서장은 잃는다
    assert call(env, "POST", team["mgr"], f"/api/meetings/{mid}/confirm").status_code == 200  # 새 부서장이 얻는다


def test_existing_rules_unchanged_with_dept_head(env, team, project):
    # 지시자 권한, 등록자 경로, 다른 부서 관리자 거부는 그대로
    mid, _ = fresh(env, team, project, registrant="kim")
    assert call(env, "POST", team["head2"], f"/api/meetings/{mid}/confirm").status_code in DENIED
    assert call(env, "POST", team["boss"], f"/api/meetings/{mid}/confirm").status_code == 200
    mid, _ = fresh(env, team, project, registrant="head2")
    assert call(env, "POST", team["head2"], f"/api/meetings/{mid}/confirm").status_code == 200


# ================= 총괄 변경 =================
def test_change_lead_by_registering_dept_head(env, team, hproject):
    assert change(env, team["head"], hproject, team["mgr"]).status_code == 200
    assert roles(env, hproject)[team["mgr"].id] == "lead"
    with env["factory"]() as s:
        assert s.get(Project, hproject).lead_account_id == team["mgr"].id


def test_change_lead_denied_for_others_and_validated(env, team, hproject):
    added = call(env, "POST", team["boss"], f"/api/projects/{hproject}/members", json={"accountId": team["mgr"].id, "role": "member"})
    assert added.status_code in (200, 201)
    assert change(env, team["head2"], hproject, team["mgr"]).status_code == 403  # 다른 부서 부서장
    assert change(env, team["mgr"], hproject, team["mgr"]).status_code == 403  # 일반 참여자
    assert change(env, team["kim"], hproject, team["mgr"]).status_code == 403
    assert change(env, team["head"], hproject, team["mgr"], reason=None).status_code == 422
    assert change(env, team["head"], hproject, team["kim"]).status_code == 409  # 새 총괄 사용권한 미달
    # 사용권한이 내려간 전 부서장은 거부
    with env["factory"]() as s:
        s.get(Account, team["head"].id).rank = "staff"
        s.commit()
    assert change(env, team["head"], hproject, team["mgr"]).status_code == 403


def test_change_lead_existing_authorities_still_work(env, team, project):
    assert change(env, team["head"], project, team["mgr"]).status_code == 200  # 현재 총괄
    assert change(env, team["mgr"], project, team["head"]).status_code == 200  # 새 총괄이 다시 바꿈
    assert change(env, team["boss"], project, team["mgr"]).status_code == 200  # 지시자


# ================= 허용 동작 =================
def test_allowed_actions_for_dept_head(env, team, hproject):
    detail = call(env, "GET", team["head"], f"/api/projects/{hproject}").json()
    assert detail["allowedActions"] == ["change_lead"]  # 참여자 관리(manage_members)는 총괄만이라 그대로
    mid, _ = fresh(env, team, hproject)
    got = call(env, "GET", team["head"], f"/api/meetings/{mid}").json()
    assert {"confirm_meeting", "edit_minutes", "upload_update", "add_item", "edit_speakers", "hold_meeting", "end_meeting", "delete_meeting"} <= set(got["allowedActions"])
    assert "confirm_item" in got["actionItems"][0]["allowedActions"] and "delete_item" in got["actionItems"][0]["allowedActions"]
    # 실제 API 와 일치
    assert call(env, "POST", team["head"], f"/api/meetings/{mid}/confirm").status_code == 200
    assert call(env, "POST", team["head"], f"/api/projects/{hproject}/members", json={"accountId": team["mgr"].id}).status_code == 403
    assert change(env, team["head"], hproject, team["mgr"]).status_code == 200
    assert call(env, "GET", team["head2"], f"/api/projects/{hproject}").json()["allowedActions"] == []


# ================= 문구 =================
def test_wording_uses_permission_term(env, team, hproject):
    low = change(env, team["boss"], hproject, team["kim"])
    assert low.status_code == 409 and "사용권한" in low.json()["detail"] and "직급" not in low.json()["detail"]
    denied = change(env, team["head2"], hproject, team["mgr"])
    assert denied.status_code == 403 and "부서장" in denied.json()["detail"]
    role = call(env, "POST", team["boss"], f"/api/projects/{hproject}/members", json={"accountId": team["kim"].id, "role": "manager"})
    assert role.status_code == 409 and "사용권한" in role.json()["detail"] and "직급" not in role.json()["detail"]
    with env["factory"]() as s:
        with pytest.raises(org.OrgError, match="사용권한") as err:
            org.set_head(s, s.get(Department, team["dept"]["dev"]), s.get(Account, team["kim"].id))
        assert "직급" not in str(err.value)
