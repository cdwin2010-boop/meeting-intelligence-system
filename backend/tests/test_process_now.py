"""즉시 변환·변환 큐 우선순위·워커 생존 신호(작업 88-1).
임시 DB·임시 업로드 폴더·fake 엔진만 쓴다(Gemini 호출 없음). 워커는 run_once 만 부른다."""
import sqlite3
from datetime import timedelta
from pathlib import Path

import pytest
from alembic import command
from sqlalchemy import select

from app.config import settings
from app.models import Event, Job, Meeting, MeetingView, SystemAdmin, TaskQueue, WorkerHeartbeat
from app.models.common import utcnow
from app.services import task_queue as tq
from app.services.history import HISTORY_KINDS, KIND_MEETING_PROCESS_NOW
from app.worker_loop import Worker
from tests.test_upload_processing import BrokenExtractor, _alembic, add_account, auth, env, upload  # noqa: F401  (env 는 픽스처)

WAITING = "시스템의 선행 작업완료 후 처리하겠습니다."
_BEFORE = "e6a0c4b9d135"  # 이 작업 마이그레이션의 down_revision


@pytest.fixture(autouse=True)
def queue_mode(monkeypatch):
    monkeypatch.setattr(settings, "processing_mode", "queue")


@pytest.fixture
def team(env):
    f = env["factory"]
    return {
        "staff": add_account(f, "고객사A", "staff1", "staff", name="담당자"),
        "mgr": add_account(f, "고객사A", "mgr1", "manager", name="관리자"),
        "exe": add_account(f, "고객사A", "exe1", "executive", name="지시자"),
        "other": add_account(f, "고객사B", "mgr2", "manager", name="타사관리자"),
    }


def post_now(env, account, meeting_id):
    return env["client"].post(f"/api/meetings/{meeting_id}/process-now", headers=auth(account))


def make(env, account, n=1, title="회의"):
    return [upload(env, account, title=f"{title}{i}").json() for i in range(n)]


def tasks(env) -> dict[int, TaskQueue]:
    with env["factory"]() as s:
        return {t.meeting_id: t for t in s.scalars(select(TaskQueue).order_by(TaskQueue.id))}


def history_events(env, meeting_id):
    with env["factory"]() as s:
        return list(s.scalars(select(Event).where(Event.entity_type == "meeting", Event.entity_id == meeting_id, Event.event_type == KIND_MEETING_PROCESS_NOW)))


def run_one(env, **kwargs) -> bool:
    return Worker(env["factory"], **kwargs).run_once()


def meeting_of_next_claim(env) -> int | None:
    task = tq.claim_next(env["factory"])
    return None if task is None else task.meeting_id


# ---------------- 우선순위·처리 순서 ----------------
def test_default_order_is_fifo(env, team):
    a, b, c = (u["meetingId"] for u in make(env, team["mgr"], 3))
    assert [meeting_of_next_claim(env) for _ in range(4)] == [a, b, c, None]


def test_promoted_task_is_claimed_first_and_rest_stay_fifo(env, team):
    a, b, c = (u["meetingId"] for u in make(env, team["mgr"], 3))
    res = post_now(env, team["mgr"], c)
    assert res.status_code == 200
    assert [meeting_of_next_claim(env) for _ in range(3)] == [c, a, b]


def test_two_promoted_keep_fifo_among_themselves(env, team):
    a, b, c = (u["meetingId"] for u in make(env, team["mgr"], 3))
    assert post_now(env, team["mgr"], c).status_code == 200
    assert post_now(env, team["mgr"], b).status_code == 200
    assert [meeting_of_next_claim(env) for _ in range(3)] == [b, c, a]  # 같은 우선순위는 접수 순서


def test_worker_picks_promoted_meeting_first_and_never_runs_two_at_once(env, team):
    a, b = (u["meetingId"] for u in make(env, team["mgr"], 2))
    assert post_now(env, team["mgr"], b).status_code == 200
    assert run_one(env) is True  # 한 번에 하나만 처리
    t = tasks(env)
    assert t[b].status == "DONE" and t[a].status == "PENDING"
    assert run_one(env) is True
    assert tasks(env)[a].status == "DONE"


