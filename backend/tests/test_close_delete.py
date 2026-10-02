"""업무 종결·삭제 API(POST /api/action-items/{id}/close·/delete)와 종결·삭제 업무의 목록·자동 확정·안내·메일 제외."""
from datetime import date, datetime, timedelta, timezone

import pytest
from alembic import command
from sqlalchemy import select, text

from app.db import make_engine
from app.jobs.auto_confirm import run_auto_confirm
from app.models import ActionItem, Event, MailOutbox, Meeting, Notice
from app.services.mail import queue_immediate_new_minutes
from tests.test_meeting_queries import make_meeting
from tests.test_upload_processing import _alembic, add_account, auth, env  # noqa: F401  (env 는 픽스처)

_BEFORE_CLOSE_DELETE = "dcdcca46db71"  # 종결·삭제 기록 칸 마이그레이션의 down_revision


REASON = {"reason": "테스트 처리 사유"}  # ㊺-2: 업무 종결·삭제, 회의록 보류·직권 종료·삭제는 사유 필수


@pytest.fixture
def team(env):
    f = env["factory"]
    return {
        "lead": add_account(f, "고객사A", "lead", "manager", name="박총괄"),
        "mgr2": add_account(f, "고객사A", "mgr2", "manager", name="정관리"),
        "exe": add_account(f, "고객사A", "boss", "executive", name="최임원"),
        "staff": add_account(f, "고객사A", "kim", "staff", name="김담당"),
        "outsider": add_account(f, "고객사B", "out", "executive", name="외부"),
    }


def call(env, method, account, path, **kwargs):
    return env["client"].request(method, path, headers=auth(account), **kwargs)


def item_ids(env, meeting_id) -> list[int]:
    with env["factory"]() as s:
        return list(s.scalars(select(ActionItem.id).where(ActionItem.meeting_id == meeting_id).order_by(ActionItem.id)))


def get_item(env, item_id) -> ActionItem:
    with env["factory"]() as s:
        return s.get(ActionItem, item_id)


def events(env, item_id) -> list[Event]:
    with env["factory"]() as s:
        return s.scalars(
            select(Event).where(Event.entity_type == "action_item", Event.entity_id == item_id).order_by(Event.id)
        ).all()


def confirmed(**fields) -> dict:
    return {"title": "업무", "due_date": date(2026, 10, 9), "status": "confirmed", "confirm_kind": "manager", **fields}


@pytest.fixture
def meeting(env, team):
    """총괄(lead)이 등록한 회의록: 0) lead 담당 1) mgr2 담당 2) staff 담당 — 모두 확정됨, 3) 확정 전 업무."""
    meeting_id = make_meeting(env["factory"], team["lead"], participants=[team["staff"], team["mgr2"]], items=[
        confirmed(title="총괄 업무", assignee_id=team["lead"].id),
        confirmed(title="정관리 업무", assignee_id=team["mgr2"].id),
        confirmed(title="김담당 업무", assignee_id=team["staff"].id),
        {"title": "확정 전 업무", "assignee_id": team["staff"].id, "due_date": date(2026, 10, 9)},
    ])
    return meeting_id, item_ids(env, meeting_id)


# ---------------- 종결 ----------------
@pytest.mark.parametrize("who, index", [("lead", 2), ("exe", 2), ("mgr2", 1)])
def test_close_allowed_for_lead_executive_and_own_assignee_manager(env, team, meeting, who, index):
    item_id = meeting[1][index]
    before = datetime.now(timezone.utc)
    res = call(env, "POST", team[who], f"/api/action-items/{item_id}/close", json=REASON)
    assert res.status_code == 200
    assert res.json()["status"] == "closed" and res.json()["id"] == item_id
    item = get_item(env, item_id)
    assert item.status == "closed" and item.closed_by == team[who].id
    assert item.closed_at is not None and item.closed_at >= before - timedelta(seconds=1)
    assert item.deleted_by is None and item.deleted_at is None
    last = events(env, item_id)[-1]
    assert (last.event_type, last.actor_account_id) == ("item.closed", team[who].id)
    assert last.payload == {"before": {"status": "confirmed"}, "after": {"status": "closed"}, "reason": REASON["reason"]}


