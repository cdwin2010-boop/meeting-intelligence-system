"""회의록 보류·재개 API(POST /api/meetings/{id}/hold·/resume)와 보류 중 쓰기 거부, 할 일·자동 확정·메일·안내 제외."""
from datetime import date, datetime, timedelta, timezone

import pytest
from alembic import command
from sqlalchemy import select, text

from app.db import make_engine
from app.jobs.auto_confirm import run_auto_confirm
from app.jobs.daily_mail import _counts as daily_counts
from app.models import ActionItem, Event, Meeting, MeetingHold, Transcript
from app.models.common import utcnow
from tests.test_meeting_queries import make_meeting
from tests.test_upload_processing import _alembic, add_account, auth, env  # noqa: F401  (env 는 픽스처)

_BEFORE_HOLDS = "4ad1176618ed"  # meeting_holds 마이그레이션의 down_revision
DUE = date(2026, 10, 9)


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


def get_hold(env, meeting_id) -> MeetingHold | None:
    with env["factory"]() as s:
        return s.get(MeetingHold, meeting_id)


def meeting_events(env, meeting_id) -> list[Event]:
    with env["factory"]() as s:
        return s.scalars(
            select(Event).where(Event.entity_type == "meeting", Event.entity_id == meeting_id,
                                Event.event_type.in_(("meeting.on_hold", "meeting.resumed"))).order_by(Event.id)
        ).all()


@pytest.fixture
def meeting(env, team):
    """총괄(lead) 등록, 참석: staff·mgr2. 업무: 0) 확정 대기(기한 있음) 1) 확정(기한 있음) 2) 확정 대기(미확정) 3) 종결 4) mgr2 담당 확정."""
    meeting_id = make_meeting(env["factory"], team["lead"], participants=[team["staff"], team["mgr2"]], transcript="화자1: 안녕", items=[
        {"title": "확정 전", "assignee_id": team["staff"].id, "due_date": DUE},
        {"title": "확정", "assignee_id": team["staff"].id, "due_date": DUE, "status": "confirmed", "confirm_kind": "manager"},
        {"title": "미확정 기한", "assignee_id": team["staff"].id, "due_undetermined": True},
        {"title": "종결", "assignee_id": team["staff"].id, "due_date": DUE, "status": "closed"},
        {"title": "정관리 업무", "assignee_id": team["mgr2"].id, "due_date": DUE, "status": "confirmed", "confirm_kind": "manager"},
    ])
    return meeting_id, item_ids(env, meeting_id)


def hold(env, account, meeting_id):
    return call(env, "POST", account, f"/api/meetings/{meeting_id}/hold")


def resume(env, account, meeting_id):
    return call(env, "POST", account, f"/api/meetings/{meeting_id}/resume")


# ---------------- 권한·기록 ----------------
@pytest.mark.parametrize("who", ["lead", "exe"])
def test_hold_and_resume_allowed_for_lead_and_executive_with_records(env, team, meeting, who):
    meeting_id, _ = meeting
    res = hold(env, team[who], meeting_id)
    assert res.status_code == 200
    body = res.json()
    assert body["onHold"] is True and body["onHoldBy"] == {"id": team[who].id, "name": team[who].name}
    assert body["onHoldAt"] and body["resumedBy"] is None
    record = get_hold(env, meeting_id)
    assert record.on_hold and record.on_hold_by == team[who].id and record.on_hold_at is not None

    res = resume(env, team[who], meeting_id)
    assert res.status_code == 200 and res.json()["onHold"] is False
    record = get_hold(env, meeting_id)
    assert not record.on_hold and record.resumed_by == team[who].id and record.resumed_at >= record.on_hold_at
    assert record.on_hold_by == team[who].id  # 마지막 보류 기록은 남는다
    events = meeting_events(env, meeting_id)
    assert [(e.event_type, e.actor_account_id) for e in events] == [
        ("meeting.on_hold", team[who].id), ("meeting.resumed", team[who].id)]


def test_hold_denied_for_staff_other_manager_and_other_tenant(env, team, meeting):
    meeting_id, _ = meeting
    assert hold(env, team["staff"], meeting_id).status_code == 403
    assert hold(env, team["mgr2"], meeting_id).status_code == 403  # 총괄 아닌 관리자(본인 담당 업무가 있어도)
    assert hold(env, team["outsider"], meeting_id).status_code == 404
    assert get_hold(env, meeting_id) is None
    hold(env, team["lead"], meeting_id)
    assert resume(env, team["staff"], meeting_id).status_code == 403
    assert resume(env, team["mgr2"], meeting_id).status_code == 403
    assert resume(env, team["outsider"], meeting_id).status_code == 404
    assert get_hold(env, meeting_id).on_hold


