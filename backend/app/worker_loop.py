"""단독 worker 프로세스 본체(작업 75-1). task_queue 의 PENDING 작업을 폴링해 한 번에 하나씩 직렬 처리한다.

- 처리 본체는 기존 파이프라인(services/processing.process_meeting)을 그대로 부른다. 상태 전이·사건·실패 코드 규칙은 같다.
- 저장 경로는 DB(원천 문서)에 기록된 값을 그대로 쓴다(저장 파일명 형식에 의존하지 않는다).
- STT 가 끝나면 파이프라인이 GPU 메모리를 해제한 뒤 추출을 시작한다(pipeline/gpu.py).
- 종료 신호(SIGINT·SIGTERM)를 받으면 현재 작업이 끝나기를 WORKER_SHUTDOWN_GRACE 초까지 기다리고, 넘으면 PROCESSING 을 PENDING 으로 되돌리고 종료한다.
- 로그에는 작업 id·회의록 id·분류 코드만 남긴다(전사 본문·파일 경로 금지).
"""
import logging
import signal
import threading
import time
from collections.abc import Callable

from sqlalchemy import select, text, update
from sqlalchemy.orm import sessionmaker

from app.config import settings
from app.db import SessionLocal
from app.models import Job
from app.pipeline.errors import error_code
from app.pipeline.extractor import Extractor
from app.pipeline.stt import SttEngine
from app.services import task_queue
from app.services.processing import mark_job_failed, process_meeting

log = logging.getLogger("app.worker")

_SUCCESS = ("completed", "no_content")


