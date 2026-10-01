"""인증·권한 의존성. 권한은 반드시 서버에서 검사한다(화면 숨김만으로 보호하지 않음)."""
from collections.abc import Callable

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from app.auth.tokens import InvalidTokenError, decode_access_token
from app.db import get_session
from app.models import Account

# 직급 서열: staff(담당자) < manager(중간관리자) < executive(지시자)
RANK_ORDER = {"staff": 0, "manager": 1, "executive": 2}

_bearer = HTTPBearer(auto_error=False)


def _unauthorized() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="인증이 필요합니다",
        headers={"WWW-Authenticate": "Bearer"},
    )


def get_current_account(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
    session: Session = Depends(get_session),
) -> Account:
    """Bearer 토큰을 검증하고 계정을 돌려준다. 토큰 없음·만료·변조·없는 계정·비활성 계정은 모두 401."""
    if credentials is None:
        raise _unauthorized()
    try:
        account_id = decode_access_token(credentials.credentials)
    except InvalidTokenError:
        raise _unauthorized() from None
    account = session.get(Account, account_id)
    if account is None or not account.is_active:
        raise _unauthorized()
    return account


def require_rank(min_rank: str) -> Callable[..., Account]:
    """min_rank 이상 직급만 통과시키는 의존성을 만든다. 모자라면 403."""
    if min_rank not in RANK_ORDER:
        raise ValueError(f"알 수 없는 직급: {min_rank}")

    def _dependency(account: Account = Depends(get_current_account)) -> Account:
        if RANK_ORDER.get(account.rank, -1) < RANK_ORDER[min_rank]:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="권한이 없습니다")
        return account

    return _dependency
