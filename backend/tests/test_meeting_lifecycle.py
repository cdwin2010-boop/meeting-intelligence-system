"""회의록 단계(진행중·종료·보류·삭제): 상태별 목록, 담당자 조회 제한, 자동 종료·직권 종료(POST /end), 삭제(POST /delete),
종료·삭제 회의록 쓰기 409, 삭제 시 딸린 업무·수정 요청의 목록·할 일·자동 확정·메일·안내 제외."""
from datetime import date, datetime, timedelta, timezone

import pytest
from alembic import command
from sqlalchemy import select, text

from app.db import make_engine
from app.jobs.auto_confirm import run_auto_confirm
from app.jobs.daily_mail import _counts as daily_counts
from app.models import ActionItem, Event, Meeting, MeetingClosure
from app.services.lifecycle import auto_end_if_all_closed
from app.services.mail import queue_immediate_new_minutes
from tests.test_meeting_queries import make_meeting
from tests.test_upload_processing import _alembic, add_account, auth, env  # noqa: F401  (env 는 픽스처)

_BEFORE_CLOSURES = "7c021789291d"  # meeting_closures 마이그레이션의 down_revision
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


def closure(env, meeting_id) -> MeetingClosure | None:
    with env["factory"]() as s:
        return s.get(MeetingClosure, meeting_id)


def meeting_events(env, meeting_id, *types) -> list[Event]:
    with env["factory"]() as s:
        return s.scalars(select(Event).where(Event.entity_type == "meeting", Event.entity_id == meeting_id,
                                             Event.event_type.in_(types)).order_by(Event.id)).all()


def confirmed(**fields) -> dict:
    return {"title": "업무", "due_date": DUE, "status": "confirmed", "confirm_kind": "manager", **fields}


def new_meeting(env, team, day=0, **kwargs):
    """총괄(lead) 등록, 담당자(staff) 참석 회의록."""
    return make_meeting(env["factory"], team["lead"], day=day, participants=[team["staff"]], **kwargs)


def list_ids(env, account, **params):
    res = call(env, "GET", account, "/api/meetings", params=params)
    assert res.status_code == 200, res.text
    return [m["id"] for m in res.json()["items"]], res.json()["items"]


def end(env, account, meeting_id):
    return call(env, "POST", account, f"/api/meetings/{meeting_id}/end")


def delete(env, account, meeting_id):
    return call(env, "POST", account, f"/api/meetings/{meeting_id}/delete")


# ---------------- 상태별 목록·담당자 조회 제한 ----------------
@pytest.fixture
def phases(env, team):
    active = new_meeting(env, team, day=0)
    ended = new_meeting(env, team, day=1)
    held = new_meeting(env, team, day=2)
    deleted = new_meeting(env, team, day=3)
    assert end(env, team["lead"], ended).status_code == 200
    assert call(env, "POST", team["lead"], f"/api/meetings/{held}/hold").status_code == 200
    assert delete(env, team["lead"], deleted).status_code == 200
    return {"active": active, "ended": ended, "on_hold": held, "deleted": deleted}


def test_list_by_phase_for_managers(env, team, phases):
    for who in ("lead", "mgr2", "exe"):
        assert list_ids(env, team[who])[0] == [phases["active"]]  # 기본은 진행중
        for phase, meeting_id in phases.items():
            ids, items = list_ids(env, team[who], phase=phase)
            assert ids == [meeting_id] and items[0]["phase"] == phase
    assert call(env, "GET", team["lead"], "/api/meetings", params={"phase": "unknown"}).status_code == 422


