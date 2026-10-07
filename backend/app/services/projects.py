"""프로젝트 서비스: 등록·승인·반려, 참여자 관리, 열람 조건, 참여자 후보.

- 부서장이 등록하면 바로 active(등록자가 총괄). 부서원이 등록하면 pending_approval 이고, 그 부서의 부서장이 승인하면 active 가 되며 승인자가 총괄이 된다.
- 총괄·관리자 역할은 관리자 이상 직급(manager·executive)만. 참여자(member)는 모든 직급이 가능하다.
- 변경은 공통 이력(events, 대상 종류 "project")에 남긴다. 커밋은 호출부가 한다.
- 회의록과의 연결·열람 규칙은 다음 작업(66-3)이며 이 모듈은 회의록 열람 판정(access.py)을 바꾸지 않는다.
"""
from sqlalchemy import ColumnElement, and_, exists, or_, select, true
from sqlalchemy.orm import Session, aliased
from fastapi import HTTPException, status

from app.auth.access import VIEW_ALL_RANKS
from app.auth.deps import RANK_ORDER
from app.auth.scope import scoped
from app.models import Account, AccountDepartment, Department, Project, ProjectMember
from app.models.common import utcnow
from app.services.history import (
    KIND_PROJECT_APPROVED, KIND_PROJECT_LEAD_CHANGED, KIND_PROJECT_MEMBER_ADDED, KIND_PROJECT_MEMBER_REMOVED, KIND_PROJECT_MEMBER_ROLE,
    KIND_PROJECT_REGISTERED, KIND_PROJECT_REJECTED, record_change,
)
from app.services.org import department_head, find_department, members_of, membership

MANAGER_ROLES = ("lead", "manager")
LEAD_MIN_RANK = "manager"


def _http(code: int, detail: str) -> HTTPException:
    return HTTPException(status_code=code, detail=detail)


def _record(session: Session, project: Project, actor_id: int, kind: str, before: dict, after: dict) -> None:
    record_change(session, tenant_id=project.tenant_id, target_type="project", target_id=project.id, kind=kind,
                  actor_id=actor_id, before=before, after=after)


def _is_manager_rank(account: Account) -> bool:
    return RANK_ORDER.get(account.rank, -1) >= RANK_ORDER[LEAD_MIN_RANK]


# ---------------- 열람 ----------------
def approver_condition(account: Account) -> ColumnElement[bool]:
    """이 계정이 프로젝트 등록 부서의 부서장(= 승인자)인 조건."""
    head = aliased(AccountDepartment)
    return exists().where(head.department_id == Project.department_id, head.account_id == account.id, head.role == "head")


def project_visibility(account: Account) -> ColumnElement[bool]:
    """Project 조회에 붙일 열람 조건(고객사 범위 포함). 참여자·등록자·승인자만 보고, 관리자 이상은 같은 고객사 전체를 본다.
    관리자 이상 전체 열람은 기존 회의록 열람(VIEW_ALL_RANKS)과 같은 방식이며, 부서 계층이 들어오면 여기서 좁힌다."""
    same_tenant = Project.tenant_id == account.tenant_id
    if account.rank in VIEW_ALL_RANKS:
        return and_(same_tenant, true())
    member = aliased(ProjectMember)
    is_member = exists().where(member.project_id == Project.id, member.account_id == account.id)
    return and_(same_tenant, or_(is_member, Project.registered_by == account.id, approver_condition(account)))


def get_visible_project(session: Session, account: Account, project_id: int) -> Project | None:
    return session.scalar(scoped(select(Project), account).where(Project.id == project_id, project_visibility(account)))


def member_row(session: Session, project_id: int, account_id: int) -> ProjectMember | None:
    return session.scalar(select(ProjectMember).where(ProjectMember.project_id == project_id, ProjectMember.account_id == account_id))


def my_role(session: Session, project_id: int, account_id: int) -> str | None:
    row = member_row(session, project_id, account_id)
    return row.role if row is not None else None


def is_approver(session: Session, account: Account, project: Project) -> bool:
    row = membership(session, account.id, project.department_id)
    return row is not None and row.role == "head"


def is_project_lead(session: Session, account: Account, project: Project) -> bool:
    return my_role(session, project.id, account.id) == "lead"


# ---------------- 등록 ----------------
def _valid_member_accounts(session: Session, account: Account, ids: list[int]) -> list[Account]:
    """같은 고객사 활성 계정만. 다른 고객사·없는·비활성 계정은 구분하지 않고 같은 400(존재 여부를 알려 주지 않음)."""
    unique = list(dict.fromkeys(ids))
    if not unique:
        return []
    found = session.scalars(
        select(Account).where(Account.id.in_(unique), Account.tenant_id == account.tenant_id, Account.is_active.is_(True))
    ).all()
    if len(found) != len(unique):
        raise _http(status.HTTP_400_BAD_REQUEST, "같은 고객사의 활성 계정만 참여자로 지정할 수 있습니다.")
    by_id = {a.id: a for a in found}
    return [by_id[i] for i in unique]


