"""업무 처리 현황(작업 69-3): 전일 집계 정의·제외 규칙, 한국 시간 기준일, 자동 실행 조건, 멱등, GET /api/workload 범위, 마이그레이션."""
import io
import sqlite3
from datetime import date, datetime, timedelta, timezone

import pytest
from alembic import command
from sqlalchemy import func, select

from app.config import settings
from app.jobs import workload_snapshot as job
from app.models import AccountDepartment, ActionItem, Department, ItemSupersession, Meeting, MeetingHold, Project
from app.models.closure import MeetingClosure
from app.models.common import utcnow
from app.models.item_closure import ItemClosure
from app.models.workload import WorkloadSnapshot
from app.services import workload as svc
from tests.test_meeting_queries import make_meeting
from tests.test_upload_processing import _alembic, add_account, auth, env  # noqa: F401  (env 는 픽스처)

D = date(2026, 10, 6)  # 집계 기준일
_BEFORE = "f6c2d8a41b75"  # workload_snapshots 마이그레이션의 down_revision


def confirmed(who, due, **extra):
    return {"title": f"업무-{who.login_id}-{due}", "assignee_id": who.id, "status": "confirmed", "confirm_kind": "manager",
            "due_date": due, **extra}


@pytest.fixture
def org(env):
    f = env["factory"]
    a = {
        "kim": add_account(f, "고객사A", "kim", "staff", name="김담당"),
        "lee": add_account(f, "고객사A", "lee", "manager", name="이겸직"),
        "head1": add_account(f, "고객사A", "head1", "manager", name="박부서장"),
        "head2": add_account(f, "고객사A", "head2", "manager", name="정부서장"),
        "exe": add_account(f, "고객사A", "exe", "executive", name="최지시"),
        "mgr": add_account(f, "고객사A", "mgr", "manager", name="한관리"),
        "idle": add_account(f, "고객사A", "idle", "staff", name="무업무"),
        "gone": add_account(f, "고객사A", "gone", "staff", name="비활성"),
        "nodept": add_account(f, "고객사A", "nodept", "staff", name="소속없음"),
        "out": add_account(f, "고객사B", "out", "executive", name="외부"),
        "outstaff": add_account(f, "고객사B", "outstaff", "staff", name="외부직원"),
    }
    with f() as s:
        tenant_a, tenant_b = a["kim"].tenant_id, a["out"].tenant_id
        dev = Department(tenant_id=tenant_a, name="개발팀")
        sales = Department(tenant_id=tenant_a, name="영업팀")
        other = Department(tenant_id=tenant_b, name="외부팀")
        s.add_all([dev, sales, other])
        s.flush()
        for key, dept, role in [("head1", dev, "head"), ("kim", dev, "member"), ("lee", dev, "member"), ("lee", sales, "member"),
                                ("head2", sales, "head"), ("idle", dev, "member"), ("gone", dev, "member"), ("mgr", sales, "member")]:
            s.add(AccountDepartment(account_id=a[key].id, department_id=dev.id if dept is dev else sales.id, role=role))
        s.add(AccountDepartment(account_id=a["outstaff"].id, department_id=other.id, role="member"))
        gone = s.get(type(a["gone"]), a["gone"].id)
        gone.is_active = False
        s.commit()
        ids = {"dev": dev.id, "sales": sales.id, "other": other.id, "tenant": tenant_a}
    return {**a, "ids": ids, "factory": f, "client": env["client"]}


def snapshot(org, d=D):
    with org["factory"]() as s:
        svc.save_snapshot(s, org["ids"]["tenant"], d)
        return list(s.scalars(select(WorkloadSnapshot).order_by(WorkloadSnapshot.id)))


def row_of(rows, account):
    found = [r for r in rows if r.account_id == account.id]
    return found[0] if found else None


def counts(r):
    return (r.completed_count, r.in_progress_count, r.overdue_count, r.due_soon_count)


def add_closure(org, item_id, kind, who):
    with org["factory"]() as s:
        s.add(ItemClosure(tenant_id=org["ids"]["tenant"], item_id=item_id, kind=kind, closed_by=who.id))
        s.commit()


def item_ids(org, meeting_id):
    with org["factory"]() as s:
        return list(s.scalars(select(ActionItem.id).where(ActionItem.meeting_id == meeting_id).order_by(ActionItem.id)))


