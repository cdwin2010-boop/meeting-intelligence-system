"""업무 처리 현황 조회 API(작업 69-3): GET /api/workload?departmentId=
최신 집계 기준일의 저장값(workload_snapshots)만 읽는다(업무 표를 직접 집계하지 않는다). 같은 고객사만.
범위(서버가 강제):
- 사용권한 지시자(executive): 전사(기본) 또는 고객사의 아무 부서(departmentId)
- 부서장(account_departments 의 role=head, 지시자 아님): 본인이 부서장인 부서만. departmentId 가 없으면 부서 이름순 첫 부서. 다른 부서는 403
- 그 밖(관리자·담당자): 본인 개인 현황만. departmentId 를 보내면 403
다른 고객사·없는 부서는 404(범위 판정보다 먼저). 집계가 아직 없으면 200 으로 snapshotDate null 과 빈 목록.
개인 범위 응답에는 다른 사람의 이름·건수를 넣지 않는다."""
from datetime import date, datetime

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.schemas import CamelModel
from app.auth.deps import get_current_account
from app.config import settings
from app.db import get_session
from app.models import Account, AccountDepartment, Department
from app.models.workload import WorkloadSnapshot
from app.services.workload import group_by_account

router = APIRouter(prefix="/api/workload", tags=["workload"])


class DepartmentRef(CamelModel):
    id: int
    name: str


class ScopeOut(CamelModel):
    kind: str  # company | department | self
    departments: list[DepartmentRef]
    department: DepartmentRef | None


class KpisOut(CamelModel):
    assigned: int
    completion_rate: float | None
    overdue: int
    avg_open_per_person: float
    member_count: int


class MemberOut(CamelModel):
    account_id: int
    name: str
    completed: int
    in_progress: int
    overdue: int
    open: int
    overloaded: bool


class UrgentOut(CamelModel):
    item_id: int
    title: str
    due_date: date
    kind: str  # overdue | due_soon
    days: int  # overdue: 기한 후 지난 일수, due_soon: 기한까지 남은 일수(기준일 기준)
    meeting_id: int
    meeting_title: str
    assignee: DepartmentRef


class WorkloadOut(CamelModel):
    snapshot_date: date | None
    generated_at: datetime | None
    scope: ScopeOut
    kpis: KpisOut
    members: list[MemberOut]
    urgent_items: list[UrgentOut]


def _empty_kpis() -> KpisOut:
    return KpisOut(assigned=0, completion_rate=None, overdue=0, avg_open_per_person=0.0, member_count=0)


@router.get("", response_model=WorkloadOut, response_model_by_alias=True)
def get_workload(
    department_id: int | None = Query(default=None, alias="departmentId"),
    account: Account = Depends(get_current_account),
    session: Session = Depends(get_session),
) -> WorkloadOut:
    tenant_id = account.tenant_id
    tenant_departments = list(session.scalars(select(Department).where(Department.tenant_id == tenant_id).order_by(Department.name, Department.id)))
    chosen = None
    if department_id is not None:
        chosen = next((d for d in tenant_departments if d.id == department_id), None)
        if chosen is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "부서를 찾을 수 없습니다")

    head_ids = set(session.scalars(
        select(AccountDepartment.department_id).where(AccountDepartment.account_id == account.id, AccountDepartment.role == "head")))
    head_departments = [d for d in tenant_departments if d.id in head_ids]

    if account.rank == "executive":
        kind, choices = ("department" if chosen else "company"), tenant_departments
    elif head_departments:
        if chosen is not None and chosen.id not in head_ids:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "본인이 부서장인 부서만 볼 수 있습니다")
        kind, choices = "department", head_departments
        chosen = chosen or head_departments[0]
    else:
        if chosen is not None:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "부서 현황을 볼 권한이 없습니다")
        kind, choices = "self", []

    ref = lambda d: DepartmentRef(id=d.id, name=d.name)  # noqa: E731
    scope = ScopeOut(kind=kind, departments=[ref(d) for d in choices], department=ref(chosen) if chosen else None)

    snapshot_date = session.scalar(select(func.max(WorkloadSnapshot.snapshot_date)).where(WorkloadSnapshot.tenant_id == tenant_id))
    if snapshot_date is None:
        return WorkloadOut(snapshot_date=None, generated_at=None, scope=scope, kpis=_empty_kpis(), members=[], urgent_items=[])

    stmt = select(WorkloadSnapshot).where(WorkloadSnapshot.tenant_id == tenant_id, WorkloadSnapshot.snapshot_date == snapshot_date)
    if kind == "department":
        stmt = stmt.where(WorkloadSnapshot.department_id == chosen.id)
    elif kind == "self":
        stmt = stmt.where(WorkloadSnapshot.account_id == account.id)
    rows = list(session.scalars(stmt.order_by(WorkloadSnapshot.account_id, WorkloadSnapshot.department_id)))
    generated_at = max((r.generated_at for r in rows), default=None)

    # 겸직자는 부서마다 행이 있지만 건수는 계정 기준으로 같으므로 계정당 한 행만 쓴다(전사 합계 중복 제거)
    grouped = group_by_account(rows)
    names = {i: n for i, n in session.execute(select(Account.id, Account.name).where(Account.id.in_(list(grouped))))} if grouped else {}
    members, urgent, seen_items = [], [], set()
    for account_id, account_rows in grouped.items():
        row = account_rows[0]
        open_count = row.in_progress_count + row.overdue_count
        members.append(MemberOut(
            account_id=account_id, name=names.get(account_id, ""), completed=row.completed_count, in_progress=row.in_progress_count,
            overdue=row.overdue_count, open=open_count, overloaded=open_count >= settings.workload_overload_threshold,
        ))
        for entry in row.urgent_items:
            if entry["itemId"] in seen_items:
                continue
            seen_items.add(entry["itemId"])
            due = date.fromisoformat(entry["dueDate"])
            days = (snapshot_date - due).days if entry["kind"] == "overdue" else (due - snapshot_date).days
            urgent.append(UrgentOut(
                item_id=entry["itemId"], title=entry["title"], due_date=due, kind=entry["kind"], days=days,
                meeting_id=entry["meetingId"], meeting_title=entry["meetingTitle"],
                assignee=DepartmentRef(id=account_id, name=names.get(account_id, "")),
            ))
    members.sort(key=lambda m: (-m.open, m.name, m.account_id))
    urgent.sort(key=lambda u: (u.kind != "overdue", u.due_date, u.item_id))
    del urgent[settings.workload_urgent_max:]

    completed = sum(m.completed for m in members)
    in_progress = sum(m.in_progress for m in members)
    overdue = sum(m.overdue for m in members)
    assigned = completed + in_progress + overdue
    count = len(members)
    kpis = KpisOut(
        assigned=assigned,
        completion_rate=round(completed / assigned * 100, 1) if assigned else None,
        overdue=overdue,
        avg_open_per_person=round((in_progress + overdue) / count, 1) if count else 0.0,
        member_count=count,
    )
    return WorkloadOut(snapshot_date=snapshot_date, generated_at=generated_at, scope=scope, kpis=kpis, members=members, urgent_items=urgent)
