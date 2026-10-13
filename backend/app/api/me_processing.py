"""GET /api/me/processing: 내가 올린 회의록의 처리 현황(로그인 필요, 읽기 전용, 열람 기록 없음). 규칙은 app/services/processing_status.py."""
from datetime import datetime

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.schemas import CamelModel
from app.auth.deps import get_current_account
from app.services.system_admin import is_system_admin
from app.db import get_session
from app.models import Account
from app.services.processing_status import my_processing

router = APIRouter(prefix="/api/me", tags=["me"])


class ProcessingItemOut(CamelModel):
    meeting_id: int
    job_id: int
    title: str
    status: str  # queued | running | completed | failed | no_content
    error_code: str | None
    elapsed_sec: int
    finished_at: datetime | None
    can_reprocess: bool
    # 즉시 변환 허용(추가 필드, 작업 88-3): 허용 동작 process_now 판정 그대로(관리자 이상·queue 모드·아직 우선 처리가 아닌 대기 작업). 담당자는 항상 false
    can_process_now: bool = False
    # 대기열 상태(추가 필드, 작업 88-1): 의미는 회의록 상세 processing 과 같다
    queue_ahead: int | None = None
    prioritized: bool = False
    waiting_for_prior: bool = False
    worker_alive: bool | None = None


@router.get("/processing", response_model=list[ProcessingItemOut], response_model_by_alias=True)
def get_my_processing(account: Account = Depends(get_current_account), session: Session = Depends(get_session)) -> list[ProcessingItemOut]:
    if is_system_admin(session, account.id):
        return []  # 시스템 관리자는 처리 중인 회의록 정보를 보지 않는다
    return [
        ProcessingItemOut(
            meeting_id=e.meeting_id, job_id=e.job_id, title=e.title, status=e.status, error_code=e.error_code,
            elapsed_sec=e.elapsed_sec, finished_at=e.finished_at, can_reprocess=e.can_reprocess,
            can_process_now=e.can_process_now,
            queue_ahead=e.queue.queue_ahead, prioritized=e.queue.prioritized, waiting_for_prior=e.queue.waiting_for_prior,
            worker_alive=e.queue.worker_alive,
        )
        for e in my_processing(session, account)
    ]