def register_project(
    session: Session, account: Account, *, name: str, description: str, department_id: int, member_ids: list[int]
) -> Project:
    department = find_department(session, account.tenant_id, department_id)
    if department is None:
        raise _http(status.HTTP_404_NOT_FOUND, "부서를 찾을 수 없습니다")
    mine = membership(session, account.id, department.id)
    if mine is None:
        raise _http(status.HTTP_403_FORBIDDEN, "소속된 부서로만 프로젝트를 등록할 수 있습니다")
    extra = _valid_member_accounts(session, account, member_ids)

    if mine.role == "head":
        if not _is_manager_rank(account):
            raise _http(status.HTTP_409_CONFLICT, "프로젝트 총괄은 관리자 이상 직급이어야 합니다")
        state, lead_id, my_role_name = "active", account.id, "lead"
    else:
        if department_head(session, department.id) is None:
            raise _http(status.HTTP_409_CONFLICT, "부서장이 지정되지 않아 등록할 수 없습니다")
        state, lead_id, my_role_name = "pending_approval", None, "member"

    project = Project(
        tenant_id=account.tenant_id, department_id=department.id, name=name, description=description,
        status=state, registered_by=account.id, lead_account_id=lead_id,
    )
    session.add(project)
    session.flush()
    session.add(ProjectMember(project_id=project.id, account_id=account.id, role=my_role_name, added_by=account.id))
    for other in extra:
        if other.id != account.id:
            session.add(ProjectMember(project_id=project.id, account_id=other.id, role="member", added_by=account.id))
    session.flush()
    _record(session, project, account.id, KIND_PROJECT_REGISTERED, {}, {
        "status": state, "name": name, "departmentId": department.id, "memberIds": [a.id for a in extra if a.id != account.id],
    })
    return project


# ---------------- 승인·반려 ----------------
def _require_approver(session: Session, account: Account, project: Project) -> None:
    if not is_approver(session, account, project):
        raise _http(status.HTTP_403_FORBIDDEN, "등록 부서의 부서장만 처리할 수 있습니다")


def approve_project(session: Session, account: Account, project: Project) -> bool:
    """승인한다. 이미 active 면 변경 없이 False. 승인자가 총괄(lead)이 되고 등록자는 member 로 남는다."""
    _require_approver(session, account, project)
    if project.status == "active":
        return False
    if project.status != "pending_approval":
        raise _http(status.HTTP_409_CONFLICT, "승인 대기 중인 프로젝트만 승인할 수 있습니다")
    if not _is_manager_rank(account):
        raise _http(status.HTTP_409_CONFLICT, "프로젝트 총괄은 관리자 이상 직급이어야 합니다")
    now = utcnow()
    project.status, project.lead_account_id, project.decided_by, project.decided_at = "active", account.id, account.id, now
    row = member_row(session, project.id, account.id)
    if row is None:
        session.add(ProjectMember(project_id=project.id, account_id=account.id, role="lead", added_by=account.id))
    else:
        row.role = "lead"
    session.flush()
    _record(session, project, account.id, KIND_PROJECT_APPROVED, {"status": "pending_approval"}, {"status": "active", "leadAccountId": account.id})
    return True


def reject_project(session: Session, account: Account, project: Project, reason: str) -> None:
    _require_approver(session, account, project)
    if project.status != "pending_approval":
        raise _http(status.HTTP_409_CONFLICT, "승인 대기 중인 프로젝트만 반려할 수 있습니다")
    project.status, project.decided_by, project.decided_at = "rejected", account.id, utcnow()
    session.flush()
    _record(session, project, account.id, KIND_PROJECT_REJECTED, {"status": "pending_approval"}, {"status": "rejected", "reason": reason})


# ---------------- 참여자 관리 ----------------
def _require_lead_and_active(session: Session, account: Account, project: Project) -> None:
    if not is_project_lead(session, account, project):
        raise _http(status.HTTP_403_FORBIDDEN, "프로젝트 총괄만 참여자를 관리할 수 있습니다")
    if project.status != "active":
        raise _http(status.HTTP_409_CONFLICT, "진행 중인 프로젝트만 참여자를 바꿀 수 있습니다")


