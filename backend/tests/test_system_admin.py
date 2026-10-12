"""시스템 관리자 지정과 회의록 내용 열람 분리(작업 82-1 파트 A)."""
import io
import secrets

import pytest
from alembic import command
from sqlalchemy import select, text

from app.auth.deps import require_system_admin
from app.config import settings
from app.models import Account, ActionItem, Event, MailOutbox, Meeting, SystemAdmin
from app.services.history import KIND_SYSTEM_ADMIN_SET, KIND_SYSTEM_ADMIN_UNSET
from app.services.processing import process_meeting
from app.services.system_admin import SystemAdminError, designate, is_system_admin, revoke
from tests.test_upload_processing import _alembic, add_account, auth, env, upload  # noqa: F401  (env 는 픽스처)


def call(env, method, account, path, **kw):
    return env["client"].request(method, path, headers=auth(account), **kw)


def designate_now(env, account, actor_id=None):
    with env["factory"]() as s:
        row = s.get(Account, account.id)
        designate(s, row, actor_id)
        s.commit()


@pytest.fixture
def world(env):
    """관리자·지시자·담당자(참석자·업무 담당) 와 처리 완료된 회의록 1건. 담당자 이름은 가짜 정답표의 담당자와 같다."""
    f = env["factory"]
    team = {
        "lead": add_account(f, "고객사A", "lead", "manager", name="박관리"),
        "boss": add_account(f, "고객사A", "boss", "executive", name="최지시"),
        "staff": add_account(f, "고객사A", "staff1", "staff", name="이서연"),
        "other": add_account(f, "고객사B", "outsider", "manager", name="타사"),
    }
    res = upload(env, team["lead"], participants=[str(team["staff"].id)])
    assert res.status_code == 202
    assert process_meeting(res.json()["jobId"], session_factory=f) == "completed"
    team["meeting_id"] = res.json()["meetingId"]
    return team


# ---------------- 지정·해제 규칙 ----------------
def test_designate_requires_active_staff_and_unique(env):
    f = env["factory"]
    staff = add_account(f, "고객사A", "sys", "staff", name="시스템")
    manager = add_account(f, "고객사A", "mgr", "manager")
    with f() as s:
        with pytest.raises(SystemAdminError, match="전용 계정"):
            designate(s, s.get(Account, manager.id))
        inactive = s.get(Account, staff.id)
        inactive.is_active = False
        with pytest.raises(SystemAdminError, match="비활성"):
            designate(s, inactive)
        inactive.is_active = True
        row = designate(s, inactive, actor_id=None)
        s.commit()
        assert row.account_id == staff.id and row.created_by is None and row.tenant_id == staff.tenant_id
        assert is_system_admin(s, staff.id) and not is_system_admin(s, manager.id)
        with pytest.raises(SystemAdminError, match="이미"):
            designate(s, s.get(Account, staff.id))
        events = s.scalars(select(Event).where(Event.event_type == KIND_SYSTEM_ADMIN_SET)).all()
        assert len(events) == 1 and events[0].actor_account_id is None and events[0].payload["actor"] == "시스템"
        revoke(s, s.get(Account, staff.id), actor_id=manager.id)
        s.commit()
        assert not is_system_admin(s, staff.id)
        with pytest.raises(SystemAdminError, match="지정된 계정이 아닙니다"):
            revoke(s, s.get(Account, staff.id))
        unset = s.scalars(select(Event).where(Event.event_type == KIND_SYSTEM_ADMIN_UNSET)).one()
        assert unset.actor_account_id == manager.id