# ---------------- 정의 ----------------
def test_boundaries_and_definitions(org):
    kim = org["kim"]
    due = [D - timedelta(days=1), D, D + timedelta(days=3), D + timedelta(days=4)]
    items = [confirmed(kim, d) for d in due]
    items.append(confirmed(kim, None, due_undetermined=True))
    items.append({"title": "확정 대기", "assignee_id": kim.id, "due_date": D + timedelta(days=10)})  # pending 도 열린 업무
    items.append(confirmed(kim, D - timedelta(days=5), status="closed"))
    meeting_id = make_meeting(org["factory"], org["head1"], items=items)
    ids = item_ids(org, meeting_id)
    add_closure(org, ids[6], "completed", org["head1"])
    rows = snapshot(org)
    r = row_of(rows, kim)
    # 완료 1, 진행 중 4(기한 당일·+3·+4·미정·대기 중 = 5 중 지연 제외) → 당일/+3/+4/미정/확정 대기 = 5, 지연 1(전일)
    assert counts(r) == (1, 5, 1, 2)  # 임박: 기한 당일, +3일 (+4일은 아님, 미정은 아님)
    assert [u["kind"] for u in r.urgent_items] == ["overdue", "due_soon", "due_soon"]
    assert [u["dueDate"] for u in r.urgent_items] == [(D - timedelta(days=1)).isoformat(), D.isoformat(), (D + timedelta(days=3)).isoformat()]
    assert {"itemId", "title", "dueDate", "kind", "meetingId", "meetingTitle"} == set(r.urgent_items[0])


def test_zero_item_members_get_rows_and_inactive_and_no_dept_excluded(org):
    rows = snapshot(org)
    accounts = {r.account_id for r in rows}
    assert org["idle"].id in accounts and counts(row_of(rows, org["idle"])) == (0, 0, 0, 0)
    assert org["gone"].id not in accounts  # 비활성
    assert org["nodept"].id not in accounts and org["exe"].id not in accounts  # 소속 없음
    assert org["outstaff"].id not in accounts  # 다른 고객사는 그 고객사 집계에만
    assert len(rows) == 7  # head1, kim, lee(두 부서), head2, idle, mgr


def test_exclusion_rules(org):
    kim = org["kim"]
    late = D - timedelta(days=2)
    f = org["factory"]
    # 포함될 기준 업무 1건 + 제외 대상들
    m_main = make_meeting(f, org["head1"], items=[
        confirmed(kim, late),                                   # 0 지연으로 센다
        confirmed(kim, late, status="deleted"),                 # 1 삭제 → 제외
        confirmed(kim, late),                                   # 2 대체됨 → 제외
        confirmed(kim, late, status="closed"),                  # 3 forced → 제외
        confirmed(kim, late, status="closed"),                  # 4 구분 없는 종결 → 제외
        {"title": "담당자 없음", "assignee_id": None, "status": "confirmed", "due_date": late},  # 5 계정 없는 담당자 → 제외
    ])
    ids = item_ids(org, m_main)
    add_closure(org, ids[3], "forced", org["head1"])
    m_hold = make_meeting(f, org["head1"], items=[confirmed(kim, late)])
    m_gone = make_meeting(f, org["head1"], items=[confirmed(kim, late)])
    m_ended = make_meeting(f, org["head1"], items=[confirmed(kim, late, status="closed")])
    with f() as s:
        s.add(MeetingHold(meeting_id=m_hold, tenant_id=org["ids"]["tenant"], on_hold=True))
        s.add(MeetingClosure(meeting_id=m_gone, tenant_id=org["ids"]["tenant"], deleted_at=utcnow(), deleted_by=org["head1"].id))
        s.add(MeetingClosure(meeting_id=m_ended, tenant_id=org["ids"]["tenant"], end_kind="auto", ended_at=utcnow()))
        project = Project(tenant_id=org["ids"]["tenant"], department_id=org["ids"]["dev"], name="p", status="active", registered_by=org["head1"].id)
        s.add(project)
        s.flush()
        s.commit()
    new_meeting = make_meeting(f, org["head1"], items=[confirmed(kim, late + timedelta(days=30))])
    (new_id,) = item_ids(org, new_meeting)
    with f() as s:
        project_id = s.scalar(select(Project.id))
        s.add(ItemSupersession(tenant_id=org["ids"]["tenant"], old_item_id=ids[2], new_item_id=new_id, project_id=project_id,
                               superseded_by=org["head1"].id, reason="r"))
        s.commit()
    ended_ids = item_ids(org, m_ended)
    add_closure(org, ended_ids[0], "completed", org["head1"])  # 종료 단계 회의록의 완료 업무는 센다
    r = row_of(snapshot(org), kim)
    # 지연 1(ids[0]) + 대체한 새 업무는 기한이 멀어 진행 중 1, 완료 1(종료된 회의록)
    assert counts(r) == (1, 1, 1, 0)