def test_close_denied_for_staff_and_other_managers_item(env, team, meeting):
    _, (lead_item, mgr2_item, staff_item, _) = meeting
    assert call(env, "POST", team["staff"], f"/api/action-items/{staff_item}/close", json=REASON).status_code == 403  # 담당자 직급
    assert call(env, "POST", team["mgr2"], f"/api/action-items/{staff_item}/close", json=REASON).status_code == 403  # 남의 업무
    assert call(env, "POST", team["mgr2"], f"/api/action-items/{lead_item}/close", json=REASON).status_code == 403
    for item_id in (lead_item, mgr2_item, staff_item):
        assert get_item(env, item_id).status == "confirmed"
        assert [e.event_type for e in events(env, item_id)] == []


def test_close_state_rules(env, team, meeting):
    _, (_, _, staff_item, pending_item) = meeting
    # 확정 전 업무는 종결할 수 없다(확정 → 종결 순서)
    res = call(env, "POST", team["lead"], f"/api/action-items/{pending_item}/close", json=REASON)
    assert res.status_code == 409 and get_item(env, pending_item).status == "pending"
    # 이미 종결이면 그대로 200, 사건·기록은 처음 것 유지
    first = call(env, "POST", team["lead"], f"/api/action-items/{staff_item}/close", json=REASON)
    again = call(env, "POST", team["exe"], f"/api/action-items/{staff_item}/close", json=REASON)
    assert first.status_code == again.status_code == 200
    assert get_item(env, staff_item).closed_by == team["lead"].id
    assert [e.event_type for e in events(env, staff_item)] == ["item.closed"]
    # 종결된 업무는 수정·확정 불가
    res = call(env, "PATCH", team["lead"], f"/api/action-items/{staff_item}", json={"title": "새 이름"})
    assert res.status_code == 409 and get_item(env, staff_item).title == "김담당 업무"
    assert call(env, "POST", team["lead"], f"/api/action-items/{staff_item}/confirm").status_code == 409


# ---------------- 삭제 ----------------
@pytest.mark.parametrize("who", ["lead", "exe"])
def test_delete_allowed_only_for_lead_and_executive(env, team, meeting, who):
    _, (_, _, staff_item, pending_item) = meeting
    for item_id in (staff_item, pending_item):  # 확정·확정 전 모두 삭제 가능
        before = get_item(env, item_id).status
        res = call(env, "POST", team[who], f"/api/action-items/{item_id}/delete", json=REASON)
        assert res.status_code == 200 and res.json()["status"] == "deleted"
        item = get_item(env, item_id)  # 행은 남는다
        assert item is not None and item.status == "deleted"
        assert item.deleted_by == team[who].id and item.deleted_at is not None
        last = events(env, item_id)[-1]
        assert (last.event_type, last.actor_account_id) == ("item.deleted", team[who].id)
        assert last.payload == {"before": {"status": before}, "after": {"status": "deleted"}, "reason": REASON["reason"]}
    # 이미 삭제면 그대로 200, 사건 추가 없음
    assert call(env, "POST", team[who], f"/api/action-items/{staff_item}/delete", json=REASON).status_code == 200
    assert [e.event_type for e in events(env, staff_item)] == ["item.deleted"]


def test_delete_denied_for_staff_and_non_lead_manager_even_own_item(env, team, meeting):
    _, (_, mgr2_item, staff_item, _) = meeting
    assert call(env, "POST", team["mgr2"], f"/api/action-items/{mgr2_item}/delete", json=REASON).status_code == 403  # 본인 담당이어도
    assert call(env, "POST", team["staff"], f"/api/action-items/{staff_item}/delete", json=REASON).status_code == 403
    assert get_item(env, mgr2_item).status == "confirmed" and get_item(env, staff_item).status == "confirmed"


