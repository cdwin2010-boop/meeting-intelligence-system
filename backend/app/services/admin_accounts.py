"""시스템 관리자의 계정 관리 서비스(작업 82-1 파트 B). 규칙은 이 모듈 한 곳에 두고 API(app/api/admin.py)는 얇게 부른다.

- 삭제는 없다(비활성화로 처리). 비밀번호는 argon2 해시만 저장하고 이력·응답·로그에 평문·해시를 남기지 않는다.
- 변경은 공통 이력(대상 종류 account)에 변경 전·후 값(비밀번호·해시 제외)·실행자와 함께 남긴다. 커밋은 호출부가 한다.
- 부서·소속 규칙은 services/org.py 를 그대로 쓴다(이 모듈은 부서장 제약만 org 조회로 확인한다).
"""
from dataclasses import dataclass

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.auth.deps import RANK_ORDER
from app.auth.passwords import hash_password, verify_password
from app.config import settings
from app.models import Account, AccountDepartment, Department, SystemAdmin
from app.models.common import ACCOUNT_RANKS
from app.services.history import (
    KIND_ACCOUNT_CREATED, KIND_ACCOUNT_PASSWORD_CHANGED, KIND_ACCOUNT_PASSWORD_RESET, KIND_ACCOUNT_UPDATED, record_change,
)
from app.services.org import HEAD_MIN_RANK
from app.services.system_admin import designate, is_system_admin, revoke

LOGIN_ID_MAX = 100
NAME_MAX = 100
EMAIL_MAX = 255
PASSWORD_MAX = 128


class AdminError(Exception):
    """관리 규칙 위반. status 는 API 응답 코드(404·409·422), message 는 사람이 읽는 한국어."""

    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


def check_password_policy(password: str) -> None:
    minimum = settings.password_min_length
    if len(password) < minimum:
        raise AdminError(422, f"비밀번호는 {minimum}자 이상이어야 합니다.")
    if len(password) > PASSWORD_MAX:
        raise AdminError(422, f"비밀번호는 {PASSWORD_MAX}자 이하여야 합니다.")


def _clean(value: str | None, label: str, maximum: int, *, required: bool) -> str:
    text = (value or "").strip()
    if required and not text:
        raise AdminError(422, f"{label}을(를) 입력하세요.")
    if len(text) > maximum:
        raise AdminError(422, f"{label}은(는) {maximum}자 이하여야 합니다.")
    return text


def _check_rank(rank: str) -> str:
    if rank not in ACCOUNT_RANKS:
        raise AdminError(422, f"사용권한은 {', '.join(ACCOUNT_RANKS)} 중 하나여야 합니다.")
    return rank


def find_account(session: Session, tenant_id: int, account_id: int) -> Account:
    account = session.scalar(select(Account).where(Account.id == account_id, Account.tenant_id == tenant_id))
    if account is None:
        raise AdminError(404, "계정을 찾을 수 없습니다.")
    return account


def _snapshot(account: Account) -> dict:
    """이력에 남길 계정 값(비밀번호·해시 제외)."""
    return {"name": account.name, "email": account.email, "rank": account.rank, "isActive": account.is_active}


# ---------------- 조회 ----------------
@dataclass
class AccountRow:
    account: Account
    departments: list[tuple[Department, str]]
    is_system_admin: bool


def list_accounts(session: Session, tenant_id: int, *, q: str = "", page: int = 1, size: int = 20) -> tuple[list[AccountRow], int]:
    stmt = select(Account).where(Account.tenant_id == tenant_id)
    keyword = q.strip()
    if keyword:
        like = f"%{keyword}%"
        stmt = stmt.where(or_(Account.login_id.like(like), Account.name.like(like), Account.email.like(like)))
    total = session.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = session.scalars(stmt.order_by(Account.name, Account.id).offset((page - 1) * size).limit(size)).all()
    ids = [a.id for a in rows]
    memberships: dict[int, list[tuple[Department, str]]] = {i: [] for i in ids}
    admins: set[int] = set()
    if ids:
        for account_id, department, role in session.execute(
            select(AccountDepartment.account_id, Department, AccountDepartment.role)
            .join(Department, Department.id == AccountDepartment.department_id)
            .where(AccountDepartment.account_id.in_(ids))
            .order_by(Department.name, Department.id)
        ):
            memberships[account_id].append((department, role))
        admins = set(session.scalars(select(SystemAdmin.account_id).where(SystemAdmin.account_id.in_(ids))))
    return [AccountRow(a, memberships[a.id], a.id in admins) for a in rows], total


# ---------------- 변경 ----------------
def create_account(
    session: Session, *, tenant_id: int, actor_id: int, login_id: str, name: str, email: str | None, rank: str, password: str,
) -> Account:
    login_id = _clean(login_id, "로그인 ID", LOGIN_ID_MAX, required=True)
    name = _clean(name, "이름", NAME_MAX, required=True)
    email = _clean(email, "이메일", EMAIL_MAX, required=False)
    _check_rank(rank)
    check_password_policy(password)
    if session.scalar(select(Account.id).where(Account.login_id == login_id)) is not None:
        raise AdminError(409, "이미 사용 중인 로그인 ID 입니다.")
    account = Account(
        tenant_id=tenant_id, login_id=login_id, password_hash=hash_password(password), name=name, email=email, rank=rank,
    )
    session.add(account)
    session.flush()
    record_change(
        session, tenant_id=tenant_id, target_type="account", target_id=account.id, kind=KIND_ACCOUNT_CREATED, actor_id=actor_id,
        before={}, after={"loginId": login_id, **_snapshot(account)},
    )
    return account


