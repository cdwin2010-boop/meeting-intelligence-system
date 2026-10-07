"""프로젝트 API: 등록·승인·반려, 참여자 관리, 목록·상세, 참여자 후보. 모두 로그인 필요, 같은 고객사 안에서만 동작한다.
- 볼 수 없거나 다른 고객사의 프로젝트는 404(존재 여부를 알리지 않음). 볼 수 있지만 권한이 없으면 403.
- 참여자 제거는 DELETE 대신 POST(CORS 가 DELETE 를 허용하지 않음).
- 응답의 계정은 id·이름·직급만(이메일·로그인 ID 없음)."""
from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from pydantic import Field, field_validator
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.action_schemas import ReasonBody
from app.api.meeting_queries import MeetingStatus, build_meeting_page
from app.api.meeting_schemas import MeetingListPage
from app.api.schemas import CamelModel
from app.auth.deps import get_current_account
from app.db import get_session
from app.models import Account, Department, MeetingClassification, Meeting, Project, ProjectMember
from app.services import projects as svc
from app.services.org import department_head

router = APIRouter(prefix="/api/projects", tags=["projects"])

NAME_MAX = 120
DESCRIPTION_MAX = 2000


class AccountRef(CamelModel):
    id: int
    name: str


class MemberOut(CamelModel):
    account_id: int
    name: str
    rank: str
    role: str


class ProjectOut(CamelModel):
    id: int
    name: str
    description: str
    status: str
    department_id: int
    department_name: str
    registered_by: AccountRef
    lead: AccountRef | None
    approver: AccountRef | None
    my_role: str | None
    member_count: int
    created_at: datetime
    decided_at: datetime | None


class ProjectDetail(ProjectOut):
    members: list[MemberOut]


class ProjectCreate(CamelModel):
    name: str = Field(min_length=1, max_length=NAME_MAX)
    description: str = Field(default="", max_length=DESCRIPTION_MAX)
    department_id: int
    member_ids: list[int] = Field(default_factory=list)

    @field_validator("name")
    @classmethod
    def _name_not_blank(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("name 은 비워 둘 수 없습니다")
        return stripped


class MemberAdd(CamelModel):
    account_id: int
    role: Literal["lead", "manager", "member"] = "member"


class CandidateMember(CamelModel):
    account_id: int
    name: str
    rank: str


class CandidateGroup(CamelModel):
    department_id: int
    department_name: str
    members: list[CandidateMember]


class Candidates(CamelModel):
    own_department: CandidateGroup
    other_departments: list[CandidateGroup]
    executive_group: list[CandidateGroup]


def _ref(account: Account | None) -> AccountRef | None:
    return AccountRef(id=account.id, name=account.name) if account is not None else None


def _out(session: Session, viewer: Account, project: Project, cls=ProjectOut):
    department = session.get(Department, project.department_id)
    registrant = session.get(Account, project.registered_by)
    lead = session.get(Account, project.lead_account_id) if project.lead_account_id is not None else None
    approver = None  # 부서장이 직접 등록한 프로젝트는 승인 절차가 없다
    if project.status == "pending_approval":
        approver = department_head(session, project.department_id)
    elif project.decided_by is not None:
        approver = session.get(Account, project.decided_by)
    count = session.scalar(select(func.count(ProjectMember.id)).where(ProjectMember.project_id == project.id)) or 0
    data = dict(
        id=project.id, name=project.name, description=project.description, status=project.status,
        department_id=project.department_id, department_name=department.name if department else "",
        registered_by=_ref(registrant), lead=_ref(lead), approver=_ref(approver),
        my_role=svc.my_role(session, project.id, viewer.id), member_count=count,
        created_at=project.created_at, decided_at=project.decided_at,
    )
    if cls is ProjectDetail:
        rows = session.execute(
            select(Account, ProjectMember.role).join(ProjectMember, ProjectMember.account_id == Account.id)
            .where(ProjectMember.project_id == project.id).order_by(ProjectMember.id)
        )
        data["members"] = [MemberOut(account_id=a.id, name=a.name, rank=a.rank, role=role) for a, role in rows]
    return cls(**data)


def _visible_or_404(session: Session, account: Account, project_id: int) -> Project:
    project = svc.get_visible_project(session, account, project_id)
    if project is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="프로젝트를 찾을 수 없습니다")
    return project


@router.post("", status_code=status.HTTP_201_CREATED, response_model=ProjectDetail, response_model_by_alias=True)
def create_project(body: ProjectCreate, account: Account = Depends(get_current_account), session: Session = Depends(get_session)) -> ProjectDetail:
    project = svc.register_project(
        session, account, name=body.name, description=body.description.strip(), department_id=body.department_id, member_ids=body.member_ids,
    )
    session.commit()
    return _out(session, account, project, ProjectDetail)


