"""외부 연동 허용 설정(시스템 관리자 전용)과 단계 적용 스위치(작업 82-1 파트 C)."""
import io

import pytest
from alembic import command
from sqlalchemy import create_engine, select, text

from app.config import settings
from app.models import Event, Job, Meeting
from app.pipeline.extractor import FakeExtractor
from app.services import external_policy
from app.services.processing import process_meeting
from app.services.system_settings import EXTERNAL_LLM_ENABLED, LOCAL_LLM_EXTERNAL_HOST_ALLOWED
from tests.test_system_admin import designate_now
from tests.test_upload_processing import _alembic, add_account, auth, env, upload  # noqa: F401  (env 는 픽스처)


def call(env, method, account, path, **kw):
    return env["client"].request(method, path, headers=auth(account), **kw)


@pytest.fixture
def world(env):
    f = env["factory"]
    sysadmin = add_account(f, "고객사A", "sys", "staff", name="시스템관리")
    designate_now(env, sysadmin)
    return {
        "sys": sysadmin,
        "staff": add_account(f, "고객사A", "staff1", "staff"),
        "mgr": add_account(f, "고객사A", "mgr1", "manager"),
    }


def put(env, account, key, value, reason="협의된 프로젝트"):
    return call(env, "PUT", account, f"/api/admin/settings/{key}", json={"value": value, "reason": reason})


def get_settings(env, account) -> dict:
    return call(env, "GET", account, "/api/admin/settings").json()


def by_key(body) -> dict:
    return {i["key"]: i for i in body["items"]}


def run_job(env, mgr, *, extractor=FakeExtractor):
    res = upload(env, mgr)
    out = process_meeting(res.json()["jobId"], session_factory=env["factory"], extractor_factory=extractor)
    return out, res.json()["meetingId"], res.json()["jobId"]


def job_row(env, job_id) -> Job:
    with env["factory"]() as s:
        return s.get(Job, job_id)


# ---------------- 조회·변경 API ----------------
def test_defaults_and_sources(env, world, monkeypatch):
    body = get_settings(env, world["sys"])
    assert body["enforced"] is False
    items = by_key(body)
    assert set(items) == {EXTERNAL_LLM_ENABLED, LOCAL_LLM_EXTERNAL_HOST_ALLOWED}
    assert all(i["value"] is False and i["source"] == "default" and i["updatedAt"] is None for i in items.values())
    # .env 에 명시한 값은 초기값 역할(출처 env)
    monkeypatch.setattr(settings, "local_llm_allow_external_host", True)
    monkeypatch.setattr(settings, "__pydantic_fields_set__", set(settings.model_fields_set) | {"local_llm_allow_external_host"})
    item = by_key(get_settings(env, world["sys"]))[LOCAL_LLM_EXTERNAL_HOST_ALLOWED]
    assert item["value"] is True and item["source"] == "env"


def test_put_requires_reason_valid_key_and_records_audit(env, world):
    sysadmin = world["sys"]
    assert put(env, sysadmin, EXTERNAL_LLM_ENABLED, True, reason="").status_code == 422
    assert put(env, sysadmin, EXTERNAL_LLM_ENABLED, True, reason="   ").status_code == 422
    assert put(env, sysadmin, EXTERNAL_LLM_ENABLED, True, reason="가" * 501).status_code == 422
    assert call(env, "PUT", sysadmin, f"/api/admin/settings/{EXTERNAL_LLM_ENABLED}", json={"value": True}).status_code == 422
    assert call(env, "PUT", sysadmin, f"/api/admin/settings/{EXTERNAL_LLM_ENABLED}", json={"value": "yes", "reason": "x"}).status_code == 422
    assert put(env, sysadmin, "stt_provider", True).status_code == 404  # 허용 목록 밖 키
    assert put(env, sysadmin, "secret_key", False).status_code == 404
    res = put(env, sysadmin, EXTERNAL_LLM_ENABLED, True, reason="고객사 협의 완료(문서 번호 12)")
    assert res.status_code == 200
    assert res.json()["key"] == EXTERNAL_LLM_ENABLED and res.json()["value"] is True and res.json()["source"] == "admin"
    assert by_key(get_settings(env, sysadmin))[EXTERNAL_LLM_ENABLED]["updatedAt"] is not None
    entries = [e for e in call(env, "GET", sysadmin, "/api/admin/audit").json()["items"] if e["eventType"] == "admin.setting_changed"]
    assert len(entries) == 1
    entry = entries[0]
    assert entry["reason"] == "고객사 협의 완료(문서 번호 12)" and entry["targetType"] == "system_setting"
    assert entry["before"]["value"] is False and entry["after"]["value"] is True and entry["actor"]["id"] == sysadmin.id
    # 다시 끄기도 이력에 남는다
    assert put(env, sysadmin, EXTERNAL_LLM_ENABLED, False, reason="협의 종료").json()["value"] is False
    with env["factory"]() as s:
        assert len(s.scalars(select(Event).where(Event.event_type == "admin.setting_changed")).all()) == 2