def test_priority_value_comes_from_settings(env, team, monkeypatch):
    monkeypatch.setattr(settings, "queue_priority_immediate", 7)
    [m] = (u["meetingId"] for u in make(env, team["mgr"], 1))
    assert post_now(env, team["mgr"], m).status_code == 200
    row = tasks(env)[m]
    assert row.priority == 7 and row.promoted_at is not None


# ---------------- 권한 ----------------
def test_staff_gets_403_even_for_own_meeting_and_nothing_changes(env, team):
    [u] = make(env, team["staff"], 1)
    res = post_now(env, team["staff"], u["meetingId"])
    assert res.status_code == 403
    assert tasks(env)[u["meetingId"]].priority == 0
    assert history_events(env, u["meetingId"]) == []


def test_manager_and_executive_succeed(env, team):
    [u1, u2] = make(env, team["staff"], 2)
    assert post_now(env, team["mgr"], u1["meetingId"]).status_code == 200  # 관리자는 같은 고객사 회의록을 모두 본다(기존 열람 규칙)
    res = post_now(env, team["exe"], u2["meetingId"])
    assert res.status_code == 200 and res.json()["prioritized"] is True


def test_manager_succeeds_on_own_meeting(env, team):
    [u] = make(env, team["mgr"], 1)
    res = post_now(env, team["mgr"], u["meetingId"])
    assert res.status_code == 200
    assert tasks(env)[u["meetingId"]].priority == settings.queue_priority_immediate


def test_other_tenant_cannot_access(env, team):
    [u] = make(env, team["mgr"], 1)
    assert post_now(env, team["other"], u["meetingId"]).status_code == 404
    assert tasks(env)[u["meetingId"]].priority == 0


def test_requires_login_and_unknown_meeting_is_404(env, team):
    assert env["client"].post("/api/meetings/1/process-now").status_code == 401
    assert post_now(env, team["mgr"], 9999).status_code == 404


def test_system_admin_cannot_use_it(env, team):
    [u] = make(env, team["mgr"], 1)
    admin = add_account(env["factory"], "고객사A", "sysadmin", "staff", name="시스템")
    with env["factory"]() as s:
        s.add(SystemAdmin(account_id=admin.id, tenant_id=admin.tenant_id))
        s.commit()
    assert post_now(env, admin, u["meetingId"]).status_code == 404


# ---------------- 상태 충돌·멱등·이력 ----------------
def test_conflict_when_processing(env, team):
    [u] = make(env, team["mgr"], 1)
    claimed = tq.claim_next(env["factory"])
    assert claimed.meeting_id == u["meetingId"]
    res = post_now(env, team["mgr"], u["meetingId"])
    assert res.status_code == 409 and "진행 중" in res.json()["detail"]


def test_conflict_when_completed(env, team):
    [u] = make(env, team["mgr"], 1)
    assert run_one(env) is True
    res = post_now(env, team["mgr"], u["meetingId"])
    assert res.status_code == 409 and "완료" in res.json()["detail"]


def test_conflict_when_failed(env, team):
    [u] = make(env, team["mgr"], 1)
    assert run_one(env, extractor_factory=BrokenExtractor) is True
    res = post_now(env, team["mgr"], u["meetingId"])
    assert res.status_code == 409 and "실패" in res.json()["detail"]
    assert history_events(env, u["meetingId"]) == []


def test_conflict_when_not_queue_mode(env, team, monkeypatch):
    [u] = make(env, team["mgr"], 1)
    monkeypatch.setattr(settings, "processing_mode", "inline")
    res = post_now(env, team["mgr"], u["meetingId"])
    assert res.status_code == 409 and "대기열" in res.json()["detail"]


def test_idempotent_second_request_records_one_history(env, team):
    [u] = make(env, team["mgr"], 1)
    first = post_now(env, team["mgr"], u["meetingId"])
    second = post_now(env, team["mgr"], u["meetingId"])
    assert first.status_code == second.status_code == 200
    assert first.json()["alreadyPrioritized"] is False and second.json()["alreadyPrioritized"] is True
    events = history_events(env, u["meetingId"])
    assert len(events) == 1
    ev = events[0]
    assert ev.actor_account_id == team["mgr"].id and ev.created_at is not None
    assert ev.payload["reason"] == "즉시 변환 요청"
    assert ev.payload["before"] == {"priority": 0} and ev.payload["after"] == {"priority": settings.queue_priority_immediate}


