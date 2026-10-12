"""라이선스 상태 조회(작업 80-1): 설정 경로의 파일을 수정 시각 기준으로 캐시해 읽고 검증 모듈에 넘긴다.
읽기 전용이며 어떤 기능도 차단하지 않는다(계정 수 초과·만료 모두 표시만)."""
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import settings
from app.licensing.verifier import LicenseStatus, device_fingerprint, verify_license_bytes
from app.models import Account

_BACKEND_DIR = Path(__file__).resolve().parent.parent.parent
_file_cache: dict = {}
_fingerprint_cache: list = []


def resolve_path(path: Path) -> Path:
    """상대 경로는 backend 폴더 기준."""
    return path if path.is_absolute() else _BACKEND_DIR / path


def _read_cached(path: Path):
    """수정 시각·크기가 같으면 캐시를 쓴다. 파일이 없으면 None."""
    key = str(path)
    try:
        stat = path.stat()
    except OSError:
        _file_cache.pop(key, None)
        return None
    signature = (stat.st_mtime_ns, stat.st_size)
    cached = _file_cache.get(key)
    if cached is not None and cached[0] == signature:
        return cached[1]
    try:
        data = path.read_bytes()
    except OSError:
        data = None
    _file_cache[key] = (signature, data)
    return data


def current_fingerprint() -> tuple:
    if not _fingerprint_cache:
        _fingerprint_cache.append(device_fingerprint())
    return _fingerprint_cache[0]


def count_active_accounts(session: Session) -> int:
    return int(session.scalar(select(func.count()).select_from(Account).where(Account.is_active.is_(True))) or 0)


def current_license_status(session: Session, now: datetime | None = None) -> LicenseStatus:
    fingerprint, _warnings = current_fingerprint()
    return verify_license_bytes(
        _read_cached(resolve_path(settings.license_path)),
        _read_cached(resolve_path(settings.license_public_key_path)),
        now or datetime.now(timezone.utc),
        fingerprint,
        count_active_accounts(session),
        settings.license_warn_days,
        settings.license_require_hardware_match,
    )