# ---------------- 인증 의존성·응답 필드 ----------------
def test_require_system_admin_dependency_and_is_system_admin_field(env):
    from fastapi import APIRouter, Depends

    from app.main import app

    router = APIRouter()

    @router.get("/api/_probe_sys")
    def probe(account: Account = Depends(require_system_admin)):
        return {"id": account.id}

    app.include_router(router)
    try:
        f = env["factory"]
        sysadmin = add_account(f, "고객사A", "sys", "staff", name="시스템")
        normal = add_account(f, "고객사A", "n", "staff", name="일반")
        manager = add_account(f, "고객사A", "m", "manager")
        designate_now(env, sysadmin)
        client = env["client"]
        assert client.get("/api/_probe_sys").status_code == 401
        assert call(env, "GET", normal, "/api/_probe_sys").status_code == 403
        assert call(env, "GET", manager, "/api/_probe_sys").status_code == 403
        assert call(env, "GET", sysadmin, "/api/_probe_sys").json() == {"id": sysadmin.id}
        # 비활성이면 get_current_account 가 401
        with f() as s:
            s.get(Account, sysadmin.id).is_active = False
            s.commit()
        assert call(env, "GET", sysadmin, "/api/_probe_sys").status_code == 401
    finally:
        app.router.routes[:] = [r for r in app.router.routes if getattr(r, "path", "") != "/api/_probe_sys"]


def test_me_and_login_expose_is_system_admin(env):
    f = env["factory"]
    password = secrets.token_urlsafe(14)
    from app.auth.passwords import hash_password

    sysadmin = add_account(f, "고객사A", "sys", "staff", name="시스템")
    normal = add_account(f, "고객사A", "n", "staff", name="일반")
    with f() as s:
        for login in ("sys", "n"):
            s.scalar(select(Account).where(Account.login_id == login)).password_hash = hash_password(password)
        s.commit()
    designate_now(env, sysadmin)
    client = env["client"]
    ok = client.post("/api/auth/login", json={"loginId": "sys", "password": password}).json()
    assert ok["isSystemAdmin"] is True and {"accessToken", "tokenType"} <= set(ok)
    assert client.post("/api/auth/login", json={"loginId": "n", "password": password}).json()["isSystemAdmin"] is False
    assert call(env, "GET", sysadmin, "/api/auth/me").json()["isSystemAdmin"] is True
    assert call(env, "GET", normal, "/api/auth/me").json()["isSystemAdmin"] is False


# ---------------- 열람 분리 ----------------
CONTENT_GETS = [
    "/api/meetings/{id}", "/api/meetings/{id}/transcript", "/api/meetings/{id}/audio-url",
    "/api/meetings/{id}/export", "/api/meetings/{id}/history", "/api/meetings/{id}/change-requests", "/api/meetings/{id}/speakers",
]


def test_visible_before_designation_then_blocked_everywhere(env, world):
    staff, mid = world["staff"], world["meeting_id"]
    # 지정 전: 참석자·담당자라서 보인다(회귀 기준)
    assert call(env, "GET", staff, "/api/meetings").json()["total"] == 1
    for pattern in ("/api/meetings/{id}", "/api/meetings/{id}/transcript", "/api/meetings/{id}/history"):
        assert call(env, "GET", staff, pattern.format(id=mid)).status_code == 200, pattern
    assert call(env, "GET", staff, "/api/me/todos").json()["myItems"]["total"] >= 1
    designate_now(env, staff)
    # 지정 후: 같은 계정이 목록은 비고, 모든 내용 경로는 없는 것처럼 404
    page = call(env, "GET", staff, "/api/meetings").json()
    assert page["total"] == 0 and page["items"] == []
    for pattern in CONTENT_GETS:
        res = call(env, "GET", staff, pattern.format(id=mid))
        assert res.status_code == 404, pattern
    for method, path in [("POST", f"/api/meetings/{mid}/reprocess"), ("POST", f"/api/meetings/{mid}/confirm"),
                         ("POST", f"/api/meetings/{mid}/hold"), ("PATCH", f"/api/meetings/{mid}/minutes")]:
        assert call(env, method, staff, path, json={}).status_code in (404, 403, 422), path
    with env["factory"]() as s:
        item_id = s.scalars(select(ActionItem.id).where(ActionItem.meeting_id == mid)).first()
    assert call(env, "PATCH", staff, f"/api/action-items/{item_id}", json={"title": "x"}).status_code in (403, 404)
    assert call(env, "GET", staff, f"/api/action-items/{item_id}/similar").status_code == 404
    todos = call(env, "GET", staff, "/api/me/todos").json()
    assert all(todos[key]["total"] == 0 for key in todos)
    assert call(env, "GET", staff, "/api/me/notices").json() == []
    assert call(env, "GET", staff, "/api/me/processing").json() == []
    workload = call(env, "GET", staff, "/api/workload").json()
    assert workload["members"] == [] and workload["urgentItems"] == [] and workload["snapshotDate"] is None
    assert call(env, "GET", staff, "/api/projects").json() == []


