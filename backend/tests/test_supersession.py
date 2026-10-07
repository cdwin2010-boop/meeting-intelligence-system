"""작업 66-4: 유사 업무 검색, 업무 대체(대체 연결), 대체 요청, 허용 동작, 대체된 업무의 제외·포함, 마이그레이션."""
import io
import re
import sqlite3
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest
from alembic import command
from openpyxl import load_workbook
from sqlalchemy import select

from app.config import settings
from app.jobs.auto_confirm import run_auto_confirm
from app.jobs.daily_mail import _counts as daily_counts
from app.models import (
    Account, ActionItem, Event, ItemSupersession, Meeting, MeetingClosure, MeetingHold, MeetingView, Notice, Project,
)
from app.models.common import utcnow
from app.models.item_conditions import deleted_item, inactive_item, live_item, not_deleted_item, open_item, pending_item
from app.services import supersession as sup
from app.services.mail import queue_immediate_new_minutes
from app.services.similar_items import score_pair
from tests.test_meeting_classification import classify, project  # noqa: F401  (project 는 픽스처)
from tests.test_meeting_queries import make_meeting
from tests.test_org_projects import call, create, events, team  # noqa: F401  (team 은 픽스처)
from tests.test_upload_processing import _alembic, env  # noqa: F401  (env 는 픽스처)

REASON = {"reason": "오늘 회의 결정으로 대체"}
DUE = date(2026, 10, 9)
_BEFORE_SUPERSESSIONS = "d3a8f15c7e92"  # item_supersessions 마이그레이션의 down_revision
_ROOT = Path(__file__).resolve().parent.parent


def pm(env, team, project_id, day, items, *, registrant="head", status="awaiting_confirmation", linked=True) -> tuple[int, list[int]]:
    """프로젝트 회의록 1건(회의 일시 = 기준일 + day). items: ActionItem 필드 dict 목록. 돌려주는 값: (회의록 id, 업무 id 목록)."""
    meeting_id = make_meeting(env["factory"], team[registrant], day=day, status=status, items=items)
    if linked:
        classify(env, meeting_id, project_id, tenant_id=team["tenant"])
    with env["factory"]() as s:
        ids = list(s.scalars(select(ActionItem.id).where(ActionItem.meeting_id == meeting_id).order_by(ActionItem.id)))
    return meeting_id, ids


def item(title="협력사 부품 재입고 일정 확인", who="kim", team_=None, **fields):
    return {"title": title, "due_date": DUE, **fields}


def supersede(env, who, new_id, old_id, reason="오늘 회의 결정으로 대체"):
    body = {"supersedesItemId": old_id}
    if reason is not None:
        body["reason"] = reason
    return call(env, "POST", who, f"/api/action-items/{new_id}/supersede", json=body)


def links(env) -> list[ItemSupersession]:
    with env["factory"]() as s:
        return list(s.scalars(select(ItemSupersession).order_by(ItemSupersession.id)))


@pytest.fixture
def pair(env, team, project):
    """같은 프로젝트의 과거(day 1)·오늘(day 5) 회의록. 각 업무 1건(kim 담당, 같은 기한). 돌려주는 값: dict."""
    kim = team["kim"].id
    old_m, [old_i] = pm(env, team, project, 1, [item(assignee_id=kim)])
    new_m, [new_i] = pm(env, team, project, 5, [item("협력사 부품 재입고 일정 확인 후 공유", assignee_id=kim)])
    return {"old_m": old_m, "old": old_i, "new_m": new_m, "new": new_i}


# ================= 유사도 점수 =================
def test_score_pair_combines_name_assignee_and_due():
    mk = lambda **kw: ActionItem(title=kw.get("title", ""), assignee_id=kw.get("a"), due_date=kw.get("due"))  # noqa: E731
    same = score_pair(mk(title="부품 재입고 확인", a=1, due=DUE), mk(title="부품 재입고 확인!", a=1, due=DUE + timedelta(days=7)))
    assert same == (100, ["업무명 유사", "담당자 동일", "기한 근접"])
    assert score_pair(mk(title="부품 재입고 확인", a=1, due=DUE), mk(title="부품 재입고 확인", a=2, due=DUE + timedelta(days=8))) == (70, ["업무명 유사"])
    assert score_pair(mk(title="가나다", a=1), mk(title="xyz", a=1)) == (20, ["담당자 동일"])
    assert score_pair(mk(title="", a=None), mk(title="", a=None)) == (0, [])
    assert score_pair(mk(title="가나다라", due=DUE), mk(title="마바사아", due=DUE)) == (10, ["기한 근접"])