def test_staff_can_list_active_and_ended_only(env, team, phases):
    assert list_ids(env, team["staff"])[0] == [phases["active"]]
    assert list_ids(env, team["staff"], phase="ended")[0] == [phases["ended"]]
    for phase in ("on_hold", "deleted"):
        assert call(env, "GET", team["staff"], "/api/meetings", params={"phase": phase}).status_code == 403
    # 상세(와 하위 자원)도 막는다: 보류·삭제는 404, 진행중·종료는 그대로
    for phase in ("on_hold", "deleted"):
        meeting_id = phases[phase]
        assert call(env, "GET", team["staff"], f"/api/meetings/{meeting_id}").status_code == 404
        assert call(env, "GET", team["staff"], f"/api/meetings/{meeting_id}/change-requests").status_code == 404
    for phase in ("active", "ended"):
        body = call(env, "GET", team["staff"], f"/api/meetings/{phases[phase]}").json()
        assert body["phase"] == phase
    # 관리자는 삭제된 회의록 상세도 본다
    body = call(env, "GET", team["exe"], f"/api/meetings/{phases['deleted']}").json()
    assert body["phase"] == "deleted" and body["deletedBy"]["id"] == team["lead"].id and body["deletedAt"]


# ---------------- 자동 종료 ----------------
def test_closing_last_item_auto_ends_meeting(env, team):
    meeting_id = new_meeting(env, team, items=[confirmed(title="첫째"), confirmed(title="둘째"), {"title": "지운 업무"}])
    first, second, removed = item_ids(env, meeting_id)
    assert call(env, "POST", team["lead"], f"/api/action-items/{removed}/delete").status_code == 200  # 삭제 업무는 셈에서 제외
    assert call(env, "POST", team["lead"], f"/api/action-items/{first}/close").status_code == 200
    assert closure(env, meeting_id) is None  # 아직 진행 중 업무가 남음

    before = datetime.now(timezone.utc)
    assert call(env, "POST", team["exe"], f"/api/action-items/{second}/close").status_code == 200
    record = closure(env, meeting_id)
    assert record.end_kind == "auto" and record.ended_by is None and record.ended_at >= before - timedelta(seconds=1)
    [event] = meeting_events(env, meeting_id, "meeting.ended")
    assert event.actor_account_id is None and event.payload["endKind"] == "auto"
    body = call(env, "GET", team["lead"], f"/api/meetings/{meeting_id}").json()
    assert body["phase"] == "ended" and body["endKind"] == "auto" and body["endedBy"] is None and body["endedAt"]


def test_no_auto_end_without_active_items(env, team):
    empty = new_meeting(env, team)
    only_deleted = new_meeting(env, team, day=1, items=[{"title": "지운 업무", "status": "deleted"}])
    with env["factory"]() as s:
        for meeting_id in (empty, only_deleted):
            assert auto_end_if_all_closed(s, s.get(Meeting, meeting_id)) is False
        s.commit()
    assert closure(env, empty) is None and closure(env, only_deleted) is None


# ---------------- 직권 종료 ----------------
@pytest.mark.parametrize("who", ["lead", "exe"])
def test_manager_end_closes_items_and_records(env, team, who):
    meeting_id = new_meeting(env, team, items=[
        {"title": "확정 전", "assignee_id": team["staff"].id}, confirmed(title="확정"), confirmed(title="종결", status="closed"),
        {"title": "삭제", "status": "deleted"},
    ])
    pending, confirmed_id, closed_id, deleted_id = item_ids(env, meeting_id)
    res = end(env, team[who], meeting_id)
    assert res.status_code == 200
    body = res.json()
    assert body["phase"] == "ended" and body["endKind"] == "manager" and body["endedBy"]["id"] == team[who].id
    assert body["closedItemIds"] == [pending, confirmed_id]
    for item_id in (pending, confirmed_id):
        item = get_item(env, item_id)
        assert item.status == "closed" and item.closed_by == team[who].id
    assert get_item(env, closed_id).closed_by is None and get_item(env, deleted_id).status == "deleted"
    record = closure(env, meeting_id)
    assert record.end_kind == "manager" and record.ended_by == team[who].id and record.ended_at is not None
    # 이미 종료면 그대로 200, 사건 추가 없음
    assert end(env, team["exe"], meeting_id).status_code == 200
    assert len(meeting_events(env, meeting_id, "meeting.ended")) == 1
    assert closure(env, meeting_id).ended_by == team[who].id


