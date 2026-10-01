"""알림 1단계: 열람 기록(meeting_views), 확정 안내(notices), 로그인 할 일(/api/me/todos). 메일 없음, 네트워크 없음."""
from datetime import date, timedelta

import pytest
from alembic import command
from sqlalchemy import event as sa_event
from sqlalchemy import select, text

from app.db import make_engine
from app.jobs.auto_confirm import run_auto_confirm
from app.models import ActionItem, Meeting, MeetingView, Notice
from app.models.common import utcnow
from app.services.processing import process_meeting
from tests.test_meeting_queries import make_meeting
from tests.test_upload_processing import _alembic, add_account, auth, env, upload  # noqa: F401  (env 는 픽스처)

_BEFORE_NOTICES = "2b93bc811d65"  # meeting_views·notices 마이그레이션의 down_revision


@pytest.fixture
def team(env):
    f = env["factory"]
    return {
        "mgr": add_account(f, "고객사A", "mgr", "manager", name="박관리"),
        "mgr2": add_account(f, "고객사A", "mgr2", "manager", name="정관리"),
        "exe": add_account(f, "고객사A", "boss", "executive", name="최임원"),
        "staff": add_account(f, "고객사A", "kim", "staff", name="김담당"),
        "staff2": add_account(f, "고객사A", "lee", "staff", name="이서연"),
        "outsider": add_account(f, "고객사B", "out", "executive", name="외부"),
    }


def call(env, method, account, path, **kwargs):
    return env["client"].request(method, path, headers=auth(account), **kwargs)


def views(env) -> list[MeetingView]:
    with env["factory"]() as s:
        return s.scalars(select(MeetingView)).all()


def notices(env) -> list[Notice]:
    with env["factory"]() as s:
        return s.scalars(select(Notice).order_by(Notice.id)).all()


def recipients(env, entity_type=None) -> list[int]:
    return sorted(n.account_id for n in notices(env) if entity_type is None or n.entity_type == entity_type)


def item_ids(env, meeting_id) -> list[int]:
    with env["factory"]() as s:
        return list(s.scalars(select(ActionItem.id).where(ActionItem.meeting_id == meeting_id).order_by(ActionItem.id)))


def set_meeting(env, meeting_id, **values):
    with env["factory"]() as s:
        meeting = s.get(Meeting, meeting_id)
        for key, value in values.items():
            setattr(meeting, key, value)
        s.commit()


def set_item(env, item_id, **values):
    with env["factory"]() as s:
        item = s.get(ActionItem, item_id)
        for key, value in values.items():
            setattr(item, key, value)
        s.commit()


# ---------------- 마이그레이션 ----------------
def test_migration_roundtrip_and_downgrade_guard(tmp_path):
    url = f"sqlite:///{(tmp_path / 'm.db').as_posix()}"
    cfg = _alembic(url)
    command.upgrade(cfg, "head")
    command.downgrade(cfg, _BEFORE_NOTICES)
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
                "INSERT INTO meeting_views (meeting_id, account_id, tenant_id, first_viewed_at, last_viewed_at) "
                "VALUES (1, 1, 1, '2026-10-01', '2026-10-01')"))
        with pytest.raises(RuntimeError, match="downgrade 거부"):
            command.downgrade(cfg, _BEFORE_NOTICES)
        with engine.connect() as conn:
            assert conn.execute(text("SELECT COUNT(*) FROM meeting_views")).scalar_one() == 1
    finally:
        engine.dispose()


# ---------------- 열람 기록 ----------------
def test_detail_records_view_keeping_first_and_updating_last(env, team):
    meeting_id = make_meeting(env["factory"], team["mgr"])
    assert call(env, "GET", team["mgr"], f"/api/meetings/{meeting_id}").status_code == 200
    [first] = views(env)
    assert (first.meeting_id, first.account_id, first.tenant_id) == (meeting_id, team["mgr"].id, team["mgr"].tenant_id)
    assert first.first_viewed_at == first.last_viewed_at

    call(env, "GET", team["mgr"], f"/api/meetings/{meeting_id}")
    [again] = views(env)
    assert again.first_viewed_at == first.first_viewed_at and again.last_viewed_at > first.last_viewed_at