def test_history_kind_is_registered_and_listed_in_meeting_history(env, team):
    assert HISTORY_KINDS[KIND_MEETING_PROCESS_NOW] == "즉시 변환"
    [u] = make(env, team["mgr"], 1)
    post_now(env, team["mgr"], u["meetingId"])
    res = env["client"].get(f"/api/meetings/{u['meetingId']}/history", headers=auth(team["mgr"]))
    assert res.status_code == 200
    [entry] = [e for e in res.json() if e["kindCode"] == KIND_MEETING_PROCESS_NOW]
    assert entry["kind"] == "즉시 변환" and entry["changedBy"]["id"] == team["mgr"].id
    assert entry["after"] == {"priority": settings.queue_priority_immediate}


# ---------------- 대기 순번·waitingForPrior·문구 ----------------
def test_idle_worker_first_in_line_does_not_wait(env, team):
    [u] = make(env, team["mgr"], 1)
    body = post_now(env, team["mgr"], u["meetingId"]).json()
    assert body["queueAhead"] == 0 and body["waitingForPrior"] is False
    assert body["message"] != WAITING
    assert body["status"] == "queued" and body["meetingId"] == u["meetingId"] and body["jobId"] == u["jobId"]
    assert set(body) == {
        "meetingId", "jobId", "status", "queueAhead", "prioritized", "alreadyPrioritized", "waitingForPrior", "message", "workerAlive",
    }


def test_waiting_for_prior_when_another_task_is_processing(env, team):
    a, b = (u["meetingId"] for u in make(env, team["mgr"], 2))
    assert tq.claim_next(env["factory"]).meeting_id == a  # a 진행 중
    body = post_now(env, team["mgr"], b).json()
    assert body["waitingForPrior"] is True and body["message"] == WAITING
    assert body["queueAhead"] == 0  # 앞에 대기 작업은 없고 진행 중 작업 하나만 있다


def test_queue_ahead_counts_pending_before_it(env, team):
    a, b, c = (u["meetingId"] for u in make(env, team["mgr"], 3))
    # 승격 전 상세 응답의 대기 순번: c 앞에 a, b
    detail = env["client"].get(f"/api/meetings/{c}", headers=auth(team["mgr"])).json()["processing"]
    assert detail["queueAhead"] == 2 and detail["prioritized"] is False and detail["waitingForPrior"] is True
    body = post_now(env, team["mgr"], c).json()
    assert body["queueAhead"] == 0 and body["waitingForPrior"] is False  # 맨 앞이고 진행 중인 작업도 없다 → 바로 시작
    detail_a = env["client"].get(f"/api/meetings/{a}", headers=auth(team["mgr"])).json()["processing"]
    assert detail_a["queueAhead"] == 1  # c 가 앞으로 와서 a 는 한 칸 뒤


def test_waiting_for_prior_when_promoted_task_is_ahead(env, team):
    a, b = (u["meetingId"] for u in make(env, team["mgr"], 2))
    post_now(env, team["mgr"], a)  # 먼저 올려진(접수도 먼저인) a 가 앞서 시작한다
    body = post_now(env, team["mgr"], b).json()
    assert body["queueAhead"] == 1 and body["waitingForPrior"] is True and body["message"] == WAITING


# ---------------- 상태 응답 확장·워커 생존 신호 ----------------
def test_worker_alive_follows_heartbeat_freshness(env, team, monkeypatch):
    [u] = make(env, team["mgr"], 1)
    mid = u["meetingId"]

    def processing():
        return env["client"].get(f"/api/meetings/{mid}", headers=auth(team["mgr"])).json()["processing"]

    assert processing()["workerAlive"] is False  # 신호 없음
    w = Worker(env["factory"])
    w.beat(force=True)
    assert processing()["workerAlive"] is True
    stale = utcnow() - timedelta(seconds=settings.worker_heartbeat_stale_sec + 5)
    tq.beat(env["factory"], w.worker_id, now=stale)
    assert processing()["workerAlive"] is False
    tq.beat(env["factory"], w.worker_id, now=utcnow() - timedelta(seconds=max(1, settings.worker_heartbeat_stale_sec - 5)))
    assert processing()["workerAlive"] is True
    with env["factory"]() as s:
        assert len(list(s.scalars(select(WorkerHeartbeat)))) == 1  # 워커 1개당 1행


