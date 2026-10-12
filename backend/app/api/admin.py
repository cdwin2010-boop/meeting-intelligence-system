"""시스템 관리자 전용 관리 API(작업 82-1 파트 B). 모든 경로는 require_system_admin 이며 같은 고객사 범위만 다룬다.
회의록 내용은 다루지 않는다(계정·부서·소속·시스템 관리자 지정·감사 이력). 삭제 API 는 없다(계정은 비활성화).
규칙은 서비스(services/admin_accounts.py, services/org.py)에 있고 여기서는 입력 검증과 오류 코드 변환만 한다.
오류 코드: 404 없음(타 고객사 포함), 409 규칙 충돌, 422 입력 검증, 403 시스템 관리자가 아님."""
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import Field, StrictBool
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.schemas import CamelModel
from app.config import settings
from app.auth.deps import require_system_admin
from app.db import get_session
from app.models import Account, AccountDepartment, Department, Event
from app.services import admin_accounts as svc
from app.services import system_settings as sys_settings
from app.services.history import ADMIN_AUDIT_KINDS, HISTORY_KINDS
from app.services.org import (
    UNSET, OrgError, add_member, create_department, find_department, members_of, remove_member, set_head, update_department,
)
from app.services.system_admin import is_system_admin

router = APIRouter(prefix="/api/admin", tags=["admin"], dependencies=[Depends(require_system_admin)])



def _raise(exc: Exception) -> HTTPException:
    if isinstance(exc, svc.AdminError):
        return HTTPException(status_code=exc.status, detail=exc.message)
    if isinstance(exc, OrgError):
        return HTTPException(status_code=exc.status, detail=str(exc))
    raise exc


# ---------------- 계정 ----------------
class DepartmentBrief(CamelModel):
    id: int
    name: str
    role: str


class AccountAdminOut(CamelModel):
    id: int
    login_id: str
    name: str
    email: str
    rank: str
    is_active: bool
    is_system_admin: bool
    departments: list[DepartmentBrief]
    created_at: datetime


class AccountPage(CamelModel):
    items: list[AccountAdminOut]
    total: int
    page: int
    size: int


class AccountCreate(CamelModel):
    login_id: str
    name: str
    email: str = ""
    rank: str
    password: str = Field(repr=False)


class AccountPatch(CamelModel):
    name: str | None = None
    email: str | None = None
    rank: str | None = None
    is_active: bool | None = None


class PasswordReset(CamelModel):
    new_password: str = Field(repr=False)


class SystemAdminFlag(CamelModel):
    enabled: bool


def _account_out(row: svc.AccountRow) -> AccountAdminOut:
    a = row.account
    return AccountAdminOut(
        id=a.id, login_id=a.login_id, name=a.name, email=a.email, rank=a.rank, is_active=a.is_active,
        is_system_admin=row.is_system_admin, created_at=a.created_at,
        departments=[DepartmentBrief(id=d.id, name=d.name, role=role) for d, role in row.departments],
    )


def _one(session: Session, tenant_id: int, account_id: int) -> AccountAdminOut:
    account = svc.find_account(session, tenant_id, account_id)
    memberships = session.execute(
        select(Department, AccountDepartment.role)
        .join(AccountDepartment, AccountDepartment.department_id == Department.id)
        .where(AccountDepartment.account_id == account.id).order_by(Department.name, Department.id)
    ).all()
    return _account_out(svc.AccountRow(account, [(d, r) for d, r in memberships], is_system_admin(session, account.id)))


@router.get("/accounts", response_model=AccountPage, response_model_by_alias=True)
def list_accounts(
    q: str = Query("", max_length=100), page: int = Query(1, ge=1), size: int = Query(20, ge=1, le=100),
    admin: Account = Depends(require_system_admin), session: Session = Depends(get_session),
) -> AccountPage:
    rows, total = svc.list_accounts(session, admin.tenant_id, q=q, page=page, size=size)
    return AccountPage(items=[_account_out(r) for r in rows], total=total, page=page, size=size)