def test_hold_state_rules(env, team, meeting):
    meeting_id, _ = meeting
    processing = make_meeting(env["factory"], team["lead"], day=1, status="processing")
    assert hold(env, team["lead"], processing).status_code == 409
    # 보류 중이 아니면 재개는 아무것도 바꾸지 않음(기한 그대로)
    res = resume(env, team["lead"], meeting_id)
    assert res.status_code == 200 and res.json()["clearedDueItemIds"] == [] and get_hold(env, meeting_id) is None
    # 이미 보류면 그대로 200, 처음 기록 유지
    hold(env, team["lead"], meeting_id)
    first_at = get_hold(env, meeting_id).on_hold_at
    assert hold(env, team["exe"], meeting_id).status_code == 200
    assert get_hold(env, meeting_id).on_hold_by == team["lead"].id and get_hold(env, meeting_id).on_hold_at == first_at
    assert [e.event_type for e in meeting_events(env, meeting_id)] == ["meeting.on_hold"]


# ---------------- 보류 중 ----------------
def test_detail_shows_hold_and_items_kept(env, team, meeting):
    meeting_id, ids = meeting
    hold(env, team["lead"], meeting_id)
    body = call(env, "GET", team["staff"], f"/api/meetings/{meeting_id}").json()  # 목록·상세에서 숨기지 않는다
    assert body["onHold"] is True and body["onHoldBy"]["id"] == team["lead"].id
    assert [i["id"] for i in body["actionItems"]] == ids  # 업무 상태는 그대로(보류는 회의록 표시로 판단)
    assert meeting_id in [m["id"] for m in call(env, "GET", team["lead"], "/api/meetings").json()["items"]]


def test_writes_rejected_while_on_hold(env, team, meeting):
    meeting_id, (pending_id, confirmed_id, *_rest) = meeting
    hold(env, team["lead"], meeting_id)
    requests = [
        ("PATCH", f"/api/action-items/{pending_id}", {"json": {"title": "새 이름"}}),
        ("POST", f"/api/action-items/{pending_id}/confirm", {}),
        ("POST", f"/api/action-items/{confirmed_id}/close", {}),
        ("POST", f"/api/action-items/{pending_id}/delete", {}),
        ("POST", f"/api/meetings/{meeting_id}/confirm", {}),
        ("PUT", f"/api/meetings/{meeting_id}/speakers", {"json": {"speakers": []}}),
    ]
    for method, path, kwargs in requests:
        for who in ("lead", "exe"):
            res = call(env, method, team[who], path, **kwargs)
            assert res.status_code == 409, (method, path, who)
            assert "보류" in res.json()["detail"]
    # 권한 없는 사람은 보류 여부와 상관없이 기존대로 403
    assert call(env, "POST", team["staff"], f"/api/action-items/{pending_id}/confirm").status_code == 403
    assert get_item(env, pending_id).title == "확정 전" and get_item(env, pending_id).status == "pending"
    assert get_item(env, confirmed_id).status == "confirmed"
    # 재개하면 다시 쓸 수 있다
    resume(env, team["lead"], meeting_id)
    assert call(env, "PATCH", team["lead"], f"/api/action-items/{pending_id}", json={"title": "새 이름"}).status_code == 200


# ---------------- 재개: 기한 비우기 ----------------
def test_resume_clears_due_dates_and_makes_items_need_completion(env, team, meeting):
    meeting_id, (pending_id, confirmed_id, undetermined_id, closed_id, mgr2_id) = meeting
    hold(env, team["lead"], meeting_id)
    res = resume(env, team["exe"], meeting_id)
    assert sorted(res.json()["clearedDueItemIds"]) == sorted([pending_id, confirmed_id, undetermined_id, mgr2_id])

    items = {i["id"]: i for i in call(env, "GET", team["lead"], f"/api/meetings/{meeting_id}").json()["actionItems"]}
    for item_id in (pending_id, confirmed_id, undetermined_id, mgr2_id):
        assert items[item_id]["dueDate"] is None and items[item_id]["dueUndetermined"] is False
        assert items[item_id]["needsCompletion"] is True and items[item_id]["missingFields"] == ["dueDate"]
    # 확정 상태는 그대로(재개 때만 확정 업무 기한 비우기 예외), 종결 업무는 건드리지 않음
    assert items[confirmed_id]["status"] == "confirmed"
    assert items[closed_id]["status"] == "closed" and items[closed_id]["dueDate"] == DUE.isoformat()
    with env["factory"]() as s:
        cleared = s.scalars(select(Event).where(Event.event_type == "item.updated", Event.entity_id == confirmed_id)).all()
    assert [(e.actor_account_id, e.payload["via"], e.payload["after"]) for e in cleared] == [
        (team["exe"].id, "meeting_resumed", {"dueDate": None, "dueUndetermined": False})]
    # 재개한 관리자가 기한을 다시 넣을 수 있다(확정 업무도)
    res = call(env, "PATCH", team["lead"], f"/api/action-items/{confirmed_id}", json={"dueDate": "2026-11-02"})
    assert res.status_code == 200 and res.json()["needsCompletion"] is False


