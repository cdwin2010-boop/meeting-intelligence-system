"""4b-2b단계: 쓰기 API — 업무 보완·확정, 회의록 확정, 수정 요청(기록·조회·해결).
네트워크 없음. env 픽스처와 도우미는 기존 테스트 모듈의 것을 쓴다."""
from datetime import date

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.api import action_items as action_items_api
from app.main import app
from app.models import ActionItem, Event, Meeting
from tests.test_meeting_queries import make_meeting
from tests.test_upload_processing import add_account, auth, env  # noqa: F401  (env 는 픽스처)


def call(env, method: str, account, path: str, **kwargs):
    return env["client"].request(method, path, headers=auth(account), **kwargs)


def item_ids(factory, meeting_id: int) -> list[int]:
    with factory() as s:
        return list(s.scalars(select(ActionItem.id).where(ActionItem.meeting_id == meeting_id).order_by(ActionItem.id)))


def load_item(factory, item_id: int) -> ActionItem:
    with factory() as s:
        return s.get(ActionItem, item_id)


def load_meeting(factory, meeting_id: int) -> Meeting:
    with factory() as s:
        return s.get(Meeting, meeting_id)


def events(factory, event_type: str | None = None) -> list[Event]:
    with factory() as s:
        stmt = select(Event).order_by(Event.id)
        if event_type:
            stmt = stmt.where(Event.event_type == event_type)
        return s.scalars(stmt).all()


@pytest.fixture
def team(env):
    f = env["factory"]
    return {
        "mgr": add_account(f, "고객사A", "mgr", "manager", name="박관리"),
        "exe": add_account(f, "고객사A", "boss", "executive", name="최임원"),
        "staff": add_account(f, "고객사A", "kim", "staff", name="김담당"),
        "lee": add_account(f, "고객사A", "lee", "staff", name="이서연"),
        "outsider": add_account(f, "고객사B", "out", "executive", name="외부"),
    }


# ---------------- 권한·범위 ----------------
def test_staff_cannot_patch_or_confirm(env, team):
    meeting_id = make_meeting(env["factory"], team["mgr"], items=[{"assignee_id": team["staff"].id}], participants=[team["staff"]])
    [item_id] = item_ids(env["factory"], meeting_id)
    staff = team["staff"]
    assert call(env, "PATCH", staff, f"/api/action-items/{item_id}", json={"title": "x"}).status_code == 403
    assert call(env, "POST", staff, f"/api/action-items/{item_id}/confirm").status_code == 403
    assert call(env, "POST", staff, f"/api/meetings/{meeting_id}/confirm").status_code == 403


def test_other_tenant_and_missing_get_404(env, team):
    meeting_id = make_meeting(env["factory"], team["mgr"], items=[{"assignee_id": team["mgr"].id}])
    [item_id] = item_ids(env["factory"], meeting_id)
    out = team["outsider"]
    for method, path, kwargs in [
        ("PATCH", f"/api/action-items/{item_id}", {"json": {"title": "x"}}),
        ("POST", f"/api/action-items/{item_id}/confirm", {}),
        ("POST", f"/api/meetings/{meeting_id}/confirm", {}),
        ("POST", f"/api/meetings/{meeting_id}/change-requests", {"json": {"comment": "x"}}),
        ("GET", f"/api/meetings/{meeting_id}/change-requests", {}),
        ("PATCH", "/api/action-items/999999", {"json": {"title": "x"}}),
    ]:
        assert call(env, method, out, path, **kwargs).status_code == 404, path


def test_staff_cannot_request_change_on_unrelated_meeting(env, team):
    unrelated = make_meeting(env["factory"], team["mgr"])
    res = call(env, "POST", team["staff"], f"/api/meetings/{unrelated}/change-requests", json={"comment": "확인 부탁"})
    assert res.status_code == 404