def test_system_admin_cannot_register_content(env, world):
    sysadmin = add_account(env["factory"], "고객사A", "sys", "staff", name="시스템")
    designate_now(env, sysadmin)
    assert upload(env, sysadmin).status_code == 403
    res = call(env, "POST", sysadmin, "/api/projects", json={"name": "P", "description": "", "departmentId": 1, "memberIds": []})
    assert res.status_code == 403


def test_other_users_view_is_unchanged(env, world):
    mid = world["meeting_id"]
    designate_now(env, world["staff"])
    for key in ("lead", "boss"):
        assert call(env, "GET", world[key], "/api/meetings").json()["total"] == 1
        assert call(env, "GET", world[key], f"/api/meetings/{mid}").status_code == 200
        assert call(env, "GET", world[key], f"/api/meetings/{mid}/transcript").status_code == 200
    assert call(env, "GET", world["other"], f"/api/meetings/{mid}").status_code == 404  # 타 고객사는 기존대로


def test_mail_is_not_queued_for_system_admin(env, world):
    from app.services.mail import queue_mail

    designate_now(env, world["staff"])
    with env["factory"]() as s:
        account = s.get(Account, world["staff"].id)
        assert queue_mail(s, account=account, kind="daily_reminder", subject="s", body="b", dedupe_key="sys-test-1") is False
        assert queue_mail(s, account=s.get(Account, world["lead"].id), kind="daily_reminder", subject="s", body="b",
                          dedupe_key="sys-test-2") is True
        s.commit()
        assert s.scalars(select(MailOutbox).where(MailOutbox.dedupe_key == "sys-test-1")).all() == []


# ---------------- 마이그레이션 ----------------
def test_migration_up_down_refuses_with_rows_and_offline_postgresql(tmp_path):
    url = f"sqlite:///{(tmp_path / 'mig.db').as_posix()}"
    cfg = _alembic(url)
    command.upgrade(cfg, "head")
    from sqlalchemy import create_engine

    engine = create_engine(url)
    with engine.begin() as conn:
        conn.execute(text("INSERT INTO tenants (name, auto_confirm_days, created_at) VALUES ('t', 3, '2026-01-01 00:00:00')"))
        conn.execute(text(
            "INSERT INTO accounts (tenant_id, login_id, password_hash, name, email, rank, is_active, created_at) "
            "VALUES (1, 'a', 'x', 'n', 'e', 'staff', 1, '2026-01-01 00:00:00')"
        ))
        conn.execute(text("INSERT INTO system_admins (tenant_id, account_id, created_at) VALUES (1, 1, '2026-01-01 00:00:00')"))
    with pytest.raises(Exception, match="downgrade 거부"):
        command.downgrade(cfg, "b8f3a1c6d204")
    with engine.begin() as conn:
        conn.execute(text("DELETE FROM system_admins"))
    command.downgrade(cfg, "b8f3a1c6d204")
    with engine.connect() as conn:
        tables = {r[0] for r in conn.execute(text("SELECT name FROM sqlite_master WHERE type='table'"))}
    assert "system_admins" not in tables and "task_queue" in tables
    command.upgrade(cfg, "head")
    engine.dispose()
    pg = _alembic("postgresql+psycopg://u:p@localhost/db")
    buffer = io.StringIO()
    pg.output_buffer = buffer
    command.upgrade(pg, "head", sql=True)
    sql = buffer.getvalue()
    assert "CREATE TABLE system_admins" in sql and "uq_system_admins_account_id" in sql and "ix_system_admins_tenant_id" in sql


