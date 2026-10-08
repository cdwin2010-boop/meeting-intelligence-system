"""작업 66-3d: 부서장의 업무 대체 권한, 66-3c 보강(부서장이 아닌 이전 총괄은 총괄을 넘기면 확정 권한을 잃는다)."""
import pytest

from app.models import Account
from tests.test_meeting_classification import project  # noqa: F401  (project 는 픽스처)
from tests.test_org_projects import call, create, team  # noqa: F401  (team 은 픽스처)
from tests.test_project_dept_head import hproject  # noqa: F401  (hproject 는 픽스처)
from tests.test_project_lead import change, fresh, roles
from tests.test_supersession import item, links, make_request, pm, supersede
from tests.test_upload_processing import env  # noqa: F401  (env 는 픽스처)


def make_pair(env, team, project_id, registrant="head"):
    """같은 프로젝트의 과거(day 1)·오늘(day 5) 회의록과 업무 1건씩. 돌려주는 값: dict."""
    kim = team["kim"].id
    old_m, [old_i] = pm(env, team, project_id, 1, [item(assignee_id=kim)], registrant=registrant)
    new_m, [new_i] = pm(env, team, project_id, 5, [item("협력사 부품 재입고 일정 확인 후 공유", assignee_id=kim)], registrant=registrant)
    return {"old_m": old_m, "old": old_i, "new_m": new_m, "new": new_i}


@pytest.fixture
def hpair(env, team, hproject):
    """부서장 head 가 참여자가 아니고 총괄이 따로(boss) 있는 프로젝트의 회의록 쌍."""
    return make_pair(env, team, hproject)


# ================= 부서장의 대체 =================
def test_dept_head_can_supersede_without_membership(env, team, hpair):
    done = supersede(env, team["head"], hpair["new"], hpair["old"])
    assert done.status_code in (200, 201), done.text
    [row] = links(env)
    assert (row.old_item_id, row.new_item_id, row.superseded_by) == (hpair["old"], hpair["new"], team["head"].id)


@pytest.mark.parametrize("who", ["head2", "mgr", "kim"])
def test_others_cannot_supersede(env, team, hpair, who):
    """다른 부서 부서장, 같은 부서 관리자 부원, 같은 부서 담당자(staff)."""
    assert supersede(env, team[who], hpair["new"], hpair["old"]).status_code == 403
    assert links(env) == []


def test_demoted_head_cannot_supersede(env, team, hpair):
    with env["factory"]() as s:
        s.get(Account, team["head"].id).rank = "staff"
        s.commit()
    assert supersede(env, team["head"], hpair["new"], hpair["old"]).status_code == 403
    assert links(env) == []


def test_pending_and_rejected_project_cannot_supersede(env, team):
    pending = create(env, team, "kim", name="대기").json()["id"]
    pair = make_pair(env, team, pending)
    assert supersede(env, team["head"], pair["new"], pair["old"]).status_code == 403
    rejected = create(env, team, "kim", name="반려").json()["id"]
    assert call(env, "POST", team["head"], f"/api/projects/{rejected}/reject", json={"reason": "사유"}).status_code == 200
    pair = make_pair(env, team, rejected)
    assert supersede(env, team["head"], pair["new"], pair["old"]).status_code == 403
    assert links(env) == []


def test_unlinked_item_cannot_be_superseded(env, team, hproject):
    kim = team["kim"].id
    _, [old_i] = pm(env, team, hproject, 1, [item(assignee_id=kim)], linked=False)
    _, [new_i] = pm(env, team, hproject, 5, [item(assignee_id=kim)], linked=False)
    # 기존 규칙: 프로젝트 회의록이 아니면 409(권한 이전에 프로젝트 확인)
    assert supersede(env, team["head"], new_i, old_i).status_code == 409
    assert links(env) == []


