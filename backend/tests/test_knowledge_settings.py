"""지식(KMS) 연동 설정 키(작업 83-1 파트 A): 기본값, 시스템 관리자 전용, 사유 필수, 주소 검사, 이력, 기존 설정과의 분리, 마이그레이션."""
import io

import pytest
from alembic import command
from sqlalchemy import create_engine, select, text

from app.config import settings
from app.models import Event
from tests.test_system_settings import call, get_settings, put, world  # noqa: F401  (world 는 픽스처)
from tests.test_upload_processing import _alembic, env  # noqa: F401  (env 는 픽스처)

ENABLED = "knowledge_integration_enabled"
ENDPOINT = "knowledge_api_endpoint"
SECRET_ADDR = "http://192.168.10.20:9000/api"


def knowledge(env, account) -> dict:
    return {i["key"]: i for i in get_settings(env, account)["knowledge"]}


def test_defaults_are_off_and_empty_and_legacy_items_unchanged(env, world):
    body = get_settings(env, world["sys"])
    assert [i["key"] for i in body["items"]] == ["external_llm_enabled", "local_llm_external_host_allowed"]  # 기존 화면 호환
    k = knowledge(env, world["sys"])
    assert k[ENABLED]["value"] is False and k[ENABLED]["source"] == "default"
    assert k[ENDPOINT]["value"] == "" and k[ENDPOINT]["source"] == "default"


def test_env_value_is_used_when_no_row(env, world, monkeypatch):
    monkeypatch.setattr(settings, "knowledge_integration_enabled", True)
    monkeypatch.setattr(settings, "__pydantic_fields_set__", set(settings.model_fields_set) | {"knowledge_integration_enabled"})
    item = knowledge(env, world["sys"])[ENABLED]
    assert item["value"] is True and item["source"] == "env"
    # 시스템 관리자 설정이 생기면 그 값이 우선한다
    assert put(env, world["sys"], ENABLED, False, reason="협의 전").status_code == 200
    item = knowledge(env, world["sys"])[ENABLED]
    assert item["value"] is False and item["source"] == "admin"


def test_only_system_admin_can_change_and_reason_is_required(env, world):
    for key in ("staff", "mgr"):
        assert put(env, world[key], ENABLED, True).status_code == 403
        assert put(env, world[key], ENDPOINT, SECRET_ADDR).status_code == 403
    assert env["client"].put(f"/api/admin/settings/{ENABLED}", json={"value": True, "reason": "x"}).status_code == 401
    sysadmin = world["sys"]
    for reason in ("", "   ", "가" * 501):
        assert put(env, sysadmin, ENABLED, True, reason=reason).status_code == 422
        assert put(env, sysadmin, ENDPOINT, SECRET_ADDR, reason=reason).status_code == 422
    assert knowledge(env, sysadmin)[ENABLED]["value"] is False  # 거부된 변경은 반영되지 않는다


def test_change_is_recorded_with_reason_and_hides_the_address(env, world):
    sysadmin = world["sys"]
    assert put(env, sysadmin, ENABLED, True, reason="연동 시험 시작").json()["value"] is True
    res = put(env, sysadmin, ENDPOINT, SECRET_ADDR, reason="내부 서버 지정")
    assert res.status_code == 200 and res.json()["value"] == SECRET_ADDR and res.json()["source"] == "admin"
    assert knowledge(env, sysadmin)[ENDPOINT]["value"] == SECRET_ADDR
    audit = call(env, "GET", sysadmin, "/api/admin/audit?size=50").json()["items"]
    changed = [e for e in audit if e["eventType"] == "admin.setting_changed"]
    assert {e["reason"] for e in changed} >= {"연동 시험 시작", "내부 서버 지정"}
    assert SECRET_ADDR not in str(audit) and "192.168" not in str(audit)  # 이력에는 주소 전체를 남기지 않는다
    with env["factory"]() as s:
        assert all("192.168" not in str(e.payload) for e in s.scalars(select(Event)).all())


@pytest.mark.parametrize("address", ["http://127.0.0.1:8080", "http://localhost:9000/api", "https://192.168.0.5/api/", "http://10.1.2.3", "http://kms-internal"])
def test_private_and_loopback_addresses_are_accepted(env, world, address):
    res = put(env, world["sys"], ENDPOINT, address)
    assert res.status_code == 200 and res.json()["value"] == address.rstrip("/")


@pytest.mark.parametrize("address", ["https://kms.example.com", "http://8.8.8.8/api", "https://tunnel.trycloudflare.com/x"])
def test_external_addresses_are_rejected_with_422(env, world, address):
    res = put(env, world["sys"], ENDPOINT, address)
    assert res.status_code == 422 and "사설망" in res.json()["detail"]
    assert knowledge(env, world["sys"])[ENDPOINT]["value"] == ""


@pytest.mark.parametrize("address", ["ftp://192.168.0.1", "not a url", "//host/x", "http://"])
def test_malformed_addresses_are_rejected_with_422(env, world, address):
    assert put(env, world["sys"], ENDPOINT, address).status_code == 422