# ================= 유사 업무 검색 =================
def similar(env, who, item_id):
    return call(env, "GET", who, f"/api/action-items/{item_id}/similar")


def test_similar_search_candidates_scoring_and_exclusions(env, team, project):
    kim = team["kim"].id
    old_m, [a1, a2, closed_i, deleted_i, superseded_i] = pm(env, team, project, 1, [
        item(assignee_id=kim), item("전혀 다른 설비 점검 계획 수립", assignee_id=team["mgr"].id, due_date=DUE + timedelta(days=60)),
        item(status="closed"), item(status="deleted"), item(),
    ])
    other_project = create(env, team, "head2", dept="sales", name="영업 프로젝트").json()["id"]
    elsewhere_m, [elsewhere_i] = pm(env, team, other_project, 2, [item()], registrant="head2")
    unlinked_m, [unlinked_i] = pm(env, team, project, 2, [item()], linked=False)
    held_m, [held_i] = pm(env, team, project, 3, [item()])
    later_m, [later_i] = pm(env, team, project, 9, [item()])
    new_m, [new_i] = pm(env, team, project, 5, [item("협력사 부품 재입고 일정 확인 후 공유", assignee_id=kim)])
    with env["factory"]() as s:
        s.add(MeetingHold(meeting_id=held_m, tenant_id=team["tenant"], on_hold=True))
        s.commit()
    # 업무 하나는 이미 대체됨(과거 → 같은 과거 회의 이전 업무 말고 오늘 회의 업무로)
    other_new_m, [other_new_i] = pm(env, team, project, 6, [item()])
    assert supersede(env, team["head"], other_new_i, superseded_i).status_code == 201

    body = similar(env, team["kim"], new_i)  # 담당자(staff, 참여자)도 실행 가능
    assert body.status_code == 200, body.text
    result = body.json()
    assert [r["itemId"] for r in result] == [a1]  # 이름·담당자·기한이 모두 가까운 과거 업무만(임계값 50 이상)
    top = result[0]
    assert top["score"] >= 90 and top["reasons"] == ["업무명 유사", "담당자 동일", "기한 근접"]
    assert top["title"] == "협력사 부품 재입고 일정 확인" and top["assignee"]["id"] == kim and top["dueDate"] == "2026-10-09" and top["status"] == "pending"
    assert top["meeting"]["id"] == old_m and top["meeting"]["title"] and top["meeting"]["heldAt"]
    # 설정: 임계값을 0 으로 낮추면 종결·삭제·대체·다른 프로젝트·미연결·보류·더 늦은 회의의 업무는 여전히 제외된다
    settings.similar_min_score = 0
    try:
        ids = [r["itemId"] for r in similar(env, team["kim"], new_i).json()]
        assert set(ids) == {a1, a2}
        assert not {closed_i, deleted_i, superseded_i, elsewhere_i, unlinked_i, held_i, later_i, new_i} & set(ids)
        settings.similar_max_results = 1
        assert len(similar(env, team["kim"], new_i).json()) == 1
        settings.similar_max_results = 10
        settings.similar_scan_max = 1  # 최신 회의 순으로 1건만 훑는다(가장 가까운 과거 회의 업무)
        assert [r["itemId"] for r in similar(env, team["kim"], new_i).json()] == [a1]
    finally:
        settings.similar_min_score, settings.similar_max_results, settings.similar_scan_max = 50, 10, 300


def test_similar_search_errors_and_no_view_record(env, team, project, pair):
    # 볼 수 없는 사람은 404
    assert similar(env, team["sales"], pair["new"]).status_code == 404
    # 프로젝트 회의록이 아니면 409
    _, [plain] = pm(env, team, project, 3, [item()], linked=False)
    assert similar(env, team["head"], plain).status_code == 409
    # 보류·종료·삭제 단계는 409
    held_m, [held_i] = pm(env, team, project, 4, [item()])
    ended_m, [ended_i] = pm(env, team, project, 4, [item()])
    gone_m, [gone_i] = pm(env, team, project, 4, [item()])
    with env["factory"]() as s:
        s.add(MeetingHold(meeting_id=held_m, tenant_id=team["tenant"], on_hold=True))
        s.add(MeetingClosure(meeting_id=ended_m, tenant_id=team["tenant"], ended_at=utcnow(), ended_by=team["head"].id, end_kind="manager"))
        s.add(MeetingClosure(meeting_id=gone_m, tenant_id=team["tenant"], deleted_at=utcnow(), deleted_by=team["head"].id))
        s.commit()
    for locked in (held_i, ended_i, gone_i):
        assert similar(env, team["head"], locked).status_code == 409
    # 열려 있지 않은 업무는 409
    _, [closed_i] = pm(env, team, project, 4, [item(status="closed")])
    assert similar(env, team["head"], closed_i).status_code == 409
    # 열람 기록을 남기지 않는다
    with env["factory"]() as s:
        before = len(list(s.scalars(select(MeetingView.meeting_id))))
    assert similar(env, team["kim"], pair["new"]).status_code == 200
    with env["factory"]() as s:
        assert len(list(s.scalars(select(MeetingView.meeting_id)))) == before


