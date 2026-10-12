"""시스템 정보(로그인 필요). 처리 엔진과 처리 방식만 알려 준다:
{"engine": "fake"|"gemini", "isFake": 불리언, "processingMode": "inline"|"queue", "queuePending": 정수, "queueProcessing": 정수}.
키·키 모드(free·paid)·모델 이름·기타 설정은 내려주지 않는다."""
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.schemas import CamelModel
from app.auth.deps import get_current_account
from app.config import settings
from app.db import get_session
from app.models import Account
from app.services.task_queue import queue_counts

router = APIRouter(prefix="/api/system", tags=["system"])


class EngineOut(CamelModel):
    engine: str
    is_fake: bool
    processing_mode: str
    queue_pending: int
    queue_processing: int


@router.get("/engine", response_model=EngineOut, response_model_by_alias=True)
def get_engine(account: Account = Depends(get_current_account), session: Session = Depends(get_session)) -> EngineOut:
    pending, processing = queue_counts(session)
    return EngineOut(
        engine=settings.stt_provider, is_fake=settings.stt_provider == "fake",
        processing_mode=settings.processing_mode, queue_pending=pending, queue_processing=processing,
    )
