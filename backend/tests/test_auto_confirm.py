"""자동 확정(백스톱) 작업: now 를 주입해 시각별 동작을 확인한다. 네트워크 없음.
시각 표기: KST = UTC+9. 예) 2026-10-06T01:00Z = 2026-10-06 10:00 KST."""
from datetime import date, datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from app.jobs import auto_confirm as job_module
from app.jobs.auto_confirm import main, run_auto_confirm
from app.models import ActionItem, Event, Meeting, SourceDocument, Tenant
from tests.test_upload_processing import add_account, env  # noqa: F401  (env 는 픽스처)

UTC = timezone.utc
AUTO_AT = datetime(2026, 10, 6, 1, 0, tzinfo=UTC)  # 회의록 자동 확정 시각 = 10/6 10:00 KST


def utc(*args) -> datetime:
    return datetime(*args, tzinfo=UTC)


@pytest.fixture
def mgr(env):
    return add_account(env["factory"], "고객사A", "mgr", "manager")


def make(factory, account, *, status="awaiting_confirmation", auto_at=AUTO_AT, items=()) -> tuple[int, list[int]]:
    """회의록 1건 + 업무들. 업무 기본값은 보완 완비(업무명·담당자·기한 미확정)."""
    with factory() as s:
        doc = SourceDocument(tenant_id=account.tenant_id, origin="audio_minutes", doc_type="회의록", title="d",
                             registered_by=account.id)
        s.add(doc)
        s.flush()
        meeting = Meeting(tenant_id=account.tenant_id, source_document_id=doc.id, title="회의",
                          held_at=utc(2026, 10, 1), status=status, first_created_at=utc(2026, 10, 1),
                          auto_confirm_at=auto_at)
        s.add(meeting)
        s.flush()
        ids = []
        for fields in items:
            item = ActionItem(tenant_id=account.tenant_id, meeting_id=meeting.id,
                              **{"title": "업무", "assignee_id": account.id, "due_undetermined": True, **fields})
            s.add(item)
            s.flush()
            ids.append(item.id)
        s.commit()
        return meeting.id, ids


def run(env, now: datetime, **kwargs) -> dict:
    with env["factory"]() as s:
        return run_auto_confirm(s, now, **kwargs)


def meeting_of(env, meeting_id: int) -> Meeting:
    with env["factory"]() as s:
        return s.get(Meeting, meeting_id)


def item_of(env, item_id: int) -> ActionItem:
    with env["factory"]() as s:
        return s.get(ActionItem, item_id)


def auto_events(env) -> list[tuple[str, int, dict]]:
    with env["factory"]() as s:
        rows = s.scalars(select(Event).where(Event.event_type.in_(["meeting.auto_confirmed", "item.auto_confirmed"])).order_by(Event.id))
        return [(e.event_type, e.entity_id, e.payload) for e in rows]


# ---------------- 회의록 ----------------
def test_meeting_before_and_after_auto_confirm_at(env, mgr):
    meeting_id, _ = make(env["factory"], mgr)
    before = run(env, AUTO_AT - timedelta(seconds=1))
    assert before["meetingsConfirmed"] == 0 and meeting_of(env, meeting_id).status == "awaiting_confirmation"

    after = run(env, AUTO_AT)
    assert after == {"meetingsConfirmed": 1, "itemsConfirmed": 0, "itemsSkippedIncomplete": 0, "failed": []}
    meeting = meeting_of(env, meeting_id)
    assert meeting.status == "confirmed" and meeting.confirm_kind == "period_elapsed"
    assert meeting.confirmed_by is None and meeting.confirmed_at == AUTO_AT
    with env["factory"]() as s:
        [event] = s.scalars(select(Event).where(Event.event_type == "meeting.auto_confirmed")).all()
        assert event.actor_account_id is None and event.payload == {"confirmKind": "period_elapsed"}


def test_manually_confirmed_meeting_and_item_untouched(env, mgr):
    meeting_id, [item_id] = make(env["factory"], mgr, status="confirmed", items=[
        {"status": "confirmed", "confirm_kind": "manager", "confirmed_by": mgr.id}])
    with env["factory"]() as s:
        m = s.get(Meeting, meeting_id)
        m.confirm_kind, m.confirmed_by = "manager", mgr.id
        s.commit()
    result = run(env, AUTO_AT + timedelta(days=30))
    assert result["meetingsConfirmed"] == 0 and result["itemsConfirmed"] == 0
    assert meeting_of(env, meeting_id).confirm_kind == "manager"
    assert item_of(env, item_id).confirm_kind == "manager" and item_of(env, item_id).confirmed_by == mgr.id
    assert auto_events(env) == []