# ================= 대체 실행 =================
def test_supersede_by_lead_records_link_and_history_on_both_items(env, team, project, pair):
    done = supersede(env, team["head"], pair["new"], pair["old"])
    assert done.status_code == 201 and done.json() == {"oldItemId": pair["old"], "newItemId": pair["new"], "projectId": project, "changed": True}
    [row] = links(env)
    assert (row.old_item_id, row.new_item_id, row.project_id, row.superseded_by, row.reason, row.source_request_id) == (
        pair["old"], pair["new"], project, team["head"].id, "오늘 회의 결정으로 대체", None)
    for item_id in (pair["old"], pair["new"]):
        evs = [e for e in events(env, "action_item", item_id) if e.event_type == "item.superseded"]
        assert len(evs) == 1 and evs[0].actor_account_id == team["head"].id
        assert evs[0].payload["oldItemId"] == pair["old"] and evs[0].payload["newItemId"] == pair["new"] and evs[0].payload["reason"] == "오늘 회의 결정으로 대체"
    # 두 회의록의 이력 조회에 모두 "대체"가 보인다
    for meeting_id in (pair["old_m"], pair["new_m"]):
        history = call(env, "GET", team["head"], f"/api/meetings/{meeting_id}/history").json()
        assert [h["kind"] for h in history if h["kindCode"] == "item.superseded"] == ["대체"]
    # 같은 쌍은 200(변경 없음), 다른 업무로 대체돼 있으면 409
    assert supersede(env, team["head"], pair["new"], pair["old"]).status_code == 200 and len(links(env)) == 1
    _, [third] = pm(env, team, project, 6, [item()])
    assert supersede(env, team["head"], third, pair["old"]).status_code == 409
    assert len(events(env, "action_item", pair["old"])) == len([e for e in events(env, "action_item", pair["old"])])


def test_supersede_chain_is_allowed_and_cycle_detection(env, team, project, pair):
    assert supersede(env, team["head"], pair["new"], pair["old"]).status_code == 201
    _, [third] = pm(env, team, project, 7, [item()])
    assert supersede(env, team["head"], third, pair["new"]).status_code == 201  # 대체한 업무가 다시 대체됨
    # 되돌려 대체하려 하면(순환) 거부: 이미 대체된 업무는 열려 있지 않다
    assert supersede(env, team["head"], pair["old"], third).status_code == 409
    assert len(links(env)) == 2
    # 순환 판정 단위: 1→2, 2→3 이 있을 때 3→1 을 더하면 순환
    with env["factory"]() as s:
        assert sup._creates_cycle(s, new_item_id=third, old_item_id=pair["old"]) is False  # 3 에서 시작하면 따라갈 연결이 없다
        assert sup._creates_cycle(s, new_item_id=pair["old"], old_item_id=third) is True  # 1→2→3 이므로 3→1 은 순환
        assert sup._creates_cycle(s, new_item_id=pair["new"], old_item_id=third) is True  # 2→3 이므로 3→2 도 순환


def test_supersede_permissions(env, team, project, pair):
    add = f"/api/projects/{project}/members"
    call(env, "POST", team["head"], add, json={"accountId": team["mgr"].id, "role": "manager"})
    for who in ("kim", "mgr", "head2"):
        assert supersede(env, team[who], pair["new"], pair["old"]).status_code == 403, who
    assert supersede(env, team["sales"], pair["new"], pair["old"]).status_code == 404  # 볼 수 없는 담당자
    assert supersede(env, team["outsider"], pair["new"], pair["old"]).status_code == 404
    assert links(env) == []
    assert supersede(env, team["boss"], pair["new"], pair["old"]).status_code == 201  # 지시자