def test_concurrent_membership_company_total_is_per_account(org):
    lee = org["lee"]
    make_meeting(org["factory"], org["head1"], items=[confirmed(lee, D - timedelta(days=1)), confirmed(lee, D + timedelta(days=1))])
    rows = snapshot(org)
    lee_rows = [r for r in rows if r.account_id == lee.id]
    assert len(lee_rows) == 2 and {r.department_id for r in lee_rows} == {org["ids"]["dev"], org["ids"]["sales"]}
    assert all(counts(r) == (0, 1, 1, 1) for r in lee_rows)
    # 전사 응답(지시자)에서는 한 번만 센다
    data = get(org, "exe").json()
    assert data["kpis"]["assigned"] == 2 and data["kpis"]["overdue"] == 1
    assert [m["accountId"] for m in data["members"]].count(lee.id) == 1
    assert len([u for u in data["urgentItems"] if u["assignee"]["id"] == lee.id]) == 2  # 겸직 중복 없이


def test_rerun_is_idempotent(org):
    make_meeting(org["factory"], org["head1"], items=[confirmed(org["kim"], D - timedelta(days=1))])
    first = snapshot(org)
    second = snapshot(org)
    assert len(first) == len(second)
    with org["factory"]() as s:
        assert s.scalar(select(func.count()).select_from(WorkloadSnapshot)) == len(second)
    assert counts(row_of(second, org["kim"])) == (0, 0, 1, 0)


def test_urgent_items_capped(org, monkeypatch):
    monkeypatch.setattr(settings, "workload_urgent_max", 2)
    make_meeting(org["factory"], org["head1"], items=[confirmed(org["kim"], D - timedelta(days=n)) for n in range(1, 6)])
    r = row_of(snapshot(org), org["kim"])
    assert r.overdue_count == 5 and len(r.urgent_items) == 2
    assert r.urgent_items[0]["dueDate"] < r.urgent_items[1]["dueDate"]  # 기한 오래된 순


# ---------------- 기준일(한국 시간) ----------------
@pytest.mark.parametrize("utc, expected", [
    (datetime(2026, 10, 7, 14, 59, tzinfo=timezone.utc), date(2026, 10, 6)),   # KST 23:59 (10/7) → 전일 10/6
    (datetime(2026, 10, 7, 15, 0, tzinfo=timezone.utc), date(2026, 10, 7)),    # KST 00:00 (10/8) → 전일 10/7
    (datetime(2026, 10, 7, 15, 10, tzinfo=timezone.utc), date(2026, 10, 7)),
    (datetime(2026, 10, 7, 0, 5, tzinfo=timezone.utc), date(2026, 10, 6)),
])
def test_default_snapshot_date_is_kst_yesterday(utc, expected):
    assert svc.default_snapshot_date(utc) == expected


# ---------------- 자동 실행 조건 ----------------
def test_scheduler_conditions(org, monkeypatch):
    f = org["factory"]
    before = datetime(2026, 10, 7, 15, 5, tzinfo=timezone.utc)  # KST 10/8 00:05 — 00:10 전
    after = datetime(2026, 10, 7, 15, 20, tzinfo=timezone.utc)  # KST 10/8 00:20
    assert job.run_if_due(f, before) == []
    with f() as s:
        assert s.scalar(select(func.count()).select_from(WorkloadSnapshot)) == 0
    assert job.run_if_due(f, after) == [org["ids"]["tenant"], org["out"].tenant_id]
    with f() as s:
        dates = set(s.scalars(select(WorkloadSnapshot.snapshot_date)))
        assert dates == {date(2026, 10, 7)}
        marker = s.scalar(select(func.max(WorkloadSnapshot.generated_at)))
    # 이미 있으면 건너뜀(다시 만들지 않는다)
    assert job.run_if_due(f, after + timedelta(hours=1)) == []
    with f() as s:
        assert s.scalar(select(func.max(WorkloadSnapshot.generated_at))) == marker
    # 설정 시각 변경: 늦은 시각이면 아직 실행하지 않는다
    monkeypatch.setattr(settings, "workload_snapshot_time", "23:00")
    assert not job.is_due(after)
    # 켜짐/꺼짐: test 환경이거나 꺼져 있으면 시작하지 않는다
    assert job.should_start() is False  # APP_ENV=test
    monkeypatch.setattr(settings, "app_env", "dev")
    assert job.should_start() is True
    monkeypatch.setattr(settings, "workload_snapshot_enabled", False)
    assert job.should_start() is False