@router.post("/accounts", status_code=201, response_model=AccountAdminOut, response_model_by_alias=True)
def create_account(body: AccountCreate, admin: Account = Depends(require_system_admin), session: Session = Depends(get_session)) -> AccountAdminOut:
    try:
        account = svc.create_account(
            session, tenant_id=admin.tenant_id, actor_id=admin.id, login_id=body.login_id, name=body.name, email=body.email,
            rank=body.rank, password=body.password,
        )
        session.commit()
    except svc.AdminError as exc:
        session.rollback()
        raise _raise(exc) from None
    return _one(session, admin.tenant_id, account.id)


@router.patch("/accounts/{account_id}", response_model=AccountAdminOut, response_model_by_alias=True)
def patch_account(
    account_id: int, body: AccountPatch, admin: Account = Depends(require_system_admin), session: Session = Depends(get_session)
) -> AccountAdminOut:
    try:
        target = svc.find_account(session, admin.tenant_id, account_id)
        svc.update_account(
            session, actor=admin, target=target, name=body.name, email=body.email, rank=body.rank, is_active=body.is_active
        )
        session.commit()
    except svc.AdminError as exc:
        session.rollback()
        raise _raise(exc) from None
    return _one(session, admin.tenant_id, account_id)


@router.post("/accounts/{account_id}/password-reset", status_code=204)
def reset_password(
    account_id: int, body: PasswordReset, admin: Account = Depends(require_system_admin), session: Session = Depends(get_session)
) -> None:
    try:
        target = svc.find_account(session, admin.tenant_id, account_id)
        svc.reset_password(session, actor=admin, target=target, new_password=body.new_password)
        session.commit()
    except svc.AdminError as exc:
        session.rollback()
        raise _raise(exc) from None


@router.put("/accounts/{account_id}/system-admin", response_model=AccountAdminOut, response_model_by_alias=True)
def put_system_admin(
    account_id: int, body: SystemAdminFlag, admin: Account = Depends(require_system_admin), session: Session = Depends(get_session)
) -> AccountAdminOut:
    try:
        target = svc.find_account(session, admin.tenant_id, account_id)
        svc.set_system_admin(session, actor=admin, target=target, enabled=body.enabled)
        session.commit()
    except svc.AdminError as exc:
        session.rollback()
        raise _raise(exc) from None
    return _one(session, admin.tenant_id, account_id)


# ---------------- 부서·소속 ----------------
class AdminMemberOut(CamelModel):
    account_id: int
    name: str
    rank: str
    role: str
    is_active: bool


class AdminDepartmentOut(CamelModel):
    id: int
    name: str
    parent_id: int | None
    kind: str
    head: AdminMemberOut | None
    members: list[AdminMemberOut]


class DepartmentCreate(CamelModel):
    name: str
    parent_id: int | None = None
    is_executive_group: bool = False


class DepartmentPatch(CamelModel):
    name: str | None = None
    parent_id: int | None = None


class AccountIdBody(CamelModel):
    account_id: int


def _department_out(session: Session, department: Department) -> AdminDepartmentOut:
    members = [
        AdminMemberOut(account_id=a.id, name=a.name, rank=a.rank, role=role, is_active=a.is_active)
        for a, role in members_of(session, department.id, active_only=False)
    ]
    head = next((m for m in members if m.role == "head"), None)
    return AdminDepartmentOut(
        id=department.id, name=department.name, parent_id=department.parent_id, kind=department.kind, head=head, members=members
    )


def _department(session: Session, admin: Account, department_id: int) -> Department:
    department = find_department(session, admin.tenant_id, department_id)
    if department is None:
        raise HTTPException(status_code=404, detail="부서를 찾을 수 없습니다.")
    return department


@router.get("/departments", response_model=list[AdminDepartmentOut], response_model_by_alias=True)
def list_departments(admin: Account = Depends(require_system_admin), session: Session = Depends(get_session)) -> list[AdminDepartmentOut]:
    """부서 트리(parentId 로 연결, 이름순). 구성원은 비활성 계정도 표시한다(isActive)."""
    rows = session.scalars(select(Department).where(Department.tenant_id == admin.tenant_id).order_by(Department.name, Department.id)).all()
    return [_department_out(session, d) for d in rows]


