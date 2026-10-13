"""처리 작업 큐(task_queue) 서비스(작업 75-1). 이 표는 이 모듈에서만 다룬다(가드 테스트).

- 획득(claim_next)은 방언에 의존하지 않는 표준 UPDATE("id 가 같고 status='PENDING'")의 영향 행 수가 1 일 때만 성공으로 본다.
  워커가 여럿이어도 같은 작업을 둘이 가져가지 못한다.
- 회의록당 활성(PENDING·PROCESSING) 작업은 하나: enqueue 가 활성 작업이 있으면 새로 만들지 않고 기존 것을 돌려준다(서비스 계층 보장).
- 오류 문구는 짧은 요약만 저장한다(전사 본문·개인 정보 금지).
- 작업 선택 순서(작업 88-1): priority 내림차순, 같으면 접수 시각(created_at)·id 오름차순. 즉시 변환(promote_immediate)은
  대기(PENDING) 작업의 priority 만 올린다. 진행 중 작업은 건드리지 않고 동시에 돌리지도 않는다(claim 은 워커가 직렬로 하나씩).
- 워커 생존 신호(worker_heartbeats): beat() 로 갱신하고 worker_alive() 가 마지막 신호의 신선도를 판정한다.
"""
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import and_, func, or_, select, update
from sqlalchemy.orm import Session, sessionmaker

from app.config import settings
from app.models import Account, Job, Meeting, TaskQueue, WorkerHeartbeat
from app.models.common import utcnow
from app.services.history import KIND_MEETING_PROCESS_NOW, record_change

ACTIVE_STATUSES = ("PENDING", "PROCESSING")
STALE_FAILURE_MESSAGE = "워커 중단으로 실패"
_ERROR_MAX = 300  # error_msg 길이 상한(짧은 요약만)
WAITING_MESSAGE = "시스템의 선행 작업완료 후 처리하겠습니다."  # 선행 작업 때문에 바로 시작하지 못할 때 사용자에게 보이는 문구(정책 고정)
START_SOON_MESSAGE = "곧 변환을 시작합니다."
PROCESS_NOW_REASON = "즉시 변환 요청"
_CANDIDATES = 20  # 한 번에 훑을 대기 작업 수(앞선 후보를 다른 워커가 가져가면 다음 후보로)


@dataclass(frozen=True)
class QueuedTask:
    id: int
    tenant_id: int
    meeting_id: int
    attempts: int


@dataclass(frozen=True)
class RecoverResult:
    requeued: list[QueuedTask]
    failed: list[QueuedTask]


def _task(row: TaskQueue) -> QueuedTask:
    return QueuedTask(id=row.id, tenant_id=row.tenant_id, meeting_id=row.meeting_id, attempts=row.attempts)


def enqueue(session: Session, meeting_id: int) -> TaskQueue:
    """회의록의 처리 작업을 대기열에 넣는다(커밋은 호출부). 활성 작업이 있으면 새로 만들지 않고 기존 것을 돌려준다."""
    existing = session.scalar(
        select(TaskQueue).where(TaskQueue.meeting_id == meeting_id, TaskQueue.status.in_(ACTIVE_STATUSES)).order_by(TaskQueue.id).limit(1)
    )
    if existing is not None:
        return existing
    meeting = session.get(Meeting, meeting_id)
    if meeting is None:
        raise ValueError("회의록이 없습니다.")
    task = TaskQueue(tenant_id=meeting.tenant_id, meeting_id=meeting_id, status="PENDING")
    session.add(task)
    session.flush()
    return task


def claim_next(factory: sessionmaker) -> QueuedTask | None:
    """우선순위가 가장 높은(같으면 가장 오래된) PENDING 을 원자적으로 PROCESSING 으로 바꿔 가져온다. 없으면 None."""
    with factory() as session:
        candidates = session.scalars(
            select(TaskQueue.id)
            .where(TaskQueue.status == "PENDING")
            .order_by(TaskQueue.priority.desc(), TaskQueue.created_at, TaskQueue.id)
            .limit(_CANDIDATES)
        ).all()
        for task_id in candidates:
            now = utcnow()
            changed = session.execute(
                update(TaskQueue)
                .where(TaskQueue.id == task_id, TaskQueue.status == "PENDING")
                .values(status="PROCESSING", started_at=now, updated_at=now, progress=0, attempts=TaskQueue.attempts + 1)
            ).rowcount
            if changed == 1:
                session.commit()
                return _task(session.get(TaskQueue, task_id))
            session.rollback()  # 다른 워커가 먼저 가져갔다: 다음 후보
        return None


