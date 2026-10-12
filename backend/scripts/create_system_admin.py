"""최초 시스템 관리자 지정 도구(작업 82-1). 비밀번호는 터미널에서 getpass 로만 입력한다(인자·로그·출력에 남기지 않음).
기본은 dry-run(저장 안 함)이고 --apply 일 때만 저장한다. 같은 고객사 계정만 대상이며 이력(실행자 "시스템")을 남긴다.

사용법 (backend 폴더에서, 먼저 `alembic upgrade head`):
    # 기존 담당자(staff) 계정을 지정
    python scripts/create_system_admin.py --tenant "고객사A" --login-id sysadmin            [--apply]
    # 새 전용 계정을 만들어 지정(비밀번호는 실행 중 입력)
    python scripts/create_system_admin.py --tenant "고객사A" --login-id sysadmin --create --name "시스템 관리자" --email a@example.com [--apply]
시스템 관리자는 회의록 내용을 볼 수 없는 전용 계정이다. 사용권한이 상위인 기존 계정은 지정할 수 없다.
"""
import argparse
import getpass
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select  # noqa: E402

from app.auth.passwords import hash_password  # noqa: E402
from app.config import settings  # noqa: E402
from app.db import SessionLocal  # noqa: E402
from app.models import Account, Tenant  # noqa: E402
from app.services.system_admin import SystemAdminError, designate  # noqa: E402


def main(argv: list[str] | None = None, *, password_input=getpass.getpass, session_factory=None, out=None) -> int:
    parser = argparse.ArgumentParser(description="시스템 관리자 지정(기본 dry-run)")
    parser.add_argument("--tenant", required=True, help="고객사 이름(이미 있어야 함)")
    parser.add_argument("--login-id", required=True)
    parser.add_argument("--create", action="store_true", help="새 계정을 만들어 지정(비밀번호 입력)")
    parser.add_argument("--name", default="")
    parser.add_argument("--email", default="")
    parser.add_argument("--apply", action="store_true", help="실제로 저장(없으면 dry-run)")
    args = parser.parse_args(argv)
    stream = out or sys.stdout
    mode = "적용" if args.apply else "dry-run(저장 안 함)"
    factory = session_factory or SessionLocal

    with factory() as session:
        tenant = session.scalar(select(Tenant).where(Tenant.name == args.tenant))
        if tenant is None:
            print(f"오류: 고객사를 찾을 수 없습니다: {args.tenant}", file=sys.stderr)
            return 2
        existing = session.scalar(select(Account).where(Account.login_id == args.login_id))
        try:
            if args.create:
                if existing is not None:
                    print("오류: 이미 있는 login_id 입니다. --create 없이 기존 계정을 지정하세요.", file=sys.stderr)
                    return 2
                if not args.name.strip() or not args.email.strip():
                    print("오류: --create 에는 --name 과 --email 이 필요합니다.", file=sys.stderr)
                    return 2
                print(f"[{mode}] 새 전용 계정 생성 후 시스템 관리자 지정: {args.login_id} (사용권한 staff)", file=stream)
                if not args.apply:
                    return 0
                password = password_input("비밀번호: ")
                if password != password_input("비밀번호 확인: "):
                    print("오류: 비밀번호가 서로 다릅니다.", file=sys.stderr)
                    return 1
                if len(password) < settings.password_min_length:
                    print(f"오류: 비밀번호는 {settings.password_min_length}자 이상이어야 합니다.", file=sys.stderr)
                    return 1
                account = Account(
                    tenant_id=tenant.id, login_id=args.login_id, password_hash=hash_password(password),
                    name=args.name.strip(), email=args.email.strip(), rank="staff",
                )
                session.add(account)
                session.flush()
            else:
                if existing is None or existing.tenant_id != tenant.id:
                    print("오류: 같은 고객사에서 해당 계정을 찾을 수 없습니다.", file=sys.stderr)
                    return 2
                account = existing
                print(f"[{mode}] 기존 계정을 시스템 관리자로 지정: {account.login_id} (사용권한 {account.rank})", file=stream)
            if args.apply:
                designate(session, account, actor_id=None)
                session.commit()
                print("저장했습니다. 이 계정은 이제 회의록 내용을 볼 수 없고 관리 기능만 사용합니다.", file=stream)
            else:
                # 규칙 점검만 한다(저장 없음)
                if account.rank != "staff" or not account.is_active:
                    print("오류: 지정할 수 없는 계정입니다(활성 담당자(staff) 계정만 가능).", file=sys.stderr)
                    return 1
                print("지정 가능합니다. 실제로 저장하려면 --apply 를 붙이세요.", file=stream)
        except SystemAdminError as exc:
            session.rollback()
            print(f"오류: {exc}", file=sys.stderr)
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
