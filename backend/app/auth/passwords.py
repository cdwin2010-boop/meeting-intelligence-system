"""비밀번호 해시(argon2). 평문은 저장하지 않고 로그에도 남기지 않는다."""
from functools import lru_cache

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError

_hasher = PasswordHasher()


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    try:
        return _hasher.verify(password_hash, password)
    except (VerificationError, InvalidHashError):
        return False


@lru_cache(maxsize=1)
def _dummy_hash() -> str:
    return _hasher.hash("dummy-password-for-timing")


def burn_verify_time(password: str) -> None:
    """없는 ID로 로그인할 때도 해시 검증만큼 시간을 써서, 응답 시간으로 ID 존재 여부를 알 수 없게 한다."""
    verify_password(_dummy_hash(), password)
