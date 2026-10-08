"""작업 66-3b: 프로젝트 총괄 승인 판정(회의록 총괄 권한 전체)과 총괄 변경 API, 프로젝트 상세 allowedActions."""
from datetime import date

import pytest
from sqlalchemy import select

from app.models import Account, Event, Meeting, MeetingHold, Project, ProjectMember
from tests.test_meeting_classification import classify, project  # noqa: F401  (project 는 픽스처)
from tests.test_meeting_queries import make_meeting
from tests.test_org_projects import call, create, events, team  # noqa: F401  (team 은 픽스처)
from tests.test_upload_processing import env  # noqa: F401  (env 는 픽스처)

REASON = {"reason": "테스트 처리 사유"}
DENIED = (403, 404)


def add_role(env, team, project_id, who, role):
    """head(총괄)가 참여자를 추가한다."""
    response = call(env, "POST", team["head"], f"/api/projects/{project_id}/members", json={"accountId": team[who].id, "role": role})
    assert response.status_code in (200, 201), response.text


def fresh(env, team, project_id, *, registrant="kim", status="awaiting_confirmation", linked=True):
    """프로젝트 회의록 1건(등록자는 기본 담당자 kim). 확정 가능한 업무 1건 포함. 돌려주는 값: (회의록 id, 업무 id)."""
    meeting_id = make_meeting(
        env["factory"], team[registrant], status=status, transcript="화자1: 안녕",
        items=[{"title": "확정할 업무", "assignee_id": team["kim"].id, "due_date": date(2026, 10, 9)}],
    )
    if linked:
        classify(env, meeting_id, project_id, tenant_id=team["tenant"])
    with env["factory"]() as s:
        from app.models import ActionItem

        item_id = s.scalar(select(ActionItem.id).where(ActionItem.meeting_id == meeting_id))
    return meeting_id, item_id


def run_all(env, team, project_id, who, **fresh_kwargs):
    """총괄 권한이 필요한 동작을 하나씩(동작마다 새 회의록) 시도하고 상태 코드를 모은다."""
    codes = {}
    path = lambda mid: f"/api/meetings/{mid}"  # noqa: E731

    def new():
        return fresh(env, team, project_id, **fresh_kwargs)

    mid, _ = new()
    codes["confirm_meeting"] = call(env, "POST", team[who], f"{path(mid)}/confirm").status_code
    mid, item = new()
    codes["confirm_item"] = call(env, "POST", team[who], f"/api/action-items/{item}/confirm").status_code
    mid, item = new()
    codes["edit_item"] = call(env, "PATCH", team[who], f"/api/action-items/{item}", json={"title": "바꾼 업무명"}).status_code
    mid, _ = new()
    codes["hold"] = call(env, "POST", team[who], f"{path(mid)}/hold", json=REASON).status_code
    mid, _ = new()
    with env["factory"]() as s:
        s.add(MeetingHold(meeting_id=mid, tenant_id=team["tenant"], on_hold=True))
        s.commit()
    codes["resume"] = call(env, "POST", team[who], f"{path(mid)}/resume").status_code
    mid, _ = new()
    codes["end"] = call(env, "POST", team[who], f"{path(mid)}/end", json=REASON).status_code
    mid, _ = new()
    codes["delete"] = call(env, "POST", team[who], f"{path(mid)}/delete", json=REASON).status_code
    mid, _ = new()
    codes["minutes"] = call(env, "PATCH", team[who], f"{path(mid)}/minutes", json={"purpose": "p"}).status_code
    mid, _ = new()
    codes["speakers"] = call(env, "PUT", team[who], f"{path(mid)}/speakers", json={"speakers": []}).status_code
    mid, _ = new()
    export = call(env, "GET", team["boss"], f"{path(mid)}/export")
    codes["upload_update"] = call(env, "POST", team[who], f"{path(mid)}/update-upload/preview",
                                  files={"file": ("a.xlsx", export.content, "application/octet-stream")}).status_code
    mid, _ = new()
    codes["add_item"] = call(env, "POST", team[who], f"{path(mid)}/action-items", json={"title": "새 업무"}).status_code
    mid, _ = new()
    codes["reprocess"] = call(env, "POST", team[who], f"{path(mid)}/reprocess").status_code
    mid, _ = new()
    created = call(env, "POST", team["kim"], f"{path(mid)}/change-requests", json={"comment": "확인 부탁"})
    assert created.status_code == 201, created.text
    codes["resolve_request"] = call(env, "POST", team[who], f"{path(mid)}/change-requests/{created.json()['requestId']}/resolve",
                                    json={"decision": "accepted"}).status_code
    return codes


