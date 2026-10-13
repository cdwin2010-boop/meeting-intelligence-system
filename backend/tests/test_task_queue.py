"""작업 큐(task_queue)와 단독 워커(작업 75-1): 큐 서비스, queue 모드 업로드·재처리, 워커 run_once, GPU 해제 훅, 마이그레이션.
임시 DB·임시 업로드 폴더·fake 엔진만 쓴다. 워커는 run_once 만 부른다(프로세스를 띄우지 않는다)."""
import io
import re
import sqlite3
import sys
import threading
import types
from datetime import timedelta
from pathlib import Path

import pytest
from alembic import command
from sqlalchemy import select, update

from app.config import settings
from app.models import ActionItem, Job, Meeting, TaskQueue
from app.models.common import utcnow
from app.pipeline import gpu
from app.pipeline.extractor import FakeExtractor
from app.services import task_queue as tq
from app.worker_loop import Worker
from tests.test_transcript_txt import TIMED, post_txt  # noqa: F401
from tests.test_upload_processing import BrokenExtractor, _alembic, add_account, auth, env, upload  # noqa: F401  (env 는 픽스처)

_BEFORE = "a7d3e9b52c16"  # task_queue 마이그레이션의 down_revision
_ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def mgr(env):
    return add_account(env["factory"], "고객사A", "mgr", "manager", name="박관리")


@pytest.fixture
def queue_mode(monkeypatch):
    monkeypatch.setattr(settings, "processing_mode", "queue")


def tasks(env) -> list[TaskQueue]:
    with env["factory"]() as s:
        return list(s.scalars(select(TaskQueue).order_by(TaskQueue.id)))


def meeting_status(env, meeting_id) -> str:
    with env["factory"]() as s:
        return s.get(Meeting, meeting_id).status


def worker(env, **kwargs) -> Worker:
    return Worker(env["factory"], **kwargs)


# ---------------- 큐 서비스 ----------------
def test_enqueue_reuses_active_task_and_creates_new_after_done(env, mgr):
    [meeting_id] = make_pending_inline(env, mgr)
    with env["factory"]() as s:
        first = tq.enqueue(s, meeting_id)
        s.commit()
        again = tq.enqueue(s, meeting_id)
        assert again.id == first.id
    task = tq.claim_next(env["factory"])
    with env["factory"]() as s:
        assert tq.enqueue(s, meeting_id).id == task.id  # PROCESSING 도 활성
    tq.mark_done(env["factory"], task.id)
    with env["factory"]() as s:
        fresh = tq.enqueue(s, meeting_id)
        s.commit()
        assert fresh.id != task.id and fresh.status == "PENDING"
    assert len(tasks(env)) == 2


def make_pending_inline(env, mgr, n=1) -> list[int]:
    """큐에 넣지 않는(inline 모드) 업로드로 회의록만 만든다."""
    return [upload(env, mgr).json()["meetingId"] for _ in range(n)]


def test_enqueue_unknown_meeting_is_error(env):
    with env["factory"]() as s:
        with pytest.raises(ValueError):
            tq.enqueue(s, 9999)


def test_claim_is_fifo_and_sets_processing(env, mgr):
    ids = make_pending_inline(env, mgr, 3)
    with env["factory"]() as s:
        for meeting_id in ids:
            tq.enqueue(s, meeting_id)
        s.commit()
    claimed = [tq.claim_next(env["factory"]) for _ in range(4)]
    assert [c.meeting_id for c in claimed[:3]] == ids and claimed[3] is None
    rows = tasks(env)
    assert [r.status for r in rows] == ["PROCESSING"] * 3
    assert all(r.attempts == 1 and r.started_at is not None for r in rows)