def test_scheduler_attempted_set_prevents_repeat_for_empty_tenant(org):
    attempted = set()
    after = datetime(2026, 10, 7, 15, 20, tzinfo=timezone.utc)
    first = job.run_if_due(org["factory"], after, attempted)
    assert org["ids"]["tenant"] in first
    assert job.run_if_due(org["factory"], after, attempted) == []


# ---------------- API ----------------
def get(org, who, **params):
    return org["client"].get("/api/workload", headers=auth(org[who]), params=params)


@pytest.fixture
def filled(org):
    f = org["factory"]
    # 개발팀: kim(완료1·진행1·지연1), lee(겸직), idle(0). 영업팀: head2, mgr(지연 6건 → 과다)
    m = make_meeting(f, org["head1"], items=[
        confirmed(org["kim"], D - timedelta(days=1)), confirmed(org["kim"], D + timedelta(days=5)), confirmed(org["kim"], D, status="closed"),
        *[confirmed(org["mgr"], D - timedelta(days=n)) for n in range(1, 7)],
    ])
    ids = item_ids(org, m)
    add_closure(org, ids[2], "completed", org["head1"])
    snapshot(org)
    return org


def test_api_no_snapshot_returns_empty_200(org):
    data = get(org, "exe").json()
    assert data["snapshotDate"] is None and data["members"] == [] and data["urgentItems"] == []
    assert data["scope"]["kind"] == "company" and data["kpis"]["assigned"] == 0 and data["kpis"]["completionRate"] is None


def test_api_requires_login(org):
    assert org["client"].get("/api/workload").status_code == 401


def test_api_executive_company_and_department(filled):
    data = get(filled, "exe").json()
    assert data["snapshotDate"] == "2026-10-06" and data["scope"]["kind"] == "company"
    assert {d["name"] for d in data["scope"]["departments"]} == {"개발팀", "영업팀"}
    k = data["kpis"]
    # 완료 1 · 진행 1 · 지연 1+6 = 9 배정, 구성원 6명(head1, kim, lee, head2, idle, mgr)
    assert (k["assigned"], k["overdue"], k["memberCount"]) == (9, 7, 6)
    assert k["completionRate"] == round(1 / 9 * 100, 1)
    assert k["avgOpenPerPerson"] == round(8 / 6, 1)
    mgr = next(m for m in data["members"] if m["accountId"] == filled["mgr"].id)
    assert mgr["overloaded"] is True and mgr["open"] == 6
    assert data["members"][0]["accountId"] == filled["mgr"].id  # 미완료 많은 순
    assert [u["kind"] for u in data["urgentItems"]][0] == "overdue"
    dates = [u["dueDate"] for u in data["urgentItems"] if u["kind"] == "overdue"]
    assert dates == sorted(dates)
    dev = get(filled, "exe", departmentId=filled["ids"]["dev"]).json()
    assert dev["scope"]["kind"] == "department" and dev["scope"]["department"]["name"] == "개발팀"
    assert {m["name"] for m in dev["members"]} == {"박부서장", "김담당", "이겸직", "무업무"}
    assert get(filled, "exe", departmentId=filled["ids"]["other"]).status_code == 404  # 다른 고객사 부서
    assert get(filled, "exe", departmentId=99999).status_code == 404


def test_api_head_only_own_department(filled):
    data = get(filled, "head1").json()
    assert data["scope"]["kind"] == "department" and data["scope"]["department"]["id"] == filled["ids"]["dev"]
    assert [d["name"] for d in data["scope"]["departments"]] == ["개발팀"]
    assert {m["name"] for m in data["members"]} == {"박부서장", "김담당", "이겸직", "무업무"}
    assert get(filled, "head1", departmentId=filled["ids"]["sales"]).status_code == 403
    assert get(filled, "head1", departmentId=filled["ids"]["other"]).status_code == 404
    assert get(filled, "head1", departmentId=filled["ids"]["dev"]).status_code == 200
    sales = get(filled, "head2").json()
    assert {m["name"] for m in sales["members"]} == {"정부서장", "한관리", "이겸직"}