def test_end_state_rules(env, team):
    processing = new_meeting(env, team, status="processing")
    held = new_meeting(env, team, day=1)
    deleted = new_meeting(env, team, day=2)
    call(env, "POST", team["lead"], f"/api/meetings/{held}/hold")
    delete(env, team["lead"], deleted)
    assert end(env, team["lead"], processing).status_code == 409
    res = end(env, team["lead"], held)
    assert res.status_code == 409 and "보류" in res.json()["detail"]
    res = end(env, team["lead"], deleted)
    assert res.status_code == 409 and "삭제" in res.json()["detail"]


@pytest.mark.parametrize("action", [end, delete])
def test_end_and_delete_permissions(env, team, action):
    meeting_id = new_meeting(env, team, items=[confirmed(assignee_id=team["mgr2"].id)])
    assert action(env, team["staff"], meeting_id).status_code == 403
    assert action(env, team["mgr2"], meeting_id).status_code == 403  # 총괄 아닌 관리자(본인 담당 업무가 있어도)
    assert action(env, team["outsider"], meeting_id).status_code == 404
    assert closure(env, meeting_id) is None


# ---------------- 삭제 ----------------
@pytest.mark.parametrize("who", ["lead", "exe"])
def test_delete_records_and_keeps_rows(env, team, who):
    meeting_id = new_meeting(env, team, items=[{"title": "업무", "assignee_id": team["staff"].id, "due_date": DUE}])
    [item_id] = item_ids(env, meeting_id)
    res = delete(env, team[who], meeting_id)
    assert res.status_code == 200
    body = res.json()
    assert body["phase"] == "deleted" and body["deletedBy"]["id"] == team[who].id and body["deletedAt"]
    record = closure(env, meeting_id)
    assert record.deleted_by == team[who].id and record.deleted_at is not None and record.ended_at is None
    with env["factory"]() as s:  # 회의록·업무 행은 그대로(업무 상태도 그대로)
        assert s.get(Meeting, meeting_id) is not None and s.get(ActionItem, item_id).status == "pending"
    assert delete(env, team["exe"], meeting_id).status_code == 200  # 멱등
    assert len(meeting_events(env, meeting_id, "meeting.deleted")) == 1
    processing = new_meeting(env, team, day=1, status="processing")
    assert delete(env, team["lead"], processing).status_code == 409


def test_delete_cascades_out_of_lists_todos_auto_confirm_mail_and_notices(env, team):
    meeting_id = new_meeting(env, team, items=[
        {"title": "내 업무", "assignee_id": team["staff"].id, "due_undetermined": True},
        {"title": "빈 담당", "assignee_id": None},
        {"title": "확정할 업무", "assignee_id": team["staff"].id, "due_date": DUE},
    ])
    mine, blank, to_confirm = item_ids(env, meeting_id)
    assert call(env, "POST", team["staff"], f"/api/meetings/{meeting_id}/change-requests", json={"comment": "확인"}).status_code == 201
    assert call(env, "POST", team["exe"], f"/api/action-items/{to_confirm}/confirm").status_code == 200  # lead 에게 확정 안내

    def todos(who):
        return call(env, "GET", team[who], "/api/me/todos").json()

    lead = todos("lead")
    assert lead["awaitingConfirmMeetings"]["total"] == 1 and lead["needsCompletionItems"]["total"] == 1
    assert lead["pendingChangeRequests"]["total"] == 1
    assert todos("staff")["myItems"]["total"] == 2
    assert [n["meetingId"] for n in call(env, "GET", team["lead"], "/api/me/notices").json()] == [meeting_id]

    assert delete(env, team["lead"], meeting_id).status_code == 200

    assert all(todos("lead")[key] == {"total": 0, "items": []} for key in todos("lead"))
    assert all(todos("exe")[key] == {"total": 0, "items": []} for key in todos("exe"))
    assert todos("staff")["myItems"] == {"total": 0, "items": []}
    assert call(env, "GET", team["lead"], "/api/me/notices").json() == []
    assert meeting_id not in list_ids(env, team["lead"])[0]
    # 자동 확정·메일 대상 아님
    with env["factory"]() as s:
        result = run_auto_confirm(s, datetime.now(timezone.utc) + timedelta(days=30))
        assert result["meetingsConfirmed"] == 0 and result["itemsConfirmed"] == 0
        awaiting, needs, unviewed = daily_counts(s)
        assert (awaiting.get(team["lead"].tenant_id, 0), needs.get(team["lead"].tenant_id, 0)) == (0, 0)
        assert unviewed.get(team["staff"].id, 0) == 0
        assert queue_immediate_new_minutes(s, s.get(Meeting, meeting_id)) == 0
    assert get_item(env, mine).status == "pending" and get_item(env, blank).status == "pending"


