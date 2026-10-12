"""시스템 정보(로그인 필요). 처리 엔진과 처리 방식만 알려 준다:
{"engine": "fake"|"gemini", "isFake": 불리언, "processingMode": "inline"|"queue", "queuePending": 정수, "queueProcessing": 정수}.
키·키 모드(free·paid)·모델 이름·기타 설정은 내려주지 않는다."""
from datetime import datetime

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.schemas import CamelModel
from app.auth.deps import get_current_account, require_rank
from app.config import settings
from app.db import get_session
from app.models import Account
from app.services.license_status import current_license_status
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


class LicenseOut(CamelModel):
    """라이선스 상태(읽기 전용). 서명·공개키·핑거프린트 값은 내려주지 않는다."""

    status: str
    licensee: str | None
    max_accounts: int | None
    active_accounts: int
    over_limit: bool
    expires_at: datetime | None
    days_left: int | None
    hardware_match: bool | None
    message: str
    warnings: list[str]


@router.get("/license", response_model=LicenseOut, response_model_by_alias=True)
def get_license(
    account: Account = Depends(require_rank("manager")), session: Session = Depends(get_session)
) -> LicenseOut:
    """임시 규칙: 시스템 관리자 개념이 아직 없어 사용권한 중간관리자(manager) 이상만 조회한다."""
    st = current_license_status(session)
    return LicenseOut(
        status=st.state, licensee=st.licensee, max_accounts=st.max_accounts, active_accounts=st.active_accounts,
        over_limit=st.over_limit, expires_at=st.expires_at, days_left=st.days_left, hardware_match=st.hardware_match,
        message=st.message, warnings=list(st.warnings),
    )