def test_supersede_conditions(env, team, project, pair):
    head = team["head"]
    # 다른 프로젝트의 업무
    other_project = create(env, team, "head2", dept="sales", name="영업 프로젝트").json()["id"]
    _, [foreign] = pm(env, team, other_project, 1, [item()], registrant="head2")
    assert supersede(env, head, pair["new"], foreign).status_code == 409
    # 같은 회의록
    _, [sibling_old, sibling_new] = pm(env, team, project, 5, [item(), item("다른 업무")])
    assert supersede(env, head, sibling_new, sibling_old).status_code == 409
    # 프로젝트 회의록이 아닌 업무가 새 업무
    _, [plain] = pm(env, team, project, 5, [item()], linked=False)
    assert supersede(env, head, plain, pair["old"]).status_code == 409
    # 순서: 대체되는 쪽의 회의 일시가 더 늦으면 409
    assert supersede(env, head, pair["old"], pair["new"]).status_code == 409
    # 열려 있지 않은 업무
    _, [closed_old] = pm(env, team, project, 2, [item(status="closed")])
    _, [deleted_old] = pm(env, team, project, 2, [item(status="deleted")])
    assert supersede(env, head, pair["new"], closed_old).status_code == 409
    assert supersede(env, head, pair["new"], deleted_old).status_code == 409
    # 보류·종료·삭제 단계의 회의록
    held_m, [held_i] = pm(env, team, project, 2, [item()])
    ended_m, [ended_i] = pm(env, team, project, 2, [item()])
    gone_m, [gone_i] = pm(env, team, project, 2, [item()])
    with env["factory"]() as s:
        s.add(MeetingHold(meeting_id=held_m, tenant_id=team["tenant"], on_hold=True))
        s.add(MeetingClosure(meeting_id=ended_m, tenant_id=team["tenant"], ended_at=utcnow(), ended_by=head.id, end_kind="manager"))
        s.add(MeetingClosure(meeting_id=gone_m, tenant_id=team["tenant"], deleted_at=utcnow(), deleted_by=head.id))
        s.commit()
    for locked in (held_i, ended_i, gone_i):
        assert supersede(env, head, pair["new"], locked).status_code == 409
    # 없는 업무는 404
    assert supersede(env, head, pair["new"], 999999).status_code == 404
    assert links(env) == []
    # 사유: 없음·공백·길이 초과는 422
    for reason in (None, "   ", "가" * 2001):
        assert supersede(env, head, pair["new"], pair["old"], reason=reason).status_code == 422
    assert links(env) == []


def test_supersede_is_one_transaction(env, team, project, pair, monkeypatch):
    import app.services.supersession as module

    calls = []
    real = module.record_change

    def flaky(*args, **kwargs):
        calls.append(1)
        if len(calls) == 2:
            raise RuntimeError("boom")
        return real(*args, **kwargs)

    monkeypatch.setattr(module, "record_change", flaky)
    with pytest.raises(RuntimeError):
        supersede(env, team["head"], pair["new"], pair["old"])
    assert links(env) == []
    assert [e for e in events(env, "action_item", pair["old"]) if e.event_type == "item.superseded"] == []


# ================= 대체된 업무의 제외·포함 =================
@pytest.fixture
def superseded(env, team, project):
    """과거 회의록(kim 만 담당, 확정 대기)의 업무가 오늘 회의 업무로 대체된 상태. 돌려주는 값: dict."""
    kim = team["kim"].id
    old_m, [old_i] = pm(env, team, project, 1, [item(assignee_id=kim)])
    new_m, [new_i] = pm(env, team, project, 5, [item("협력사 부품 재입고 일정 확인 후 공유", assignee_id=kim)])
    control_m, [control_i] = pm(env, team, project, 2, [item("대체되지 않은 업무", assignee_id=kim)])
    return {"old_m": old_m, "old": old_i, "new_m": new_m, "new": new_i, "control_m": control_m, "control": control_i}


