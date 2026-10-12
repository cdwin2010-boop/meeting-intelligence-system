"""시스템 관리자 지정(작업 82-1). 사용자 등급(rank)과 별개이며 별도 표 system_admins 로 지정한다.
- 지정 대상은 활성 계정이고 사용권한이 staff 인 경우만(전용 계정). 이미 상위 권한이면 거부하고 안내한다.
- 지정·해제는 공통 이력(대상 종류 account)에 남긴다. 커밋은 호출부가 한다(변경과 이력이 같은 트랜잭션).
- 회의록 내용 열람 거부 규칙 자체는 app/auth/access.py 의 not_system_admin 한 곳에 있다."""
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Account, SystemAdmin
from app.services.history import KIND_SYSTEM_ADMIN_SET, KIND_SYSTEM_ADMIN_UNSET, record_change


class SystemAdminError(ValueError):
    """지정·해제 규칙 위반(메시지는 사람이 읽는 한국어)."""


def is_system_admin(session: Session, account_id: int) -> bool:
    return session.scalar(select(SystemAdmin.id).where(SystemAdmin.account_id == account_id)) is not None


def designate(session: Session, account: Account, actor_id: int | None = None) -> SystemAdmin:
    """계정을 시스템 관리자로 지정한다. 이미 지정된 계정이면 SystemAdminError."""
    if not account.is_active:
        raise SystemAdminError("비활성 계정은 시스템 관리자로 지정할 수 없습니다.")
    if account.rank != "staff":
        raise SystemAdminError("시스템 관리자는 사용권한이 담당자(staff)인 전용 계정으로 둡니다. 상위 권한 계정은 지정할 수 없습니다.")
    if is_system_admin(session, account.id):
        raise SystemAdminError("이미 시스템 관리자로 지정된 계정입니다.")
    row = SystemAdmin(tenant_id=account.tenant_id, account_id=account.id, created_by=actor_id)
    session.add(row)
    session.flush()
    record_change(
        session, tenant_id=account.tenant_id, target_type="account", target_id=account.id, kind=KIND_SYSTEM_ADMIN_SET,
        actor_id=actor_id, before={"systemAdmin": False}, after={"systemAdmin": True},
        extra={"actor": "시스템"} if actor_id is None else None,  # 최초 지정 도구가 실행한 경우 실행자 표시
    )
    return row


def revoke(session: Session, account: Account, actor_id: int | None = None) -> None:
    """시스템 관리자 지정을 해제한다. 지정되어 있지 않으면 SystemAdminError."""
    row = session.scalar(select(SystemAdmin).where(SystemAdmin.account_id == account.id))
    if row is None:
        raise SystemAdminError("시스템 관리자로 지정된 계정이 아닙니다.")
    session.delete(row)
    session.flush()
    record_change(
        session, tenant_id=account.tenant_id, target_type="account", target_id=account.id, kind=KIND_SYSTEM_ADMIN_UNSET,
        actor_id=actor_id, before={"systemAdmin": True}, after={"systemAdmin": False},
    )


def count_system_admins(session: Session, tenant_id: int) -> int:
    return len(session.scalars(select(SystemAdmin.id).where(SystemAdmin.tenant_id == tenant_id)).all())
