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


# ---------------- 재생용 서명 주소 토큰 ----------------
# 로그인 토큰과 같은 비밀값·HS256 을 쓰되, 'sub' 없이 typ="audio" 로 구분한다.
# 그래서 재생 토큰은 로그인 토큰으로 통하지 않고(decode_access_token 은 sub 필수), 로그인 토큰도 재생에 쓸 수 없다.
AUDIO_TOKEN_TYPE = "audio"
AUDIO_TOKEN_MINUTES = 10


def create_audio_token(account_id: int, meeting_id: int, expires_delta: timedelta | None = None) -> str:
    now = utcnow()
    expires = now + (expires_delta if expires_delta is not None else timedelta(minutes=AUDIO_TOKEN_MINUTES))
    claims = {"typ": AUDIO_TOKEN_TYPE, "uid": account_id, "mid": meeting_id, "iat": now, "exp": expires}
    return jwt.encode(claims, settings.secret_key.get_secret_value(), algorithm=_ALGORITHM)


def decode_audio_token(token: str) -> tuple[int, int]:
    """서명·만료·종류를 검증하고 (계정 id, 회의록 id)를 돌려준다."""
    try:
        claims = jwt.decode(
            token,
            settings.secret_key.get_secret_value(),
            algorithms=[_ALGORITHM],
            options={"require": ["typ", "uid", "mid", "exp"]},
        )
        if claims["typ"] != AUDIO_TOKEN_TYPE:
            raise ValueError("not an audio token")
        return int(claims["uid"]), int(claims["mid"])
    except (jwt.PyJWTError, ValueError, TypeError) as exc:
        raise InvalidTokenError from exc