# ---------------- 업무 ----------------
def test_due_date_first_is_due_reached(env, mgr):
    _, [item_id] = make(env["factory"], mgr, items=[{"due_date": date(2026, 10, 3), "due_undetermined": False}])
    assert run(env, utc(2026, 10, 2, 14, 59))["itemsConfirmed"] == 0  # 10/2 23:59 KST
    assert run(env, utc(2026, 10, 2, 15, 0))["itemsConfirmed"] == 1  # 10/3 00:00 KST
    item = item_of(env, item_id)
    assert item.status == "confirmed" and item.confirm_kind == "due_reached" and item.confirmed_by is None


def test_auto_confirm_at_first_is_period_elapsed(env, mgr):
    _, [item_id] = make(env["factory"], mgr, items=[{"due_date": date(2026, 10, 20), "due_undetermined": False}])
    assert run(env, AUTO_AT - timedelta(minutes=1))["itemsConfirmed"] == 0
    assert run(env, AUTO_AT)["itemsConfirmed"] == 1
    assert item_of(env, item_id).confirm_kind == "period_elapsed"


def test_same_day_is_due_reached(env, mgr):
    # 마감일 = 10/6, 회의록 기한 = 10/6 10:00 KST. 10/6 00:00 KST 에 마감일 도달 → due_reached
    _, [item_id] = make(env["factory"], mgr, items=[{"due_date": date(2026, 10, 6), "due_undetermined": False}])
    assert run(env, utc(2026, 10, 5, 15, 0))["itemsConfirmed"] == 1
    assert item_of(env, item_id).confirm_kind == "due_reached"


def test_same_day_after_both_still_due_reached(env, mgr):
    _, [item_id] = make(env["factory"], mgr, items=[{"due_date": date(2026, 10, 6), "due_undetermined": False}])
    run(env, AUTO_AT + timedelta(hours=1))
    assert item_of(env, item_id).confirm_kind == "due_reached"


def test_undetermined_due_uses_auto_confirm_at_only(env, mgr):
    _, [item_id] = make(env["factory"], mgr, items=[{"due_date": None, "due_undetermined": True}])
    assert run(env, AUTO_AT - timedelta(seconds=1))["itemsConfirmed"] == 0
    assert run(env, AUTO_AT)["itemsConfirmed"] == 1
    assert item_of(env, item_id).confirm_kind == "period_elapsed"


def test_incomplete_items_never_auto_confirmed(env, mgr):
    _, ids = make(env["factory"], mgr, items=[
        {"assignee_id": None},  # 담당자 없음
        {"title": "  "},  # 업무명 빈칸
        {"due_date": None, "due_undetermined": False},  # 기한 빈칸
        {"assignee_id": None, "due_date": date(2026, 10, 2), "due_undetermined": False},  # 마감일 지났어도 보완 필요
    ])
    result = run(env, AUTO_AT + timedelta(days=30))
    assert result["itemsConfirmed"] == 0 and result["itemsSkippedIncomplete"] == 4
    assert {item_of(env, i).status for i in ids} == {"pending"}


@pytest.mark.parametrize("item_status", ["deleted", "closed"])
def test_deleted_and_closed_items_excluded(env, mgr, item_status):
    _, [item_id] = make(env["factory"], mgr, items=[{"status": item_status}])
    assert run(env, AUTO_AT + timedelta(days=30))["itemsConfirmed"] == 0
    assert item_of(env, item_id).status == item_status and item_of(env, item_id).confirm_kind is None


@pytest.mark.parametrize("meeting_status", ["processing", "failed", "no_content"])
def test_items_of_unfinished_meetings_excluded(env, mgr, meeting_status):
    meeting_id, [item_id] = make(env["factory"], mgr, status=meeting_status, items=[
        {"due_date": date(2026, 10, 2), "due_undetermined": False}])
    result = run(env, AUTO_AT + timedelta(days=30))
    assert result == {"meetingsConfirmed": 0, "itemsConfirmed": 0, "itemsSkippedIncomplete": 0, "failed": []}
    assert item_of(env, item_id).status == "pending" and meeting_of(env, meeting_id).status == meeting_status


def test_items_of_confirmed_meeting_are_eligible(env, mgr):
    _, [item_id] = make(env["factory"], mgr, status="confirmed", items=[{}])
    assert run(env, AUTO_AT)["itemsConfirmed"] == 1
    assert item_of(env, item_id).confirm_kind == "period_elapsed"


