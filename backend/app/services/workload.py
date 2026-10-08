"""업무 처리 현황 전일 집계(작업 69-3). 한 기준일의 (계정 × 부서) 행을 계산해 workload_snapshots 에 저장한다.

정의(기준일 = D)
- 대상: 활성 계정 중 부서 소속이 있는 사람. 겸직자는 소속 부서마다 행을 만든다. 업무 0건이어도 행을 만든다.
- 건수 기준: 담당자(assignee_id)가 계정인 업무만. 삭제·대체된 업무, 보류·삭제 단계 회의록의 업무는 뺀다
  (회의록 단계는 할 일 화면과 같은 모델 조건을 쓰되, 종료 단계는 포함한다: 모두 종결돼 자동 종료된 회의록의 완료 업무를 세기 위해).
- 완료 = 종결 + 종결 구분 completed. 직권 종료(forced)·구분 없는 종결은 어디에도 세지 않는다.
- 진행 중 = 열린 업무(확정 대기·확정) 중 기한이 D 이후이거나 기한 미정. 지연 = 열린 업무 중 기한이 D 보다 이전(기한 당일은 지연 아님).
- D-3 임박 = 열린 업무 중 D <= 기한 <= D+3일(진행 중의 부분 집합).
"""
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import and_, delete, select
from sqlalchemy.orm import Session

from app.config import settings
from app.models import Account, AccountDepartment, ActionItem, Department, Meeting, Tenant
from app.models.closure import MeetingClosure  # noqa: F401  (Meeting.deleted 계산 칸 등록)
from app.models.common import utcnow
from app.models.item_conditions import live_item, not_deleted_item, open_item
from app.models.workload import WorkloadSnapshot
from app.services.item_closure import completed_closure

DUE_SOON_DAYS = 3


def local_today(now: datetime | None = None) -> date:
    """APP_TIMEZONE(기본 한국 시간) 기준 오늘."""
    moment = now or datetime.now(timezone.utc)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(ZoneInfo(settings.app_timezone)).date()


def default_snapshot_date(now: datetime | None = None) -> date:
    """집계 기준일 = 집계 실행일의 전일(한국 시간)."""
    return local_today(now) - timedelta(days=1)


def _meeting_in_scope():
    """보류·삭제 단계 회의록의 업무는 뺀다."""
    return and_(~Meeting.on_hold, ~Meeting.deleted)


@dataclass
class _Counts:
    completed: int = 0
    in_progress: int = 0
    overdue: int = 0
    due_soon: int = 0

    def __post_init__(self):
        self.urgent: list[dict] = []


def _members(session: Session, tenant_id: int) -> list[tuple[int, int]]:
    """(계정 id, 부서 id) — 활성 계정 중 소속이 있는 사람, 소속 부서마다."""
    rows = session.execute(
        select(AccountDepartment.account_id, AccountDepartment.department_id)
        .join(Account, Account.id == AccountDepartment.account_id)
        .join(Department, Department.id == AccountDepartment.department_id)
        .where(Account.tenant_id == tenant_id, Department.tenant_id == tenant_id, Account.is_active.is_(True))
        .order_by(AccountDepartment.account_id, AccountDepartment.department_id)
    )
    return [(a, d) for a, d in rows]


def _count_by_account(session: Session, tenant_id: int, account_ids: set[int], base: date) -> dict[int, _Counts]:
    counts: dict[int, _Counts] = {a: _Counts() for a in account_ids}
    if not account_ids:
        return counts
    common = [
        ActionItem.tenant_id == tenant_id, ActionItem.assignee_id.in_(account_ids),
        not_deleted_item(), live_item(), _meeting_in_scope(),
    ]
    done = session.execute(
        select(ActionItem.assignee_id).join(Meeting, Meeting.id == ActionItem.meeting_id).where(*common, completed_closure())
    ).scalars()
    for assignee in done:
        counts[assignee].completed += 1

    opened = session.execute(
        select(ActionItem.id, ActionItem.assignee_id, ActionItem.title, ActionItem.due_date, ActionItem.due_undetermined,
               Meeting.id.label("meeting_id"), Meeting.title.label("meeting_title"))
        .join(Meeting, Meeting.id == ActionItem.meeting_id)
        .where(*common, open_item())
    )
    soon_limit = base + timedelta(days=DUE_SOON_DAYS)
    for row in opened:
        c = counts[row.assignee_id]
        due = None if row.due_undetermined else row.due_date
        kind = None
        if due is not None and due < base:
            c.overdue += 1
            kind = "overdue"
        else:
            c.in_progress += 1
            if due is not None and due <= soon_limit:
                c.due_soon += 1
                kind = "due_soon"
        if kind:
            c.urgent.append({
                "itemId": row.id, "title": row.title, "dueDate": due.isoformat(), "kind": kind,
                "meetingId": row.meeting_id, "meetingTitle": row.meeting_title,
            })
    for c in counts.values():
        # 지연 먼저, 기한이 오래된 순(같으면 업무 id 순), 최대 WORKLOAD_URGENT_MAX 건
        c.urgent.sort(key=lambda u: (u["kind"] != "overdue", u["dueDate"], u["itemId"]))
        del c.urgent[settings.workload_urgent_max:]
    return counts


def build_snapshot(session: Session, tenant_id: int, snapshot_date: date) -> list[WorkloadSnapshot]:
    """저장하지 않고 행만 계산한다(dry-run·테스트용)."""
    members = _members(session, tenant_id)
    counts = _count_by_account(session, tenant_id, {a for a, _ in members}, snapshot_date)
    generated = utcnow()
    return [
        WorkloadSnapshot(
            tenant_id=tenant_id, snapshot_date=snapshot_date, account_id=account_id, department_id=department_id,
            completed_count=counts[account_id].completed, in_progress_count=counts[account_id].in_progress,
            overdue_count=counts[account_id].overdue, due_soon_count=counts[account_id].due_soon,
            urgent_items=list(counts[account_id].urgent), generated_at=generated,
        )
        for account_id, department_id in members
    ]


def save_snapshot(session: Session, tenant_id: int, snapshot_date: date) -> int:
    """같은 기준일 행을 지우고 다시 쓴다(한 트랜잭션, 멱등). 저장한 행 수를 돌려준다."""
    rows = build_snapshot(session, tenant_id, snapshot_date)
    session.execute(delete(WorkloadSnapshot).where(
        WorkloadSnapshot.tenant_id == tenant_id, WorkloadSnapshot.snapshot_date == snapshot_date))
    session.add_all(rows)
    session.commit()
    return len(rows)


def tenants_missing_snapshot(session: Session, snapshot_date: date) -> list[int]:
    """그 기준일 집계 행이 하나도 없는 고객사 id."""
    have = set(session.scalars(select(WorkloadSnapshot.tenant_id).where(WorkloadSnapshot.snapshot_date == snapshot_date).distinct()))
    return [t for t in session.scalars(select(Tenant.id).order_by(Tenant.id)) if t not in have]


def group_by_account(rows: list[WorkloadSnapshot]) -> dict[int, list[WorkloadSnapshot]]:
    grouped: dict[int, list[WorkloadSnapshot]] = defaultdict(list)
    for row in rows:
        grouped[row.account_id].append(row)
    return grouped
