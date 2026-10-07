"""조직(부서·소속) 서비스. 부서 만들기, 부서장 지정, 소속 추가·제거와 조회.

- 직원 표는 없고 기존 계정에 소속(account_departments)을 붙인다. 겸직은 소속이 여러 개다.
- "부서장은 부서마다 1명"은 DB 제약이 아니라 여기서 보장한다: 새 부서장을 지정하면 기존 부서장은 부서원으로 내린다.
- 부서장은 관리자 이상 직급(manager·executive) 계정만 될 수 있다.
- 변경은 공통 이력(events, 대상 종류 "department")에 남긴다. 커밋은 호출부가 한다(변경과 이력이 같은 트랜잭션).
- 조직 등록·수정 API 는 없다(시스템 관리자 기능 때 만든다). 지금은 scripts/manage_org.py 가 이 함수들을 쓴다.
"""
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.deps import RANK_ORDER
from app.models import Account, AccountDepartment, Department
from app.models.common import DEPARTMENT_KINDS
from app.services.history import KIND_DEPARTMENT_UPDATE, record_change

NAME_MAX = 100
HEAD_MIN_RANK = "manager"


class OrgError(ValueError):
    """조직 변경 규칙 위반(메시지는 사람이 읽는 한국어)."""


def _record(session: Session, department: Department, actor_id: int | None, before: dict, after: dict) -> None:
    record_change(
        session, tenant_id=department.tenant_id, target_type="department", target_id=department.id,
        kind=KIND_DEPARTMENT_UPDATE, actor_id=actor_id, before=before, after=after,
    )


def find_department(session: Session, tenant_id: int, department_id: int) -> Department | None:
    return session.scalar(select(Department).where(Department.id == department_id, Department.tenant_id == tenant_id))


def create_department(
    session: Session, *, tenant_id: int, name: str, parent_id: int | None = None, kind: str = "normal", actor_id: int | None = None
) -> Department:
    name = (name or "").strip()
    if not name or len(name) > NAME_MAX:
        raise OrgError(f"부서 이름은 1~{NAME_MAX}자여야 합니다.")
    if kind not in DEPARTMENT_KINDS:
        raise OrgError(f"부서 종류는 {', '.join(DEPARTMENT_KINDS)} 중 하나여야 합니다.")
    if session.scalar(select(Department.id).where(Department.tenant_id == tenant_id, Department.name == name)) is not None:
        raise OrgError(f"같은 이름의 부서가 이미 있습니다: {name}")
    if parent_id is not None and find_department(session, tenant_id, parent_id) is None:
        raise OrgError("상위 부서를 찾을 수 없습니다(같은 고객사의 부서만 가능).")
    department = Department(tenant_id=tenant_id, name=name, parent_id=parent_id, kind=kind)
    session.add(department)
    session.flush()
    _record(session, department, actor_id, {}, {"action": "department_created", "name": name, "parentId": parent_id, "kind": kind})
    return department


def _check_account(department: Department, account: Account) -> None:
    if account.tenant_id != department.tenant_id:
        raise OrgError("같은 고객사의 계정만 소속시킬 수 있습니다.")
    if not account.is_active:
        raise OrgError("비활성 계정은 소속시킬 수 없습니다.")


def membership(session: Session, account_id: int, department_id: int) -> AccountDepartment | None:
    return session.scalar(
        select(AccountDepartment).where(AccountDepartment.account_id == account_id, AccountDepartment.department_id == department_id)
    )


def department_head(session: Session, department_id: int) -> Account | None:
    """부서장 계정(없으면 None)."""
    return session.scalar(
        select(Account)
        .join(AccountDepartment, AccountDepartment.account_id == Account.id)
        .where(AccountDepartment.department_id == department_id, AccountDepartment.role == "head")
        .order_by(AccountDepartment.id)
        .limit(1)
    )


def add_member(session: Session, department: Department, account: Account, actor_id: int | None = None) -> bool:
    """부서원으로 소속시킨다. 이미 소속(부서장 포함)이면 변경 없이 False."""
    _check_account(department, account)
    if membership(session, account.id, department.id) is not None:
        return False
    session.add(AccountDepartment(account_id=account.id, department_id=department.id, role="member"))
    session.flush()
    _record(session, department, actor_id, {}, {"action": "member_added", "accountId": account.id, "role": "member"})
    return True


def set_head(session: Session, department: Department, account: Account, actor_id: int | None = None) -> bool:
    """부서장으로 지정한다(기존 부서장은 부서원으로 내림, 소속이 없으면 함께 추가). 이미 부서장이면 False."""
    _check_account(department, account)
    if RANK_ORDER.get(account.rank, -1) < RANK_ORDER[HEAD_MIN_RANK]:
        raise OrgError("부서장은 관리자 이상 직급 계정만 될 수 있습니다.")
    rows = session.scalars(
        select(AccountDepartment).where(AccountDepartment.department_id == department.id, AccountDepartment.role == "head")
    ).all()
    if len(rows) == 1 and rows[0].account_id == account.id:
        return False
    previous = [row.account_id for row in rows]
    for row in rows:
        row.role = "member"
    mine = membership(session, account.id, department.id)
    if mine is None:
        session.add(AccountDepartment(account_id=account.id, department_id=department.id, role="head"))
    else:
        mine.role = "head"
    session.flush()
    _record(session, department, actor_id, {"headAccountIds": previous}, {"action": "head_set", "headAccountId": account.id})
    return True


def remove_member(session: Session, department: Department, account: Account, actor_id: int | None = None) -> None:
    """소속을 지운다. 소속이 없으면 오류. 부서장은 먼저 다른 사람을 부서장으로 지정해야 한다."""
    row = membership(session, account.id, department.id)
    if row is None:
        raise OrgError("그 부서에 소속되어 있지 않습니다.")
    if row.role == "head":
        raise OrgError("부서장은 소속을 지울 수 없습니다. 먼저 다른 사람을 부서장으로 지정하세요.")
    session.delete(row)
    session.flush()
    _record(session, department, actor_id, {"accountId": account.id, "role": row.role}, {"action": "member_removed"})


def members_of(session: Session, department_id: int, *, active_only: bool = True) -> list[tuple[Account, str]]:
    """(계정, 역할) 목록. 부서장이 먼저, 그다음 이름순."""
    stmt = (
        select(Account, AccountDepartment.role)
        .join(AccountDepartment, AccountDepartment.account_id == Account.id)
        .where(AccountDepartment.department_id == department_id)
    )
    if active_only:
        stmt = stmt.where(Account.is_active.is_(True))
    rows = [(account, role) for account, role in session.execute(stmt)]
    rows.sort(key=lambda r: (r[1] != "head", r[0].name, r[0].id))
    return rows


def my_memberships(session: Session, account_id: int) -> list[tuple[Department, str]]:
    rows = session.execute(
        select(Department, AccountDepartment.role)
        .join(AccountDepartment, AccountDepartment.department_id == Department.id)
        .where(AccountDepartment.account_id == account_id)
        .order_by(Department.name, Department.id)
    )
    return [(department, role) for department, role in rows]