def test_deleted_item_cannot_be_closed_or_edited(env, team, meeting):
    _, (_, _, staff_item, _) = meeting
    call(env, "POST", team["lead"], f"/api/action-items/{staff_item}/delete", json=REASON)
    assert call(env, "POST", team["lead"], f"/api/action-items/{staff_item}/close", json=REASON).status_code == 409
    assert call(env, "PATCH", team["lead"], f"/api/action-items/{staff_item}", json={"title": "x"}).status_code == 409


def test_other_tenant_items_are_not_found(env, team, meeting):
    _, (_, _, staff_item, _) = meeting
    assert call(env, "POST", team["outsider"], f"/api/action-items/{staff_item}/close", json=REASON).status_code == 404
    assert call(env, "POST", team["outsider"], f"/api/action-items/{staff_item}/delete", json=REASON).status_code == 404
    assert get_item(env, staff_item).status == "confirmed"


# ---------------- 목록·할 일·자동 확정·안내·메일 제외 ----------------
def test_detail_hides_deleted_and_shows_closed_status(env, team, meeting):
    meeting_id, (lead_item, _, staff_item, pending_item) = meeting
    call(env, "POST", team["lead"], f"/api/action-items/{lead_item}/close", json=REASON)
    call(env, "POST", team["lead"], f"/api/action-items/{pending_item}/delete", json=REASON)
    items = call(env, "GET", team["lead"], f"/api/meetings/{meeting_id}").json()["actionItems"]
    statuses = {i["id"]: i["status"] for i in items}
    assert pending_item not in statuses
    assert statuses[lead_item] == "closed" and statuses[staff_item] == "confirmed"


def test_todos_exclude_closed_and_deleted(env, team, meeting):
    _, (_, _, staff_item, pending_item) = meeting

    def my_ids():
        return [i["itemId"] for i in call(env, "GET", team["staff"], "/api/me/todos").json()["myItems"]["items"]]

    assert sorted(my_ids()) == sorted([staff_item, pending_item])
    call(env, "POST", team["lead"], f"/api/action-items/{staff_item}/close", json=REASON)
    call(env, "POST", team["lead"], f"/api/action-items/{pending_item}/delete", json=REASON)
    assert my_ids() == []


def test_needs_completion_todo_excludes_deleted(env, team):
    meeting_id = make_meeting(env["factory"], team["lead"], items=[{"title": "빈 담당", "assignee_id": None}])
    [item_id] = item_ids(env, meeting_id)
    assert call(env, "GET", team["lead"], "/api/me/todos").json()["needsCompletionItems"]["total"] == 1
    call(env, "POST", team["lead"], f"/api/action-items/{item_id}/delete", json=REASON)
    assert call(env, "GET", team["lead"], "/api/me/todos").json()["needsCompletionItems"]["total"] == 0


def test_auto_confirm_skips_deleted_items(env, team):
    meeting_id = make_meeting(env["factory"], team["lead"], items=[
        {"title": "지울 업무", "assignee_id": team["staff"].id, "due_undetermined": True},
        {"title": "남길 업무", "assignee_id": team["staff"].id, "due_undetermined": True},
    ])
    deleted_id, kept_id = item_ids(env, meeting_id)
    call(env, "POST", team["lead"], f"/api/action-items/{deleted_id}/delete", json=REASON)
    with env["factory"]() as s:
        result = run_auto_confirm(s, datetime.now(timezone.utc) + timedelta(days=30))
    assert result["itemsConfirmed"] == 1
    assert get_item(env, deleted_id).status == "deleted" and get_item(env, kept_id).status == "confirmed"