def update_account(
    session: Session, *, actor: Account, target: Account, name: str | None = None, email: str | None = None,
    rank: str | None = None, is_active: bool | None = None,
) -> bool:
    """이름·이메일·사용권한·활성 여부를 바꾼다. 바뀐 것이 있으면 True. 규칙 위반은 AdminError."""
    before = _snapshot(target)
    changes: dict = {}
    if name is not None:
        changes["name"] = _clean(name, "이름", NAME_MAX, required=True)
    if email is not None:
        changes["email"] = _clean(email, "이메일", EMAIL_MAX, required=False)
    if rank is not None:
        changes["rank"] = _check_rank(rank)
    if is_active is not None:
        changes["is_active"] = bool(is_active)
    changes = {k: v for k, v in changes.items() if getattr(target, k) != v}
    if not changes:
        return False
    if actor.id == target.id:
        if "is_active" in changes and changes["is_active"] is False:
            raise AdminError(409, "자기 자신의 계정은 비활성화할 수 없습니다.")
        if "rank" in changes:
            raise AdminError(409, "자기 자신의 사용권한은 바꿀 수 없습니다.")
    if "rank" in changes:
        if is_system_admin(session, target.id) and changes["rank"] != "staff":
            raise AdminError(409, "시스템 관리자 계정의 사용권한은 담당자(staff)로 고정입니다. 먼저 시스템 관리자 지정을 해제하세요.")
        if RANK_ORDER[changes["rank"]] < RANK_ORDER[HEAD_MIN_RANK]:
            heads = session.scalars(
                select(Department.name)
                .join(AccountDepartment, AccountDepartment.department_id == Department.id)
                .where(AccountDepartment.account_id == target.id, AccountDepartment.role == "head")
            ).all()
            if heads:
                raise AdminError(409, f"부서장인 계정은 사용권한을 관리자 미만으로 내릴 수 없습니다. 먼저 부서장을 변경하세요({', '.join(heads)}).")
    for key, value in changes.items():
        setattr(target, key, value)
    session.flush()
    after = _snapshot(target)
    changed_keys = {"name", "email", "rank", "isActive"} & {k for k in after if after[k] != before[k]}
    record_change(
        session, tenant_id=target.tenant_id, target_type="account", target_id=target.id, kind=KIND_ACCOUNT_UPDATED, actor_id=actor.id,
        before={k: before[k] for k in changed_keys}, after={k: after[k] for k in changed_keys},
    )
    return True


def reset_password(session: Session, *, actor: Account, target: Account, new_password: str) -> None:
    """관리자가 새 비밀번호를 정해 준다. 이력에는 사건만 남기고 값·해시는 남기지 않는다."""
    check_password_policy(new_password)
    target.password_hash = hash_password(new_password)
    session.flush()
    record_change(
        session, tenant_id=target.tenant_id, target_type="account", target_id=target.id, kind=KIND_ACCOUNT_PASSWORD_RESET,
        actor_id=actor.id, before={}, after={},
    )


def change_own_password(session: Session, account: Account, current_password: str, new_password: str) -> None:
    """본인 비밀번호 변경. 현재 비밀번호가 다르면 일반 오류(400)로만 답한다."""
    if not verify_password(account.password_hash, current_password):
        raise AdminError(400, "비밀번호를 변경하지 못했습니다.")
    check_password_policy(new_password)
    account.password_hash = hash_password(new_password)
    session.flush()
    record_change(
        session, tenant_id=account.tenant_id, target_type="account", target_id=account.id, kind=KIND_ACCOUNT_PASSWORD_CHANGED,
        actor_id=account.id, before={}, after={},
    )


def set_system_admin(session: Session, *, actor: Account, target: Account, enabled: bool) -> bool:
    """시스템 관리자 지정·해제. 자기 자신의 해제와 마지막 시스템 관리자의 해제는 거부한다(잠김 방지). 바뀌었으면 True."""
    from app.services.system_admin import SystemAdminError, count_system_admins  # noqa: PLC0415

    current = is_system_admin(session, target.id)
    if current == enabled:
        return False
    try:
        if enabled:
            designate(session, target, actor.id)
        else:
            if actor.id == target.id:
                raise AdminError(409, "자기 자신의 시스템 관리자 지정은 해제할 수 없습니다.")
            if count_system_admins(session, target.tenant_id) <= 1:
                raise AdminError(409, "마지막 시스템 관리자는 해제할 수 없습니다.")
            revoke(session, target, actor.id)
    except SystemAdminError as exc:
        raise AdminError(409, str(exc)) from None
    return True
