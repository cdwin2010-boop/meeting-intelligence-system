"""라이선스(사용 허가) 검증 모듈(작업 80-1).

.lic 는 UTF-8 JSON {"payload": {...}, "signature": "<base64 Ed25519>"} 이다. 서명 대상은 payload 를 키 정렬·공백 제거로
정규화한 바이트다. 라이선스는 "사용 허가"만 담으며 기능 플래그 필드는 없다(알 수 없는 payload 키는 형식 오류).

Cython 컴파일 대상이므로 동적 import·exec·eval·전역 상태 변경 없이 순수 함수 중심으로 쓴다(파일 시각 캐시는 services/license_status.py).
예외를 올리지 않고 항상 LicenseStatus 를 돌려준다. 장치 고유 값 원본은 해시로만 쓰고 메시지·결과에 넣지 않는다.
"""
import base64
import binascii
import hashlib
import json
import platform
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from cryptography.hazmat.primitives.serialization import load_pem_public_key

try:  # Windows 전용 표준 라이브러리(정적 import, 없으면 건너뜀)
    import winreg
except ImportError:  # pragma: no cover - 비 Windows
    winreg = None

LICENSE_VERSION = 1
FINGERPRINT_SALT = "ai-dots-license-fingerprint-v1"  # 제품 고유 솔트(다른 제품과의 값 혼용 방지용, 비밀 아님)

VALID = "valid"
EXPIRING_SOON = "expiring_soon"
EXPIRED = "expired"
INVALID_SIGNATURE = "invalid_signature"
HARDWARE_MISMATCH = "hardware_mismatch"
MISSING = "missing"
MALFORMED = "malformed"
PUBLIC_KEY_MISSING = "public_key_missing"

PAYLOAD_KEYS = frozenset(
    {"version", "license_id", "licensee", "max_accounts", "issued_at", "expires_at", "hardware_fingerprint"}
)
REQUIRED_KEYS = PAYLOAD_KEYS - {"hardware_fingerprint"}

_MESSAGES = {
    VALID: "",
    EXPIRING_SOON: "라이선스 만료가 가깝습니다.",
    EXPIRED: "라이선스가 만료되었습니다.",
    INVALID_SIGNATURE: "라이선스 서명을 확인할 수 없습니다.",
    HARDWARE_MISMATCH: "라이선스가 발급된 장비와 다른 장비입니다.",
    MISSING: "라이선스 파일이 없습니다.",
    MALFORMED: "라이선스 파일 형식이 올바르지 않습니다.",
    PUBLIC_KEY_MISSING: "라이선스 검증용 공개키를 읽을 수 없습니다.",
}


@dataclass(frozen=True)
class LicenseStatus:
    state: str
    licensee: str | None = None
    license_id: str | None = None
    max_accounts: int | None = None
    active_accounts: int = 0
    over_limit: bool = False
    expires_at: datetime | None = None
    days_left: int | None = None
    hardware_match: bool | None = None  # None = 바인딩 검사 안 함(발급 시 핑거프린트 없음 또는 설정 꺼짐)
    message: str = ""
    warnings: tuple = ()


def canonical_bytes(payload: dict) -> bytes:
    """서명 대상 정규화: 키 정렬, 공백 제거, UTF-8."""
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def _parse_time(value: object):
    if not isinstance(value, str) or not value:
        return None
    text = value[:-1] + "+00:00" if value.endswith(("Z", "z")) else value
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)  # 시각대 없음은 UTC 로 본다
    return parsed.astimezone(timezone.utc)


def _status(state: str, **fields) -> LicenseStatus:
    return LicenseStatus(state=state, message=_MESSAGES[state], **fields)


def device_fingerprint() -> tuple:
    """이 장비의 핑거프린트(SHA-256 16진수)와 경고 튜플. 원본 값은 반환·로그에 넣지 않는다."""
    raw = ""
    warnings: tuple = ()
    if winreg is not None:
        try:
            with winreg.OpenKey(
                winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Cryptography", 0, winreg.KEY_READ | winreg.KEY_WOW64_64KEY
            ) as key:
                raw = str(winreg.QueryValueEx(key, "MachineGuid")[0]).strip()
        except OSError:
            raw = ""
    else:
        try:
            raw = Path("/etc/machine-id").read_text(encoding="ascii").strip()
        except (OSError, UnicodeDecodeError):
            raw = ""
    if not raw:
        raw = "|".join((platform.node(), platform.system(), platform.machine()))
        warnings = ("장비 고유 값을 읽지 못해 대체 정보로 핑거프린트를 만들었습니다(변경될 수 있음).",)
    digest = hashlib.sha256((FINGERPRINT_SALT + "\n" + raw).encode("utf-8")).hexdigest()
    return digest, warnings


