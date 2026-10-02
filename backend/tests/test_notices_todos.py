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
    # 관리자 등록 회의록은 등록자·지시자만 확정(㉟) → 지시자가 확정
    res = call(env, "POST", team["exe"], f"/api/meetings/{meeting_id}/confirm")
    assert res.status_code == 200
    assert recipients(env) == sorted([team["mgr2"].id, team["mgr"].id])  # 본인·staff·비활성 제외, 등록자 중복 없음
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
    # 담당자 등록 회의록: 참석한 관리자(mgr)가 총괄이라 확정 대기 묶음에 보인다
    awaiting = make_meeting(env["factory"], team["staff"], participants=[team["mgr"]], items=[
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


def test_awaiting_meetings_only_for_those_who_can_confirm(env, team):
    f = env["factory"]
    by_mgr = make_meeting(f, team["mgr"], items=[{"title": "빈 담당", "assignee_id": None}])
    by_staff = make_meeting(f, team["staff"], day=1, participants=[team["mgr2"]])

    def awaiting_ids(who):
        body = call(env, "GET", team[who], "/api/me/todos").json()
        return sorted(m["id"] for m in body["awaitingConfirmMeetings"]["items"]), body

    ids, mgr = awaiting_ids("mgr")  # 본인 등록 회의록만(담당자 등록 회의록은 참석 안 해 총괄 아님)
    assert ids == [by_mgr] and mgr["awaitingConfirmMeetings"]["total"] == 1
    ids, mgr2 = awaiting_ids("mgr2")  # 등록 안 한 관리자 회의록은 미표시, 참석한 담당자 등록 회의록은 표시
    assert ids == [by_staff] and mgr2["awaitingConfirmMeetings"]["total"] == 1
    assert awaiting_ids("exe")[0] == sorted([by_mgr, by_staff])  # 지시자는 모두
    # 보완 필요 업무 묶음은 그대로(관리자 이상이면 열람 가능한 회의록 전체)
    assert [i["meetingId"] for i in mgr2["needsCompletionItems"]["items"]] == [by_mgr]


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


# ---------------- 수정 요청 대기 ----------------
def pending_requests(env, account) -> dict:
    return call(env, "GET", account, "/api/me/todos").json()["pendingChangeRequests"]


def create_request(env, account, meeting_id, comment, item_id=None) -> int:
    res = call(env, "POST", account, f"/api/meetings/{meeting_id}/change-requests", json={"comment": comment, "itemId": item_id})
    assert res.status_code == 201
    return res.json()["requestId"]


def test_pending_change_requests_shown_to_resolvers_not_staff(env, team):
    meeting_id = make_meeting(env["factory"], team["mgr"], participants=[team["staff"]], title="주간 회의",
                              items=[{"title": "자료 정리", "assignee_id": team["staff"].id}])
    [item_id] = item_ids(env, meeting_id)
    first = create_request(env, team["staff"], meeting_id, "기한을 다음 주로 바꿔 주세요", item_id)
    second = create_request(env, team["staff"], meeting_id, "요약 보완")

    for who in ("mgr", "mgr2", "exe"):  # 해결 API 와 같은 판정: 관리자 이상 + 열람 가능
        body = pending_requests(env, team[who])
        assert body["total"] == 2
        assert [i["requestId"] for i in body["items"]] == [first, second]  # 오래된 요청 먼저
    item_entry, meeting_entry = pending_requests(env, team["mgr"])["items"]
    assert set(item_entry) == {"requestId", "meetingId", "meetingTitle", "itemId", "itemTitle", "requester", "createdAt",
                               "commentPreview"}
    assert (item_entry["meetingId"], item_entry["meetingTitle"], item_entry["itemId"], item_entry["itemTitle"]) == (
        meeting_id, "주간 회의", item_id, "자료 정리")
    assert item_entry["requester"] == {"id": team["staff"].id, "name": "김담당"}
    assert item_entry["commentPreview"] == "기한을 다음 주로 바꿔 주세요"
    assert (meeting_entry["itemId"], meeting_entry["itemTitle"]) == (None, None)

    # 담당자(처리 불가)에게는 보이지 않는다. 요청자 본인도 마찬가지
    assert pending_requests(env, team["staff"]) == {"total": 0, "items": []}
    assert pending_requests(env, team["staff2"]) == {"total": 0, "items": []}


def test_pending_change_requests_drop_after_resolve(env, team):
    meeting_id = make_meeting(env["factory"], team["mgr"], participants=[team["staff"]])
    accepted = create_request(env, team["staff"], meeting_id, "첫 요청")
    waiting = create_request(env, team["staff"], meeting_id, "둘째 요청")
    rejected = create_request(env, team["staff"], meeting_id, "셋째 요청")
    res = call(env, "POST", team["mgr"], f"/api/meetings/{meeting_id}/change-requests/{accepted}/resolve", json={"decision": "accepted"})
    assert res.status_code == 200
    res = call(env, "POST", team["exe"], f"/api/meetings/{meeting_id}/change-requests/{rejected}/resolve",
               json={"decision": "rejected", "reason": "불가"})
    assert res.status_code == 200
    for who in ("mgr", "mgr2", "exe"):
        body = pending_requests(env, team[who])
        assert body["total"] == 1 and [i["requestId"] for i in body["items"]] == [waiting]


def test_pending_change_requests_isolated_by_tenant(env, team):
    ours = make_meeting(env["factory"], team["mgr"], participants=[team["staff"]])
    theirs = make_meeting(env["factory"], team["outsider"])
    mine = create_request(env, team["staff"], ours, "우리 요청")
    create_request(env, team["outsider"], theirs, "다른 고객사 요청")
    assert [i["requestId"] for i in pending_requests(env, team["exe"])["items"]] == [mine]
    # 다른 고객사 쪽은 그 고객사의 다른 관리자에게만(요청자 본인 요청은 본인 목록에서 제외되므로 다른 계정으로 확인)
    their_mgr = add_account(env["factory"], "고객사B", "out-mgr", "manager", name="외부관리")
    outsider = pending_requests(env, their_mgr)
    assert outsider["total"] == 1 and outsider["items"][0]["meetingId"] == theirs


def test_pending_change_requests_comment_preview_and_limit(env, team):
    meeting_id = make_meeting(env["factory"], team["mgr"])
    long_comment = "가" * 100
    create_request(env, team["mgr"], meeting_id, long_comment)
    for n in range(54):
        create_request(env, team["mgr"], meeting_id, f"요청 {n}")
    body = pending_requests(env, team["exe"])
    assert body["total"] == 55 and len(body["items"]) == 50
    assert body["items"][0]["commentPreview"] == "가" * 80 + "…"


def test_pending_change_requests_exclude_own_requests(env, team):
    meeting_id = make_meeting(env["factory"], team["mgr"], participants=[team["staff"]])
    by_mgr = create_request(env, team["mgr"], meeting_id, "관리자가 남긴 요청")
    by_exe = create_request(env, team["exe"], meeting_id, "지시자가 남긴 요청")
    by_staff = create_request(env, team["staff"], meeting_id, "담당자가 남긴 요청")

    def ids(who):
        body = pending_requests(env, team[who])
        assert body["total"] == len(body["items"])  # 건수도 같은 기준
        return [i["requestId"] for i in body["items"]]

    assert ids("mgr") == [by_exe, by_staff]  # 본인 요청 제외
    assert ids("exe") == [by_mgr, by_staff]
    assert ids("mgr2") == [by_mgr, by_exe, by_staff]  # 다른 관리자에게는 모두
    assert pending_requests(env, team["staff"]) == {"total": 0, "items": []}
