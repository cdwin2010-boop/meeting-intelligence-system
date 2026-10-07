"""조직 조회 API(읽기 전용, 로그인 필요, 같은 고객사만). 등록·수정 API 는 없다(시스템 관리자 기능 때 만든다. 지금은 scripts/manage_org.py).
응답에는 이메일·로그인 ID·비밀번호 관련 값을 두지 않는다(계정은 id·이름·직급만)."""
from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.schemas import CamelModel
from app.auth.deps import get_current_account
from app.db import get_session
from app.models import Account, Department
from app.services.org import members_of, my_memberships

router = APIRouter(prefix="/api/departments", tags=["departments"])
me_router = APIRouter(prefix="/api/me/org", tags=["departments"])


class MemberOut(CamelModel):
    account_id: int
    name: str
    rank: str
    role: str


class DepartmentOut(CamelModel):
    id: int
    name: str
    parent_id: int | None
    kind: str
    head: MemberOut | None
    members: list[MemberOut]


class MyDepartmentOut(CamelModel):
    department_id: int
    name: str
    kind: str
    role: str


@router.get("", response_model=list[DepartmentOut], response_model_by_alias=True)
def list_departments(account: Account = Depends(get_current_account), session: Session = Depends(get_session)) -> list[DepartmentOut]:
    """같은 고객사의 부서(상하 관계는 parentId), 각 부서의 부서장과 구성원(활성 계정만, 부서장 먼저)."""
    departments = session.scalars(
        select(Department).where(Department.tenant_id == account.tenant_id).order_by(Department.name, Department.id)
    ).all()
    result = []
    for department in departments:
        members = [MemberOut(account_id=a.id, name=a.name, rank=a.rank, role=role) for a, role in members_of(session, department.id)]
        head = next((m for m in members if m.role == "head"), None)
        result.append(DepartmentOut(id=department.id, name=department.name, parent_id=department.parent_id, kind=department.kind, head=head, members=members))
    return result


@me_router.get("", response_model=list[MyDepartmentOut], response_model_by_alias=True)
def my_org(account: Account = Depends(get_current_account), session: Session = Depends(get_session)) -> list[MyDepartmentOut]:
    """내 소속 부서와 부서별 내 역할."""
    return [
        MyDepartmentOut(department_id=d.id, name=d.name, kind=d.kind, role=role)
        for d, role in my_memberships(session, account.id)
    ]