def verify_license_bytes(
    license_bytes,
    public_key_bytes,
    now: datetime,
    fingerprint: str,
    active_accounts: int,
    warn_days: int = 30,
    require_hardware_match: bool = False,
) -> LicenseStatus:
    """바이트 단위 검증(캐시·테스트용). 예외를 올리지 않는다."""
    try:
        return _verify(license_bytes, public_key_bytes, now, fingerprint, active_accounts, warn_days, require_hardware_match)
    except Exception:  # noqa: BLE001 — 어떤 경우에도 상태 객체로 답한다(내용은 노출하지 않음)
        return _status(MALFORMED, active_accounts=active_accounts)


def _verify(license_bytes, public_key_bytes, now, fingerprint, active_accounts, warn_days, require_hardware_match) -> LicenseStatus:
    if license_bytes is None:
        return _status(MISSING, active_accounts=active_accounts)
    if public_key_bytes is None:
        return _status(PUBLIC_KEY_MISSING, active_accounts=active_accounts)
    try:
        public_key = load_pem_public_key(public_key_bytes)
    except (ValueError, TypeError):
        return _status(PUBLIC_KEY_MISSING, active_accounts=active_accounts)
    if not isinstance(public_key, Ed25519PublicKey):
        return _status(PUBLIC_KEY_MISSING, active_accounts=active_accounts)

    try:
        document = json.loads(license_bytes.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return _status(MALFORMED, active_accounts=active_accounts)
    if not isinstance(document, dict) or set(document) != {"payload", "signature"}:
        return _status(MALFORMED, active_accounts=active_accounts)
    payload, signature_text = document["payload"], document["signature"]
    if not isinstance(payload, dict) or not isinstance(signature_text, str):
        return _status(MALFORMED, active_accounts=active_accounts)
    try:
        signature = base64.b64decode(signature_text, validate=True)
    except (binascii.Error, ValueError):
        return _status(MALFORMED, active_accounts=active_accounts)
    try:
        public_key.verify(signature, canonical_bytes(payload))
    except InvalidSignature:
        return _status(INVALID_SIGNATURE, active_accounts=active_accounts)

    # 서명이 맞는 payload 만 신뢰한다. 이후 오류는 발급 쪽 형식 오류.
    keys = set(payload)
    if keys - PAYLOAD_KEYS or not REQUIRED_KEYS <= keys:
        return _status(MALFORMED, active_accounts=active_accounts)
    max_accounts = payload["max_accounts"]
    expires_at = _parse_time(payload["expires_at"])
    issued_at = _parse_time(payload["issued_at"])
    bound = payload.get("hardware_fingerprint")
    version = payload["version"]
    valid_shape = (
        version == LICENSE_VERSION and not isinstance(version, bool)
        and isinstance(payload["license_id"], str) and bool(payload["license_id"])
        and isinstance(payload["licensee"], str) and bool(payload["licensee"])
        and isinstance(max_accounts, int) and not isinstance(max_accounts, bool) and max_accounts >= 0
        and expires_at is not None and issued_at is not None
        and (bound is None or isinstance(bound, str))
    )
    if not valid_shape:
        return _status(MALFORMED, active_accounts=active_accounts)

    over_limit = active_accounts > max_accounts
    common = dict(
        licensee=payload["licensee"], license_id=payload["license_id"], max_accounts=max_accounts,
        active_accounts=active_accounts, over_limit=over_limit, expires_at=expires_at,
    )
    now_utc = now.astimezone(timezone.utc) if now.tzinfo else now.replace(tzinfo=timezone.utc)
    remaining_seconds = (expires_at - now_utc).total_seconds()
    days_left = int(remaining_seconds // 86400)  # 내림(음수면 만료)
    warnings: tuple = ()
    if over_limit:
        warnings = (f"허용 계정 수({max_accounts})를 초과했습니다(현재 활성 계정 {active_accounts}).",)

    hardware_match = None
    if require_hardware_match and bound:
        hardware_match = bound.strip().lower() == fingerprint.strip().lower()
        if not hardware_match:
            return _status(HARDWARE_MISMATCH, hardware_match=False, days_left=days_left, warnings=warnings, **common)
    if remaining_seconds < 0:
        return _status(EXPIRED, hardware_match=hardware_match, days_left=days_left, warnings=warnings, **common)
    if days_left <= warn_days:
        return _status(EXPIRING_SOON, hardware_match=hardware_match, days_left=days_left, warnings=warnings, **common)
    return _status(VALID, hardware_match=hardware_match, days_left=days_left, warnings=warnings, **common)


def _read_or_none(path):
    try:
        return Path(path).read_bytes()
    except OSError:
        return None


def verify_license(
    license_path,
    public_key_path,
    now: datetime,
    fingerprint: str,
    active_accounts: int,
    warn_days: int = 30,
    require_hardware_match: bool = False,
) -> LicenseStatus:
    """파일 경로 기반 검증. 예외를 올리지 않고 항상 상태 객체를 돌려준다."""
    return verify_license_bytes(
        _read_or_none(license_path), _read_or_none(public_key_path), now, fingerprint, active_accounts, warn_days,
        require_hardware_match,
    )