def powers(codes: dict) -> list:
    """수정 요청 해결(resolve_request)은 관리자 직급이면 열람 가능한 요청을 해결하는 기존 규칙이라 총괄 판정과 무관하므로 뺀 나머지 코드."""
    return [code for name, code in codes.items() if name != "resolve_request"]


# ================= 총괄 권한 =================
def test_project_lead_has_all_lead_powers_on_project_meetings(env, team, project):
    codes = run_all(env, team, project, "head")
    # reprocess 는 실패한 회의록만 가능해 409 일 수 있다(권한 거부 403·404 가 아니면 판정 통과)
    for name, code in codes.items():
        assert code not in DENIED, f"{name}: {code}"
    for name in ("confirm_meeting", "confirm_item", "edit_item", "hold", "resume", "end", "delete", "minutes", "speakers", "upload_update", "resolve_request"):
        assert codes[name] == 200, (name, codes[name])
    assert codes["add_item"] == 201


@pytest.mark.parametrize("who", ["kim", "head2", "mgr"])
def test_non_lead_participants_cannot_use_lead_powers(env, team, project, who):
    """일반 참여자(member)와 manager 역할 참여자(관리자 직급이어도), 다른 부서 관리자."""
    if who == "head2":
        add_role(env, team, project, "head2", "member")
    if who == "mgr":
        add_role(env, team, project, "mgr", "manager")
    codes = run_all(env, team, project, who)
    # resolve_request 는 관리자 직급이면 열람 가능한 요청을 해결할 수 있는 기존 규칙이라(총괄 판정과 무관) 제외한다
    for name, code in codes.items():
        if name == "resolve_request" and who != "kim":
            continue
        assert code in DENIED, f"{who} {name}: {code}"


def test_lead_with_rank_demoted_to_staff_loses_powers(env, team, project):
    with env["factory"]() as s:
        s.get(Account, team["head"].id).rank = "staff"
        s.commit()
    codes = run_all(env, team, project, "head")
    assert all(code in DENIED for code in codes.values()), codes


def test_lead_of_pending_or_rejected_project_has_no_powers(env, team):
    pending = create(env, team, "kim", name="대기").json()["id"]
    with env["factory"]() as s:
        s.add(ProjectMember(project_id=pending, account_id=team["head"].id, role="lead"))
        s.commit()
    assert all(c in DENIED for c in powers(run_all(env, team, pending, "head")))
    rejected = create(env, team, "kim", name="반려").json()["id"]
    call(env, "POST", team["head"], f"/api/projects/{rejected}/reject", json=REASON)
    with env["factory"]() as s:
        s.add(ProjectMember(project_id=rejected, account_id=team["head"].id, role="lead"))
        s.commit()
    assert all(c in DENIED for c in powers(run_all(env, team, rejected, "head")))


def test_lead_has_no_power_on_unlinked_or_other_project_or_other_tenant(env, team, project):
    # 연결되지 않은 회의록
    assert all(c in DENIED for c in powers(run_all(env, team, project, "head", linked=False)))
    # 다른 프로젝트의 총괄(영업팀 head2)
    assert all(c in DENIED for c in powers(run_all(env, team, project, "head2")))
    # 다른 고객사
    assert all(c == 404 for c in run_all(env, team, project, "outsider").values())