# ---------------- 멱등·시계·경계 ----------------
def test_running_twice_does_not_duplicate_events(env, mgr):
    make(env["factory"], mgr, items=[{}, {"due_date": date(2026, 10, 2), "due_undetermined": False}])
    first = run(env, AUTO_AT)
    second = run(env, AUTO_AT + timedelta(hours=1))
    assert (first["meetingsConfirmed"], first["itemsConfirmed"]) == (1, 2)
    assert (second["meetingsConfirmed"], second["itemsConfirmed"]) == (0, 0)
    assert len(auto_events(env)) == 3


def test_tenant_days_change_does_not_move_existing_deadline(env, mgr):
    meeting_id, _ = make(env["factory"], mgr)  # auto_confirm_at = 10/6 (5일 기준으로 저장된 값)
    with env["factory"]() as s:
        s.get(Tenant, mgr.tenant_id).auto_confirm_days = 10
        s.commit()
    assert run(env, AUTO_AT)["meetingsConfirmed"] == 1  # 10일로 다시 계산하지 않음
    assert meeting_of(env, meeting_id).auto_confirm_at == AUTO_AT


def test_kst_midnight_boundary(env, mgr):
    """마감일 10/10: UTC 10/9 14:59(KST 10/9 23:59)는 아직, UTC 10/9 15:00(KST 10/10 00:00)부터 도달."""
    _, [item_id] = make(env["factory"], mgr, auto_at=utc(2026, 10, 30), items=[
        {"due_date": date(2026, 10, 10), "due_undetermined": False}])
    assert run(env, utc(2026, 10, 9, 14, 59, 59))["itemsConfirmed"] == 0
    assert item_of(env, item_id).status == "pending"
    assert run(env, utc(2026, 10, 9, 15, 0, 0))["itemsConfirmed"] == 1
    assert item_of(env, item_id).confirm_kind == "due_reached"


def test_one_failure_does_not_block_others(env, mgr, monkeypatch):
    meeting_a, [item_a] = make(env["factory"], mgr, items=[{}])
    meeting_b, [item_b] = make(env["factory"], mgr, items=[{}])
    real = job_module.append_event

    def flaky(session, **kwargs):
        if kwargs["entity_type"] == "meeting" and kwargs["entity_id"] == meeting_a:
            raise RuntimeError("secret detail")
        if kwargs["entity_type"] == "action_item" and kwargs["entity_id"] == item_b:
            raise RuntimeError("secret detail")
        return real(session, **kwargs)

    monkeypatch.setattr(job_module, "append_event", flaky)
    result = run(env, AUTO_AT)
    assert result["failed"] == [{"kind": "meeting", "id": meeting_a}, {"kind": "item", "id": item_b}]
    assert "secret" not in str(result)
    assert result["meetingsConfirmed"] == 1 and result["itemsConfirmed"] == 1
    # 실패 건은 상태 변경도 롤백, 나머지는 확정
    assert meeting_of(env, meeting_a).status == "awaiting_confirmation"
    assert meeting_of(env, meeting_b).status == "confirmed"
    assert item_of(env, item_a).status == "confirmed" and item_of(env, item_b).status == "pending"


def test_naive_now_rejected(env):
    with pytest.raises(ValueError):
        run(env, datetime(2026, 10, 6, 1, 0))


# ---------------- 실행 진입점 ----------------
def test_dry_run_counts_without_changes(env, mgr, capsys, monkeypatch):
    meeting_id, ids = make(env["factory"], mgr, auto_at=utc(2026, 1, 1), items=[{}, {"assignee_id": None}])
    events_before = len(auto_events(env))

    assert main(["--dry-run"], session_factory=env["factory"]) == 0
    out = capsys.readouterr().out
    assert out.strip() == "[dry-run] meetingsConfirmed=1 itemsConfirmed=1 itemsSkippedIncomplete=1 failed=0"
    assert meeting_of(env, meeting_id).status == "awaiting_confirmation"
    assert [item_of(env, i).status for i in ids] == ["pending", "pending"]
    assert len(auto_events(env)) == events_before

    assert main([], session_factory=env["factory"]) == 0
    assert capsys.readouterr().out.strip() == "meetingsConfirmed=1 itemsConfirmed=1 itemsSkippedIncomplete=1 failed=0"
    assert meeting_of(env, meeting_id).status == "confirmed"