def test_api_head_of_multiple_departments_defaults_to_first(filled):
    with filled["factory"]() as s:
        s.add(AccountDepartment(account_id=filled["head1"].id, department_id=filled["ids"]["sales"], role="head"))
        s.commit()
    data = get(filled, "head1").json()
    assert [d["name"] for d in data["scope"]["departments"]] == ["개발팀", "영업팀"]
    assert data["scope"]["department"]["name"] == "개발팀"
    assert get(filled, "head1", departmentId=filled["ids"]["sales"]).json()["scope"]["department"]["name"] == "영업팀"


@pytest.mark.parametrize("who", ["kim", "mgr"])
def test_api_others_get_self_only(filled, who):
    data = get(filled, who).json()
    assert data["scope"]["kind"] == "self" and data["scope"]["departments"] == [] and data["scope"]["department"] is None
    assert [m["accountId"] for m in data["members"]] == [filled[who].id]
    assert all(u["assignee"]["id"] == filled[who].id for u in data["urgentItems"])
    assert data["kpis"]["memberCount"] == 1
    text = str(data)
    for other in ("김담당", "한관리", "이겸직", "박부서장"):
        if other != filled[who].name:
            assert other not in text
    assert get(filled, who, departmentId=filled["ids"]["dev"]).status_code == 403
    assert get(filled, who, departmentId=filled["ids"]["other"]).status_code == 404


def test_api_other_tenant_sees_only_own_tenant(filled):
    data = get(filled, "out").json()
    assert data["snapshotDate"] is None and data["members"] == []
    assert get(filled, "out", departmentId=filled["ids"]["dev"]).status_code == 404


def test_api_reads_only_stored_values(filled):
    """집계 후 업무를 바꿔도 다음 집계 전까지 응답은 저장값 그대로다."""
    before = get(filled, "exe").json()["kpis"]
    with filled["factory"]() as s:
        for item in s.scalars(select(ActionItem)):
            item.status = "deleted"
        s.commit()
    assert get(filled, "exe").json()["kpis"] == before


# ---------------- 마이그레이션 ----------------
def test_migration_roundtrip_and_downgrade_guard(tmp_path):
    url = f"sqlite:///{(tmp_path / 'm.db').as_posix()}"
    cfg = _alembic(url)
    command.upgrade(cfg, "head")
    command.downgrade(cfg, _BEFORE)
    command.upgrade(cfg, "head")
    command.check(cfg)
    raw = sqlite3.connect(tmp_path / "m.db")
    raw.execute("INSERT INTO workload_snapshots (tenant_id, snapshot_date, account_id, department_id, completed_count, in_progress_count, "
                "overdue_count, due_soon_count, urgent_items, generated_at) VALUES (1, '2026-10-06', 1, 1, 0, 0, 0, 0, '[]', '2026-10-07')")
    raw.commit()
    raw.close()
    with pytest.raises(RuntimeError, match="downgrade 거부"):
        command.downgrade(cfg, _BEFORE)
    check = sqlite3.connect(tmp_path / "m.db")
    try:
        assert check.execute("SELECT COUNT(*) FROM workload_snapshots").fetchone()[0] == 1
        with pytest.raises(sqlite3.IntegrityError):  # (고객사, 기준일, 계정, 부서) 유니크
            check.execute("INSERT INTO workload_snapshots (tenant_id, snapshot_date, account_id, department_id, completed_count, in_progress_count, "
                          "overdue_count, due_soon_count, urgent_items, generated_at) VALUES (1, '2026-10-06', 1, 1, 0, 0, 0, 0, '[]', '2026-10-07')")
    finally:
        check.close()


def test_alembic_offline_postgresql_ddl_has_workload_table():
    cfg = _alembic("postgresql+psycopg://u:p@localhost/db")
    buffer = io.StringIO()
    cfg.output_buffer = buffer
    command.upgrade(cfg, "head", sql=True)
    sql = buffer.getvalue()
    assert "CREATE TABLE workload_snapshots" in sql and "uq_workload_snapshots_key" in sql


def test_manual_script_dry_run_does_not_save(org, capsys):
    import importlib.util
    from pathlib import Path

    spec = importlib.util.spec_from_file_location("run_workload_snapshot", Path(__file__).resolve().parent.parent / "scripts" / "run_workload_snapshot.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    with org["factory"]() as s:
        lines = module.run(s, "고객사A", D, apply=False)
        assert "dry-run" in lines[-1]
        assert s.scalar(select(func.count()).select_from(WorkloadSnapshot)) == 0
        lines = module.run(s, "고객사A", D, apply=True)
        assert "저장했습니다" in lines[-1]
        assert s.scalar(select(func.count()).select_from(WorkloadSnapshot)) > 0