def test_existing_lead_rules_are_unchanged(env, team, project):
    # 회의록을 등록한 관리자(프로젝트와 무관한 연결 없는 회의록)와 지시자는 그대로
    mid, _ = fresh(env, team, project, registrant="mgr", linked=False)
    assert call(env, "POST", team["mgr"], f"/api/meetings/{mid}/confirm").status_code == 200
    mid, _ = fresh(env, team, project, registrant="kim")
    assert call(env, "POST", team["boss"], f"/api/meetings/{mid}/confirm").status_code == 200
    # 프로젝트 회의록을 관리자가 등록했어도(프로젝트 총괄이 아님) 등록자 경로로 확정된다
    mid, _ = fresh(env, team, project, registrant="head2")
    assert call(env, "POST", team["head2"], f"/api/meetings/{mid}/confirm").status_code == 200
    # 열람 규칙 불변: 비참여자 담당자는 못 본다
    mid, _ = fresh(env, team, project)
    assert call(env, "GET", team["sales"], f"/api/meetings/{mid}").status_code == 404


def test_allowed_actions_follow_project_lead(env, team, project):
    mid, _ = fresh(env, team, project)
    lead_actions = call(env, "GET", team["head"], f"/api/meetings/{mid}").json()["allowedActions"]
    assert {"confirm_meeting", "edit_minutes", "upload_update", "add_item", "edit_speakers", "hold_meeting", "end_meeting", "delete_meeting"} <= set(lead_actions)
    member_actions = call(env, "GET", team["kim"], f"/api/meetings/{mid}").json()["allowedActions"]
    assert not {"confirm_meeting", "edit_minutes", "hold_meeting", "delete_meeting", "add_item"} & set(member_actions)
    detail = call(env, "GET", team["head"], f"/api/meetings/{mid}").json()
    assert "confirm_item" in detail["actionItems"][0]["allowedActions"] and "delete_item" in detail["actionItems"][0]["allowedActions"]


def test_todos_list_awaiting_meeting_for_project_lead(env, team, project):
    mid, _ = fresh(env, team, project)
    body = call(env, "GET", team["head"], "/api/me/todos").json()
    assert mid in [m["id"] for m in body["awaitingConfirmMeetings"]["items"]]


# ================= 총괄 변경 =================
def change(env, who, project_id, new_lead, reason="인사 이동"):
    body = {"newLeadId": new_lead.id}
    if reason is not None:
        body["reason"] = reason
    return call(env, "POST", who, f"/api/projects/{project_id}/change-lead", json=body)


def roles(env, project_id):
    with env["factory"]() as s:
        return {m.account_id: m.role for m in s.scalars(select(ProjectMember).where(ProjectMember.project_id == project_id))}


def test_lead_changes_lead_old_lead_stays_as_manager(env, team, project):
    add_role(env, team, project, "mgr", "member")  # 새 총괄은 이미 참여자
    done = change(env, team["head"], project, team["mgr"])
    assert done.status_code == 200, done.text
    body = done.json()
    assert body["lead"]["id"] == team["mgr"].id
    assert roles(env, project) == {team["head"].id: "manager", team["mgr"].id: "lead", team["kim"].id: "member"}
    # 이력: 이전·새 총괄, 사유, 변경자
    last = events(env, "project", project)[-1]
    assert last.event_type == "project.lead_changed" and last.actor_account_id == team["head"].id
    assert last.payload["before"]["leadAccountId"] == team["head"].id and last.payload["after"] == {"leadAccountId": team["mgr"].id, "reason": "인사 이동"}
    # 이전 총괄은 계속 프로젝트와 회의록을 본다
    mid, _ = fresh(env, team, project)
    assert call(env, "GET", team["head"], f"/api/projects/{project}").status_code == 200
    assert call(env, "GET", team["head"], f"/api/meetings/{mid}").status_code == 200
    # 변경 직후: 새 총괄은 권한을 갖는다. 이전 총괄(head)은 개발팀 부서장이라 작업 66-3c 정책으로 부서장 권한이 남는다
    assert call(env, "POST", team["head"], f"/api/meetings/{mid}/confirm").status_code == 200
    mid, _ = fresh(env, team, project)
    assert call(env, "POST", team["mgr"], f"/api/meetings/{mid}/confirm").status_code == 200