# ---------------- 업무 수정 ----------------
def test_patch_updates_fields_and_records_before_after(env, team):
    meeting_id = make_meeting(env["factory"], team["mgr"], items=[{"title": "원래 업무", "assignee_id": None}])
    [item_id] = item_ids(env["factory"], meeting_id)
    res = call(env, "PATCH", team["mgr"], f"/api/action-items/{item_id}",
               json={"title": "  견적서 송부\t", "assigneeId": team["lee"].id, "dueDate": "2026-10-09"})
    assert res.status_code == 200
    body = res.json()
    assert body["title"] == "견적서 송부" and body["assignee"] == {"id": team["lee"].id, "name": "이서연"}
    assert body["dueDate"] == "2026-10-09" and body["needsCompletion"] is False and body["missingFields"] == []

    [updated] = events(env["factory"], "item.updated")
    assert updated.actor_account_id == team["mgr"].id
    assert updated.payload == {
        "before": {"title": "원래 업무", "assigneeId": None, "dueDate": None},
        "after": {"title": "견적서 송부", "assigneeId": team["lee"].id, "dueDate": "2026-10-09"},
    }


@pytest.mark.parametrize("who", ["outsider", "inactive", "missing"])
def test_patch_rejects_invalid_assignee(env, team, who):
    meeting_id = make_meeting(env["factory"], team["mgr"], items=[{"assignee_id": None}])
    [item_id] = item_ids(env["factory"], meeting_id)
    if who == "outsider":
        assignee_id = team["outsider"].id
    elif who == "inactive":
        off = add_account(env["factory"], "고객사A", "off", "staff")
        with env["factory"]() as s:
            s.get(type(off), off.id).is_active = False
            s.commit()
        assignee_id = off.id
    else:
        assignee_id = 999999
    res = call(env, "PATCH", team["mgr"], f"/api/action-items/{item_id}", json={"assigneeId": assignee_id})
    assert res.status_code == 400
    assert load_item(env["factory"], item_id).assignee_id is None
    assert events(env["factory"], "item.updated") == []


def test_patch_assignee_null_is_allowed(env, team):
    meeting_id = make_meeting(env["factory"], team["mgr"], items=[{"assignee_id": team["lee"].id}])
    [item_id] = item_ids(env["factory"], meeting_id)
    res = call(env, "PATCH", team["mgr"], f"/api/action-items/{item_id}", json={"assigneeId": None})
    assert res.status_code == 200 and res.json()["assignee"] is None and "assignee" in res.json()["missingFields"]


def test_due_date_and_undetermined_rules(env, team):
    meeting_id = make_meeting(env["factory"], team["mgr"], items=[{"assignee_id": team["lee"].id, "due_date": None}])
    [item_id] = item_ids(env["factory"], meeting_id)
    path = f"/api/action-items/{item_id}"
    mgr = team["mgr"]

    # 미확정 선택 → 날짜 비움, 보완 아님
    body = call(env, "PATCH", mgr, path, json={"dueDate": None, "dueUndetermined": True}).json()
    assert body["dueDate"] is None and body["dueUndetermined"] is True and body["missingFields"] == []
    # 날짜 입력 → 미확정 해제
    body = call(env, "PATCH", mgr, path, json={"dueDate": "2026-10-09"}).json()
    assert body["dueDate"] == "2026-10-09" and body["dueUndetermined"] is False
    # 미확정만 보내면 날짜를 비운다
    body = call(env, "PATCH", mgr, path, json={"dueUndetermined": True}).json()
    assert body["dueDate"] is None and body["dueUndetermined"] is True
    # 둘 다 채우면 400, 값은 그대로
    res = call(env, "PATCH", mgr, path, json={"dueDate": "2026-10-09", "dueUndetermined": True})
    assert res.status_code == 400
    item = load_item(env["factory"], item_id)
    assert item.due_date is None and item.due_undetermined is True
    # 날짜를 지우고 미확정도 아니면 보완 필요(기한 빈칸)
    body = call(env, "PATCH", mgr, path, json={"dueUndetermined": False}).json()
    assert body["missingFields"] == ["dueDate"]


