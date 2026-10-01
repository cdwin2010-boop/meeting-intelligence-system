"""계정 생성 스크립트. 비밀번호는 실행 중 getpass 로만 입력받는다(인자·환경변수로 받지 않음).

사용법 (backend 폴더에서, 먼저 `alembic upgrade head` 로 표를 만든다):
    python scripts/create_account.py --tenant "고객사A" --login-id kim --name "김담당" --email kim@example.com --rank staff
고객사가 없으면 새로 만든다. 저장소에는 기본 계정·비밀번호를 두지 않는다.
"""
import argparse
import getpass
import sys
from pathlib import Path

# backend 폴더를 import 경로에 추가(scripts/ 에서 실행해도 app 패키지를 찾도록)
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from app.auth.deps import RANK_ORDER  # noqa: E402
from app.auth.passwords import hash_password  # noqa: E402
from app.db import SessionLocal  # noqa: E402
from app.models import Account, Tenant  # noqa: E402

MIN_PASSWORD_LENGTH = 8


def create_account(session: Session, *, tenant_name: str, login_id: str, name: str, email: str, rank: str, password: str) -> Account:
    """고객사(없으면 생성)와 계정을 만든다. 같은 login_id 가 있으면 거절한다. 커밋은 호출부가 한다."""
    if rank not in RANK_ORDER:
        raise ValueError(f"rank 는 {', '.join(RANK_ORDER)} 중 하나여야 합니다.")
    if len(password) < MIN_PASSWORD_LENGTH:
        raise ValueError(f"비밀번호는 {MIN_PASSWORD_LENGTH}자 이상이어야 합니다.")
    if session.scalar(select(Account).where(Account.login_id == login_id)) is not None:
        raise ValueError(f"이미 있는 login_id 입니다: {login_id}")

    tenant = session.scalar(select(Tenant).where(Tenant.name == tenant_name))
    if tenant is None:
        tenant = Tenant(name=tenant_name)
        session.add(tenant)
        session.flush()

    account = Account(
        tenant_id=tenant.id,
        login_id=login_id,
        password_hash=hash_password(password),
        name=name,
        email=email,
        rank=rank,
    )
    session.add(account)
    session.flush()
    return account


def main() -> int:
    parser = argparse.ArgumentParser(description="계정 생성 (비밀번호는 실행 중 입력)")
    parser.add_argument("--tenant", required=True, help="고객사 이름(없으면 생성)")
    parser.add_argument("--login-id", required=True)
    parser.add_argument("--name", required=True)
    parser.add_argument("--email", required=True)
    parser.add_argument("--rank", required=True, choices=list(RANK_ORDER))
    args = parser.parse_args()

    password = getpass.getpass("비밀번호: ")
    if password != getpass.getpass("비밀번호 확인: "):
        print("비밀번호가 서로 다릅니다.", file=sys.stderr)
        return 1

    try:
        with SessionLocal() as session:
            account = create_account(
                session,
                tenant_name=args.tenant,
                login_id=args.login_id,
                name=args.name,
                email=args.email,
                rank=args.rank,
                password=password,
            )
            session.commit()
            # 비밀번호·해시는 출력하지 않는다
            print(f"계정 생성 완료: id={account.id}, login_id={account.login_id}, rank={account.rank}, tenant_id={account.tenant_id}")
    except ValueError as exc:
        print(f"오류: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