def test_list_transcript_and_404_do_not_record_views(env, team):
    meeting_id = make_meeting(env["factory"], team["mgr"], transcript="전사")
    call(env, "GET", team["mgr"], "/api/meetings")
    call(env, "GET", team["mgr"], f"/api/meetings/{meeting_id}/transcript")
    assert call(env, "GET", team["staff"], f"/api/meetings/{meeting_id}").status_code == 404  # 관련 없는 staff
    assert call(env, "GET", team["outsider"], f"/api/meetings/{meeting_id}").status_code == 404
    assert views(env) == []


# ---------------- 확정 안내 ----------------
def test_manual_meeting_confirm_notifies_managers_except_actor(env, team):
    inactive = add_account(env["factory"], "고객사A", "offmgr", "manager")
    with env["factory"]() as s:
        s.get(type(inactive), inactive.id).is_active = False
        s.commit()
    meeting_id = make_meeting(env["factory"], team["mgr2"],  # 등록자 = manager → 수신 대상
                              participants=[team["mgr"], team["mgr2"], team["exe"], team["staff"], inactive])
    res = call(env, "POST", team["mgr"], f"/api/meetings/{meeting_id}/confirm")
    assert res.status_code == 200
    assert recipients(env) == sorted([team["mgr2"].id, team["exe"].id])  # 본인·staff·비활성 제외, 등록자 중복 없음
    notice = notices(env)[0]
    assert notice.kind == "confirmed_notice" and notice.entity_type == "meeting" and notice.entity_id == meeting_id
    assert notice.meeting_id == meeting_id and notice.payload == {"confirmKind": "manager", "title": "회의 0"}
    assert notice.seen_at is None


def test_item_confirm_notifies(env, team):
    meeting_id = make_meeting(env["factory"], team["staff"], participants=[team["mgr"], team["exe"]],
                              items=[{"assignee_id": team["staff"].id, "due_undetermined": True}])
    [item_id] = item_ids(env, meeting_id)
    assert call(env, "POST", team["exe"], f"/api/action-items/{item_id}/confirm").status_code == 200
    assert recipients(env, "action_item") == [team["mgr"].id]
    assert notices(env)[0].payload["confirmKind"] == "manager"


def test_registration_confirm_notifies_other_managers(env, team):
    body = upload(env, team["mgr"], participants=f"{team['mgr'].id},{team['exe'].id},{team['staff'].id}").json()
    process_meeting(body["jobId"], session_factory=env["factory"])
    assert recipients(env, "meeting") == [team["exe"].id]
    assert notices(env)[0].payload["confirmKind"] == "registration"


def test_auto_confirm_notifies_all_managers(env, team):
    meeting_id = make_meeting(env["factory"], team["mgr"], participants=[team["exe"], team["staff"]],
                              items=[{"assignee_id": team["staff"].id, "due_undetermined": True}])
    run_auto_confirm_now(env, meeting_id)
    # 수행자가 없으므로 등록자(manager)도 받는다
    assert recipients(env, "meeting") == sorted([team["mgr"].id, team["exe"].id])
    assert recipients(env, "action_item") == sorted([team["mgr"].id, team["exe"].id])
    assert {n.payload["confirmKind"] for n in notices(env)} == {"period_elapsed"}


def run_auto_confirm_now(env, meeting_id):
    """회의록 기한을 과거로 당기고 지금 시각으로 자동 확정을 돌린다(확정 시각 = 실제 현재)."""
    set_meeting(env, meeting_id, auto_confirm_at=utcnow() - timedelta(hours=1))
    with env["factory"]() as s:
        return run_auto_confirm(s, utcnow())