def test_notices_hide_closed_and_deleted_items(env, team):
    meeting_id = make_meeting(env["factory"], team["lead"], items=[
        {"title": "닫을 업무", "assignee_id": team["staff"].id, "due_date": date(2026, 10, 9)},
        {"title": "지울 업무", "assignee_id": team["staff"].id, "due_date": date(2026, 10, 9)},
        {"title": "남길 업무", "assignee_id": team["staff"].id, "due_date": date(2026, 10, 9)},
    ])
    close_id, delete_id, keep_id = item_ids(env, meeting_id)
    for item_id in (close_id, delete_id, keep_id):  # 지시자가 확정 → 관리자(lead·mgr2)에게 확정 안내
        assert call(env, "POST", team["exe"], f"/api/action-items/{item_id}/confirm").status_code == 200

    def notice_items(who):
        return sorted(n["entityId"] for n in call(env, "GET", team[who], "/api/me/notices").json()
                      if n["entityType"] == "action_item")

    assert notice_items("lead") == sorted([close_id, delete_id, keep_id])
    call(env, "POST", team["lead"], f"/api/action-items/{close_id}/close", json=REASON)
    call(env, "POST", team["lead"], f"/api/action-items/{delete_id}/delete", json=REASON)
    assert notice_items("lead") == [keep_id]
    with env["factory"]() as s:  # 안내 행 자체는 지우지 않는다
        assert s.scalars(select(Notice.id).where(Notice.entity_type == "action_item", Notice.entity_id == close_id)).first()


def test_immediate_mail_skips_assignees_of_closed_or_deleted_items(env, team):
    only_closed = add_account(env["factory"], "고객사A", "closed-only", "staff", name="종결담당")
    only_deleted = add_account(env["factory"], "고객사A", "deleted-only", "staff", name="삭제담당")
    meeting_id = make_meeting(env["factory"], team["lead"], items=[
        {"title": "종결", "assignee_id": only_closed.id, "status": "closed"},
        {"title": "삭제", "assignee_id": only_deleted.id, "status": "deleted"},
        {"title": "진행", "assignee_id": team["staff"].id},
    ])
    with env["factory"]() as s:
        queue_immediate_new_minutes(s, s.get(Meeting, meeting_id))
        s.commit()
        recipients = set(s.scalars(select(MailOutbox.account_id)))
    assert recipients == {team["staff"].id}


# ---------------- 마이그레이션 ----------------
def test_migration_roundtrip_and_downgrade_guard(tmp_path):
    url = f"sqlite:///{(tmp_path / 'm.db').as_posix()}"
    cfg = _alembic(url)
    command.upgrade(cfg, "head")
    command.downgrade(cfg, _BEFORE_CLOSE_DELETE)
    command.upgrade(cfg, "head")
    command.check(cfg)
    engine = make_engine(url)
    try:
        with engine.begin() as conn:
            conn.execute(text("INSERT INTO tenants (id, name, auto_confirm_days, created_at) VALUES (1, 't', 5, '2026-10-01')"))
            conn.execute(text(
                "INSERT INTO accounts (id, tenant_id, login_id, password_hash, name, email, rank, is_active, created_at) "
                "VALUES (1, 1, 'a', 'x', 'a', 'a@x', 'manager', 1, '2026-10-01')"))
            conn.execute(text(
                "INSERT INTO source_documents (id, tenant_id, origin, doc_type, title, registered_by, created_at) "
                "VALUES (1, 1, 'audio_minutes', 'm', 't', 1, '2026-10-01')"))
            conn.execute(text(
                "INSERT INTO meetings (id, tenant_id, source_document_id, title, held_at, summary, decisions, status, "
                "first_created_at, created_at) VALUES (1, 1, 1, 't', '2026-10-01', '', '[]', 'confirmed', '2026-10-01', '2026-10-01')"))
            conn.execute(text(
                "INSERT INTO action_items (id, tenant_id, meeting_id, title, due_undetermined, status, extract_model, "
                "prompt_version, created_at) VALUES (1, 1, 1, 't', 0, 'pending', '', '', '2026-10-01')"))
        # 기록이 없는 업무만 있으면 되돌리기 가능
        command.downgrade(cfg, _BEFORE_CLOSE_DELETE)
        command.upgrade(cfg, "head")
        with engine.begin() as conn:
            conn.execute(text("UPDATE action_items SET status='closed', closed_by=1, closed_at='2026-10-02' WHERE id=1"))
        with pytest.raises(RuntimeError, match="downgrade 거부"):
            command.downgrade(cfg, _BEFORE_CLOSE_DELETE)
        with engine.connect() as conn:
            assert conn.execute(text("SELECT closed_by FROM action_items WHERE id=1")).scalar_one() == 1
    finally:
        engine.dispose()
