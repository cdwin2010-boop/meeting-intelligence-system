"""시스템 정보(로그인 필요). 처리 엔진만 알려 준다: {"engine": "fake"|"gemini", "isFake": 불리언}.
키·키 모드(free·paid)·모델 이름·기타 설정은 내려주지 않는다."""
from fastapi import APIRouter, Depends

from app.api.schemas import CamelModel
from app.auth.deps import get_current_account
from app.config import settings
from app.models import Account

router = APIRouter(prefix="/api/system", tags=["system"])


class EngineOut(CamelModel):
    engine: str
    is_fake: bool


@router.get("/engine", response_model=EngineOut, response_model_by_alias=True)
def get_engine(account: Account = Depends(get_current_account)) -> EngineOut:
    return EngineOut(engine=settings.stt_provider, is_fake=settings.stt_provider == "fake")