@router.post("/departments", status_code=201, response_model=AdminDepartmentOut, response_model_by_alias=True)
def post_department(
    body: DepartmentCreate, admin: Account = Depends(require_system_admin), session: Session = Depends(get_session)
) -> AdminDepartmentOut:
    try:
        department = create_department(
            session, tenant_id=admin.tenant_id, name=body.name, parent_id=body.parent_id,
            kind="executive" if body.is_executive_group else "normal", actor_id=admin.id,
        )
        session.commit()
    except OrgError as exc:
        session.rollback()
        raise _raise(exc) from None
    return _department_out(session, department)


@router.patch("/departments/{department_id}", response_model=AdminDepartmentOut, response_model_by_alias=True)
def patch_department(
    department_id: int, body: DepartmentPatch, admin: Account = Depends(require_system_admin), session: Session = Depends(get_session)
) -> AdminDepartmentOut:
    department = _department(session, admin, department_id)
    try:
        update_department(
            session, department, actor_id=admin.id, name=body.name,
            parent_id=body.parent_id if "parent_id" in body.model_fields_set else UNSET,
        )
        session.commit()
    except OrgError as exc:
        session.rollback()
        raise _raise(exc) from None
    return _department_out(session, department)


def _member_account(session: Session, admin: Account, account_id: int) -> Account:
    return svc.find_account(session, admin.tenant_id, account_id)


@router.put("/departments/{department_id}/head", response_model=AdminDepartmentOut, response_model_by_alias=True)
def put_head(
    department_id: int, body: AccountIdBody, admin: Account = Depends(require_system_admin), session: Session = Depends(get_session)
) -> AdminDepartmentOut:
    department = _department(session, admin, department_id)
    try:
        set_head(session, department, _member_account(session, admin, body.account_id), admin.id)
        session.commit()
    except (OrgError, svc.AdminError) as exc:
        session.rollback()
        raise _raise(exc) from None
    return _department_out(session, department)


@router.post("/departments/{department_id}/members", response_model=AdminDepartmentOut, response_model_by_alias=True)
def post_member(
    department_id: int, body: AccountIdBody, admin: Account = Depends(require_system_admin), session: Session = Depends(get_session)
) -> AdminDepartmentOut:
    department = _department(session, admin, department_id)
    try:
        add_member(session, department, _member_account(session, admin, body.account_id), admin.id)
        session.commit()
    except (OrgError, svc.AdminError) as exc:
        session.rollback()
        raise _raise(exc) from None
    return _department_out(session, department)


@router.post("/departments/{department_id}/members/{account_id}/remove", response_model=AdminDepartmentOut, response_model_by_alias=True)
def post_member_remove(
    department_id: int, account_id: int, admin: Account = Depends(require_system_admin), session: Session = Depends(get_session)
) -> AdminDepartmentOut:
    department = _department(session, admin, department_id)
    try:
        remove_member(session, department, _member_account(session, admin, account_id), admin.id)
        session.commit()
    except (OrgError, svc.AdminError) as exc:
        session.rollback()
        raise _raise(exc) from None
    return _department_out(session, department)


# ---------------- 감사 이력 ----------------
class AuditEntry(CamelModel):
    id: int
    event_type: str
    label: str
    target_type: str
    target_id: int
    actor: dict[str, Any] | None
    created_at: datetime
    before: dict[str, Any]
    after: dict[str, Any]
    reason: str | None = None


class AuditPage(CamelModel):
    items: list[AuditEntry]
    total: int
    page: int
    size: int