# ---------------- 종료·삭제 회의록 쓰기 409 ----------------
@pytest.mark.parametrize("phase, word", [("ended", "종료"), ("deleted", "삭제")])
def test_writes_rejected_on_ended_or_deleted(env, team, phase, word):
    meeting_id = new_meeting(env, team, transcript="화자1: 안녕", items=[
        confirmed(title="확정", status="closed"), {"title": "확정 전", "assignee_id": team["staff"].id, "due_date": DUE},
    ])
    closed_id, pending_id = item_ids(env, meeting_id)
    if phase == "ended":
        with env["factory"]() as s:  # 확정 전 업무가 남은 채로 종료된 상태를 직접 만든다(쓰기 거부 확인용)
            s.add(MeetingClosure(meeting_id=meeting_id, tenant_id=team["lead"].tenant_id, end_kind="auto",
                                 ended_at=datetime.now(timezone.utc)))
            s.commit()
    else:
        delete(env, team["lead"], meeting_id)
    requests = [
        ("PATCH", f"/api/action-items/{pending_id}", {"json": {"title": "새 이름"}}),
        ("POST", f"/api/action-items/{pending_id}/confirm", {}),
        ("POST", f"/api/action-items/{pending_id}/delete", {}),
        ("POST", f"/api/meetings/{meeting_id}/confirm", {}),
        ("PUT", f"/api/meetings/{meeting_id}/speakers", {"json": {"speakers": []}}),
        ("POST", f"/api/meetings/{meeting_id}/hold", {}),
        ("POST", f"/api/meetings/{meeting_id}/resume", {}),
    ]
    for method, path, kwargs in requests:
        res = call(env, method, team["exe"], path, **kwargs)
        assert res.status_code == 409, (method, path, res.text)
        assert word in res.json()["detail"]
    res = call(env, "POST", team["exe"], f"/api/action-items/{closed_id}/close")  # 이미 종결된 업무도 409
    assert res.status_code == 409
    assert get_item(env, pending_id).title == "확정 전" and get_item(env, pending_id).status == "pending"


# ---------------- 마이그레이션 ----------------
def test_migration_roundtrip_and_downgrade_guard(tmp_path):
    url = f"sqlite:///{(tmp_path / 'm.db').as_posix()}"
    cfg = _alembic(url)
    command.upgrade(cfg, "head")
    command.downgrade(cfg, _BEFORE_CLOSURES)
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
        # 회의록이 있어도 종료·삭제 기록이 없으면 되돌리기·다시 올리기 가능
        command.downgrade(cfg, _BEFORE_CLOSURES)
        command.upgrade(cfg, "head")
        with engine.begin() as conn:
            conn.execute(text(
                "INSERT INTO meeting_closures (meeting_id, tenant_id, end_kind, ended_at, updated_at) "
                "VALUES (1, 1, 'auto', '2026-10-02', '2026-10-02')"))
            with pytest.raises(Exception):  # 종료 구분은 auto·manager 만
                with conn.begin_nested():
                    conn.execute(text("UPDATE meeting_closures SET end_kind='other' WHERE meeting_id=1"))
        with pytest.raises(RuntimeError, match="downgrade 거부"):
            command.downgrade(cfg, _BEFORE_CLOSURES)
        with engine.connect() as conn:
            assert conn.execute(text("SELECT end_kind FROM meeting_closures")).scalar_one() == "auto"
    finally:
        engine.dispose()
