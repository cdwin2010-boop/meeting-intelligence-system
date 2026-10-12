"""라이선스(.lic) 발급 도구 — 발급하는 쪽(우리) 전용. 개인키 경로를 인자로 받아 서명한다.
개인키는 저장소·backend/data·.env 어디에도 두지 않는다. 개인키 값은 출력하지 않으며 출력 파일 지정이 필수다.

사용법 (backend 폴더에서):
    python scripts/issue_license.py --private-key <개인키.pem> --licensee "고객사명" --max-accounts 50 \\
        --expires 2027-12-31 --out <출력.lic> [--fingerprint <show_fingerprint 출력>] [--license-id ID]
--expires 가 날짜만이면 그날 23:59:59 UTC 까지, 날짜·시각(ISO-8601)이면 그 시각까지다. 기본 시각대는 UTC.
"""
import argparse
import base64
import json
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey  # noqa: E402
from cryptography.hazmat.primitives.serialization import load_pem_private_key  # noqa: E402

from app.licensing.verifier import LICENSE_VERSION, canonical_bytes  # noqa: E402


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_expires(text: str) -> datetime:
    value = text.strip()
    if len(value) == 10:  # YYYY-MM-DD
        day = datetime.strptime(value, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        return day + timedelta(hours=23, minutes=59, seconds=59)
    parsed = datetime.fromisoformat(value[:-1] + "+00:00" if value.endswith(("Z", "z")) else value)
    return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed.astimezone(timezone.utc)


def build_license(private_key: Ed25519PrivateKey, *, licensee: str, max_accounts: int, expires_at: datetime,
                  issued_at: datetime | None = None, license_id: str | None = None, fingerprint: str | None = None) -> dict:
    payload = {
        "version": LICENSE_VERSION,
        "license_id": license_id or uuid.uuid4().hex,
        "licensee": licensee,
        "max_accounts": max_accounts,
        "issued_at": _iso(issued_at or datetime.now(timezone.utc)),
        "expires_at": _iso(expires_at),
    }
    if fingerprint:
        payload["hardware_fingerprint"] = fingerprint.strip().lower()
    signature = base64.b64encode(private_key.sign(canonical_bytes(payload))).decode("ascii")
    return {"payload": payload, "signature": signature}


def load_private_key(path: Path) -> Ed25519PrivateKey:
    key = load_pem_private_key(path.read_bytes(), password=None)
    if not isinstance(key, Ed25519PrivateKey):
        raise ValueError("Ed25519 개인키가 아닙니다.")
    return key


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="라이선스(.lic) 발급")
    parser.add_argument("--private-key", required=True, help="Ed25519 개인키(PEM) 경로")
    parser.add_argument("--licensee", required=True, help="발급 대상 이름")
    parser.add_argument("--max-accounts", required=True, type=int, help="허용 활성 계정 수")
    parser.add_argument("--expires", required=True, help="만료일(YYYY-MM-DD 또는 ISO-8601 UTC)")
    parser.add_argument("--out", required=True, help="출력 .lic 파일 경로(없으면 만들고, 있으면 --force 필요)")
    parser.add_argument("--fingerprint", default=None, help="장비 핑거프린트(선택, show_fingerprint 출력값)")
    parser.add_argument("--license-id", default=None, help="라이선스 ID(기본: 무작위)")
    parser.add_argument("--force", action="store_true", help="출력 파일이 있어도 덮어쓴다")
    args = parser.parse_args(argv)
    if args.max_accounts < 0:
        print("오류: --max-accounts 는 0 이상이어야 합니다.", file=sys.stderr)
        return 2
    if not args.licensee.strip():
        print("오류: --licensee 가 비어 있습니다.", file=sys.stderr)
        return 2
    out = Path(args.out)
    if out.exists() and not args.force:
        print(f"오류: 출력 파일이 이미 있습니다: {out.name} (--force 로 덮어쓰기)", file=sys.stderr)
        return 2
    try:
        expires_at = parse_expires(args.expires)
    except ValueError:
        print("오류: --expires 형식이 올바르지 않습니다.", file=sys.stderr)
        return 2
    try:
        key = load_private_key(Path(args.private_key))
    except (OSError, ValueError, TypeError):
        print("오류: 개인키를 읽을 수 없습니다(경로·형식 확인).", file=sys.stderr)
        return 2
    document = build_license(
        key, licensee=args.licensee.strip(), max_accounts=args.max_accounts, expires_at=expires_at,
        license_id=args.license_id, fingerprint=args.fingerprint,
    )
    out.write_text(json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    p = document["payload"]
    print(f"발급 완료: {out.name} / 대상 {p['licensee']} / 허용 계정 {p['max_accounts']} / 만료 {p['expires_at']}"
          + (" / 장비 바인딩" if "hardware_fingerprint" in p else ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