@pytest.mark.parametrize("title", ["", "   ", "\t\n", "　", " ​ ", "\r\n\t "])
def test_blank_title_rejected(env, team, title):
    meeting_id = make_meeting(env["factory"], team["mgr"], items=[{"title": "원래"}])
    [item_id] = item_ids(env["factory"], meeting_id)
    res = call(env, "PATCH", team["mgr"], f"/api/action-items/{item_id}", json={"title": title})
    assert res.status_code == 400
    assert load_item(env["factory"], item_id).title == "원래"


def test_patch_deleted_item_is_409_and_empty_body_400(env, team):
    meeting_id = make_meeting(env["factory"], team["mgr"], items=[{"status": "deleted"}, {"title": "살아 있음"}])
    deleted_id, alive_id = item_ids(env["factory"], meeting_id)
    assert call(env, "PATCH", team["mgr"], f"/api/action-items/{deleted_id}", json={"title": "x"}).status_code == 409
    assert call(env, "PATCH", team["mgr"], f"/api/action-items/{alive_id}", json={}).status_code == 400


# ---------------- 업무 확정 ----------------
def test_confirm_item_requires_completion_then_is_idempotent(env, team):
    meeting_id = make_meeting(env["factory"], team["mgr"], items=[{"assignee_id": None, "due_date": None}])
    [item_id] = item_ids(env["factory"], meeting_id)
    path = f"/api/action-items/{item_id}/confirm"

    res = call(env, "POST", team["mgr"], path)
    assert res.status_code == 409
    assert res.json()["detail"]["missingFields"] == ["assignee", "dueDate"]
    assert load_item(env["factory"], item_id).status == "pending"

    call(env, "PATCH", team["mgr"], f"/api/action-items/{item_id}", json={"assigneeId": team["lee"].id, "dueUndetermined": True})
    first = call(env, "POST", team["exe"], path)
    assert first.status_code == 200 and first.json()["status"] == "confirmed" and first.json()["confirmKind"] == "manager"
    again = call(env, "POST", team["mgr"], path)
    assert again.status_code == 200 and again.json()["status"] == "confirmed"

    item = load_item(env["factory"], item_id)
    assert item.confirmed_by == team["exe"].id and item.confirmed_at is not None
    assert len(events(env["factory"], "item.confirmed")) == 1  # 멱등: 사건은 한 번만


# ---------------- 회의록 확정 ----------------
def test_confirm_meeting_ignores_item_completion(env, team):
    # 담당자 등록 회의록: 참석한 관리자가 총괄로 확정(㉟)
    meeting_id = make_meeting(env["factory"], team["staff"], items=[{"assignee_id": None}], participants=[team["mgr"]])
    res = call(env, "POST", team["mgr"], f"/api/meetings/{meeting_id}/confirm")
    assert res.status_code == 200
    body = res.json()
    assert body["status"] == "confirmed" and body["confirmKind"] == "manager"
    assert body["confirmedBy"] == {"id": team["mgr"].id, "name": "박관리"}
    assert body["confirmedItemIds"] == [] and body["skippedItemIds"] == []
    [item_id] = item_ids(env["factory"], meeting_id)
    assert load_item(env["factory"], item_id).status == "pending"  # 업무는 그대로

    again = call(env, "POST", team["exe"], f"/api/meetings/{meeting_id}/confirm")
    assert again.status_code == 200 and again.json()["confirmedBy"]["id"] == team["mgr"].id
    assert len(events(env["factory"], "meeting.confirmed")) == 1


@pytest.mark.parametrize("status_", ["processing", "failed", "no_content"])
def test_confirm_meeting_in_wrong_state_is_409(env, team, status_):
    meeting_id = make_meeting(env["factory"], team["mgr"], status=status_)
    assert call(env, "POST", team["mgr"], f"/api/meetings/{meeting_id}/confirm").status_code == 409
    assert load_meeting(env["factory"], meeting_id).status == status_