def test_new_lead_not_yet_participant_is_added_and_executive_can_change(env, team, project):
    done = change(env, team["boss"], project, team["head2"])  # 지시자, 새 총괄은 비참여자
    assert done.status_code == 200
    assert roles(env, project)[team["head2"].id] == "lead" and roles(env, project)[team["head"].id] == "manager"
    # 예전 총괄이 회의록 등록자였던 경우 기존 경로(등록자) 권한은 유지된다
    mid, _ = fresh(env, team, project, registrant="head")
    assert call(env, "POST", team["head"], f"/api/meetings/{mid}/confirm").status_code == 200


def test_change_lead_permissions_validation_and_states(env, team, project):
    # 권한: 다른 관리자·참여자(일반·manager 역할)·담당자는 거부, 볼 수 없는 사람은 404
    add_role(env, team, project, "mgr", "manager")
    assert change(env, team["head2"], project, team["mgr"]).status_code == 403
    assert change(env, team["mgr"], project, team["mgr"]).status_code == 403
    assert change(env, team["kim"], project, team["mgr"]).status_code == 403
    assert change(env, team["sales"], project, team["mgr"]).status_code == 404
    assert change(env, team["outsider"], project, team["mgr"]).status_code == 404
    # 사유: 없음·공백·길이 초과는 422
    assert change(env, team["head"], project, team["mgr"], reason=None).status_code == 422
    assert change(env, team["head"], project, team["mgr"], reason="   ").status_code == 422
    assert change(env, team["head"], project, team["mgr"], reason="가" * 2001).status_code == 422
    # 새 총괄: 직급 미달 409, 다른 고객사·없는 계정 400
    assert change(env, team["head"], project, team["kim"]).status_code == 409
    assert change(env, team["head"], project, team["outsider"]).status_code == 400
    # 같은 계정은 200(변경 없음, 이력 추가 없음)
    before = len(events(env, "project", project))
    assert change(env, team["head"], project, team["head"]).status_code == 200
    assert len(events(env, "project", project)) == before
    assert roles(env, project)[team["head"].id] == "lead"
    # 비활성 프로젝트는 409
    pending = create(env, team, "kim", name="대기").json()["id"]
    assert change(env, team["boss"], pending, team["head2"]).status_code == 409
    # 아무것도 바뀌지 않았다
    with env["factory"]() as s:
        assert s.get(Project, project).lead_account_id == team["head"].id and s.get(Project, pending).lead_account_id is None


def test_existing_member_api_still_protects_lead_role(env, team, project):
    add = f"/api/projects/{project}/members"
    assert call(env, "POST", team["head"], add, json={"accountId": team["head"].id, "role": "member"}).status_code == 409
    assert call(env, "POST", team["head"], f"{add}/{team['head'].id}/remove").status_code == 409


# ================= 프로젝트 상세 allowedActions =================
def test_project_detail_allowed_actions(env, team, project):
    def actions(who, pid=project):
        return call(env, "GET", team[who], f"/api/projects/{pid}").json()["allowedActions"]

    assert actions("head") == ["change_lead", "manage_members"]
    assert actions("boss") == ["change_lead"]  # 지시자: 총괄 변경만(참여자 관리는 총괄만)
    assert actions("kim") == [] and actions("head2") == []
    pending = create(env, team, "kim", name="대기").json()["id"]
    assert actions("head", pending) == ["approve_project", "reject_project"]  # 승인자
    assert actions("kim", pending) == [] and actions("boss", pending) == []
    # 실제 API 와 일치: 목록에 있는 동작은 성공하고 없는 동작은 거부된다
    assert call(env, "POST", team["head"], f"/api/projects/{pending}/approve").status_code == 200
    assert call(env, "POST", team["kim"], f"/api/projects/{project}/members", json={"accountId": team["mgr"].id}).status_code == 403
    assert actions("head", pending) == ["change_lead", "manage_members"]