def test_other_department_head_of_other_project_is_denied(env, team):
    """영업팀 부서장 head2 는 개발팀 프로젝트(별도 총괄 head)에서 권한이 없고, 개발팀 부서장 head 도 영업팀 프로젝트에서 권한이 없다."""
    sales_project = create(env, team, "head2", dept="sales", name="영업 프로젝트").json()["id"]
    pair = make_pair(env, team, sales_project, registrant="head2")
    assert supersede(env, team["head"], pair["new"], pair["old"]).status_code == 403
    assert supersede(env, team["head2"], pair["new"], pair["old"]).status_code in (200, 201)


# ================= 허용 동작 =================
def test_allowed_actions_supersede_item(env, team, hpair):
    def actions(who):
        detail = call(env, "GET", team[who], f"/api/meetings/{hpair['new_m']}").json()
        return next(i for i in detail["actionItems"] if i["id"] == hpair["new"])["allowedActions"]

    assert "supersede_item" in actions("head") and "supersede_item" in actions("boss")
    assert "supersede_item" not in actions("kim") and "supersede_item" not in actions("mgr")
    # 실제 API 와 일치
    assert supersede(env, team["head"], hpair["new"], hpair["old"]).status_code in (200, 201)


# ================= 대체 요청 수락 =================
def test_dept_head_accepting_supersede_request(env, team, hpair):
    rid = make_request(env, team["kim"], hpair["new_m"], hpair["new"], hpair["old"]).json()["requestId"]
    path = f"/api/meetings/{hpair['new_m']}/change-requests/{rid}/resolve"
    assert call(env, "POST", team["mgr"], path, json={"decision": "accepted"}).status_code == 403  # 일반 부서원은 기존대로
    assert links(env) == []
    assert call(env, "POST", team["head"], path, json={"decision": "accepted"}).status_code == 200
    [row] = links(env)
    assert (row.old_item_id, row.new_item_id, row.source_request_id, row.superseded_by) == (hpair["old"], hpair["new"], rid, team["head"].id)


# ================= 기존 권한자 =================
def test_existing_authorities_still_supersede(env, team, project):
    pair = make_pair(env, team, project)
    assert supersede(env, team["head"], pair["new"], pair["old"]).status_code in (200, 201)  # 총괄(이자 부서장)
    other = make_pair(env, team, project)
    assert supersede(env, team["boss"], other["new"], other["old"]).status_code in (200, 201)  # 지시자
    third = make_pair(env, team, project)
    assert change(env, team["head"], project, team["boss"]).status_code == 200  # 총괄을 지시자에게 넘겨도
    assert supersede(env, team["mgr"], third["new"], third["old"]).status_code == 403  # 일반 부서원은 불가


# ================= 66-3c 보강 =================
def test_previous_lead_who_is_not_dept_head_loses_confirm_but_keeps_view(env, team, project):
    """head(부서장) → mgr → head2 로 총괄을 넘긴다. mgr 는 부서장이 아니므로 넘긴 뒤 확정 403, 관리자 역할 참여자로 열람은 가능."""
    assert change(env, team["head"], project, team["mgr"]).status_code == 200
    mid, _ = fresh(env, team, project)
    assert call(env, "POST", team["mgr"], f"/api/meetings/{mid}/confirm").status_code == 200  # 총괄일 때는 가능
    assert change(env, team["mgr"], project, team["head2"]).status_code == 200
    mid, _ = fresh(env, team, project)
    assert call(env, "POST", team["mgr"], f"/api/meetings/{mid}/confirm").status_code == 403
    assert call(env, "GET", team["mgr"], f"/api/meetings/{mid}").status_code == 200
    assert call(env, "GET", team["mgr"], f"/api/projects/{project}").status_code == 200
    assert roles(env, project)[team["mgr"].id] == "manager"
    assert "confirm_meeting" not in call(env, "GET", team["mgr"], f"/api/meetings/{mid}").json()["allowedActions"]
    assert call(env, "POST", team["head2"], f"/api/meetings/{mid}/confirm").status_code == 200  # 새 총괄은 가능