def test_superseded_item_leaves_todos_notices_auto_confirm_and_mail(env, team, superseded):
    s_ = superseded
    todo_ids = lambda: [i["itemId"] for i in call(env, "GET", team["kim"], "/api/me/todos").json()["myItems"]["items"]]  # noqa: E731
    assert s_["old"] in todo_ids()
    with env["factory"]() as s:
        for meeting_id, item_id in ((s_["old_m"], s_["old"]), (s_["control_m"], s_["control"])):
            s.add(Notice(tenant_id=team["tenant"], account_id=team["kim"].id, kind="confirmed_notice", entity_type="action_item",
                         entity_id=item_id, meeting_id=meeting_id, payload={}))
        s.commit()
        awaiting, needs, unviewed_before = daily_counts(s)
    assert unviewed_before[team["kim"].id] == 3
    assert supersede(env, team["head"], s_["new"], s_["old"]).status_code == 201
    # 할 일
    assert s_["old"] not in todo_ids() and s_["control"] in todo_ids()
    # 안내
    notice_ids = [n["entityId"] for n in call(env, "GET", team["kim"], "/api/me/notices").json()]
    assert s_["old"] not in notice_ids and s_["control"] in notice_ids
    # 일일 메일의 미열람 집계(대체된 업무만 담당인 회의록은 빠진다)
    with env["factory"]() as s:
        _, _, unviewed_after = daily_counts(s)
    assert unviewed_after[team["kim"].id] == 2
    # 자동 확정
    with env["factory"]() as s:
        for meeting_id in (s_["old_m"], s_["control_m"]):
            s.get(Meeting, meeting_id).auto_confirm_at = utcnow() - timedelta(days=1)
        s.commit()
        run_auto_confirm(s, datetime.now(timezone.utc))
    with env["factory"]() as s:
        assert s.get(ActionItem, s_["old"]).status == "pending"  # 대체된 업무는 확정되지 않는다
        assert s.get(ActionItem, s_["control"]).status == "confirmed"
    # 즉시 메일: 담당자 수신 대상에서 빠진다(대체된 업무만 담당인 회의록은 kim 에게 메일이 쌓이지 않는다)
    with env["factory"]() as s:
        assert queue_immediate_new_minutes(s, s.get(Meeting, s_["old_m"])) == 0
        assert queue_immediate_new_minutes(s, s.get(Meeting, s_["control_m"])) == 1


def test_superseded_confirmed_item_is_not_listed_as_unread_auto_confirmed(env, team, project):
    kim = team["kim"].id
    m1, [i1] = pm(env, team, project, 1, [item(status="confirmed", assignee_id=kim, confirm_kind="period_elapsed", confirmed_at=utcnow())], status="confirmed")
    _, [new_i] = pm(env, team, project, 5, [item()])
    unread = lambda: [e["itemId"] for e in call(env, "GET", team["head"], "/api/me/todos").json()["unreadAutoConfirmed"]["items"] if e["entityType"] == "action_item"]  # noqa: E731
    assert i1 in unread()
    assert supersede(env, team["head"], new_i, i1).status_code == 201
    assert i1 not in unread()


def test_superseded_item_stays_in_ledger_export_history_and_conditions(env, team, project, superseded):
    s_ = superseded
    assert supersede(env, team["head"], s_["new"], s_["old"]).status_code == 201
    # 업무 원장: 계속 보이고 대체 정보가 표시된다
    old_detail = call(env, "GET", team["head"], f"/api/meetings/{s_['old_m']}").json()
    [old_out] = old_detail["actionItems"]
    assert old_out["id"] == s_["old"] and old_out["status"] == "pending"
    assert old_out["supersededBy"] == {"itemId": s_["new"], "meetingId": s_["new_m"], "meetingTitle": old_out["supersededBy"]["meetingTitle"]}
    assert old_out["supersededBy"]["meetingTitle"] and old_out["supersedes"] == []
    new_out = call(env, "GET", team["head"], f"/api/meetings/{s_['new_m']}").json()["actionItems"][0]
    assert [r["itemId"] for r in new_out["supersedes"]] == [s_["old"]] and new_out["supersededBy"] is None
    # 엑셀 업무 시트 상태 라벨
    export = call(env, "GET", team["head"], f"/api/meetings/{s_['old_m']}/export")
    sheet = load_workbook(io.BytesIO(export.content))["업무"]
    header = [c.value for c in sheet[1]]
    assert sheet[2][header.index("상태")].value == "대체됨"
    # 조건 함수: 보여 주는 조건은 포함, 열려 있음·확정 대기는 제외, 끝남(안내 숨김)은 포함
    with env["factory"]() as s:
        pick = lambda cond: sorted(s.scalars(select(ActionItem.id).where(cond())))  # noqa: E731
        assert s_["old"] in pick(not_deleted_item) and s_["old"] not in pick(deleted_item)
        assert s_["old"] not in pick(open_item) and s_["old"] not in pick(pending_item) and s_["old"] not in pick(live_item)
        assert s_["old"] in pick(inactive_item) and s_["new"] in pick(open_item) and s_["new"] in pick(live_item)
    # 목록의 보완 필요 수 집계는 대체된 업무를 세지 않는다
    with env["factory"]() as s:
        s.get(ActionItem, s_["old"]).title = ""
        s.commit()
    row = next(m for m in call(env, "GET", team["head"], "/api/meetings").json()["items"] if m["id"] == s_["old_m"])
    assert row["itemCount"] == 1 and row["needsCompletionCount"] == 0


