"""이 장비의 라이선스 핑거프린트(해시)를 출력한다. 고객 장비에서 실행해 발급 요청 때 전달한다. 원시 장비 값은 출력하지 않는다.

사용법 (backend 폴더에서): python scripts/show_fingerprint.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.licensing.verifier import device_fingerprint  # noqa: E402


def main() -> int:
    digest, warnings = device_fingerprint()
    print(digest)
    for warning in warnings:
        print(f"경고: {warning}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