def test_claim_is_atomic_across_threads(env, mgr):
    [meeting_id] = make_pending_inline(env, mgr)
    with env["factory"]() as s:
        tq.enqueue(s, meeting_id)
        s.commit()
    results: list[object] = []
    barrier = threading.Barrier(6)

    def go():
        barrier.wait()
        results.append(tq.claim_next(env["factory"]))

    threads = [threading.Thread(target=go) for _ in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sum(r is not None for r in results) == 1
    assert tasks(env)[0].attempts == 1


def test_progress_is_clamped_and_only_while_processing(env, mgr):
    [meeting_id] = make_pending_inline(env, mgr)
    with env["factory"]() as s:
        task = tq.enqueue(s, meeting_id)
        s.commit()
    tq.update_progress(env["factory"], task.id, 50)  # PENDING 이면 무시
    assert tasks(env)[0].progress == 0
    tq.claim_next(env["factory"])
    tq.update_progress(env["factory"], task.id, 50)
    assert tasks(env)[0].progress == 50
    tq.update_progress(env["factory"], task.id, 500)
    assert tasks(env)[0].progress == 100


def test_mark_failed_truncates_message(env, mgr):
    [meeting_id] = make_pending_inline(env, mgr)
    with env["factory"]() as s:
        task = tq.enqueue(s, meeting_id)
        s.commit()
    tq.mark_failed(env["factory"], task.id, "x" * 5000)
    row = tasks(env)[0]
    assert row.status == "FAILED" and len(row.error_msg) <= 300


def _age(env, task_id, seconds, attempts):
    with env["factory"]() as s:
        s.execute(
            update(TaskQueue).where(TaskQueue.id == task_id)
            .values(started_at=utcnow() - timedelta(seconds=seconds), attempts=attempts)
        )
        s.commit()


def test_recover_stale_requeues_below_limit_and_fails_at_limit(env, mgr, monkeypatch):
    monkeypatch.setattr(settings, "worker_stale_seconds", 60)
    monkeypatch.setattr(settings, "worker_max_attempts", 3)
    a, b, fresh = make_pending_inline(env, mgr, 3)
    with env["factory"]() as s:
        ids = [tq.enqueue(s, m).id for m in (a, b, fresh)]
        s.commit()
    for _ in range(3):
        tq.claim_next(env["factory"])
    _age(env, ids[0], 120, 2)  # 한도 미만 → PENDING
    _age(env, ids[1], 120, 3)  # 한도 이상 → FAILED
    _age(env, ids[2], 5, 1)  # 아직 멈춘 것으로 보지 않음
    result = tq.recover_stale(env["factory"])
    assert [t.id for t in result.requeued] == [ids[0]] and [t.id for t in result.failed] == [ids[1]]
    by_id = {r.id: r for r in tasks(env)}
    assert by_id[ids[0]].status == "PENDING"
    assert by_id[ids[1]].status == "FAILED" and by_id[ids[1]].error_msg == "워커 중단으로 실패"
    assert by_id[ids[2]].status == "PROCESSING"


def test_release_all_processing_returns_to_pending(env, mgr):
    ids = make_pending_inline(env, mgr, 2)
    with env["factory"]() as s:
        for m in ids:
            tq.enqueue(s, m)
        s.commit()
    tq.claim_next(env["factory"])
    assert tq.release_all_processing(env["factory"]) == 1
    assert [r.status for r in tasks(env)] == ["PENDING", "PENDING"]


# ---------------- queue 모드: 업로드 → 워커 ----------------
def test_queue_upload_only_enqueues_then_worker_completes(env, mgr, queue_mode):
    res = upload(env, mgr)
    assert res.status_code == 202 and set(res.json()) == {"meetingId", "jobId"}
    meeting_id, job_id = res.json()["meetingId"], res.json()["jobId"]
    assert env["queued"] == []  # BackgroundTasks 로 직접 실행하지 않는다
    [row] = tasks(env)
    assert row.status == "PENDING" and row.meeting_id == meeting_id
    with env["factory"]() as s:
        assert s.get(Job, job_id).status == "queued"  # 기존 "대기" 값 그대로
    assert meeting_status(env, meeting_id) == "processing"

    assert worker(env).run_once() is True
    assert worker(env).run_once() is False  # 더 없음
    [row] = tasks(env)
    assert row.status == "DONE" and row.progress == 100 and row.error_msg is None
    with env["factory"]() as s:
        assert s.get(Job, job_id).status == "completed"
        assert len(list(s.scalars(select(ActionItem).where(ActionItem.meeting_id == meeting_id)))) > 0
    assert meeting_status(env, meeting_id) == "confirmed"  # 관리자 등록 = 등록 시 확정(기존 규칙)


def test_queue_failure_marks_task_job_and_meeting(env, mgr, queue_mode):
    res = upload(env, mgr)
    assert worker(env, extractor_factory=BrokenExtractor).run_once() is True
    [row] = tasks(env)
    assert row.status == "FAILED" and row.error_msg.startswith("처리 실패(")
    assert meeting_status(env, res.json()["meetingId"]) == "failed"
    with env["factory"]() as s:
        assert s.get(Job, res.json()["jobId"]).status == "failed"


def test_queue_progress_is_reported_in_stage_order(env, mgr, queue_mode, monkeypatch):
    seen: list[int] = []
    monkeypatch.setattr(tq, "update_progress", lambda factory, task_id, value: seen.append(value))
    upload(env, mgr)
    worker(env).run_once()
    assert seen == sorted(seen) and seen[0] == 10 and seen[-1] == 85 and len(seen) >= 4


def test_queue_reprocess_enqueues_and_worker_recovers(env, mgr, queue_mode):
    res = upload(env, mgr)
    meeting_id = res.json()["meetingId"]
    worker(env, extractor_factory=BrokenExtractor).run_once()
    again = env["client"].post(f"/api/meetings/{meeting_id}/reprocess", headers=auth(mgr))
    assert again.status_code == 202 and set(again.json()) == {"meetingId", "jobId"}
    assert env["queued"] == []
    assert [r.status for r in tasks(env)] == ["FAILED", "PENDING"]
    assert meeting_status(env, meeting_id) == "processing"
    assert worker(env).run_once() is True
    assert [r.status for r in tasks(env)] == ["FAILED", "DONE"]
    assert meeting_status(env, meeting_id) == "confirmed"


def test_queue_txt_source_skips_stt(env, mgr, queue_mode):
    class NoStt:
        def __call__(self):
            raise AssertionError("STT 를 부르면 안 됩니다")

    res = post_txt(env, mgr, TIMED.encode())
    assert res.status_code == 202 and tasks(env)[0].status == "PENDING"
    assert worker(env, stt_factory=NoStt()).run_once() is True
    assert tasks(env)[0].status == "DONE"
    assert meeting_status(env, res.json()["meetingId"]) == "confirmed"


def test_two_workers_never_process_the_same_task(env, mgr, queue_mode):
    upload(env, mgr)
    calls: list[int] = []

    class CountingExtractor(FakeExtractor):
        def extract(self, *args, **kwargs):
            calls.append(1)
            return super().extract(*args, **kwargs)

    results: list[bool] = []
    barrier = threading.Barrier(2)

    def go():
        barrier.wait()
        results.append(worker(env, extractor_factory=CountingExtractor).run_once())

    threads = [threading.Thread(target=go) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sorted(results) == [False, True] and calls == [1]
    assert tasks(env)[0].attempts == 1


def test_worker_recovers_orphaned_running_job_after_crash(env, mgr, queue_mode, monkeypatch):
    monkeypatch.setattr(settings, "worker_stale_seconds", 60)
    res = upload(env, mgr)
    task = tq.claim_next(env["factory"])  # 워커가 가져간 뒤 죽은 상황: jobs 도 running 으로 남는다
    with env["factory"]() as s:
        s.execute(update(Job).where(Job.id == res.json()["jobId"]).values(status="running"))
        s.commit()
    _age(env, task.id, 600, 1)
    w = worker(env)
    w.recover()
    assert tasks(env)[0].status == "PENDING"
    assert w.run_once() is True
    assert tasks(env)[0].status == "DONE" and meeting_status(env, res.json()["meetingId"]) == "confirmed"


def test_worker_fails_meeting_when_attempts_exhausted(env, mgr, queue_mode, monkeypatch):
    monkeypatch.setattr(settings, "worker_stale_seconds", 60)
    monkeypatch.setattr(settings, "worker_max_attempts", 1)
    res = upload(env, mgr)
    task = tq.claim_next(env["factory"])
    with env["factory"]() as s:
        s.execute(update(Job).where(Job.id == res.json()["jobId"]).values(status="running"))
        s.commit()
    _age(env, task.id, 600, 1)
    worker(env).recover()
    assert tasks(env)[0].status == "FAILED"
    assert meeting_status(env, res.json()["meetingId"]) == "failed"


def test_graceful_stop_returns_processing_to_pending(env, mgr, queue_mode, monkeypatch):
    monkeypatch.setattr(Worker, "_install_signal_handlers", lambda self: None)  # 테스트 프로세스의 신호 처리기를 바꾸지 않는다
    upload(env, mgr)
    tq.claim_next(env["factory"])
    w = worker(env)
    w.request_stop()
    w.run_forever()  # 이미 종료 요청됨: 반복 없이 정리만 한다
    assert tasks(env)[0].status == "PENDING"


# ---------------- GPU 해제 훅 ----------------
def test_gpu_release_called_after_stt_and_before_extraction(env, mgr, monkeypatch):
    order: list[str] = []
    monkeypatch.setattr("app.services.processing.release_gpu_memory", lambda: order.append("release"))

    class Stt:
        def transcribe(self, path, mime):
            order.append("stt")
            from app.pipeline.fakes import fake_transcript
            return fake_transcript()

        def release(self):
            order.append("stt.release")

    class Extractor(FakeExtractor):
        def extract(self, *args, **kwargs):
            order.append("extract")
            return super().extract(*args, **kwargs)

    from app.services.processing import process_meeting

    res = upload(env, mgr)
    assert process_meeting(res.json()["jobId"], session_factory=env["factory"], stt_factory=Stt, extractor_factory=Extractor) == "completed"
    assert order == ["stt", "stt.release", "release", "extract"]


def test_gpu_release_is_safe_without_torch(monkeypatch):
    monkeypatch.setitem(sys.modules, "torch", None)  # import 하면 ImportError
    assert gpu.release_gpu_memory() is False


def test_gpu_release_empties_cuda_cache_when_available(monkeypatch):
    calls: list[str] = []
    fake = types.SimpleNamespace(cuda=types.SimpleNamespace(is_available=lambda: True, empty_cache=lambda: calls.append("empty")))
    monkeypatch.setitem(sys.modules, "torch", fake)
    assert gpu.release_gpu_memory() is True and calls == ["empty"]


def test_gpu_release_swallows_errors(monkeypatch):
    def boom():
        raise RuntimeError("cuda error")

    fake = types.SimpleNamespace(cuda=types.SimpleNamespace(is_available=lambda: True, empty_cache=boom))
    monkeypatch.setitem(sys.modules, "torch", fake)
    assert gpu.release_gpu_memory() is False


# ---------------- inline 회귀·시스템 API ----------------
def test_inline_mode_does_not_touch_queue(env, mgr):
    assert settings.processing_mode == "inline"
    res = upload(env, mgr)
    assert env["queued"] == [res.json()["jobId"]]  # 기존처럼 BackgroundTasks 로 실행기 호출
    assert tasks(env) == []


def test_engine_endpoint_exposes_queue_fields(env, mgr, queue_mode):
    upload(env, mgr)
    upload(env, mgr)
    tq.claim_next(env["factory"])
    body = env["client"].get("/api/system/engine", headers=auth(mgr)).json()
    assert body == {"engine": "fake", "isFake": True, "processingMode": "queue", "queuePending": 1, "queueProcessing": 1,
                    "llmProvider": "fake", "llmModel": "", "engineTestMode": False, "engineTestDefault": "default"}


def test_settings_defaults():
    from app.config import Settings

    s = Settings(_env_file=None)
    assert s.processing_mode == "inline" and s.worker_poll_seconds == 1.5
    assert s.worker_stale_seconds == 1800 and s.worker_max_attempts == 3 and s.worker_shutdown_grace == 30


# ---------------- 가드·마이그레이션 ----------------
def test_task_queue_table_is_only_used_by_its_service():
    allowed = {"models/task_queue.py", "models/__init__.py", "services/task_queue.py"}
    raw_sql = re.compile(r"(?i)(from|into|update|join)\s+task_queue")
    offenders = []
    for path in (_ROOT / "app").rglob("*.py"):
        rel = path.relative_to(_ROOT / "app").as_posix()
        text = path.read_text(encoding="utf-8")
        if rel not in allowed and (re.search(r"TaskQueue", text) or raw_sql.search(text)):
            offenders.append(rel)
    assert offenders == [], offenders


def test_migration_roundtrip_and_downgrade_guard(tmp_path):
    url = f"sqlite:///{(tmp_path / 'm.db').as_posix()}"
    cfg = _alembic(url)
    command.upgrade(cfg, "head")
    command.downgrade(cfg, _BEFORE)
    command.upgrade(cfg, "head")
    command.check(cfg)
    raw = sqlite3.connect(tmp_path / "m.db")
    raw.execute(
        "INSERT INTO task_queue (tenant_id, meeting_id, status, progress, attempts, created_at, updated_at) "
        "VALUES (1, 1, 'PENDING', 0, 0, '2026-10-01', '2026-10-01')"
    )
    raw.commit()
    raw.close()
    with pytest.raises(RuntimeError, match="downgrade 거부"):
        command.downgrade(cfg, _BEFORE)
    check = sqlite3.connect(tmp_path / "m.db")
    try:
        assert check.execute("SELECT COUNT(*) FROM task_queue").fetchone()[0] == 1
        with pytest.raises(sqlite3.IntegrityError):  # status CHECK
            check.execute("INSERT INTO task_queue (tenant_id, meeting_id, status, created_at, updated_at) VALUES (1, 1, 'x', '2026-10-01', '2026-10-01')")
        with pytest.raises(sqlite3.IntegrityError):  # progress CHECK
            check.execute(
                "INSERT INTO task_queue (tenant_id, meeting_id, status, progress, created_at, updated_at) "
                "VALUES (1, 1, 'PENDING', 101, '2026-10-01', '2026-10-01')"
            )
    finally:
        check.close()


def test_alembic_offline_postgresql_ddl_has_task_queue():
    cfg = _alembic("postgresql+psycopg://u:p@localhost/db")
    buffer = io.StringIO()
    cfg.output_buffer = buffer
    command.upgrade(cfg, "head", sql=True)
    sql = buffer.getvalue()
    assert "CREATE TABLE task_queue" in sql
    assert "ck_task_queue_status_valid" in sql and "ck_task_queue_progress_range" in sql and "ix_task_queue_status_created" in sql