def test_superseded_item_is_protected_and_skipped_by_upload_update(env, team, superseded):
    s_ = superseded
    export = call(env, "GET", team["head"], f"/api/meetings/{s_['old_m']}/export")
    assert supersede(env, team["head"], s_["new"], s_["old"]).status_code == 201
    old = s_["old"]
    for response in (
        call(env, "PATCH", team["head"], f"/api/action-items/{old}", json={"title": "바꿈"}),
        call(env, "POST", team["head"], f"/api/action-items/{old}/confirm"),
        call(env, "POST", team["head"], f"/api/action-items/{old}/close", json=REASON),
        call(env, "POST", team["head"], f"/api/action-items/{old}/delete", json=REASON),
    ):
        assert response.status_code == 409 and "대체된 업무" in response.json()["detail"]
    preview = call(env, "POST", team["head"], f"/api/meetings/{s_['old_m']}/update-upload/preview",
                   files={"file": ("a.xlsx", export.content, "application/octet-stream")})
    assert preview.status_code == 200 and "대체됨" in preview.text
    # 수정 요청 남기기는 기존대로 허용
    assert call(env, "POST", team["kim"], f"/api/meetings/{s_['old_m']}/change-requests", json={"comment": "확인", "itemId": old}).status_code == 201
    # 대체하는 업무는 그대로 쓸 수 있다
    assert call(env, "PATCH", team["head"], f"/api/action-items/{s_['new']}", json={"title": "새 업무명"}).status_code == 200


def test_reference_title_is_null_when_other_meeting_is_not_viewable(env, team, superseded):
    s_ = superseded
    assert supersede(env, team["head"], s_["new"], s_["old"]).status_code == 201
    with env["factory"]() as s:  # 대체 뒤에 과거 회의록이 보류됨 → 담당자(kim)는 그 회의록을 볼 수 없다
        s.add(MeetingHold(meeting_id=s_["old_m"], tenant_id=team["tenant"], on_hold=True))
        s.commit()
    kim_view = call(env, "GET", team["kim"], f"/api/meetings/{s_['new_m']}").json()["actionItems"][0]
    assert kim_view["supersedes"] == [{"itemId": s_["old"], "meetingId": s_["old_m"], "meetingTitle": None}]
    head_view = call(env, "GET", team["head"], f"/api/meetings/{s_['new_m']}").json()["actionItems"][0]
    assert head_view["supersedes"][0]["meetingTitle"]


# ================= 대체 요청 =================
def make_request(env, account, meeting_id, new_item_id, old_item_id, comment="같은 업무입니다"):
    body = {"comment": comment, "itemId": new_item_id, "kind": "supersede", "supersedesItemId": old_item_id}
    return call(env, "POST", account, f"/api/meetings/{meeting_id}/change-requests", json=body)


def test_supersede_request_creation_validation_and_output(env, team, project, pair):
    created = make_request(env, team["kim"], pair["new_m"], pair["new"], pair["old"])
    assert created.status_code == 201, created.text
    rid = created.json()["requestId"]
    listed = call(env, "GET", team["kim"], f"/api/meetings/{pair['new_m']}/change-requests").json()
    assert listed[0]["requestId"] == rid and listed[0]["kind"] == "supersede" and listed[0]["supersedesItemId"] == pair["old"]
    assert links(env) == []  # 요청만으로는 대체되지 않는다
    # 기존 수정 요청(종류 없음)은 edit 으로 동작
    plain = call(env, "POST", team["kim"], f"/api/meetings/{pair['new_m']}/change-requests", json={"comment": "수정 부탁", "itemId": pair["new"]})
    assert plain.status_code == 201
    listed = {r["requestId"]: r for r in call(env, "GET", team["kim"], f"/api/meetings/{pair['new_m']}/change-requests").json()}
    assert listed[plain.json()["requestId"]]["kind"] == "edit" and listed[plain.json()["requestId"]]["supersedesItemId"] is None
    # 입력 검증
    assert call(env, "POST", team["kim"], f"/api/meetings/{pair['new_m']}/change-requests",
                json={"comment": "x", "itemId": pair["new"], "kind": "supersede"}).status_code == 422
    assert call(env, "POST", team["kim"], f"/api/meetings/{pair['new_m']}/change-requests",
                json={"comment": "x", "itemId": pair["new"], "supersedesItemId": pair["old"]}).status_code == 422
    # 조건 위반은 대체와 같은 코드(권한 제외)
    other_project = create(env, team, "head2", dept="sales", name="영업 프로젝트").json()["id"]
    _, [foreign] = pm(env, team, other_project, 1, [item()], registrant="head2")
    assert make_request(env, team["head"], pair["new_m"], pair["new"], foreign).status_code == 409
    assert make_request(env, team["head"], pair["new_m"], pair["new"], pair["new"]).status_code in (409, 422)
    assert make_request(env, team["head"], pair["new_m"], pair["new"], 999999).status_code == 404
    assert make_request(env, team["head"], pair["old_m"], pair["old"], pair["new"]).status_code == 409  # 순서 위반
    # 열람할 수 없는 사람은 404
    assert make_request(env, team["sales"], pair["new_m"], pair["new"], pair["old"]).status_code == 404
    # "수정 요청 대기" 응답에 kind·supersedesItemId
    todos = call(env, "GET", team["head"], "/api/me/todos").json()["pendingChangeRequests"]["items"]
    by_id = {t["requestId"]: t for t in todos}
    assert by_id[rid]["kind"] == "supersede" and by_id[rid]["supersedesItemId"] == pair["old"]