def test_confirm_meeting_with_items_reports_skipped(env, team):
    meeting_id = make_meeting(env["factory"], team["staff"], items=[
        {"assignee_id": team["lee"].id, "due_date": date(2026, 10, 9)},  # 확정
        {"assignee_id": None},  # 보완 필요 → 건너뜀
        {"assignee_id": team["lee"].id, "due_undetermined": True},  # 미확정 선택 → 확정
        {"assignee_id": team["lee"].id, "due_undetermined": True, "status": "deleted"},  # 삭제 → 대상 아님
    ], participants=[team["mgr"]])  # 참석 관리자 = 총괄(㉟)
    ok1, skipped, ok2, deleted = item_ids(env["factory"], meeting_id)
    res = call(env, "POST", team["mgr"], f"/api/meetings/{meeting_id}/confirm", params={"withItems": "true"})
    assert res.status_code == 200
    assert res.json()["confirmedItemIds"] == [ok1, ok2] and res.json()["skippedItemIds"] == [skipped]
    assert [load_item(env["factory"], i).status for i in (ok1, skipped, ok2, deleted)] == ["confirmed", "pending", "confirmed", "deleted"]


def test_clock_never_resets_on_edit_or_confirm(env, team):
    meeting_id = make_meeting(env["factory"], team["staff"], items=[{"assignee_id": None}])
    [item_id] = item_ids(env["factory"], meeting_id)
    before = load_meeting(env["factory"], meeting_id)

    call(env, "PATCH", team["mgr"], f"/api/action-items/{item_id}", json={"assigneeId": team["lee"].id, "dueUndetermined": True})
    call(env, "POST", team["mgr"], f"/api/action-items/{item_id}/confirm")
    call(env, "POST", team["staff"], f"/api/meetings/{meeting_id}/change-requests", json={"comment": "기한 조정 부탁"})
    call(env, "POST", team["mgr"], f"/api/meetings/{meeting_id}/confirm", params={"withItems": "true"})

    after = load_meeting(env["factory"], meeting_id)
    assert after.first_created_at == before.first_created_at
    assert after.auto_confirm_at == before.auto_confirm_at


# ---------------- 수정 요청 ----------------
def test_change_request_is_event_only(env, team):
    meeting_id = make_meeting(env["factory"], team["mgr"], items=[{"assignee_id": team["staff"].id}])
    [item_id] = item_ids(env["factory"], meeting_id)
    before_meeting = load_meeting(env["factory"], meeting_id)
    before_item = load_item(env["factory"], item_id)

    res = call(env, "POST", team["staff"], f"/api/meetings/{meeting_id}/change-requests",
               json={"comment": "  담당자가 바뀌었습니다  ", "itemId": item_id})
    assert res.status_code == 201
    request_id = res.json()["requestId"]
    [created] = events(env["factory"], "change_request.created")
    assert created.id == request_id and created.payload == {"comment": "담당자가 바뀌었습니다", "itemId": item_id}

    after_meeting, after_item = load_meeting(env["factory"], meeting_id), load_item(env["factory"], item_id)
    assert (after_meeting.status, after_meeting.auto_confirm_at, after_meeting.first_created_at) == (
        before_meeting.status, before_meeting.auto_confirm_at, before_meeting.first_created_at)
    assert (after_item.status, after_item.assignee_id) == (before_item.status, before_item.assignee_id)


@pytest.mark.parametrize("payload", [{"comment": ""}, {"comment": "   "}, {"comment": "x" * 2001}, {}])
def test_change_request_comment_validation(env, team, payload):
    meeting_id = make_meeting(env["factory"], team["mgr"])
    assert call(env, "POST", team["mgr"], f"/api/meetings/{meeting_id}/change-requests", json=payload).status_code == 422


