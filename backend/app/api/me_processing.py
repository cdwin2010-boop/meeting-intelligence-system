"""GET /api/me/processing: 내가 올린 회의록의 처리 현황(로그인 필요, 읽기 전용, 열람 기록 없음). 규칙은 app/services/processing_status.py."""
from datetime import datetime

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.schemas import CamelModel
from app.auth.deps import get_current_account
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


@router.get("/processing", response_model=list[ProcessingItemOut], response_model_by_alias=True)
def get_my_processing(account: Account = Depends(get_current_account), session: Session = Depends(get_session)) -> list[ProcessingItemOut]:
    return [
        ProcessingItemOut(
            meeting_id=e.meeting_id, job_id=e.job_id, title=e.title, status=e.status, error_code=e.error_code,
            elapsed_sec=e.elapsed_sec, finished_at=e.finished_at, can_reprocess=e.can_reprocess,
        )
        for e in my_processing(session, account)
    ]
