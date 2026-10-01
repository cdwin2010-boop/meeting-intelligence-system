"""로그인 토큰(JWT, HS256). 토큰에는 계정 id(sub)와 발급·만료 시각만 담는다."""
from datetime import timedelta

import jwt

from app.config import settings
from app.models.common import utcnow

_ALGORITHM = "HS256"


class InvalidTokenError(Exception):
    """만료·변조·형식 오류를 하나로 묶는다(호출부는 모두 401로 처리)."""


def create_access_token(account_id: int, expires_delta: timedelta | None = None) -> str:
    now = utcnow()
    expires = now + (expires_delta if expires_delta is not None else timedelta(minutes=settings.access_token_minutes))
    claims = {"sub": str(account_id), "iat": now, "exp": expires}
    return jwt.encode(claims, settings.secret_key.get_secret_value(), algorithm=_ALGORITHM)


def decode_access_token(token: str) -> int:
    """서명·만료를 검증하고 계정 id를 돌려준다. 알고리즘은 HS256만 허용한다."""
    try:
        claims = jwt.decode(
            token,
            settings.secret_key.get_secret_value(),
            algorithms=[_ALGORITHM],
            options={"require": ["sub", "exp"]},
        )
        return int(claims["sub"])
    except (jwt.PyJWTError, ValueError) as exc:
        raise InvalidTokenError from exc