def update_progress(factory: sessionmaker, task_id: int, progress: int) -> None:
    """처리 중인 작업의 진행률(0~100)을 갱신한다. 처리 중이 아니면 아무것도 하지 않는다."""
    value = max(0, min(100, int(progress)))
    with factory() as session:
        session.execute(
            update(TaskQueue).where(TaskQueue.id == task_id, TaskQueue.status == "PROCESSING").values(progress=value, updated_at=utcnow())
        )
        session.commit()


def mark_done(factory: sessionmaker, task_id: int) -> None:
    with factory() as session:
        session.execute(
            update(TaskQueue).where(TaskQueue.id == task_id, TaskQueue.status == "PROCESSING")
            .values(status="DONE", progress=100, error_msg=None, updated_at=utcnow())
        )
        session.commit()


def mark_failed(factory: sessionmaker, task_id: int, message: str) -> None:
    """실패로 기록한다. message 는 짧은 요약만(상한을 넘으면 자른다)."""
    with factory() as session:
        session.execute(
            update(TaskQueue).where(TaskQueue.id == task_id, TaskQueue.status.in_(ACTIVE_STATUSES))
            .values(status="FAILED", error_msg=(message or "처리 실패")[:_ERROR_MAX], updated_at=utcnow())
        )
        session.commit()


def recover_stale(factory: sessionmaker, now: datetime | None = None) -> RecoverResult:
    """started_at 이 WORKER_STALE_SECONDS 지난 PROCESSING 작업을 복구한다.
    시도 횟수가 WORKER_MAX_ATTEMPTS 미만이면 PENDING 으로, 이상이면 FAILED("워커 중단으로 실패")로 바꾼다."""
    now = now or utcnow()
    limit = now - timedelta(seconds=settings.worker_stale_seconds)
    requeued: list[QueuedTask] = []
    failed: list[QueuedTask] = []
    with factory() as session:
        rows = session.scalars(
            select(TaskQueue).where(TaskQueue.status == "PROCESSING", TaskQueue.started_at.is_not(None), TaskQueue.started_at <= limit)
            .order_by(TaskQueue.id)
        ).all()
        for row in rows:
            if row.attempts < settings.worker_max_attempts:
                changed = session.execute(
                    update(TaskQueue).where(TaskQueue.id == row.id, TaskQueue.status == "PROCESSING")
                    .values(status="PENDING", progress=0, updated_at=now)
                ).rowcount
                if changed == 1:
                    requeued.append(_task(row))
            else:
                changed = session.execute(
                    update(TaskQueue).where(TaskQueue.id == row.id, TaskQueue.status == "PROCESSING")
                    .values(status="FAILED", error_msg=STALE_FAILURE_MESSAGE, updated_at=now)
                ).rowcount
                if changed == 1:
                    failed.append(_task(row))
        session.commit()
    return RecoverResult(requeued=requeued, failed=failed)


def release_all_processing(factory: sessionmaker) -> int:
    """정상 종료 때 PROCESSING 을 PENDING 으로 되돌린다(다음 워커가 이어서 처리). 되돌린 건수."""
    with factory() as session:
        changed = session.execute(
            update(TaskQueue).where(TaskQueue.status == "PROCESSING").values(status="PENDING", progress=0, updated_at=utcnow())
        ).rowcount
        session.commit()
        return changed


def queue_counts(session: Session) -> tuple[int, int]:
    """(대기 건수, 처리 중 건수)."""
    rows = dict(session.execute(select(TaskQueue.status, func.count()).group_by(TaskQueue.status)).all())
    return int(rows.get("PENDING", 0)), int(rows.get("PROCESSING", 0))


