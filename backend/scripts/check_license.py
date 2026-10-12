"""현재 설정(LICENSE_PATH, LICENSE_PUBLIC_KEY_PATH 등)으로 라이선스를 검증해 결과를 출력한다. 서버·DB 쓰기 없음(읽기만).
활성 계정 수는 DATABASE_URL 의 DB 를 읽어 센다. --accounts N 으로 지정하면 DB 를 열지 않는다.

사용법 (backend 폴더에서): python scripts/check_license.py [--accounts N] [--json]
종료 코드: 0 = valid / expiring_soon, 1 = 그 외(사용 허가 문제)
"""
import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import settings  # noqa: E402
from app.licensing.verifier import verify_license_bytes  # noqa: E402
from app.services import license_status as service  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="라이선스 검증 결과 출력")
    parser.add_argument("--accounts", type=int, default=None, help="활성 계정 수를 직접 지정(DB 를 열지 않음)")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    if args.accounts is not None:
        active = args.accounts
    else:
        from app.db import SessionLocal  # noqa: PLC0415 — 계정 수가 필요할 때만 DB 모듈을 불러온다

        with SessionLocal() as session:
            active = service.count_active_accounts(session)
    fingerprint, _ = service.current_fingerprint()
    status = verify_license_bytes(
        service._read_cached(service.resolve_path(settings.license_path)),  # noqa: SLF001
        service._read_cached(service.resolve_path(settings.license_public_key_path)),  # noqa: SLF001
        datetime.now(timezone.utc), fingerprint, active, settings.license_warn_days, settings.license_require_hardware_match,
    )
    data = {
        "status": status.state, "licensee": status.licensee, "maxAccounts": status.max_accounts,
        "activeAccounts": status.active_accounts, "overLimit": status.over_limit,
        "expiresAt": status.expires_at.isoformat() if status.expires_at else None, "daysLeft": status.days_left,
        "hardwareMatch": status.hardware_match, "message": status.message, "warnings": list(status.warnings),
    }
    if args.json:
        print(json.dumps(data, ensure_ascii=False, indent=2))
    else:
        print(f"상태: {data['status']}" + (f" — {data['message']}" if data["message"] else ""))
        if status.licensee is not None:
            print(f"발급 대상: {data['licensee']} / 허용 계정 {data['maxAccounts']} / 활성 계정 {data['activeAccounts']}"
                  + (" (초과)" if data["overLimit"] else ""))
            print(f"만료: {data['expiresAt']} (남은 일수 {data['daysLeft']})")
        for warning in data["warnings"]:
            print(f"경고: {warning}")
    return 0 if status.state in ("valid", "expiring_soon") else 1


if __name__ == "__main__":
    raise SystemExit(main())