def test_change_request_item_must_belong_to_meeting(env, team):
    meeting_a = make_meeting(env["factory"], team["mgr"])
    meeting_b = make_meeting(env["factory"], team["mgr"], items=[{"title": "다른 회의 업무"}])
    [other_item] = item_ids(env["factory"], meeting_b)
    res = call(env, "POST", team["mgr"], f"/api/meetings/{meeting_a}/change-requests", json={"comment": "x", "itemId": other_item})
    assert res.status_code == 400


def test_change_request_list_and_resolution_priority(env, team):
    meeting_id = make_meeting(env["factory"], team["mgr"], participants=[team["staff"]])
    base = f"/api/meetings/{meeting_id}/change-requests"
    request_id = call(env, "POST", team["staff"], base, json={"comment": "업무명 수정 요청"}).json()["requestId"]

    [listed] = call(env, "GET", team["staff"], base).json()
    assert listed["requestId"] == request_id and listed["requester"] == {"id": team["staff"].id, "name": "김담당"}
    assert listed["comment"] == "업무명 수정 요청" and listed["itemId"] is None and listed["resolution"] is None

    resolve = f"{base}/{request_id}/resolve"
    assert call(env, "POST", team["staff"], resolve, json={"decision": "accepted"}).status_code == 403

    # manager 결정 → executive 덮어쓰기 가능
    assert call(env, "POST", team["mgr"], resolve, json={"decision": "rejected", "reason": "근거 부족"}).status_code == 200
    res = call(env, "POST", team["exe"], resolve, json={"decision": "accepted"})
    assert res.status_code == 200 and res.json()["resolution"]["decision"] == "accepted"
    # executive 결정 후 manager 는 409
    res = call(env, "POST", team["mgr"], resolve, json={"decision": "rejected"})
    assert res.status_code == 409
    # executive 는 다시 덮어쓸 수 있음(같은 직급)
    assert call(env, "POST", team["exe"], resolve, json={"decision": "rejected"}).status_code == 200

    [listed] = call(env, "GET", team["mgr"], base).json()
    assert listed["resolution"]["decision"] == "rejected"
    assert listed["resolution"]["resolvedBy"] == {"id": team["exe"].id, "name": "최임원"}
    # 이전 결정 사건은 모두 남아 있다
    resolved = events(env["factory"], "change_request.resolved")
    assert [(e.payload["before"], e.payload["after"], e.payload["rank"]) for e in resolved] == [
        (None, "rejected", "manager"), ("rejected", "accepted", "executive"), ("accepted", "rejected", "executive"),
    ]


def test_resolve_unknown_request_is_404(env, team):
    meeting_id = make_meeting(env["factory"], team["mgr"])
    other = make_meeting(env["factory"], team["mgr"])
    request_id = call(env, "POST", team["mgr"], f"/api/meetings/{other}/change-requests", json={"comment": "x"}).json()["requestId"]
    res = call(env, "POST", team["mgr"], f"/api/meetings/{meeting_id}/change-requests/{request_id}/resolve",
               json={"decision": "accepted"})
    assert res.status_code == 404


# ---------------- 트랜잭션 ----------------
def test_change_and_event_roll_back_together(env, team, monkeypatch):
    """이벤트 기록이 실패하면 데이터 변경도 남지 않는다(같은 트랜잭션)."""
    meeting_id = make_meeting(env["factory"], team["mgr"], items=[{"title": "원래", "assignee_id": None}])
    [item_id] = item_ids(env["factory"], meeting_id)
    events_before = len(events(env["factory"]))

    def broken_append_event(*args, **kwargs):
        raise RuntimeError("event store unavailable")

    monkeypatch.setattr(action_items_api, "append_event", broken_append_event)
    client = TestClient(app, raise_server_exceptions=False)
    res = client.patch(f"/api/action-items/{item_id}", headers=auth(team["mgr"]),
                       json={"title": "바뀐 업무", "assigneeId": team["lee"].id})
    assert res.status_code == 500

    item = load_item(env["factory"], item_id)
    assert item.title == "원래" and item.assignee_id is None
    assert len(events(env["factory"])) == events_before