def test_my_processing_carries_queue_fields_and_worker_alive(env, team):
    a, b = (u["meetingId"] for u in make(env, team["mgr"], 2))
    items = {i["meetingId"]: i for i in env["client"].get("/api/me/processing", headers=auth(team["mgr"])).json()}
    assert items[a]["queueAhead"] == 0 and items[b]["queueAhead"] == 1
    assert items[a]["prioritized"] is False and items[b]["workerAlive"] is False
    post_now(env, team["mgr"], b)
    items = {i["meetingId"]: i for i in env["client"].get("/api/me/processing", headers=auth(team["mgr"])).json()}
    assert items[b]["prioritized"] is True and items[b]["queueAhead"] == 0 and items[a]["queueAhead"] == 1


def test_inline_mode_has_no_queue_fields(env, team, monkeypatch):
    monkeypatch.setattr(settings, "processing_mode", "inline")
    [u] = make(env, team["mgr"], 1)
    detail = env["client"].get(f"/api/meetings/{u['meetingId']}", headers=auth(team["mgr"])).json()["processing"]
    assert detail["queueAhead"] is None and detail["workerAlive"] is None and detail["prioritized"] is False


def test_allowed_actions_lists_process_now_only_for_manager_up_on_pending_task(env, team):
    [u] = make(env, team["staff"], 1)
    mid = u["meetingId"]

    def actions(account):
        res = env["client"].get(f"/api/meetings/{mid}", headers=auth(account))
        return res.json().get("allowedActions", []) if res.status_code == 200 else None

    assert "process_now" not in actions(team["staff"])
    assert "process_now" in actions(team["exe"])
    post_now(env, team["exe"], mid)
    assert "process_now" not in actions(team["exe"])  # 이미 올려짐


def test_worker_run_once_records_heartbeat(env, team):
    make(env, team["mgr"], 1)
    w = Worker(env["factory"])
    assert w.run_once() is True
    with env["factory"]() as s:
        assert s.scalar(select(WorkerHeartbeat.worker_id)) == w.worker_id


# ---------------- /api/me/processing 의 canProcessNow (작업 88-3) ----------------
def mine(env, account) -> list[dict]:
    res = env["client"].get("/api/me/processing", headers=auth(account))
    assert res.status_code == 200
    return res.json()


def can_now(env, account, meeting_id) -> bool:
    [row] = [r for r in mine(env, account) if r["meetingId"] == meeting_id]
    return row["canProcessNow"]


def test_can_process_now_false_for_staff_true_for_manager_and_executive_own_queued(env, team):
    [su] = make(env, team["staff"], 1)
    [mu] = make(env, team["mgr"], 1)
    [eu] = make(env, team["exe"], 1)
    assert can_now(env, team["staff"], su["meetingId"]) is False  # 담당자는 항상 false
    assert can_now(env, team["mgr"], mu["meetingId"]) is True
    assert can_now(env, team["exe"], eu["meetingId"]) is True


def test_can_process_now_matches_allowed_actions_and_follows_state(env, team):
    [a, b] = (u["meetingId"] for u in make(env, team["mgr"], 2))
    detail = env["client"].get(f"/api/meetings/{b}", headers=auth(team["mgr"])).json()
    assert ("process_now" in detail["allowedActions"]) is can_now(env, team["mgr"], b) is True  # 같은 판정 함수
    assert post_now(env, team["mgr"], b).status_code == 200
    assert can_now(env, team["mgr"], b) is False  # 이미 우선 처리
    assert tq.claim_next(env["factory"]).meeting_id == b  # 진행 중
    assert can_now(env, team["mgr"], b) is False
    assert can_now(env, team["mgr"], a) is True  # 아직 대기


def test_can_process_now_false_when_done_or_failed(env, team):
    [done, failed] = (u["meetingId"] for u in make(env, team["mgr"], 2))
    assert run_one(env) is True  # done 완료
    assert run_one(env, extractor_factory=BrokenExtractor) is True  # failed 실패
    assert can_now(env, team["mgr"], done) is False
    assert can_now(env, team["mgr"], failed) is False


def test_can_process_now_false_when_not_queue_mode(env, team, monkeypatch):
    [u] = make(env, team["mgr"], 1)
    monkeypatch.setattr(settings, "processing_mode", "inline")
    assert can_now(env, team["mgr"], u["meetingId"]) is False