# ---------------- 초기 지정 도구 ----------------
def _tool():
    import importlib.util
    from pathlib import Path

    spec = importlib.util.spec_from_file_location(
        "create_system_admin_tool", Path(__file__).resolve().parent.parent / "scripts" / "create_system_admin.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_tool_dry_run_and_apply_existing_account(env, capsys):
    tool = _tool()
    f = env["factory"]
    staff = add_account(f, "고객사A", "sys", "staff", name="시스템")
    add_account(f, "고객사A", "mgr", "manager")
    base = ["--tenant", "고객사A", "--login-id", "sys"]
    assert tool.main(base, session_factory=f) == 0
    assert "dry-run" in capsys.readouterr().out
    with f() as s:
        assert not is_system_admin(s, staff.id)
    assert tool.main(base + ["--apply"], session_factory=f) == 0
    with f() as s:
        assert is_system_admin(s, staff.id)
        event = s.scalars(select(Event).where(Event.event_type == KIND_SYSTEM_ADMIN_SET)).one()
        assert event.actor_account_id is None and event.payload["actor"] == "시스템"
    # 상위 권한 계정·없는 계정·다른 고객사
    assert tool.main(["--tenant", "고객사A", "--login-id", "mgr", "--apply"], session_factory=f) == 1
    assert "전용 계정" in capsys.readouterr().err
    assert tool.main(["--tenant", "고객사A", "--login-id", "nobody"], session_factory=f) == 2
    assert tool.main(["--tenant", "없는고객사", "--login-id", "sys"], session_factory=f) == 2


def test_tool_create_new_account_password_never_printed_or_stored_in_history(env, capsys):
    tool = _tool()
    f = env["factory"]
    add_account(f, "고객사A", "seed", "staff")
    password = "Zq9!" + secrets.token_urlsafe(12)
    answers = iter([password, password])
    args = ["--tenant", "고객사A", "--login-id", "newsys", "--create", "--name", "시스템", "--email", "s@example.com"]
    # dry-run 은 비밀번호를 묻지 않고 저장하지 않는다
    assert tool.main(args, password_input=lambda prompt: pytest.fail("dry-run 에서 비밀번호를 물으면 안 됩니다"), session_factory=f) == 0
    with f() as s:
        assert s.scalar(select(Account).where(Account.login_id == "newsys")) is None
    assert tool.main(args + ["--apply"], password_input=lambda prompt: next(answers), session_factory=f) == 0
    captured = capsys.readouterr()
    assert password not in captured.out and password not in captured.err
    with f() as s:
        account = s.scalar(select(Account).where(Account.login_id == "newsys"))
        assert account.rank == "staff" and is_system_admin(s, account.id)
        assert password not in account.password_hash
        assert all(password not in str(e.payload) for e in s.scalars(select(Event)).all())
    # 비밀번호 불일치·짧은 비밀번호
    mismatch = iter(["a" * 12, "b" * 12])
    assert tool.main(["--tenant", "고객사A", "--login-id", "x2", "--create", "--name", "n", "--email", "e@e.com", "--apply"],
                     password_input=lambda p: next(mismatch), session_factory=f) == 1
    short = iter(["short", "short"])
    assert tool.main(["--tenant", "고객사A", "--login-id", "x3", "--create", "--name", "n", "--email", "e@e.com", "--apply"],
                     password_input=lambda p: next(short), session_factory=f) == 1
    assert "--create" not in settings.model_fields  # 설정으로 노출되지 않음(도구 전용)