# ---------------- 확정된 업무 빈칸 차단 ----------------
def _confirmed_item(env, team, **fields) -> int:
    meeting_id = make_meeting(env["factory"], team["mgr"], items=[{
        "title": "견적서 송부", "assignee_id": team["lee"].id, "due_date": date(2026, 10, 9),
        "status": "confirmed", "confirm_kind": "manager", "confirmed_by": team["mgr"].id, **fields,
    }])
    [item_id] = item_ids(env["factory"], meeting_id)
    return item_id


@pytest.mark.parametrize(
    ("payload", "missing"),
    [
        ({"assigneeId": None}, ["assignee"]),
        ({"title": " \t\n"}, ["title"]),
        ({"dueDate": None}, ["dueDate"]),
        ({"title": "", "assigneeId": None}, ["title", "assignee"]),
        ({"dueUndetermined": False, "dueDate": None}, ["dueDate"]),
    ],
)
def test_confirmed_item_cannot_be_blanked(env, team, payload, missing):
    item_id = _confirmed_item(env, team)
    before = load_item(env["factory"], item_id)
    events_before = len(events(env["factory"]))

    res = call(env, "PATCH", team["mgr"], f"/api/action-items/{item_id}", json=payload)
    assert res.status_code == 400
    assert res.json()["detail"]["missingFields"] == missing and "message" in res.json()["detail"]

    after = load_item(env["factory"], item_id)
    assert (after.title, after.assignee_id, after.due_date, after.due_undetermined, after.status) == (
        before.title, before.assignee_id, before.due_date, before.due_undetermined, before.status)
    assert len(events(env["factory"])) == events_before


def test_confirmed_item_accepts_valid_replacements(env, team):
    item_id = _confirmed_item(env, team)
    path = f"/api/action-items/{item_id}"
    mgr = team["mgr"]

    res = call(env, "PATCH", mgr, path, json={"assigneeId": team["staff"].id})  # 담당자 교체
    assert res.status_code == 200 and res.json()["assignee"]["id"] == team["staff"].id
    res = call(env, "PATCH", mgr, path, json={"dueUndetermined": True})  # 날짜 → 미확정
    assert res.status_code == 200 and res.json()["dueDate"] is None and res.json()["dueUndetermined"] is True
    res = call(env, "PATCH", mgr, path, json={"dueDate": "2026-10-16"})  # 미확정 → 날짜
    assert res.status_code == 200 and res.json()["dueDate"] == "2026-10-16" and res.json()["dueUndetermined"] is False
    res = call(env, "PATCH", mgr, path, json={"title": " 견적서 재송부 "})  # 업무명 수정
    assert res.status_code == 200 and res.json()["title"] == "견적서 재송부"
    assert res.json()["status"] == "confirmed" and res.json()["missingFields"] == []
    assert len(events(env["factory"], "item.updated")) == 4


def test_pending_item_can_still_be_blanked(env, team):
    meeting_id = make_meeting(env["factory"], team["mgr"], items=[
        {"title": "견적서 송부", "assignee_id": team["lee"].id, "due_date": date(2026, 10, 9)}])
    [item_id] = item_ids(env["factory"], meeting_id)
    res = call(env, "PATCH", team["mgr"], f"/api/action-items/{item_id}", json={"assigneeId": None, "dueDate": None})
    assert res.status_code == 200
    assert res.json()["status"] == "pending" and res.json()["missingFields"] == ["assignee", "dueDate"]
    # pending 업무의 공백 업무명은 기존대로 400(메시지 형식)
    res = call(env, "PATCH", team["mgr"], f"/api/action-items/{item_id}", json={"title": "  "})
    assert res.status_code == 400 and res.json()["detail"] == "업무명은 비워 둘 수 없습니다"