# ---------------- 즉시 변환·대기 순번(작업 88-1) ----------------
@dataclass(frozen=True)
class QueueInfo:
    """회의록의 활성 작업 1건의 대기열 상태. ahead 는 이 작업보다 먼저 시작할 대기(PENDING) 작업 수."""

    task_id: int
    status: str  # PENDING | PROCESSING
    prioritized: bool
    ahead: int
    waiting_for_prior: bool

    @property
    def message(self) -> str:
        return WAITING_MESSAGE if self.waiting_for_prior else START_SOON_MESSAGE


class QueueConflict(Exception):
    """즉시 변환을 할 수 없는 상태(진행·완료·실패 등). message 는 사용자에게 그대로 보이는 한국어 사유."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


@dataclass(frozen=True)
class PromoteResult:
    info: QueueInfo
    job_id: int | None
    already: bool  # 이미 우선 처리로 올려져 있던 건(이력을 남기지 않았다)


def _is_prioritized(priority: int) -> bool:
    return priority >= settings.queue_priority_immediate


def _processing_exists(session: Session) -> bool:
    return session.scalar(select(TaskQueue.id).where(TaskQueue.status == "PROCESSING").limit(1)) is not None


def _ahead_count(session: Session, row: TaskQueue) -> int:
    """row(PENDING)보다 먼저 시작할 PENDING 작업 수: 우선순위가 높거나, 같으면 먼저 접수된 것."""
    return int(
        session.scalar(
            select(func.count()).select_from(TaskQueue).where(
                TaskQueue.status == "PENDING",
                or_(
                    TaskQueue.priority > row.priority,
                    and_(
                        TaskQueue.priority == row.priority,
                        or_(
                            TaskQueue.created_at < row.created_at,
                            and_(TaskQueue.created_at == row.created_at, TaskQueue.id < row.id),
                        ),
                    ),
                ),
            )
        )
        or 0
    )


def _active_task(session: Session, meeting_id: int) -> TaskQueue | None:
    return session.scalar(
        select(TaskQueue).where(TaskQueue.meeting_id == meeting_id, TaskQueue.status.in_(ACTIVE_STATUSES)).order_by(TaskQueue.id.desc()).limit(1)
    )


def _active_job_id(session: Session, meeting_id: int) -> int | None:
    return session.scalar(
        select(Job.id).where(Job.meeting_id == meeting_id, Job.status.in_(("queued", "running"))).order_by(Job.id.desc()).limit(1)
    )


def _info(session: Session, row: TaskQueue) -> QueueInfo:
    if row.status == "PROCESSING":
        return QueueInfo(task_id=row.id, status=row.status, prioritized=_is_prioritized(row.priority), ahead=0, waiting_for_prior=False)
    ahead = _ahead_count(session, row)
    return QueueInfo(
        task_id=row.id, status=row.status, prioritized=_is_prioritized(row.priority), ahead=ahead,
        waiting_for_prior=ahead > 0 or _processing_exists(session),
    )


def queue_info(session: Session, meeting_id: int) -> QueueInfo | None:
    """회의록의 활성(대기·처리 중) 작업의 대기열 상태. 활성 작업이 없으면 None."""
    row = _active_task(session, meeting_id)
    return _info(session, row) if row is not None else None


def can_promote(session: Session, meeting_id: int) -> bool:
    """즉시 변환으로 올릴 수 있는 대기 작업이 있는가(아직 우선 처리가 아닌 PENDING). 허용 동작 목록용."""
    row = _active_task(session, meeting_id)
    return row is not None and row.status == "PENDING" and not _is_prioritized(row.priority)


def promote_immediate(session: Session, meeting: Meeting, actor: Account, now: datetime | None = None) -> PromoteResult:
    """대기 작업을 대기열 맨 앞(우선순위 상향)으로 올린다. 커밋은 호출부. 권한·회의록 열람은 호출부가 먼저 판정한다.
    - 대기 상태만 승격한다. 이미 올려진 건은 성공(멱등, 이력 중복 없음). 진행 중·완료·실패는 QueueConflict(사유 문구).
    - 승격은 조건부 UPDATE(status='PENDING')로 하고, 그 사이 워커가 가져갔으면 진행 중 사유로 거절한다.
    - 올린 사람·시각·사유는 공통 변경 이력에 한 건 남긴다."""
    now = now or utcnow()
    row = _active_task(session, meeting.id)
    if row is None:
        job = session.scalar(select(Job).where(Job.meeting_id == meeting.id).order_by(Job.id.desc()).limit(1))
        if job is not None and job.status in ("completed", "no_content"):
            raise QueueConflict("이미 변환이 완료된 회의록입니다.")
        if job is not None and job.status == "failed":
            raise QueueConflict("변환에 실패한 회의록입니다. 다시 처리해 주세요.")
        raise QueueConflict("변환할 대기 작업이 없습니다.")
    if row.status == "PROCESSING":
        raise QueueConflict("이미 변환이 진행 중입니다. 완료될 때까지 기다려 주세요.")
    job_id = _active_job_id(session, meeting.id)
    if _is_prioritized(row.priority):
        return PromoteResult(info=_info(session, row), job_id=job_id, already=True)
    before = row.priority
    changed = session.execute(
        update(TaskQueue)
        .where(TaskQueue.id == row.id, TaskQueue.status == "PENDING", TaskQueue.priority < settings.queue_priority_immediate)
        .values(priority=settings.queue_priority_immediate, promoted_at=now, updated_at=now)
    ).rowcount
    session.expire(row)
    if changed != 1:
        fresh = session.get(TaskQueue, row.id)
        if fresh.status == "PENDING":  # 그 사이 다른 요청이 먼저 올렸다: 멱등 성공
            return PromoteResult(info=_info(session, fresh), job_id=job_id, already=True)
        raise QueueConflict("이미 변환이 진행 중입니다. 완료될 때까지 기다려 주세요.")
    record_change(
        session, tenant_id=meeting.tenant_id, target_type="meeting", target_id=meeting.id, kind=KIND_MEETING_PROCESS_NOW, actor_id=actor.id,
        before={"priority": before}, after={"priority": settings.queue_priority_immediate},
        extra={"reason": PROCESS_NOW_REASON, "taskId": row.id, "jobId": job_id},
    )
    return PromoteResult(info=_info(session, session.get(TaskQueue, row.id)), job_id=job_id, already=False)


# ---------------- 워커 생존 신호(작업 88-1) ----------------
def beat(factory: sessionmaker, worker_id: str, now: datetime | None = None) -> None:
    """워커 생존 신호를 기록한다(워커 1개당 1행 갱신). 실패해도 워커를 멈추지 않도록 호출부가 예외를 삼킨다."""
    now = now or utcnow()
    with factory() as session:
        changed = session.execute(update(WorkerHeartbeat).where(WorkerHeartbeat.worker_id == worker_id).values(beat_at=now)).rowcount
        if changed == 0:
            session.add(WorkerHeartbeat(worker_id=worker_id, beat_at=now))
        try:
            session.commit()
        except Exception:  # noqa: BLE001 — 동시에 처음 기록한 다른 프로세스와 겹침: 갱신으로 다시 시도
            session.rollback()
            session.execute(update(WorkerHeartbeat).where(WorkerHeartbeat.worker_id == worker_id).values(beat_at=now))
            session.commit()


def worker_alive(session: Session, now: datetime | None = None) -> bool:
    """가장 최근 생존 신호가 WORKER_HEARTBEAT_STALE_SEC 이내인가. 신호가 한 번도 없으면 False."""
    now = now or utcnow()
    last = session.scalar(select(func.max(WorkerHeartbeat.beat_at)))
    if last is None:
        return False
    return (now - last) <= timedelta(seconds=settings.worker_heartbeat_stale_sec)


@dataclass(frozen=True)
class QueueFields:
    """상태 응답(회의록 상세 processing·내 처리 현황)에 덧붙이는 대기열 칸. queue 모드의 대기·처리 중 작업이 아니면 모두 기본값(None·False)."""

    queue_ahead: int | None = None
    prioritized: bool = False
    waiting_for_prior: bool = False
    worker_alive: bool | None = None


def queue_fields(session: Session, meeting_id: int, job_status: str) -> QueueFields:
    if settings.processing_mode != "queue" or job_status not in ("queued", "running"):
        return QueueFields()
    info = queue_info(session, meeting_id)
    alive = worker_alive(session)
    if info is None:
        return QueueFields(worker_alive=alive)
    return QueueFields(queue_ahead=info.ahead, prioritized=info.prioritized, waiting_for_prior=info.waiting_for_prior, worker_alive=alive)
