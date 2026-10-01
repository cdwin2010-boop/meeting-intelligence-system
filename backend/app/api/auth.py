from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.schemas import LoginRequest, MeResponse, TokenResponse
from app.auth.deps import get_current_account
from app.auth.passwords import burn_verify_time, verify_password
from app.auth.tokens import create_access_token
from app.db import get_session
from app.models import Account

router = APIRouter(prefix="/api/auth", tags=["auth"])

# 없는 ID·틀린 비밀번호·비활성 계정을 구분하지 않는다(어떤 ID가 있는지 알려 주지 않기 위해)
LOGIN_FAILED_MESSAGE = "ID 또는 비밀번호가 올바르지 않습니다"


@router.post("/login", response_model=TokenResponse, response_model_by_alias=True)
def login(body: LoginRequest, session: Session = Depends(get_session)) -> TokenResponse:
    account = session.scalar(select(Account).where(Account.login_id == body.login_id))
    if account is None:
        burn_verify_time(body.password)
        ok = False
    else:
        ok = verify_password(account.password_hash, body.password) and account.is_active
    if not ok:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=LOGIN_FAILED_MESSAGE,
            headers={"WWW-Authenticate": "Bearer"},
        )
    return TokenResponse(access_token=create_access_token(account.id))


@router.get("/me", response_model=MeResponse, response_model_by_alias=True)
def me(account: Account = Depends(get_current_account)) -> MeResponse:
    return MeResponse(id=account.id, name=account.name, rank=account.rank, tenant_id=account.tenant_id)
