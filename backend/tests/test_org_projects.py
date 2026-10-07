"""작업 66-2: 조직(부서·소속)과 프로젝트(등록·승인·반려·참여자 관리·조회·후보), 관리 스크립트, 마이그레이션."""
import importlib.util
import json
from pathlib import Path

import pytest
from alembic import command
from sqlalchemy import select, text

from app.db import make_engine
from app.models import Account, AccountDepartment, Department, Event, Project, ProjectMember
from app.services import org
from tests.test_upload_processing import _alembic, add_account, auth, env  # noqa: F401  (env 는 픽스처)

_BEFORE_ORG = "b4d8f2a6c901"  # 조직·프로젝트 마이그레이션의 down_revision
_SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "manage_org.py"
SENSITIVE = ("email", "loginId", "login_id", "password", "@example.com")


def _load_script():
    spec = importlib.util.spec_from_file_location("manage_org_script", _SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def team(env):
    """고객사A: 임원 그룹(boss·exec2), 개발팀(head 부서장, mgr·kim 부서원), 영업팀(head2 부서장, sales). 소속 없음 loner. 고객사B: outsider."""
    f = env["factory"]
    t = {
        "boss": add_account(f, "고객사A", "boss", "executive", name="최임원"),
        "head": add_account(f, "고객사A", "head", "manager", name="박개발장"),
        "mgr": add_account(f, "고객사A", "mgr", "manager", name="정관리"),
        "kim": add_account(f, "고객사A", "kim", "staff", name="김담당"),
        "head2": add_account(f, "고객사A", "head2", "manager", name="이영업장"),
        "sales": add_account(f, "고객사A", "sales", "staff", name="오영업"),
        "loner": add_account(f, "고객사A", "loner", "staff", name="무소속"),
        "outsider": add_account(f, "고객사B", "out", "executive", name="외부"),
    }
    with f() as s:
        tid = s.get(Account, t["head"].id).tenant_id
        bid = s.get(Account, t["outsider"].id).tenant_id
        exe = org.create_department(s, tenant_id=tid, name="임원", kind="executive")
        dev = org.create_department(s, tenant_id=tid, name="개발팀")
        sales = org.create_department(s, tenant_id=tid, name="영업팀")
        other = org.create_department(s, tenant_id=bid, name="외부팀")
        org.add_member(s, exe, s.get(Account, t["boss"].id))
        org.set_head(s, dev, s.get(Account, t["head"].id))
        org.add_member(s, dev, s.get(Account, t["mgr"].id))
        org.add_member(s, dev, s.get(Account, t["kim"].id))
        org.set_head(s, sales, s.get(Account, t["head2"].id))
        org.add_member(s, sales, s.get(Account, t["sales"].id))
        org.set_head(s, other, s.get(Account, t["outsider"].id))
        s.commit()
        t["dept"] = {"exe": exe.id, "dev": dev.id, "sales": sales.id, "other": other.id}
        t["tenant"] = tid
    return t


def call(env, method, account, path, **kwargs):
    return env["client"].request(method, path, headers=auth(account), **kwargs)


def create(env, team, who, *, dept="dev", name="신제품 프로젝트", members=None, **extra):
    body = {"name": name, "departmentId": team["dept"][dept] if isinstance(dept, str) else dept, "memberIds": [m.id for m in (members or [])], **extra}
    return call(env, "POST", team[who], "/api/projects", json=body)


def events(env, entity_type, entity_id=None):
    with env["factory"]() as s:
        stmt = select(Event).where(Event.entity_type == entity_type).order_by(Event.id)
        if entity_id is not None:
            stmt = stmt.where(Event.entity_id == entity_id)
        return list(s.scalars(stmt))


# ================= 조직 =================
def test_department_hierarchy_duplicate_name_and_parent_scope(env, team):
    with env["factory"]() as s:
        child = org.create_department(s, tenant_id=team["tenant"], name="개발1팀", parent_id=team["dept"]["dev"])
        assert child.parent_id == team["dept"]["dev"]
        with pytest.raises(org.OrgError, match="이미 있"):
            org.create_department(s, tenant_id=team["tenant"], name="개발팀")
        # 다른 고객사의 같은 이름은 가능, 다른 고객사의 부서를 상위로는 불가
        org.create_department(s, tenant_id=s.get(Account, team["outsider"].id).tenant_id, name="개발팀")
        with pytest.raises(org.OrgError, match="상위 부서"):
            org.create_department(s, tenant_id=team["tenant"], name="이상한팀", parent_id=team["dept"]["other"])
        with pytest.raises(org.OrgError):
            org.create_department(s, tenant_id=team["tenant"], name="   ")


def test_one_head_per_department_and_rank_rule(env, team):
    with env["factory"]() as s:
        dev = s.get(Department, team["dept"]["dev"])
        assert org.set_head(s, dev, s.get(Account, team["mgr"].id)) is True
        s.commit()
        roles = {a.login_id: r for a, r in org.members_of(s, dev.id)}
        assert roles["mgr"] == "head" and roles["head"] == "member" and list(roles.values()).count("head") == 1
        assert org.set_head(s, dev, s.get(Account, team["mgr"].id)) is False  # 이미 부서장
        with pytest.raises(org.OrgError, match="관리자 이상"):
            org.set_head(s, dev, s.get(Account, team["kim"].id))
        with pytest.raises(org.OrgError, match="같은 고객사"):
            org.set_head(s, dev, s.get(Account, team["outsider"].id))
        inactive = s.get(Account, team["sales"].id)
        inactive.is_active = False
        with pytest.raises(org.OrgError, match="비활성"):
            org.add_member(s, dev, inactive)
        with pytest.raises(org.OrgError, match="부서장은"):
            org.remove_member(s, dev, s.get(Account, team["mgr"].id))


def test_concurrent_membership_is_multiple_and_my_org(env, team):
    with env["factory"]() as s:
        org.add_member(s, s.get(Department, team["dept"]["sales"]), s.get(Account, team["kim"].id))  # 겸직
        s.commit()
    body = call(env, "GET", team["kim"], "/api/me/org").json()
    assert sorted((d["name"], d["role"]) for d in body) == [("개발팀", "member"), ("영업팀", "member")]
    assert call(env, "GET", team["loner"], "/api/me/org").json() == []
    assert call(env, "GET", team["head"], "/api/me/org").json()[0]["role"] == "head"


def test_departments_api_scope_and_no_sensitive_values(env, team):
    response = call(env, "GET", team["kim"], "/api/departments")
    assert response.status_code == 200
    raw = response.text
    assert not any(word in raw for word in SENSITIVE)
    by_name = {d["name"]: d for d in response.json()}
    assert set(by_name) == {"임원", "개발팀", "영업팀"}  # 다른 고객사 부서는 보이지 않음
    dev = by_name["개발팀"]
    assert dev["head"]["name"] == "박개발장" and dev["head"]["role"] == "head" and dev["kind"] == "normal"
    assert [m["role"] for m in dev["members"]][0] == "head" and len(dev["members"]) == 3
    assert by_name["임원"]["kind"] == "executive"
    assert call(env, "GET", team["kim"], "/api/departments").status_code == 200
    assert env["client"].get("/api/departments").status_code == 401


def test_org_changes_recorded_in_history(env, team):
    kinds = {e.event_type for e in events(env, "department")}
    assert kinds == {"department.updated"}
    with env["factory"]() as s:
        dev = s.get(Department, team["dept"]["dev"])
        before = len(events(env, "department", dev.id))
        org.set_head(s, dev, s.get(Account, team["mgr"].id), actor_id=None)
        s.commit()
    assert len(events(env, "department", team["dept"]["dev"])) == before + 1
    last = events(env, "department", team["dept"]["dev"])[-1]
    assert last.actor_account_id is None and last.payload["after"]["action"] == "head_set"


def test_manage_script_dry_run_then_apply(env, team, capsys):
    script = _load_script()
    f = env["factory"]
    base = ["--tenant", "고객사A"]
    # dry-run: 저장하지 않는다
    assert script.main(["create-dept", *base, "--name", "기획팀", "--parent", "개발팀"], session_factory=f) == 0
    assert "변경 예정" in capsys.readouterr().out
    with f() as s:
        assert s.scalar(select(Department).where(Department.name == "기획팀")) is None
    # --apply 만 저장
    assert script.main(["create-dept", *base, "--name", "기획팀", "--parent", "개발팀", "--apply"], session_factory=f) == 0
    assert script.main(["add-member", *base, "--dept", "기획팀", "--login-id", "kim"], session_factory=f) == 0
    with f() as s:
        plan = s.scalar(select(Department).where(Department.name == "기획팀"))
        assert plan is not None and plan.parent_id == team["dept"]["dev"]
        assert org.membership(s, team["kim"].id, plan.id) is None  # add-member 는 dry-run 이라 저장 안 됨
    assert script.main(["add-member", *base, "--dept", "기획팀", "--login-id", "kim", "--apply"], session_factory=f) == 0
    assert script.main(["set-head", *base, "--dept", "기획팀", "--login-id", "mgr", "--apply"], session_factory=f) == 0
    capsys.readouterr()
    assert script.main(["list", *base], session_factory=f) == 0
    listing = capsys.readouterr().out
    assert "기획팀" in listing and "mgr(부서장)" in listing and "kim(부서원)" in listing
    # 오류: 없는 대상, 직급 미달, 다른 고객사 계정은 멈춘다
    assert script.main(["set-head", *base, "--dept", "없는팀", "--login-id", "mgr", "--apply"], session_factory=f) == 1
    assert script.main(["set-head", *base, "--dept", "기획팀", "--login-id", "kim", "--apply"], session_factory=f) == 1
    assert script.main(["add-member", *base, "--dept", "기획팀", "--login-id", "out", "--apply"], session_factory=f) == 1
    assert script.main(["list", "--tenant", "없는 고객사"], session_factory=f) == 1
    assert script.main(["remove-member", *base, "--dept", "기획팀", "--login-id", "kim", "--apply"], session_factory=f) == 0
    with f() as s:
        assert org.membership(s, team["kim"].id, plan.id) is None
    assert all(e.actor_account_id is None for e in events(env, "department"))  # 실행자는 시스템


# ================= 프로젝트 등록 =================
def test_head_registers_active_project_with_lead(env, team):
    response = create(env, team, "head", members=[team["kim"], team["boss"]], description="  설명  ")
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["status"] == "active" and body["lead"]["id"] == team["head"].id and body["description"] == "설명"
    roles = {m["accountId"]: m["role"] for m in body["members"]}
    assert roles == {team["head"].id: "lead", team["kim"].id: "member", team["boss"].id: "member"}  # 임원 그룹 계정도 가능
    assert body["myRole"] == "lead" and body["memberCount"] == 3
    assert not any(word in response.text for word in SENSITIVE)
    assert [e.event_type for e in events(env, "project", body["id"])] == ["project.registered"]


def test_member_registers_pending_with_head_as_approver(env, team):
    body = create(env, team, "kim", members=[team["mgr"]]).json()
    assert body["status"] == "pending_approval" and body["lead"] is None and body["approver"]["id"] == team["head"].id
    roles = {m["accountId"]: m["role"] for m in body["members"]}
    assert roles == {team["kim"].id: "member", team["mgr"].id: "member"}


def test_register_rules_and_validation(env, team):
    assert create(env, team, "loner").status_code == 403  # 소속이 아닌 부서
    assert create(env, team, "kim", dept="sales").status_code == 403
    assert create(env, team, "head", dept=team["dept"]["other"]).status_code == 404  # 다른 고객사 부서
    assert create(env, team, "head", name="   ").status_code == 422
    assert create(env, team, "head", name="가" * 121).status_code == 422
    assert create(env, team, "head", description="가" * 2001).status_code == 422
    assert create(env, team, "head", members=[team["outsider"]]).status_code == 400  # 다른 고객사 계정
    assert env["client"].post("/api/projects", json={}).status_code == 401
    # 부서장이 없는 부서에 부서원이 등록하면 409
    with env["factory"]() as s:
        empty = org.create_department(s, tenant_id=team["tenant"], name="신설팀")
        org.add_member(s, empty, s.get(Account, team["kim"].id))
        s.commit()
        empty_id = empty.id
    assert create(env, team, "kim", dept=empty_id).status_code == 409
    # 부서장의 직급이 관리자 미만이면(부서장 지정 후 강등된 경우) 바로 생성 불가 409
    with env["factory"]() as s:
        s.get(Account, team["head2"].id).rank = "staff"
        s.commit()
    assert create(env, team, "head2", dept="sales").status_code == 409
    with env["factory"]() as s:
        assert s.scalar(select(Project).where(Project.department_id == team["dept"]["sales"])) is None


# ================= 승인·반려 =================
def test_only_department_head_approves_and_becomes_lead(env, team):
    project = create(env, team, "kim", members=[team["mgr"]]).json()
    path = f"/api/projects/{project['id']}/approve"
    assert call(env, "POST", team["head2"], path).status_code == 403  # 다른 부서의 부서장
    assert call(env, "POST", team["boss"], path).status_code == 403  # 지시자
    assert call(env, "POST", team["mgr"], path).status_code == 403  # 같은 부서 관리자 직급 부서원
    assert call(env, "POST", team["kim"], path).status_code == 403  # 등록한 담당자
    assert call(env, "POST", team["loner"], path).status_code == 404  # 볼 수 없는 사람
    ok = call(env, "POST", team["head"], path)
    assert ok.status_code == 200
    body = ok.json()
    assert body["status"] == "active" and body["lead"]["id"] == team["head"].id
    roles = {m["accountId"]: m["role"] for m in body["members"]}
    assert roles[team["head"].id] == "lead" and roles[team["kim"].id] == "member" and roles[team["mgr"].id] == "member"
    with env["factory"]() as s:
        row = s.get(Project, project["id"])
        assert row.decided_by == team["head"].id and row.decided_at is not None and row.lead_account_id == team["head"].id
    before = len(events(env, "project", project["id"]))
    again = call(env, "POST", team["head"], path)
    assert again.status_code == 200 and again.json()["status"] == "active"
    assert len(events(env, "project", project["id"])) == before  # 변경 없음
    assert [e.event_type for e in events(env, "project", project["id"])] == ["project.registered", "project.approved"]


def test_reject_requires_reason_and_records_history(env, team):
    project = create(env, team, "kim").json()
    path = f"/api/projects/{project['id']}/reject"
    assert call(env, "POST", team["head"], path, json={}).status_code == 422
    assert call(env, "POST", team["head"], path, json={"reason": "   "}).status_code == 422
    assert call(env, "POST", team["head"], path, json={"reason": "가" * 2001}).status_code == 422
    assert call(env, "POST", team["head2"], path, json={"reason": "안 됨"}).status_code == 403
    done = call(env, "POST", team["head"], path, json={"reason": "예산 부족"})
    assert done.status_code == 200 and done.json()["status"] == "rejected"
    last = events(env, "project", project["id"])[-1]
    assert last.event_type == "project.rejected" and last.payload["after"]["reason"] == "예산 부족" and last.actor_account_id == team["head"].id
    # 반려된 프로젝트는 승인·재반려 불가
    assert call(env, "POST", team["head"], f"/api/projects/{project['id']}/approve").status_code == 409
    assert call(env, "POST", team["head"], path, json={"reason": "다시"}).status_code == 409
    # 활성 프로젝트는 반려 불가
    active = create(env, team, "head").json()
    assert call(env, "POST", team["head"], f"/api/projects/{active['id']}/reject", json={"reason": "x"}).status_code == 409


# ================= 참여자 관리 =================
def test_member_management_rules(env, team):
    pid = create(env, team, "head").json()["id"]
    add = f"/api/projects/{pid}/members"
    added = call(env, "POST", team["head"], add, json={"accountId": team["kim"].id, "role": "member"})
    assert added.status_code == 201 and len(added.json()["members"]) == 2
    assert call(env, "POST", team["head"], add, json={"accountId": team["kim"].id, "role": "member"}).status_code == 200  # 중복은 변경 없음
    assert call(env, "POST", team["head"], add, json={"accountId": team["kim"].id, "role": "manager"}).status_code == 409  # 직급 미달
    assert call(env, "POST", team["head"], add, json={"accountId": team["mgr"].id, "role": "manager"}).status_code == 201
    changed = call(env, "POST", team["head"], add, json={"accountId": team["mgr"].id, "role": "member"})  # 역할만 바뀜
    assert changed.status_code == 200 and {m["accountId"]: m["role"] for m in changed.json()["members"]}[team["mgr"].id] == "member"
    assert call(env, "POST", team["head"], add, json={"accountId": team["boss"].id, "role": "lead"}).status_code == 201
    assert call(env, "POST", team["head"], add, json={"accountId": team["head"].id, "role": "member"}).status_code == 409  # 총괄 역할 변경 불가
    assert call(env, "POST", team["head"], add, json={"accountId": team["outsider"].id}).status_code == 400
    assert call(env, "POST", team["head"], add, json={"accountId": team["sales"].id, "role": "boss"}).status_code == 422
    # 총괄(lead 역할)이 아니면 거부: 일반 참여자는 403, 볼 수 없는 사람은 404
    assert call(env, "POST", team["kim"], add, json={"accountId": team["sales"].id}).status_code == 403
    assert call(env, "POST", team["loner"], add, json={"accountId": team["sales"].id}).status_code == 404
    # 제거: lead 는 불가, 일반 참여자는 가능, 없는 사람은 404
    assert call(env, "POST", team["head"], f"{add}/{team['head'].id}/remove").status_code == 409
    assert call(env, "POST", team["head"], f"{add}/{team['boss'].id}/remove").status_code == 409  # lead 역할은 제거 불가
    removed = call(env, "POST", team["head"], f"{add}/{team['kim'].id}/remove")
    assert removed.status_code == 200 and team["kim"].id not in [m["accountId"] for m in removed.json()["members"]]
    assert call(env, "POST", team["head"], f"{add}/{team['kim'].id}/remove").status_code == 404
    assert call(env, "POST", team["kim"], f"{add}/{team['mgr'].id}/remove").status_code in (403, 404)
    kinds = [e.event_type for e in events(env, "project", pid)]
    assert kinds.count("project.member_added") == 3 and "project.member_removed" in kinds and "project.member_role_changed" in kinds


def test_member_management_only_on_active_projects(env, team):
    pending = create(env, team, "kim").json()["id"]
    # 대기 중에는 총괄이 없으므로 참여자 관리 불가(승인자 포함: lead 역할이 없음 → 403)
    assert call(env, "POST", team["head"], f"/api/projects/{pending}/members", json={"accountId": team["mgr"].id}).status_code == 403
    rejected = create(env, team, "kim", name="반려될 것").json()["id"]
    call(env, "POST", team["head"], f"/api/projects/{rejected}/reject", json={"reason": "x"})
    assert call(env, "POST", team["kim"], f"/api/projects/{rejected}/members", json={"accountId": team["mgr"].id}).status_code in (403, 409)
    # lead 역할이 있어도 active 가 아니면 409
    with env["factory"]() as s:
        row = s.get(Project, rejected)
        s.add(ProjectMember(project_id=rejected, account_id=team["head"].id, role="lead"))
        s.commit()
    assert call(env, "POST", team["head"], f"/api/projects/{rejected}/members", json={"accountId": team["mgr"].id}).status_code == 409
    assert call(env, "POST", team["head"], f"/api/projects/{rejected}/members/{team['kim'].id}/remove").status_code == 409


# ================= 조회 =================
def test_list_and_detail_visibility(env, team):
    mine = create(env, team, "head", members=[team["kim"]]).json()["id"]  # kim 참여
    pending = create(env, team, "sales", dept="sales", name="대기 건").json()["id"]  # head2 가 승인자, sales(담당자) 등록자
    sales_only = create(env, team, "head2", dept="sales", name="영업 건").json()["id"]  # sales 는 참여자 아님

    def ids(who):
        return sorted(p["id"] for p in call(env, "GET", team[who], "/api/projects").json())

    assert ids("kim") == [mine]  # 참여자만
    assert ids("loner") == []
    assert ids("sales") == [pending]  # 등록자: 대기 건은 보이고, 참여하지 않은 프로젝트는 안 보임
    # 관리자 이상은 같은 고객사 전체
    assert ids("head2") == ids("head") == ids("boss") == sorted([mine, pending, sales_only])
    assert ids("outsider") == []
    item = next(p for p in call(env, "GET", team["kim"], "/api/projects").json())
    assert item["myRole"] == "member" and item["status"] == "active" and item["lead"]["name"] == "박개발장" and item["memberCount"] == 2
    assert call(env, "GET", team["kim"], f"/api/projects/{mine}").status_code == 200
    assert call(env, "GET", team["kim"], f"/api/projects/{sales_only}").status_code == 404
    assert call(env, "GET", team["loner"], f"/api/projects/{mine}").status_code == 404
    assert call(env, "GET", team["outsider"], f"/api/projects/{mine}").status_code == 404  # 다른 고객사
    assert call(env, "GET", team["head"], "/api/projects/99999").status_code == 404
    # 승인자(부서장)는 대기 건을 본다. 관리자 직급이 아닌 승인자도 보이는 규칙은 조건식(approver_condition)이 담당
    assert call(env, "GET", team["head2"], f"/api/projects/{pending}").json()["approver"]["id"] == team["head2"].id
    detail = call(env, "GET", team["kim"], f"/api/projects/{mine}")
    assert not any(word in detail.text for word in SENSITIVE)


def test_candidates_structure_and_scope(env, team):
    body = call(env, "GET", team["kim"], f"/api/projects/candidates?departmentId={team['dept']['dev']}").json()
    assert body["ownDepartment"]["departmentName"] == "개발팀"
    assert sorted(m["name"] for m in body["ownDepartment"]["members"]) == ["김담당", "박개발장", "정관리"]
    assert [g["departmentName"] for g in body["otherDepartments"]] == ["영업팀"]  # 임원 그룹은 타 부서 목록에 섞이지 않음
    assert sorted(m["name"] for m in body["otherDepartments"][0]["members"]) == ["오영업", "이영업장"]
    assert [g["departmentName"] for g in body["executiveGroup"]] == ["임원"]
    assert [m["name"] for m in body["executiveGroup"][0]["members"]] == ["최임원"]
    member = body["ownDepartment"]["members"][0]
    assert set(member) == {"accountId", "name", "rank"}
    assert not any(word in json.dumps(body, ensure_ascii=False) for word in SENSITIVE)
    # 소속이 아닌 부서는 403, 소속 없는 사람도 403, 다른 고객사 부서는 404
    assert call(env, "GET", team["kim"], f"/api/projects/candidates?departmentId={team['dept']['sales']}").status_code == 403
    assert call(env, "GET", team["loner"], f"/api/projects/candidates?departmentId={team['dept']['dev']}").status_code == 403
    assert call(env, "GET", team["kim"], f"/api/projects/candidates?departmentId={team['dept']['other']}").status_code == 404
    assert call(env, "GET", team["kim"], "/api/projects/candidates").status_code == 422
    # 임원 그룹 소속은 자기 그룹이 ownDepartment 이고 executiveGroup 에는 다시 나오지 않는다
    own_exec = call(env, "GET", team["boss"], f"/api/projects/candidates?departmentId={team['dept']['exe']}").json()
    assert own_exec["ownDepartment"]["departmentName"] == "임원" and own_exec["executiveGroup"] == []
    # 비활성 계정은 후보에서 빠진다
    with env["factory"]() as s:
        s.get(Account, team["mgr"].id).is_active = False
        s.commit()
    again = call(env, "GET", team["kim"], f"/api/projects/candidates?departmentId={team['dept']['dev']}").json()
    assert "정관리" not in [m["name"] for m in again["ownDepartment"]["members"]]


# ================= 마이그레이션 =================
def test_migration_roundtrip_and_downgrade_guard(tmp_path):
    url = f"sqlite:///{(tmp_path / 'm.db').as_posix()}"
    cfg = _alembic(url)
    command.upgrade(cfg, "head")
    command.downgrade(cfg, _BEFORE_ORG)
    command.upgrade(cfg, "head")
    command.check(cfg)
    engine = make_engine(url)
    try:
        with engine.begin() as conn:
            conn.execute(text("INSERT INTO tenants (id, name, auto_confirm_days, created_at) VALUES (1, 't', 5, '2026-10-01')"))
            conn.execute(text(
                "INSERT INTO accounts (id, tenant_id, login_id, password_hash, name, email, rank, is_active, created_at) "
                "VALUES (1, 1, 'a', 'x', 'a', 'a@x', 'manager', 1, '2026-10-01')"))
        # 부서가 없으면 되돌리기·다시 올리기 가능(기존 표는 그대로)
        command.downgrade(cfg, _BEFORE_ORG)
        command.upgrade(cfg, "head")
        with engine.begin() as conn:
            conn.execute(text("INSERT INTO departments (id, tenant_id, name, kind, created_at) VALUES (1, 1, '개발', 'normal', '2026-10-01')"))
        with pytest.raises(RuntimeError, match="downgrade 거부"):
            command.downgrade(cfg, _BEFORE_ORG)
        with engine.connect() as conn:
            assert conn.execute(text("SELECT COUNT(*) FROM departments")).scalar_one() == 1
            assert conn.execute(text("SELECT COUNT(*) FROM accounts")).scalar_one() == 1
    finally:
        engine.dispose()


def test_constraints_reject_bad_values(env, team):
    from sqlalchemy.exc import IntegrityError

    with env["factory"]() as s:
        with pytest.raises(IntegrityError):
            s.add(Department(tenant_id=team["tenant"], name="개발팀"))  # 같은 고객사 이름 중복
            s.flush()
        s.rollback()
        with pytest.raises(IntegrityError):
            s.add(AccountDepartment(account_id=team["kim"].id, department_id=team["dept"]["dev"], role="member"))  # 같은 소속 중복
            s.flush()
        s.rollback()
        with pytest.raises(IntegrityError):
            s.add(Department(tenant_id=team["tenant"], name="이상", kind="weird"))
            s.flush()


def test_alembic_offline_postgresql_ddl(tmp_path):
    """알렘빅 오프라인(--sql)으로 postgresql 방언 DDL 이 생성되고 새 표 4개가 들어 있다(서버·드라이버 없이)."""
    import io
    from contextlib import redirect_stdout

    cfg = _alembic("postgresql+psycopg://u:p@localhost/db")
    buffer = io.StringIO()
    cfg.attributes["output_buffer"] = buffer
    cfg.output_buffer = buffer
    command.upgrade(cfg, "head", sql=True)
    sql = buffer.getvalue()
    for table in ("departments", "account_departments", "projects", "project_members"):
        assert f"CREATE TABLE {table}" in sql
    assert "uq_departments_tenant_name" in sql and "ck_projects_status_valid" in sql