class Worker:
    def __init__(
        self,
        factory: sessionmaker | None = None,
        *,
        stt_factory: Callable[[], SttEngine] | None = None,
        extractor_factory: Callable[[], Extractor] | None = None,
    ) -> None:
        self.factory = factory or SessionLocal
        self.stt_factory = stt_factory
        self.extractor_factory = extractor_factory
        self._stop = threading.Event()

    # ---- 시작 점검·복구 ----
    def startup(self) -> None:
        """WAL 설정 확인 로그 + 멈춘 작업 복구."""
        with self.factory() as session:
            if session.get_bind().dialect.name == "sqlite":
                mode = session.execute(text("PRAGMA journal_mode")).scalar()
                log.info("SQLite journal_mode=%s (WAL 이 아니면 서버와 워커가 같은 파일을 쓸 때 잠금이 잦아집니다)", mode)
        self.recover()

    def recover(self) -> None:
        result = task_queue.recover_stale(self.factory)
        for task in result.failed:  # 한도를 넘긴 작업: 회의록도 기존 규칙대로 실패로
            self._fail_meeting_job(task.meeting_id, "worker_stopped")
        if result.requeued or result.failed:
            log.warning("멈춘 작업 복구: 재대기 %d건, 실패 %d건", len(result.requeued), len(result.failed))

    def _fail_meeting_job(self, meeting_id: int, code: str) -> None:
        job_id = self._active_job_id(meeting_id)
        if job_id is not None:
            mark_job_failed(self.factory, job_id, code)

    # ---- 작업 1건 ----
    def _active_job_id(self, meeting_id: int) -> int | None:
        """회의록의 가장 최근 대기·실행 중 jobs 행. 워커가 죽어 running 으로 남은 행은 queued 로 되돌려 다시 처리할 수 있게 한다."""
        with self.factory() as session:
            job = session.scalar(
                select(Job).where(Job.meeting_id == meeting_id, Job.status.in_(("queued", "running"))).order_by(Job.id.desc()).limit(1)
            )
            if job is None:
                return None
            if job.status == "running":
                session.execute(update(Job).where(Job.id == job.id, Job.status == "running").values(status="queued"))
                session.commit()
            return job.id

    def _process(self, task: task_queue.QueuedTask) -> str:
        job_id = self._active_job_id(task.meeting_id)
        if job_id is None:
            task_queue.mark_failed(self.factory, task.id, "처리할 작업이 없습니다")
            return "failed"
        try:
            outcome = process_meeting(
                job_id,
                session_factory=self.factory,
                stt_factory=self.stt_factory,
                extractor_factory=self.extractor_factory,
                on_progress=lambda value: task_queue.update_progress(self.factory, task.id, value),
            )
        except Exception as exc:  # noqa: BLE001 — 파이프라인 밖(DB 등) 예외도 작업 실패로 기록하고 워커는 계속 돈다
            code = error_code(exc)
            log.warning("task %s failed outside pipeline: %s (%s)", task.id, code, type(exc).__name__)
            try:
                mark_job_failed(self.factory, job_id, code)
            except Exception as inner:  # noqa: BLE001
                log.warning("task %s job failure record failed: %s", task.id, type(inner).__name__)
            task_queue.mark_failed(self.factory, task.id, f"처리 실패({code})")
            return "failed"
        if outcome in _SUCCESS:
            task_queue.mark_done(self.factory, task.id)
            return outcome
        if outcome == "failed":
            task_queue.mark_failed(self.factory, task.id, f"처리 실패({self._job_error_code(job_id)})")
            return "failed"
        task_queue.mark_failed(self.factory, task.id, "작업이 대기 상태가 아닙니다")
        return "failed"

    def _job_error_code(self, job_id: int) -> str:
        with self.factory() as session:
            return session.scalar(select(Job.error_code).where(Job.id == job_id)) or "internal_error"

    def run_once(self) -> bool:
        """대기 작업 하나를 처리하고 돌아온다. 처리한 작업이 없으면 False."""
        task = task_queue.claim_next(self.factory)
        if task is None:
            return False
        log.info("task %s 시작 (meeting %s, 시도 %s)", task.id, task.meeting_id, task.attempts)
        result = self._process(task)
        log.info("task %s 종료: %s", task.id, result)
        return True

    # ---- 반복 실행 ----
    def request_stop(self) -> None:
        self._stop.set()

    def run_forever(self) -> None:
        self.startup()
        self._install_signal_handlers()
        log.info("워커 시작(폴링 %.1f초)", settings.worker_poll_seconds)
        try:
            while not self._stop.is_set():
                if not self._run_once_guarded():
                    self._stop.wait(settings.worker_poll_seconds)
        except KeyboardInterrupt:  # 신호 처리기를 못 단 환경 대비
            self.request_stop()
        finally:
            released = task_queue.release_all_processing(self.factory)
            if released:
                log.warning("종료 시 처리 중이던 작업 %d건을 대기로 되돌렸습니다.", released)
            log.info("워커 종료")

    def _run_once_guarded(self) -> bool:
        """작업을 별도 스레드에서 돌리며, 종료 신호 뒤 유예 시간을 넘으면 기다리지 않고 돌아온다(남은 작업은 finally 에서 PENDING 으로)."""
        box: dict[str, object] = {}

        def target() -> None:
            try:
                box["processed"] = self.run_once()
            except BaseException as exc:  # noqa: BLE001
                box["error"] = exc

        thread = threading.Thread(target=target, name="worker-task", daemon=True)
        thread.start()
        deadline: float | None = None
        while thread.is_alive():
            thread.join(0.2)
            if self._stop.is_set():
                deadline = deadline if deadline is not None else time.monotonic() + settings.worker_shutdown_grace
                if time.monotonic() >= deadline:
                    log.warning("종료 유예 시간(%d초)을 넘겨 현재 작업을 두고 종료합니다.", settings.worker_shutdown_grace)
                    return False
        if "error" in box:
            log.warning("워커 반복 중 오류(계속 진행): %s", type(box["error"]).__name__)
            self._stop.wait(settings.worker_poll_seconds)
            return True
        return bool(box.get("processed"))

    def _install_signal_handlers(self) -> None:
        def handler(_signum, _frame) -> None:
            self.request_stop()

        for name in ("SIGINT", "SIGTERM"):
            sig = getattr(signal, name, None)
            if sig is None:
                continue
            try:
                signal.signal(sig, handler)
            except (ValueError, OSError):  # 메인 스레드가 아니거나 지원하지 않는 신호
                log.debug("신호 처리기를 달지 못했습니다: %s", name)


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    Worker().run_forever()
    return 0
