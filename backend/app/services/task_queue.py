"""처리 작업 큐(task_queue) 서비스(작업 75-1). 이 표는 이 모듈에서만 다룬다(가드 테스트).

- 획득(claim_next)은 방언에 의존하지 않는 표준 UPDATE("id 가 같고 status='PENDING'")의 영향 행 수가 1 일 때만 성공으로 본다.
  워커가 여럿이어도 같은 작업을 둘이 가져가지 못한다.
- 회의록당 활성(PENDING·PROCESSING) 작업은 하나: enqueue 가 활성 작업이 있으면 새로 만들지 않고 기존 것을 돌려준다(서비스 계층 보장).
- 오류 문구는 짧은 요약만 저장한다(전사 본문·개인 정보 금지).
"""
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session, sessionmaker

from app.config import settings
from app.models import Meeting, TaskQueue
from app.models.common import utcnow

ACTIVE_STATUSES = ("PENDING", "PROCESSING")
STALE_FAILURE_MESSAGE = "워커 중단으로 실패"
_ERROR_MAX = 300  # error_msg 길이 상한(짧은 요약만)
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
    """가장 오래된 PENDING 을 원자적으로 PROCESSING 으로 바꿔 가져온다. 없으면 None."""
    with factory() as session:
        candidates = session.scalars(
            select(TaskQueue.id).where(TaskQueue.status == "PENDING").order_by(TaskQueue.created_at, TaskQueue.id).limit(_CANDIDATES)
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
