"""시스템 모니터링(작업 A-1, 읽기 전용): 디스크 여유 공간·GPU 상태·워커(생존 신호·자동 재기동 기록)·큐 현황.
권한: 관리자(manager) 이상 또는 시스템 관리자. 그 밖에는 403. 어떤 상태도 바꾸지 않고, 점검에 실패한 항목은 예외 없이 "확인 불가"다.
경로·주소·키는 내려주지 않는다(표시 이름만)."""
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.schemas import CamelModel
from app.auth.deps import get_current_account, has_rank
from app.config import settings
from app.db import get_session
from app.models import Account
from app.models.common import utcnow
from app.models.task_queue import TaskQueue
from app.services import system_health as health
from app.services.system_admin import is_system_admin
from app.services.task_queue import queue_counts, worker_alive

router = APIRouter(prefix="/api/admin/monitoring", tags=["monitoring"])


class DiskOut(CamelModel):
    name: str
    status: str  # ok | warning | critical | unknown
    free_mb: int | None
    total_mb: int | None
    message: str


class GpuOut(CamelModel):
    name: str
    total_mb: int
    used_mb: int
    free_mb: int
    used_percent: int


class GpuStateOut(CamelModel):
    status: str  # ok | unknown
    label: str  # 정상 | 확인 불가
    gpus: list[GpuOut]
    message: str


class SupervisorOut(CamelModel):
    """워커 자동 재기동 기록. 감독 프로세스를 쓰지 않으면 used=false."""

    used: bool
    status: str | None
    restart_count: int
    recent_restarts: int
    max_restarts: int
    last_exit_code: int | None
    last_exit_at: str | None
    last_restart_at: str | None
    limit_exceeded_at: str | None


class WorkerOut(CamelModel):
    processing_mode: str
    alive: bool | None  # queue 모드가 아니면 None(워커를 쓰지 않음)
    supervisor: SupervisorOut


class QueueOut(CamelModel):
    pending: int
    processing: int
    oldest_pending_at: datetime | None


class DiskGroupOut(CamelModel):
    warn_free_mb: int
    min_free_mb: int
    items: list[DiskOut]


class MonitoringOut(CamelModel):
    generated_at: datetime
    overall: str  # ok | warning | critical
    disk: DiskGroupOut
    gpu: GpuStateOut
    worker: WorkerOut
    queue: QueueOut


def _supervisor_out(state: dict | None) -> SupervisorOut:
    if state is None:
        return SupervisorOut(
            used=False, status=None, restart_count=0, recent_restarts=0, max_restarts=settings.worker_restart_max,
            last_exit_code=None, last_exit_at=None, last_restart_at=None, limit_exceeded_at=None,
        )

    def _int(key: str, default: int = 0) -> int:
        value = state.get(key)
        return value if isinstance(value, int) else default

    def _text(key: str) -> str | None:
        value = state.get(key)
        return value if isinstance(value, str) else None

    code = state.get("lastExitCode")
    return SupervisorOut(
        used=True, status=_text("status"), restart_count=_int("restartCount"), recent_restarts=_int("recentRestarts"),
        max_restarts=_int("maxRestarts", settings.worker_restart_max), last_exit_code=code if isinstance(code, int) else None,
        last_exit_at=_text("lastExitAt"), last_restart_at=_text("lastRestartAt"), limit_exceeded_at=_text("limitExceededAt"),
    )


@router.get("", response_model=MonitoringOut, response_model_by_alias=True)
def get_monitoring(account: Account = Depends(get_current_account), session: Session = Depends(get_session)) -> MonitoringOut:
    if not (has_rank(account, "manager") or is_system_admin(session, account.id)):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="권한이 없습니다")
    disks = health.disk_status()
    gpu = health.gpu_status()
    pending, processing = queue_counts(session)
    oldest = session.scalar(select(func.min(TaskQueue.created_at)).where(TaskQueue.status == "PENDING"))
    queue_mode = settings.processing_mode == "queue"
    alive = worker_alive(session) if queue_mode else None
    supervisor = _supervisor_out(health.supervisor_state())

    overall = health.OK
    for item in disks:
        if item.status in (health.WARNING, health.CRITICAL):
            overall = health.worse(overall, item.status)
    if supervisor.status == "limit_exceeded":
        overall = health.worse(overall, health.CRITICAL)  # 자동 재기동 한도를 넘겨 워커가 멈춘 상태
    elif queue_mode and alive is False and pending > 0:
        overall = health.worse(overall, health.WARNING)  # 대기 작업이 있는데 워커 응답이 없다
    # GPU 는 없는 장비(외부 AI 사용)가 정상일 수 있어 확인 불가를 전체 상태에 반영하지 않는다

    return MonitoringOut(
        generated_at=utcnow(), overall=overall,
        disk=DiskGroupOut(
            warn_free_mb=settings.disk_warn_free_mb, min_free_mb=settings.disk_min_free_mb,
            items=[DiskOut(name=i.name, status=i.status, free_mb=i.free_mb, total_mb=i.total_mb, message=i.message) for i in disks],
        ),
        gpu=GpuStateOut(
            status=gpu.status, label=gpu.label, message=gpu.message,
            gpus=[GpuOut(name=g.name, total_mb=g.total_mb, used_mb=g.used_mb, free_mb=g.free_mb, used_percent=g.used_percent) for g in gpu.gpus],
        ),
        worker=WorkerOut(processing_mode=settings.processing_mode, alive=alive, supervisor=supervisor),
        queue=QueueOut(pending=pending, processing=processing, oldest_pending_at=oldest),
    )