# ---------------- 안내 조회·확인 ----------------
def test_notices_list_and_seen_is_idempotent_and_ignores_others(env, team):
    meeting_id = make_meeting(env["factory"], team["staff"], participants=[team["mgr"], team["exe"]])
    call(env, "POST", team["mgr"], f"/api/meetings/{meeting_id}/confirm")
    other_id = make_meeting(env["factory"], team["staff"], participants=[team["mgr"], team["exe"]])
    call(env, "POST", team["exe"], f"/api/meetings/{other_id}/confirm")

    mine = call(env, "GET", team["exe"], "/api/me/notices").json()
    assert [n["meetingId"] for n in mine] == [meeting_id]
    assert set(mine[0]) == {"id", "kind", "entityType", "entityId", "meetingId", "payload", "createdAt"}
    mgr_notice_id = call(env, "GET", team["mgr"], "/api/me/notices").json()[0]["id"]

    res = call(env, "POST", team["exe"], "/api/me/notices/seen", json={"ids": [mine[0]["id"], mgr_notice_id, 999999]})
    assert res.json() == {"updated": 1}  # 남의 안내·없는 id 는 무시
    assert call(env, "POST", team["exe"], "/api/me/notices/seen", json={"ids": [mine[0]["id"]]}).json() == {"updated": 0}
    assert call(env, "GET", team["exe"], "/api/me/notices").json() == []
    assert len(call(env, "GET", team["mgr"], "/api/me/notices").json()) == 1  # 남의 것은 그대로
    assert call(env, "GET", team["outsider"], "/api/me/notices").json() == []


# ---------------- 할 일 ----------------
def test_todos_for_staff_and_manager(env, team):
    awaiting = make_meeting(env["factory"], team["staff"], items=[
        {"title": "빈 담당", "assignee_id": None},
        {"title": "내 업무", "assignee_id": team["staff"].id, "due_date": date(2026, 10, 9)},
        {"title": "내 미확정", "assignee_id": team["staff"].id, "due_undetermined": True, "status": "confirmed"},
        {"title": "삭제", "assignee_id": team["staff"].id, "status": "deleted"},
        {"title": "종결", "assignee_id": team["staff"].id, "due_undetermined": True, "status": "closed"},
    ])
    make_meeting(env["factory"], team["mgr"], day=1, status="confirmed")  # staff 와 무관
    blank_id, mine_id, mine_undetermined_id, _, _ = item_ids(env, awaiting)

    staff = call(env, "GET", team["staff"], "/api/me/todos").json()
    assert staff["awaitingConfirmMeetings"] == {"total": 0, "items": []}
    assert staff["needsCompletionItems"] == {"total": 0, "items": []}
    assert staff["myItems"]["total"] == 2
    assert [i["itemId"] for i in staff["myItems"]["items"]] == [mine_id, mine_undetermined_id]  # 기한 있는 것 먼저
    assert staff["myItems"]["items"][0] == {"meetingId": awaiting, "itemId": mine_id, "title": "내 업무",
                                            "dueDate": "2026-10-09", "dueUndetermined": False, "status": "pending"}

    mgr = call(env, "GET", team["mgr"], "/api/me/todos").json()
    assert mgr["awaitingConfirmMeetings"]["total"] == 1
    assert mgr["awaitingConfirmMeetings"]["items"][0]["id"] == awaiting
    assert set(mgr["awaitingConfirmMeetings"]["items"][0]) == {"id", "title", "autoConfirmAt"}
    assert mgr["needsCompletionItems"] == {"total": 1, "items": [
        {"meetingId": awaiting, "itemId": blank_id, "title": "빈 담당", "missingFields": ["assignee", "dueDate"]}]}
    assert mgr["myItems"] == {"total": 0, "items": []}