def test_empty_address_clears_and_types_are_enforced(env, world):
    sysadmin = world["sys"]
    assert put(env, sysadmin, ENDPOINT, SECRET_ADDR).status_code == 200
    cleared = put(env, sysadmin, ENDPOINT, "")
    assert cleared.status_code == 200 and cleared.json()["value"] == ""
    assert put(env, sysadmin, ENDPOINT, True).status_code == 422  # 주소 키에 참·거짓
    assert put(env, sysadmin, ENABLED, "http://127.0.0.1").status_code == 422  # 참·거짓 키에 문자열
    assert put(env, sysadmin, "knowledge_other", True).status_code == 404


def test_knowledge_and_external_llm_settings_are_independent(env, world):
    sysadmin = world["sys"]
    assert put(env, sysadmin, "external_llm_enabled", True).status_code == 200
    k = knowledge(env, sysadmin)
    assert k[ENABLED]["value"] is False and k[ENDPOINT]["value"] == ""  # 외부 LLM 허용이 지식 연동에 영향 없음
    assert put(env, sysadmin, ENABLED, True).status_code == 200
    assert put(env, sysadmin, ENDPOINT, SECRET_ADDR).status_code == 200
    legacy = {i["key"]: i["value"] for i in get_settings(env, sysadmin)["items"]}
    assert legacy == {"external_llm_enabled": True, "local_llm_external_host_allowed": False}
    assert put(env, sysadmin, "external_llm_enabled", False, reason="회수").status_code == 200
    k = knowledge(env, sysadmin)
    assert k[ENABLED]["value"] is True and k[ENDPOINT]["value"] == SECRET_ADDR  # 반대 방향도 영향 없음


def test_service_token_and_timeout_settings():
    from app.config import Settings

    s = Settings(_env_file=None)
    assert s.knowledge_integration_enabled is False and s.knowledge_api_endpoint == ""
    assert s.knowledge_service_token.get_secret_value() == "" and s.knowledge_timeout_seconds == 3
    from pydantic import ValidationError

    for bad in (0, -1, 31):
        with pytest.raises(ValidationError):
            Settings(_env_file=None, knowledge_timeout_seconds=bad)


def test_token_value_is_never_serialized(env, world, monkeypatch):
    from pydantic import SecretStr

    monkeypatch.setattr(settings, "knowledge_service_token", SecretStr("svc-token-VALUE-123"))
    text_out = str(get_settings(env, world["sys"])) + str(call(env, "GET", world["sys"], "/api/admin/audit").json())
    assert "svc-token-VALUE-123" not in text_out


# ---------------- 마이그레이션 ----------------
def test_migration_up_down_and_offline_postgresql(tmp_path):
    url = f"sqlite:///{(tmp_path / 'k.db').as_posix()}"
    cfg = _alembic(url)
    command.upgrade(cfg, "head")
    engine = create_engine(url)
    with engine.begin() as conn:
        conn.execute(text("INSERT INTO tenants (name, auto_confirm_days, created_at) VALUES ('t', 3, '2026-01-01 00:00:00')"))
        conn.execute(text(
            "INSERT INTO system_settings (tenant_id, key, value_bool, value_text, updated_at) "
            "VALUES (1, 'knowledge_api_endpoint', 1, 'http://127.0.0.1', '2026-01-01 00:00:00')"
        ))
    with engine.begin() as conn:
        with pytest.raises(Exception, match="(?i)check|constraint"):
            conn.execute(text(
                "INSERT INTO system_settings (tenant_id, key, value_bool, updated_at) VALUES (1, 'other_key', 1, '2026-01-01 00:00:00')"
            ))
    with pytest.raises(Exception, match="downgrade 거부"):
        command.downgrade(cfg, "d5f9b3a8c024")
    with engine.begin() as conn:
        conn.execute(text("DELETE FROM system_settings"))
        conn.execute(text(
            "INSERT INTO system_settings (tenant_id, key, value_bool, updated_at) VALUES (1, 'external_llm_enabled', 1, '2026-01-01 00:00:00')"
        ))
    command.downgrade(cfg, "d5f9b3a8c024")  # 기존 키 행만 있으면 되돌릴 수 있다
    with engine.connect() as conn:
        columns = {r[1] for r in conn.execute(text("PRAGMA table_info(system_settings)"))}
        assert "value_text" not in columns
        assert conn.execute(text("SELECT COUNT(*) FROM system_settings")).scalar_one() == 1  # 기존 행 보존
    command.upgrade(cfg, "head")
    engine.dispose()
    pg = _alembic("postgresql+psycopg://u:p@localhost/db")
    buffer = io.StringIO()
    pg.output_buffer = buffer
    command.upgrade(pg, "head", sql=True)
    sql = buffer.getvalue()
    assert "ALTER TABLE system_settings ADD COLUMN value_text VARCHAR(500)" in sql
    assert "knowledge_integration_enabled" in sql and "knowledge_api_endpoint" in sql
    assert sql.count("ck_system_settings_key_valid") >= 2  # 기존 CHECK 삭제 후 확장 CHECK 생성