# ---------------- 자동 확정 ----------------
def test_auto_confirm_skips_held_meeting_and_resume_pushes_clock_back(env, team, meeting):
    meeting_id, (pending_id, *_rest) = meeting
    hold(env, team["lead"], meeting_id)
    with env["factory"]() as s:
        result = run_auto_confirm(s, datetime.now(timezone.utc) + timedelta(days=30))
    assert result["meetingsConfirmed"] == 0 and result["itemsConfirmed"] == 0
    with env["factory"]() as s:
        assert s.get(Meeting, meeting_id).status == "awaiting_confirmation"
    assert get_item(env, pending_id).status == "pending"

    # 보류 기간(2일)만큼 자동 확정 시각을 미룬다
    with env["factory"]() as s:
        record = s.get(MeetingHold, meeting_id)
        record.on_hold_at = utcnow() - timedelta(days=2)
        before = s.get(Meeting, meeting_id).auto_confirm_at
        s.commit()
    after = datetime.fromisoformat(resume(env, team["lead"], meeting_id).json()["autoConfirmAt"].replace("Z", "+00:00"))
    assert timedelta(days=2) <= after - before < timedelta(days=2, minutes=1)


# ---------------- 할 일·안내·메일 ----------------
def test_todos_exclude_held_meeting_and_return_after_resume(env, team, meeting):
    meeting_id, _ = meeting
    blank = make_meeting(env["factory"], team["lead"], day=2, items=[{"title": "빈 담당", "assignee_id": None}])
    assert call(env, "POST", team["mgr2"], f"/api/meetings/{blank}/change-requests", json={"comment": "확인 부탁"}).status_code == 201

    def todos(who):
        return call(env, "GET", team[who], "/api/me/todos").json()

    lead = todos("lead")
    assert {m["id"] for m in lead["awaitingConfirmMeetings"]["items"]} == {meeting_id, blank}
    assert lead["needsCompletionItems"]["total"] == 1 and lead["pendingChangeRequests"]["total"] == 1
    assert todos("staff")["myItems"]["total"] == 3  # 확정 전·확정·미확정 기한(종결 제외)

    hold(env, team["lead"], meeting_id)
    hold(env, team["lead"], blank)
    lead = todos("lead")
    assert all(lead[key] == {"total": 0, "items": []} for key in lead)
    assert todos("staff")["myItems"] == {"total": 0, "items": []}
    assert todos("mgr2")["myItems"] == {"total": 0, "items": []}

    resume(env, team["lead"], meeting_id)
    assert todos("staff")["myItems"]["total"] == 3


def test_notices_hidden_while_on_hold(env, team, meeting):
    meeting_id, (pending_id, *_rest) = meeting
    assert call(env, "POST", team["exe"], f"/api/action-items/{pending_id}/confirm").status_code == 200

    def notice_meetings():
        return [n["meetingId"] for n in call(env, "GET", team["lead"], "/api/me/notices").json()]

    assert notice_meetings() == [meeting_id]
    hold(env, team["lead"], meeting_id)
    assert notice_meetings() == []
    resume(env, team["lead"], meeting_id)
    assert notice_meetings() == [meeting_id]  # 안내 행은 지우지 않았다


def test_daily_mail_counts_exclude_held_meeting(env, team):
    meeting_id = make_meeting(env["factory"], team["lead"], items=[{"title": "빈 담당", "assignee_id": None},
                                                                    {"title": "담당", "assignee_id": team["staff"].id}])
    tenant = team["lead"].tenant_id
    with env["factory"]() as s:
        awaiting, needs, unviewed = daily_counts(s)
    assert (awaiting.get(tenant), needs.get(tenant), unviewed.get(team["staff"].id)) == (1, 2, 1)
    hold(env, team["lead"], meeting_id)
    with env["factory"]() as s:
        awaiting, needs, unviewed = daily_counts(s)
    assert (awaiting.get(tenant, 0), needs.get(tenant, 0), unviewed.get(team["staff"].id, 0)) == (0, 0, 0)


# ---------------- 마이그레이션 ----------------
def test_migration_roundtrip_and_downgrade_guard(tmp_path):
    url = f"sqlite:///{(tmp_path / 'm.db').as_posix()}"
    cfg = _alembic(url)
    command.upgrade(cfg, "head")
    command.downgrade(cfg, _BEFORE_HOLDS)
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
        # 회의록·업무가 있어도 보류 기록이 없으면 되돌리기·다시 올리기 가능(meetings 표는 다시 만들지 않음)
        command.downgrade(cfg, _BEFORE_HOLDS)
        command.upgrade(cfg, "head")
        with engine.begin() as conn:
            conn.execute(text(
                "INSERT INTO meeting_holds (meeting_id, tenant_id, on_hold, on_hold_by, on_hold_at, updated_at) "
                "VALUES (1, 1, 1, 1, '2026-10-02', '2026-10-02')"))
        with pytest.raises(RuntimeError, match="downgrade 거부"):
            command.downgrade(cfg, _BEFORE_HOLDS)
        with engine.connect() as conn:
            assert conn.execute(text("SELECT COUNT(*) FROM meeting_holds")).scalar_one() == 1
            assert conn.execute(text("SELECT COUNT(*) FROM meetings")).scalar_one() == 1
    finally:
        engine.dispose()
