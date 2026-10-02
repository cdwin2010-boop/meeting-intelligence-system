"""같은 고객사 계정 목록(참석자 선택용, 읽기 전용).
응답은 id·표시 이름·직급만. 이메일·로그인 ID·비밀번호 해시 같은 값은 스키마에 두지 않는다.
고객사 범위는 scoped() 로 로그인 계정의 tenant_id 에 고정하고, 비활성 계정은 뺀다."""
from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.schemas import CamelModel
from app.auth.deps import get_current_account
from app.auth.scope import scoped
from app.db import get_session
from app.models import Account

router = APIRouter(prefix="/api/accounts", tags=["accounts"])


class AccountOption(CamelModel):
    id: int
    name: str
    rank: str


@router.get("", response_model=list[AccountOption], response_model_by_alias=True)
def list_accounts(account: Account = Depends(get_current_account), session: Session = Depends(get_session)) -> list[AccountOption]:
    """로그인한 사람과 같은 고객사의 활성 계정(본인 포함), 이름순."""
    rows = session.execute(
        scoped(select(Account), account).where(Account.is_active.is_(True)).order_by(Account.name, Account.id)
    ).scalars()
    return [AccountOption(id=a.id, name=a.name, rank=a.rank) for a in rows]
