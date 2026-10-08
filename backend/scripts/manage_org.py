"""조직(부서·소속) 관리 스크립트. 비밀번호·키는 다루지 않는다. 실행자는 이력에 "시스템"(행위자 없음)으로 남는다.
기본은 변경 예정만 보여 주는 dry-run(저장하지 않음)이고, --apply 를 붙여야 저장한다. 모든 대상은 같은 고객사 안에서만 찾는다.

사용법 (backend 폴더에서, 먼저 `alembic upgrade head`):
    python scripts/manage_org.py list --tenant "고객사A"
    python scripts/manage_org.py create-dept --tenant "고객사A" --name "개발팀" [--parent "본부"] [--executive] [--apply]
    python scripts/manage_org.py set-head --tenant "고객사A" --dept "개발팀" --login-id kim [--apply]      # 기존 부서장은 부서원으로, 관리자 이상 사용권한만
    python scripts/manage_org.py add-member --tenant "고객사A" --dept "개발팀" --login-id lee [--apply]
    python scripts/manage_org.py remove-member --tenant "고객사A" --dept "개발팀" --login-id lee [--apply]
"""
import argparse
import sys
from pathlib import Path

# backend 폴더를 import 경로에 추가(scripts/ 에서 실행해도 app 패키지를 찾도록)
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select  # noqa: E402
from sqlalchemy.orm import Session, sessionmaker  # noqa: E402

from app.db import SessionLocal  # noqa: E402
from app.models import Account, Department, Tenant  # noqa: E402
from app.services import org  # noqa: E402


def _tenant(session: Session, name: str) -> Tenant:
    tenant = session.scalar(select(Tenant).where(Tenant.name == name))
    if tenant is None:
        raise org.OrgError(f"없는 고객사입니다: {name}")
    return tenant


def _department(session: Session, tenant: Tenant, name: str) -> Department:
    department = session.scalar(select(Department).where(Department.tenant_id == tenant.id, Department.name == name))
    if department is None:
        raise org.OrgError(f"없는 부서입니다: {name}")
    return department


def _account(session: Session, tenant: Tenant, login_id: str) -> Account:
    account = session.scalar(select(Account).where(Account.login_id == login_id, Account.tenant_id == tenant.id))
    if account is None:
        raise org.OrgError(f"그 고객사에 없는 계정입니다: {login_id}")
    return account


def run(session: Session, args: argparse.Namespace) -> list[str]:
    """명령을 실행하고 (변경 예정/결과) 문장 목록을 돌려준다. 커밋은 하지 않는다."""
    tenant = _tenant(session, args.tenant)
    if args.command == "list":
        lines = []
        for department in session.scalars(select(Department).where(Department.tenant_id == tenant.id).order_by(Department.name)):
            parent = session.get(Department, department.parent_id).name if department.parent_id else "-"
            members = ", ".join(f"{a.login_id}({'부서장' if role == 'head' else '부서원'})" for a, role in org.members_of(session, department.id, active_only=False))
            lines.append(f"[{department.id}] {department.name} (종류 {department.kind}, 상위 {parent}): {members or '구성원 없음'}")
        return lines or ["부서가 없습니다."]
    if args.command == "create-dept":
        parent = _department(session, tenant, args.parent) if args.parent else None
        department = org.create_department(
            session, tenant_id=tenant.id, name=args.name, parent_id=parent.id if parent else None,
            kind="executive" if args.executive else "normal",
        )
        return [f"부서 생성: {department.name} (종류 {department.kind}, 상위 {parent.name if parent else '-'})"]
    department = _department(session, tenant, args.dept)
    account = _account(session, tenant, args.login_id)
    if args.command == "set-head":
        changed = org.set_head(session, department, account)
        return [f"부서장 지정: {department.name} ← {account.login_id}" if changed else f"이미 부서장입니다: {account.login_id}"]
    if args.command == "add-member":
        changed = org.add_member(session, department, account)
        return [f"소속 추가: {department.name} ← {account.login_id}" if changed else f"이미 소속되어 있습니다: {account.login_id}"]
    org.remove_member(session, department, account)
    return [f"소속 제거: {department.name} ✕ {account.login_id}"]


def main(argv: list[str] | None = None, session_factory: sessionmaker | None = None) -> int:
    parser = argparse.ArgumentParser(description="조직(부서·소속) 관리 (기본은 dry-run, --apply 일 때만 저장)")
    sub = parser.add_subparsers(dest="command", required=True)

    def common(p: argparse.ArgumentParser, *, writes: bool = True) -> argparse.ArgumentParser:
        p.add_argument("--tenant", required=True, help="고객사 이름")
        if writes:
            p.add_argument("--apply", action="store_true", help="저장한다(없으면 변경 예정만 보여 준다)")
        return p

    common(sub.add_parser("list", help="부서와 구성원 보기"), writes=False)
    create = common(sub.add_parser("create-dept", help="부서 만들기"))
    create.add_argument("--name", required=True)
    create.add_argument("--parent", help="상위 부서 이름")
    create.add_argument("--executive", action="store_true", help="임원 그룹으로 만든다")
    for name, help_text in (("set-head", "부서장 지정"), ("add-member", "소속 추가(부서원)"), ("remove-member", "소속 제거")):
        p = common(sub.add_parser(name, help=help_text))
        p.add_argument("--dept", required=True, help="부서 이름")
        p.add_argument("--login-id", required=True)
    args = parser.parse_args(argv)
    apply = getattr(args, "apply", False)

    with (session_factory or SessionLocal)() as session:
        try:
            lines = run(session, args)
        except org.OrgError as exc:
            session.rollback()
            print(f"오류: {exc}", file=sys.stderr)
            return 1
        if args.command == "list":
            print("\n".join(lines))
            return 0
        if apply:
            session.commit()
            print("\n".join(f"{line} (저장됨)" for line in lines))
        else:
            session.rollback()
            print("\n".join(f"{line} (변경 예정, 저장하지 않음 — 저장하려면 --apply)" for line in lines))
    return 0


if __name__ == "__main__":
    sys.exit(main())