def test_other_tenant_items_not_exposed(env, team):
    [u] = make(env, team["mgr"], 1)
    assert [r["meetingId"] for r in mine(env, team["other"])] == []
    assert [r["meetingId"] for r in mine(env, team["mgr"])] == [u["meetingId"]]


def test_me_processing_creates_no_view_record(env, team):
    make(env, team["mgr"], 2)
    with env["factory"]() as s:
        before = len(list(s.scalars(select(MeetingView))))
    for _ in range(3):
        mine(env, team["mgr"])
        mine(env, team["staff"])
    with env["factory"]() as s:
        assert len(list(s.scalars(select(MeetingView)))) == before


# ---------------- 마이그레이션 ----------------
def test_migration_roundtrip_and_downgrade_guard(tmp_path):
    url = f"sqlite:///{(tmp_path / 'm.db').as_posix()}"
    cfg = _alembic(url)
    command.upgrade(cfg, "head")
    command.downgrade(cfg, _BEFORE)
    raw = sqlite3.connect(tmp_path / "m.db")
    cols = {r[1] for r in raw.execute("PRAGMA table_info(task_queue)")}
    tables = {r[0] for r in raw.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    raw.close()
    assert "priority" not in cols and "worker_heartbeats" not in tables
    command.upgrade(cfg, "head")
    command.check(cfg)
    raw = sqlite3.connect(tmp_path / "m.db")
    cols = {r[1]: r for r in raw.execute("PRAGMA table_info(task_queue)")}
    assert cols["priority"][3] == 1 and cols["priority"][4] in ("0", "'0'")  # NOT NULL, 기본 0
    raw.execute(
        "INSERT INTO task_queue (tenant_id, meeting_id, status, progress, attempts, priority, created_at, updated_at) "
        "VALUES (1, 1, 'PENDING', 0, 0, 100, '2026-10-01', '2026-10-01')"
    )
    raw.commit()
    raw.close()
    with pytest.raises(RuntimeError, match="downgrade 거부"):
        command.downgrade(cfg, _BEFORE)
    check = sqlite3.connect(tmp_path / "m.db")
    try:
        assert check.execute("SELECT priority FROM task_queue").fetchone()[0] == 100  # 거부했으므로 기록 유지
    finally:
        check.close()


def test_existing_rows_get_default_priority_zero(tmp_path):
    url = f"sqlite:///{(tmp_path / 'old.db').as_posix()}"
    cfg = _alembic(url)
    command.upgrade(cfg, _BEFORE)
    raw = sqlite3.connect(tmp_path / "old.db")
    raw.execute(
        "INSERT INTO task_queue (tenant_id, meeting_id, status, progress, attempts, created_at, updated_at) "
        "VALUES (1, 1, 'PENDING', 0, 0, '2026-10-01', '2026-10-01')"
    )
    raw.commit()
    raw.close()
    command.upgrade(cfg, "head")
    check = sqlite3.connect(tmp_path / "old.db")
    try:
        assert check.execute("SELECT priority, promoted_at FROM task_queue").fetchone() == (0, None)
    finally:
        check.close()


# ---------------- 계약 파일(docs/contracts/process-now-v1.json) ----------------
def test_contract_schema_matches_response_model_and_examples():
    import json

    from pydantic import ValidationError

    from app.api.meeting_process_now import ProcessNowOut
    from app.services.task_queue import START_SOON_MESSAGE, WAITING_MESSAGE

    docs = Path(__file__).resolve().parent.parent.parent / "docs" / "contracts"
    schema = json.loads((docs / "process-now-v1.json").read_text(encoding="utf-8"))
    aliases = {field.alias or name for name, field in ProcessNowOut.model_fields.items()}
    assert set(schema["properties"]) == aliases == set(schema["required"])
    valid = json.loads((docs / "process-now-examples" / "valid.json").read_text(encoding="utf-8"))
    ProcessNowOut.model_validate(valid)
    assert valid["message"] == WAITING_MESSAGE
    assert START_SOON_MESSAGE in schema["properties"]["message"]["description"]
    invalid = json.loads((docs / "process-now-examples" / "invalid.json").read_text(encoding="utf-8"))
    with pytest.raises(ValidationError):
        ProcessNowOut.model_validate(invalid)
