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


def has_rank(account: Account, min_rank: str) -> bool:
    """account 가 min_rank 이상 직급인가(require_rank 와 같은 판정, 목록 조회처럼 403 대신 걸러 낼 때 쓴다)."""
    return RANK_ORDER.get(account.rank, -1) >= RANK_ORDER[min_rank]


def require_rank(min_rank: str) -> Callable[..., Account]:
    """min_rank 이상 직급만 통과시키는 의존성을 만든다. 모자라면 403."""
    if min_rank not in RANK_ORDER:
        raise ValueError(f"알 수 없는 직급: {min_rank}")

    def _dependency(account: Account = Depends(get_current_account)) -> Account:
        if not has_rank(account, min_rank):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="권한이 없습니다")
        return account

    return _dependency


def require_system_admin(account: Account = Depends(get_current_account), session: Session = Depends(get_session)) -> Account:
    """시스템 관리자(system_admins 에 지정된 활성 계정)만 통과시킨다. 아니면 403. 사용자 등급과 별개다."""
    from app.services.system_admin import is_system_admin  # noqa: PLC0415 — 순환 import 회피

    if not is_system_admin(session, account.id):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="권한이 없습니다")
    return account


def forbid_system_admin(session: Session, account: Account) -> None:
    """회의록·프로젝트 같은 내용을 새로 등록하는 API 용: 시스템 관리자는 내용 영역에 쓰지 않는다(403)."""
    from app.services.system_admin import is_system_admin  # noqa: PLC0415

    if is_system_admin(session, account.id):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="시스템 관리자는 회의록 내용 영역을 사용할 수 없습니다")