@router.get("", response_model=list[ProjectOut], response_model_by_alias=True)
def list_projects(account: Account = Depends(get_current_account), session: Session = Depends(get_session)) -> list[ProjectOut]:
    """내가 보는 프로젝트(참여자·등록자·승인자, 관리자 이상은 같은 고객사 전체), 최신순."""
    projects = session.scalars(
        select(Project).where(svc.project_visibility(account)).order_by(Project.id.desc())
    ).all()
    return [_out(session, account, p) for p in projects]


# 고정 경로(candidates)는 /{project_id} 보다 먼저 등록한다
@router.get("/candidates", response_model=Candidates, response_model_by_alias=True)
def project_candidates(
    department_id: int = Query(alias="departmentId"),
    account: Account = Depends(get_current_account),
    session: Session = Depends(get_session),
) -> Candidates:
    """참여자 후보: 자기 부서원, 타 부서의 부서별 인원, 임원 그룹(선택적 참여). 내가 소속된 부서의 departmentId 만 허용(아니면 403)."""
    groups = svc.candidate_groups(session, account, department_id)

    def group(department: Department, members: list[Account]) -> CandidateGroup:
        return CandidateGroup(
            department_id=department.id, department_name=department.name,
            members=[CandidateMember(account_id=a.id, name=a.name, rank=a.rank) for a in members],
        )

    return Candidates(
        own_department=group(*groups["own"]),
        other_departments=[group(d, m) for d, m in groups["others"]],
        executive_group=[group(d, m) for d, m in groups["executive"]],
    )


@router.get("/{project_id}", response_model=ProjectDetail, response_model_by_alias=True)
def get_project(project_id: int, account: Account = Depends(get_current_account), session: Session = Depends(get_session)) -> ProjectDetail:
    return _out(session, account, _visible_or_404(session, account, project_id), ProjectDetail)


@router.get("/{project_id}/meetings", response_model=MeetingListPage, response_model_by_alias=True)
def project_meetings(
    project_id: int,
    status_filter: MeetingStatus | None = Query(None, alias="status"),
    phase: Literal["active", "ended", "on_hold", "deleted"] = Query("active"),
    mine: Literal["registered", "assigned"] | None = Query(None),
    needs_completion: bool | None = Query(None, alias="needsCompletion"),
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=100),
    account: Account = Depends(get_current_account),
    session: Session = Depends(get_session),
) -> MeetingListPage:
    """이 프로젝트에 연결된 회의록 중 내가 열람할 수 있는 것만(기존 회의록 목록과 같은 항목·쪽 나눔·단계 규칙). 프로젝트를 볼 수 없으면 404."""
    project = _visible_or_404(session, account, project_id)
    linked = Meeting.id.in_(select(MeetingClassification.meeting_id).where(MeetingClassification.project_id == project.id))
    return build_meeting_page(
        session, account, status_filter=status_filter, phase=phase, mine=mine, needs_completion=needs_completion,
        page=page, size=size, extra_conditions=(linked,),
    )


@router.post("/{project_id}/approve", response_model=ProjectDetail, response_model_by_alias=True)
def approve(project_id: int, account: Account = Depends(get_current_account), session: Session = Depends(get_session)) -> ProjectDetail:
    project = _visible_or_404(session, account, project_id)
    svc.approve_project(session, account, project)
    session.commit()
    return _out(session, account, project, ProjectDetail)


@router.post("/{project_id}/reject", response_model=ProjectDetail, response_model_by_alias=True)
def reject(project_id: int, body: ReasonBody, account: Account = Depends(get_current_account), session: Session = Depends(get_session)) -> ProjectDetail:
    project = _visible_or_404(session, account, project_id)
    svc.reject_project(session, account, project, body.reason)
    session.commit()
    return _out(session, account, project, ProjectDetail)


@router.post("/{project_id}/members", response_model=ProjectDetail, response_model_by_alias=True)
def add_member(
    project_id: int, body: MemberAdd, response: Response,
    account: Account = Depends(get_current_account), session: Session = Depends(get_session),
) -> ProjectDetail:
    """참여자 추가(없으면 201, 이미 있으면 200: 역할이 다르면 역할만 바꿈)."""
    project = _visible_or_404(session, account, project_id)
    result = svc.add_project_member(session, account, project, body.account_id, body.role)
    session.commit()
    response.status_code = status.HTTP_201_CREATED if result == "added" else status.HTTP_200_OK
    return _out(session, account, project, ProjectDetail)


@router.post("/{project_id}/members/{account_id}/remove", response_model=ProjectDetail, response_model_by_alias=True)
def remove_member(project_id: int, account_id: int, account: Account = Depends(get_current_account), session: Session = Depends(get_session)) -> ProjectDetail:
    project = _visible_or_404(session, account, project_id)
    svc.remove_project_member(session, account, project, account_id)
    session.commit()
    return _out(session, account, project, ProjectDetail)