def test_accepting_supersede_request_executes_supersession(env, team, project, pair):
    add = f"/api/projects/{project}/members"
    call(env, "POST", team["head"], add, json={"accountId": team["mgr"].id, "role": "manager"})
    rid = make_request(env, team["kim"], pair["new_m"], pair["new"], pair["old"]).json()["requestId"]
    resolve = lambda who, **body: call(env, "POST", who, f"/api/meetings/{pair['new_m']}/change-requests/{rid}/resolve", json=body)  # noqa: E731
    # 대체 권한이 없는 처리자의 수락은 403(요청은 대기로 남는다)
    assert resolve(team["mgr"], decision="accepted").status_code == 403
    assert links(env) == [] and call(env, "GET", team["kim"], f"/api/meetings/{pair['new_m']}/change-requests").json()[0]["resolution"] is None
    # 조건이 맞지 않으면 409 로 거부되고 요청은 대기로 남는다(과거 회의록이 보류됨)
    with env["factory"]() as s:
        hold = MeetingHold(meeting_id=pair["old_m"], tenant_id=team["tenant"], on_hold=True)
        s.add(hold)
        s.commit()
    assert resolve(team["head"], decision="accepted").status_code == 409
    assert links(env) == [] and call(env, "GET", team["head"], f"/api/meetings/{pair['new_m']}/change-requests").json()[0]["resolution"] is None
    with env["factory"]() as s:
        s.delete(s.get(MeetingHold, pair["old_m"]))
        s.commit()
    # 수락: 같은 트랜잭션으로 대체가 실행되고 source_request_id 가 기록된다. 사유는 답변이 없으면 요청 코멘트
    done = resolve(team["head"], decision="accepted")
    assert done.status_code == 200 and done.json()["resolution"]["decision"] == "accepted" and done.json()["kind"] == "supersede"
    [row] = links(env)
    assert (row.old_item_id, row.new_item_id, row.source_request_id, row.reason, row.superseded_by) == (
        pair["old"], pair["new"], rid, "같은 업무입니다", team["head"].id)
    # 이미 대체돼 있으면 다시 수락해도 오류 없이 유지(같은 쌍)
    assert resolve(team["head"], decision="accepted", reason="답변 사유").status_code == 200 and len(links(env)) == 1


def test_accepting_with_reply_uses_reply_as_reason_and_reject_does_not_supersede(env, team, project, pair):
    rid = make_request(env, team["kim"], pair["new_m"], pair["new"], pair["old"]).json()["requestId"]
    path = f"/api/meetings/{pair['new_m']}/change-requests/{rid}/resolve"
    assert call(env, "POST", team["head"], path, json={"decision": "rejected", "reason": "다른 업무"}).status_code == 200
    assert links(env) == []
    assert call(env, "POST", team["head"], path, json={"decision": "accepted", "reason": "오늘 회의에서 합치기로 함"}).status_code == 200
    assert links(env)[0].reason == "오늘 회의에서 합치기로 함"


# ================= 허용 동작 =================
def item_actions(env, who, meeting_id, item_id):
    detail = call(env, "GET", who, f"/api/meetings/{meeting_id}").json()
    return next(i["allowedActions"] for i in detail["actionItems"] if i["id"] == item_id)