def add_project_member(session: Session, account: Account, project: Project, target_id: int, role: str) -> str:
    """참여자를 추가하거나 역할만 바꾼다. 돌려주는 값: "added" / "changed" / "unchanged"."""
    _require_lead_and_active(session, account, project)
    targets = _valid_member_accounts(session, account, [target_id])
    target = targets[0]
    if role in MANAGER_ROLES and not _is_manager_rank(target):
        raise _http(status.HTTP_409_CONFLICT, "총괄·관리자 역할은 관리자 이상 직급만 맡을 수 있습니다")
    row = member_row(session, project.id, target.id)
    if row is None:
        session.add(ProjectMember(project_id=project.id, account_id=target.id, role=role, added_by=account.id))
        session.flush()
        _record(session, project, account.id, KIND_PROJECT_MEMBER_ADDED, {}, {"accountId": target.id, "role": role})
        return "added"
    if row.role == role:
        return "unchanged"
    if target.id == project.lead_account_id:
        raise _http(status.HTTP_409_CONFLICT, "프로젝트 총괄의 역할은 바꿀 수 없습니다")
    before = row.role
    row.role = role
    session.flush()
    _record(session, project, account.id, KIND_PROJECT_MEMBER_ROLE, {"accountId": target.id, "role": before}, {"accountId": target.id, "role": role})
    return "changed"


def remove_project_member(session: Session, account: Account, project: Project, target_id: int) -> None:
    _require_lead_and_active(session, account, project)
    row = member_row(session, project.id, target_id)
    if row is None:
        raise _http(status.HTTP_404_NOT_FOUND, "참여자를 찾을 수 없습니다")
    if row.role == "lead":
        raise _http(status.HTTP_409_CONFLICT, "총괄은 제거할 수 없습니다")
    session.delete(row)
    session.flush()
    _record(session, project, account.id, KIND_PROJECT_MEMBER_REMOVED, {"accountId": target_id, "role": row.role}, {})


# ---------------- 총괄 변경 ----------------
def change_project_lead(session: Session, account: Account, project: Project, new_lead_id: int, reason: str) -> bool:
    """총괄을 바꾼다(현재 총괄 본인 또는 지시자만). 이미 그 계정이 총괄이면 변경 없이 False.
    새 총괄은 lead 로 만들고(참여자가 아니면 추가) 이전 총괄은 manager 참여자로 남긴다. 이미 확정된 건은 건드리지 않는다. 커밋은 호출부."""
    if not (account.rank == "executive" or project.lead_account_id == account.id):
        raise _http(status.HTTP_403_FORBIDDEN, "현재 총괄 또는 지시자만 총괄을 바꿀 수 있습니다")
    if project.status != "active":
        raise _http(status.HTTP_409_CONFLICT, "진행 중인 프로젝트만 총괄을 바꿀 수 있습니다")
    target = _valid_member_accounts(session, account, [new_lead_id])[0]
    if not _is_manager_rank(target):
        raise _http(status.HTTP_409_CONFLICT, "프로젝트 총괄은 관리자 이상 직급이어야 합니다")
    previous_id = project.lead_account_id
    if previous_id == target.id:
        return False
    if previous_id is not None:
        old_row = member_row(session, project.id, previous_id)
        if old_row is not None:
            old_row.role = "manager"
    row = member_row(session, project.id, target.id)
    if row is None:
        session.add(ProjectMember(project_id=project.id, account_id=target.id, role="lead", added_by=account.id))
    else:
        row.role = "lead"
    project.lead_account_id = target.id
    session.flush()
    _record(session, project, account.id, KIND_PROJECT_LEAD_CHANGED, {"leadAccountId": previous_id}, {"leadAccountId": target.id, "reason": reason})
    return True


# ---------------- 참여자 후보 ----------------
def candidate_groups(session: Session, account: Account, department_id: int) -> dict:
    """참여자 후보: 자기 부서원, 타 부서의 부서별 인원, 임원 그룹. 내가 소속된 부서만 기준으로 삼을 수 있다.
    각 구성원은 (계정, 역할) 중 계정만 쓴다(응답 형식은 호출부)."""
    department = find_department(session, account.tenant_id, department_id)
    if department is None:
        raise _http(status.HTTP_404_NOT_FOUND, "부서를 찾을 수 없습니다")
    if membership(session, account.id, department.id) is None:
        raise _http(status.HTTP_403_FORBIDDEN, "소속된 부서만 선택할 수 있습니다")
    others = session.scalars(
        select(Department).where(Department.tenant_id == account.tenant_id, Department.id != department.id).order_by(Department.name, Department.id)
    ).all()
    return {
        "own": (department, [a for a, _ in members_of(session, department.id)]),
        "others": [(d, [a for a, _ in members_of(session, d.id)]) for d in others if d.kind != "executive"],
        "executive": [(d, [a for a, _ in members_of(session, d.id)]) for d in others if d.kind == "executive"],
    }