def test_only_system_admin_can_read_or_change(env, world):
    client = env["client"]
    assert client.get("/api/admin/settings").status_code == 401
    assert client.put(f"/api/admin/settings/{EXTERNAL_LLM_ENABLED}", json={"value": True, "reason": "x"}).status_code == 401
    for key in ("staff", "mgr"):
        assert call(env, "GET", world[key], "/api/admin/settings").status_code == 403
        assert put(env, world[key], EXTERNAL_LLM_ENABLED, True).status_code == 403
    assert by_key(get_settings(env, world["sys"]))[EXTERNAL_LLM_ENABLED]["value"] is False  # 거부된 변경은 반영되지 않음


# ---------------- 스위치 꺼짐(현행 동작) ----------------
def test_switch_off_keeps_current_behavior(env, world, monkeypatch):
    assert settings.external_policy_enforced is False
    monkeypatch.setattr(settings, "llm_provider", "gemini")
    out, meeting_id, job_id = run_job(env, world["mgr"])  # 외부 LLM 허용 설정이 거짓이어도 현행처럼 실행
    assert out == "completed" and job_row(env, job_id).error_code is None
    # 로컬 외부 주소는 .env 값만 따른다
    monkeypatch.setattr(settings, "local_llm_allow_external_host", True)
    assert external_policy.local_external_host_allowed(env["factory"], meeting_id) is True
    monkeypatch.setattr(settings, "local_llm_allow_external_host", False)
    assert external_policy.local_external_host_allowed(env["factory"], meeting_id) is False


# ---------------- 스위치 켜짐 ----------------
@pytest.fixture
def enforced(monkeypatch):
    monkeypatch.setattr(settings, "external_policy_enforced", True)


def test_enforced_blocks_external_llm_until_allowed_then_reprocess_works(env, world, enforced, monkeypatch):
    monkeypatch.setattr(settings, "llm_provider", "gemini")
    out, meeting_id, job_id = run_job(env, world["mgr"])
    assert out == "failed" and job_row(env, job_id).error_code == "external_disabled"
    with env["factory"]() as s:
        assert s.get(Meeting, meeting_id).status == "failed"
    detail = call(env, "GET", world["mgr"], f"/api/meetings/{meeting_id}").json()
    assert detail["processing"]["status"] == "failed" and detail["processing"]["errorCode"] == "external_disabled"
    # 허용하면 재처리가 되고 즉시 반영된다(재시작 없음)
    assert put(env, world["sys"], EXTERNAL_LLM_ENABLED, True).status_code == 200
    again = call(env, "POST", world["mgr"], f"/api/meetings/{meeting_id}/reprocess")
    assert again.status_code == 202
    assert process_meeting(again.json()["jobId"], session_factory=env["factory"], extractor_factory=FakeExtractor) == "completed"
    # 다시 막으면 새 처리가 즉시 실패한다
    assert put(env, world["sys"], EXTERNAL_LLM_ENABLED, False, reason="재차단").status_code == 200
    out2, _m2, job2 = run_job(env, world["mgr"])
    assert out2 == "failed" and job_row(env, job2).error_code == "external_disabled"


def test_enforced_does_not_affect_local_or_fake_engines(env, world, enforced):
    assert settings.effective_llm_provider == "fake"
    out, _meeting_id, job_id = run_job(env, world["mgr"])
    assert out == "completed" and job_row(env, job_id).error_code is None


def test_enforced_gemini_stt_is_also_gated(env, world, enforced, monkeypatch):
    monkeypatch.setattr(settings, "stt_provider", "gemini")
    out, _m, job_id = run_job(env, world["mgr"])
    assert out == "failed" and job_row(env, job_id).error_code == "external_disabled"