def test_unread_auto_confirmed_disappears_after_view_and_returns_after_new_confirm(env, team):
    meeting_id = make_meeting(env["factory"], team["staff"], participants=[team["staff2"]],
                              items=[{"assignee_id": team["staff2"].id, "due_undetermined": True}])
    [item_id] = item_ids(env, meeting_id)
    run_auto_confirm_now(env, meeting_id)

    unread = call(env, "GET", team["mgr"], "/api/me/todos").json()["unreadAutoConfirmed"]
    assert unread["total"] == 2
    assert {(e["entityType"], e["itemId"]) for e in unread["items"]} == {("meeting", None), ("action_item", item_id)}
    assert {e["confirmKind"] for e in unread["items"]} == {"period_elapsed"}
    # staff2 도 참석자라 보이고, 관련 없는 staff 계정은 보이지 않는다
    assert call(env, "GET", team["staff2"], "/api/me/todos").json()["unreadAutoConfirmed"]["total"] == 2
    unrelated = add_account(env["factory"], "고객사A", "park", "staff")
    assert call(env, "GET", unrelated, "/api/me/todos").json()["unreadAutoConfirmed"]["total"] == 0

    call(env, "GET", team["mgr"], f"/api/meetings/{meeting_id}")  # 열람
    assert call(env, "GET", team["mgr"], "/api/me/todos").json()["unreadAutoConfirmed"] == {"total": 0, "items": []}

    # 열람 뒤에 새로 자동 확정된 업무는 다시 미열람으로 잡힌다(확정 시각 기준)
    with env["factory"]() as s:
        s.add(ActionItem(tenant_id=team["mgr"].tenant_id, meeting_id=meeting_id, title="나중 업무",
                         assignee_id=team["staff2"].id, due_date=date(2026, 10, 1), status="confirmed",
                         confirm_kind="due_reached", confirmed_at=utcnow() + timedelta(seconds=1)))
        s.commit()
    later = call(env, "GET", team["mgr"], "/api/me/todos").json()["unreadAutoConfirmed"]
    assert later["total"] == 1 and later["items"][0]["confirmKind"] == "due_reached"


def test_manual_confirm_is_not_unread_auto_confirmed(env, team):
    meeting_id = make_meeting(env["factory"], team["staff"])
    call(env, "POST", team["mgr"], f"/api/meetings/{meeting_id}/confirm")
    assert call(env, "GET", team["exe"], "/api/me/todos").json()["unreadAutoConfirmed"]["total"] == 0


def test_todos_isolated_by_tenant(env, team):
    meeting_id = make_meeting(env["factory"], team["mgr"], items=[{"assignee_id": None}])
    run_auto_confirm_now(env, meeting_id)
    out = call(env, "GET", team["outsider"], "/api/me/todos").json()
    assert all(out[key] == {"total": 0, "items": []} for key in out)


def test_todos_limit_50_with_total(env, team):
    make_meeting(env["factory"], team["mgr"], items=[{"assignee_id": None} for _ in range(55)])
    needs = call(env, "GET", team["mgr"], "/api/me/todos").json()["needsCompletionItems"]
    assert needs["total"] == 55 and len(needs["items"]) == 50


def test_todos_query_count_is_constant(env, team):
    engine = env["factory"].kw["bind"]
    statements: list[str] = []

    def _count(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement)

    def queries() -> int:
        statements.clear()
        sa_event.listen(engine, "before_cursor_execute", _count)
        try:
            assert call(env, "GET", team["mgr"], "/api/me/todos").status_code == 200
        finally:
            sa_event.remove(engine, "before_cursor_execute", _count)
        return len(statements)

    def add_data(n):
        for d in range(n):
            mid = make_meeting(env["factory"], team["mgr"], day=d, items=[
                {"assignee_id": None}, {"assignee_id": team["mgr"].id, "due_undetermined": True}])
            set_meeting(env, mid, status="confirmed", confirm_kind="period_elapsed", confirmed_at=utcnow())

    add_data(2)
    few = queries()
    add_data(10)
    assert queries() == few