@router.get("/audit", response_model=AuditPage, response_model_by_alias=True)
def get_audit(
    page: int = Query(1, ge=1), size: int = Query(20, ge=1, le=100),
    admin: Account = Depends(require_system_admin), session: Session = Depends(get_session),
) -> AuditPage:
    """관리 변경 이력(최신순). 비밀번호·해시는 이력에 없고, 회의록 내용 이력은 포함하지 않는다."""
    cond = (Event.tenant_id == admin.tenant_id, Event.event_type.in_(sorted(ADMIN_AUDIT_KINDS)))
    total = session.scalar(select(func.count()).select_from(Event).where(*cond)) or 0
    rows = session.execute(
        select(Event, Account.name)
        .outerjoin(Account, Account.id == Event.actor_account_id)
        .where(*cond).order_by(Event.id.desc()).offset((page - 1) * size).limit(size)
    ).all()
    items = []
    for event, actor_name in rows:
        payload = event.payload or {}
        reason = payload.get("reason")
        items.append(AuditEntry(
            id=event.id, event_type=event.event_type, label=HISTORY_KINDS.get(event.event_type, event.event_type),
            target_type=event.entity_type, target_id=event.entity_id,
            actor={"id": event.actor_account_id, "name": actor_name} if event.actor_account_id is not None else None,
            created_at=event.created_at,
            before=payload.get("before") if isinstance(payload.get("before"), dict) else {},
            after=payload.get("after") if isinstance(payload.get("after"), dict) else {},
            reason=reason if isinstance(reason, str) else None,
        ))
    return AuditPage(items=items, total=total, page=page, size=size)


# ---------------- 외부 연동 설정(작업 82-1 파트 C) ----------------
class SettingOut(CamelModel):
    key: str
    value: bool | str  # 참·거짓 키는 bool, 지식 연동 주소(knowledge_api_endpoint)는 문자열
    source: str  # default(기본값) | env(.env 에 명시) | admin(시스템 관리자 설정)
    updated_at: datetime | None
    updated_by: dict[str, Any] | None


class SettingsOut(CamelModel):
    # 지식(KMS) 연동 설정(추가 필드, 작업 83-1): 기존 화면의 items 목록에는 섞지 않고 따로 내려준다
    knowledge: list[SettingOut] = []
    enforced: bool  # EXTERNAL_POLICY_ENFORCED: 거짓이면 이 설정은 아직 처리에 적용되지 않는다(현행 동작)
    items: list[SettingOut]


class SettingPut(CamelModel):
    value: StrictBool | str
    reason: str = ""


def _setting_out(session: Session, item: sys_settings.EffectiveSetting) -> SettingOut:
    actor = session.get(Account, item.updated_by) if item.updated_by is not None else None
    return SettingOut(
        key=item.key, value=item.text if item.text is not None else item.value, source=item.source, updated_at=item.updated_at,
        updated_by={"id": actor.id, "name": actor.name} if actor is not None else None,
    )


@router.get("/settings", response_model=SettingsOut, response_model_by_alias=True)
def get_settings(admin: Account = Depends(require_system_admin), session: Session = Depends(get_session)) -> SettingsOut:
    """현재 유효값과 출처. 키·주소 같은 비밀 값은 다루지 않는다(참·거짓 두 개뿐)."""
    return SettingsOut(
        enforced=settings.external_policy_enforced,
        items=[_setting_out(session, i) for i in sys_settings.all_effective(session, admin.tenant_id)],
        knowledge=[_setting_out(session, i) for i in sys_settings.knowledge_effective(session, admin.tenant_id)],
    )


@router.put("/settings/{key}", response_model=SettingOut, response_model_by_alias=True)
def put_setting(
    key: str, body: SettingPut, admin: Account = Depends(require_system_admin), session: Session = Depends(get_session)
) -> SettingOut:
    """참·거짓 변경(사유 1~500자 필수, 이력에 기록). 즉시 반영된다(재시작 불필요). 허용 목록 밖 키는 404."""
    try:
        item = sys_settings.set_value(
            session, tenant_id=admin.tenant_id, key=key, value=body.value, reason=body.reason, actor_id=admin.id
        )
        session.commit()
    except sys_settings.SettingError as exc:
        session.rollback()
        raise HTTPException(status_code=exc.status, detail=str(exc)) from None
    return _setting_out(session, item)