def test_enforced_local_external_host_follows_system_setting(env, world, enforced, monkeypatch):
    res = upload(env, world["mgr"])
    meeting_id = res.json()["meetingId"]
    # 스위치가 켜지면 시스템 설정을 따른다. 행이 없을 때의 초기값은 .env 값이다(기본 거짓 → 거부)
    assert external_policy.local_external_host_allowed(env["factory"], meeting_id) is False
    monkeypatch.setattr(settings, "local_llm_allow_external_host", True)
    assert external_policy.local_external_host_allowed(env["factory"], meeting_id) is True  # 행이 없으면 .env 값
    assert put(env, world["sys"], LOCAL_LLM_EXTERNAL_HOST_ALLOWED, False, reason="회수 먼저").status_code == 200
    assert external_policy.local_external_host_allowed(env["factory"], meeting_id) is False  # 행이 있으면 .env 가 허용이어도 행을 따른다
    assert put(env, world["sys"], LOCAL_LLM_EXTERNAL_HOST_ALLOWED, True).status_code == 200
    assert external_policy.local_external_host_allowed(env["factory"], meeting_id) is True  # 즉시 반영
    assert put(env, world["sys"], LOCAL_LLM_EXTERNAL_HOST_ALLOWED, False, reason="회수").status_code == 200
    assert external_policy.local_external_host_allowed(env["factory"], meeting_id) is False
    # 실제 처리 경로: 외부 주소는 거부되어 llm_host_blocked 로 실패
    monkeypatch.setattr(settings, "llm_provider", "local")
    monkeypatch.setattr(settings, "local_llm_base_url", "https://llm.example.com/v1")
    monkeypatch.setattr(settings, "local_llm_model", "m")
    out = process_meeting(res.json()["jobId"], session_factory=env["factory"])
    assert out == "failed" and job_row(env, res.json()["jobId"]).error_code == "llm_host_blocked"


def test_decision_rule_lives_in_one_module():
    """허용 판단은 external_policy 한 곳이다: 설정 키 이름을 쓰는 곳은 설정 서비스·정책 모듈·관리 API 뿐이다."""
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent / "app"
    users = sorted(
        p.relative_to(root).as_posix() for p in root.rglob("*.py")
        if any(name in p.read_text(encoding="utf-8") for name in ("EXTERNAL_LLM_ENABLED", "enforce_external_policy", "local_external_host_allowed"))
    )
    assert users == [
        "services/external_policy.py", "services/extraction_engine.py", "services/processing.py", "services/system_settings.py",
    ]


# ---------------- 마이그레이션 ----------------
def test_migration_up_down_refuses_with_rows_and_offline_postgresql(tmp_path):
    url = f"sqlite:///{(tmp_path / 'mig.db').as_posix()}"
    cfg = _alembic(url)
    command.upgrade(cfg, "head")
    engine = create_engine(url)
    with engine.begin() as conn:
        conn.execute(text("INSERT INTO tenants (name, auto_confirm_days, created_at) VALUES ('t', 3, '2026-01-01 00:00:00')"))
        conn.execute(text(
            "INSERT INTO system_settings (tenant_id, key, value_bool, updated_at) "
            "VALUES (1, 'external_llm_enabled', 1, '2026-01-01 00:00:00')"
        ))
        # 허용 목록 밖 키는 CHECK 로 거부
        with pytest.raises(Exception, match="(?i)check|constraint"):
            conn.execute(text(
                "INSERT INTO system_settings (tenant_id, key, value_bool, updated_at) VALUES (1, 'other_key', 1, '2026-01-01 00:00:00')"
            ))
    with engine.begin() as conn:
        with pytest.raises(Exception, match="(?i)unique|constraint"):  # (tenant_id, key) 유니크
            conn.execute(text(
                "INSERT INTO system_settings (tenant_id, key, value_bool, updated_at) "
                "VALUES (1, 'external_llm_enabled', 0, '2026-01-01 00:00:00')"
            ))
    with pytest.raises(Exception, match="downgrade 거부"):
        command.downgrade(cfg, "c4e8a2f7b913")
    with engine.begin() as conn:
        conn.execute(text("DELETE FROM system_settings"))
    command.downgrade(cfg, "c4e8a2f7b913")
    with engine.connect() as conn:
        tables = {r[0] for r in conn.execute(text("SELECT name FROM sqlite_master WHERE type='table'"))}
    assert "system_settings" not in tables and "system_admins" in tables
    command.upgrade(cfg, "head")
    engine.dispose()
    pg = _alembic("postgresql+psycopg://u:p@localhost/db")
    buffer = io.StringIO()
    pg.output_buffer = buffer
    command.upgrade(pg, "head", sql=True)
    sql = buffer.getvalue()
    assert "CREATE TABLE system_settings" in sql and "ck_system_settings_key_valid" in sql
    assert "uq_system_settings_tenant_key" in sql and "ix_system_settings_tenant_id" in sql