def test_allowed_actions_by_role_and_state(env, team, project, pair):
    add = f"/api/projects/{project}/members"
    call(env, "POST", team["head"], add, json={"accountId": team["mgr"].id, "role": "manager"})
    three = {"find_similar_items", "request_supersede", "supersede_item"}
    got = lambda who: three & set(item_actions(env, team[who], pair["new_m"], pair["new"]))  # noqa: E731
    assert got("head") == three and got("boss") == three
    assert got("kim") == {"find_similar_items", "request_supersede"} and got("mgr") == {"find_similar_items", "request_supersede"}
    # 실제 API 결과와 일치
    assert similar(env, team["kim"], pair["new"]).status_code == 200
    assert supersede(env, team["kim"], pair["new"], pair["old"]).status_code == 403
    assert make_request(env, team["kim"], pair["new_m"], pair["new"], pair["old"]).status_code == 201
    # 프로젝트 회의록이 아니면 나오지 않는다
    plain_m, [plain] = pm(env, team, project, 5, [item()], linked=False)
    assert not three & set(item_actions(env, team["head"], plain_m, plain))
    # 보류된 회의록(총괄은 볼 수 있음)에서는 나오지 않고 API 도 409
    held_m, [held_i] = pm(env, team, project, 6, [item()])
    with env["factory"]() as s:
        s.add(MeetingHold(meeting_id=held_m, tenant_id=team["tenant"], on_hold=True))
        s.commit()
    assert not three & set(item_actions(env, team["head"], held_m, held_i))
    assert similar(env, team["head"], held_i).status_code == 409
    # 종결된 업무에는 나오지 않는다
    closed_m, [closed_i] = pm(env, team, project, 6, [item(status="closed")])
    assert not three & set(item_actions(env, team["head"], closed_m, closed_i))
    # 대체된 업무에는 쓰기 동작이 나오지 않는다(수정 요청만)
    assert supersede(env, team["head"], pair["new"], pair["old"]).status_code == 201
    assert item_actions(env, team["head"], pair["old_m"], pair["old"]) == ["request_change"]
    # 대체하는 업무는 계속 쓸 수 있다
    assert {"confirm_item", "set_due"} <= set(item_actions(env, team["head"], pair["new_m"], pair["new"]))


# ================= 마이그레이션·가드 =================
def test_migration_roundtrip_and_downgrade_guard(tmp_path):
    url = f"sqlite:///{(tmp_path / 'm.db').as_posix()}"
    cfg = _alembic(url)
    command.upgrade(cfg, "head")
    command.downgrade(cfg, _BEFORE_SUPERSESSIONS)
    command.upgrade(cfg, "head")
    command.check(cfg)
    # 행이 있으면 되돌리기 거부(외래키 검사를 끈 직접 연결로 행만 넣는다)
    raw = sqlite3.connect(tmp_path / "m.db")
    raw.execute("INSERT INTO item_supersessions (tenant_id, old_item_id, new_item_id, project_id, superseded_by, reason, created_at) "
                "VALUES (1, 1, 2, 1, 1, 'r', '2026-10-01')")
    raw.commit()
    raw.close()
    with pytest.raises(RuntimeError, match="downgrade 거부"):
        command.downgrade(cfg, _BEFORE_SUPERSESSIONS)
    check = sqlite3.connect(tmp_path / "m.db")
    try:
        assert check.execute("SELECT COUNT(*) FROM item_supersessions").fetchone()[0] == 1
        with pytest.raises(sqlite3.IntegrityError):
            check.execute("INSERT INTO item_supersessions (tenant_id, old_item_id, new_item_id, project_id, superseded_by, reason, created_at) "
                          "VALUES (1, 5, 5, 1, 1, 'r', '2026-10-01')")
    finally:
        check.close()


def test_alembic_offline_postgresql_ddl_has_supersession_table():
    cfg = _alembic("postgresql+psycopg://u:p@localhost/db")
    buffer = io.StringIO()
    cfg.output_buffer = buffer
    command.upgrade(cfg, "head", sql=True)
    sql = buffer.getvalue()
    assert "CREATE TABLE item_supersessions" in sql
    assert "ck_item_supersessions_old_new_differ" in sql and "uq_item_supersessions_old_item_id" in sql


def test_supersession_table_is_not_used_outside_its_owners():
    """대체 조건·표는 item_conditions(조건), supersession 서비스(읽기·쓰기), 모델 정의와 등록 파일 밖에서 직접 쓰지 않는다."""
    allowed = {"models/item_conditions.py", "models/supersession.py", "models/__init__.py", "services/supersession.py"}
    offenders = []
    for path in (_ROOT / "app").rglob("*.py"):
        rel = path.relative_to(_ROOT / "app").as_posix()
        if rel in allowed:
            continue
        if re.search(r"\bItemSupersession\b|item_supersessions", path.read_text(encoding="utf-8")):
            offenders.append(rel)
    assert offenders == [], offenders
