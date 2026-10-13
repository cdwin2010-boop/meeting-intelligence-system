"""시스템 정보(로그인 필요). 처리 엔진과 처리 방식만 알려 준다:
{"engine": "fake"|"gemini", "isFake": 불리언, "processingMode": "inline"|"queue", "queuePending": 정수, "queueProcessing": 정수,
 "llmProvider": "fake"|"gemini"|"local", "llmModel": 로컬 모델 이름 또는 ""}.
키·키 모드(free·paid)·모델 이름·기타 설정은 내려주지 않는다."""
from datetime import datetime

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.schemas import CamelModel
from fastapi import HTTPException, status

from app.auth.deps import get_current_account, has_rank
from app.config import settings
from app.db import get_session
from app.models import Account
from app.services.license_status import current_license_status
from app.services.system_admin import is_system_admin
from app.services.task_queue import queue_counts

router = APIRouter(prefix="/api/system", tags=["system"])


class EngineOut(CamelModel):
    engine: str
    is_fake: bool
    processing_mode: str
    queue_pending: int
    queue_processing: int
    # 추출(LLM) 엔진(추가 필드): fake | gemini | local 과 로컬 모델 이름(local 일 때만, 그 밖에는 ""). 주소·키는 내려주지 않는다
    llm_provider: str
    llm_model: str
    # 엔진 시험 모드(추가 필드, 작업 91-1): 꺼져 있으면(기본) 화면이 원격 엔진 선택·설정을 숨긴다. 서버도 관련 요청을 거부한다
    engine_test_mode: bool
    engine_test_default: str


@router.get("/engine", response_model=EngineOut, response_model_by_alias=True)
def get_engine(account: Account = Depends(get_current_account), session: Session = Depends(get_session)) -> EngineOut:
    pending, processing = queue_counts(session)
    return EngineOut(
        engine=settings.stt_provider, is_fake=settings.stt_provider == "fake",
        processing_mode=settings.processing_mode, queue_pending=pending, queue_processing=processing,
        llm_provider=settings.effective_llm_provider,
        llm_model=settings.local_llm_model if settings.effective_llm_provider == "local" else "",
        engine_test_mode=settings.engine_test_mode, engine_test_default=settings.engine_test_default,
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
    account: Account = Depends(get_current_account), session: Session = Depends(get_session)
) -> LicenseOut:
    """사용권한 중간관리자(manager) 이상 또는 시스템 관리자(회의록 내용은 못 보지만 라이선스 확인은 한다)만 조회한다. 그 밖에는 403."""
    if not (has_rank(account, "manager") or is_system_admin(session, account.id)):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="권한이 없습니다")
    st = current_license_status(session)
    return LicenseOut(
        status=st.state, licensee=st.licensee, max_accounts=st.max_accounts, active_accounts=st.active_accounts,
        over_limit=st.over_limit, expires_at=st.expires_at, days_left=st.days_left, hardware_match=st.hardware_match,
        message=st.message, warnings=list(st.warnings),
    )
