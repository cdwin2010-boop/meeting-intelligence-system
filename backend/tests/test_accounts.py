"""같은 고객사 계정 목록 API(GET /api/accounts): 같은 고객사만, 다른 고객사·비활성 제외, 미로그인 401, 민감 항목 없음."""
import secrets
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

from app.auth.passwords import hash_password
from app.auth.tokens import create_access_token
from app.db import get_session, make_engine
from app.main import app
from app.models import Account, Tenant

_BACKEND = Path(__file__).resolve().parent.parent


@pytest.fixture
def db(tmp_path):
    """테스트마다 새 임시 DB에 마이그레이션을 적용하고, API 가 이 DB를 쓰도록 의존성을 바꾼다."""
    url = f"sqlite:///{(tmp_path / 'accounts.db').as_posix()}"
    cfg = Config(str(_BACKEND / "alembic.ini"))
    cfg.set_main_option("sqlalchemy.url", url)
    command.upgrade(cfg, "head")
    engine = make_engine(url)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)

    def _override():
        with factory() as s:
            yield s

    app.dependency_overrides[get_session] = _override
    try:
        yield factory
    finally:
        app.dependency_overrides.pop(get_session, None)
        engine.dispose()


@pytest.fixture
def client(db):
    return TestClient(app)


@pytest.fixture
def people(db):
    """고객사 A: 지시자·중간관리자·담당자·비활성 담당자, 고객사 B: 담당자 1명"""
    with db() as s:
        a, b = Tenant(name="A사"), Tenant(name="B사")
        s.add_all([a, b])
        s.flush()

        def acc(tenant, login, name, rank, active=True):
            row = Account(tenant_id=tenant.id, login_id=login, password_hash=hash_password(secrets.token_urlsafe(12)),
                          name=name, email=f"{login}@example.com", rank=rank, is_active=active)
            s.add(row)
            return row

        ids = {
            "exec": acc(a, "a-exec", "권부장", "executive"),
            "mgr": acc(a, "a-mgr", "한팀장", "manager"),
            "staff": acc(a, "a-staff", "김대리", "staff"),
            "inactive": acc(a, "a-off", "퇴사자", "staff", active=False),
            "other": acc(b, "b-staff", "타사직원", "staff"),
        }
        s.commit()
        return {key: row.id for key, row in ids.items()}


def auth(account_id: int) -> dict[str, str]:
    return {"Authorization": f"Bearer {create_access_token(account_id)}"}


def test_same_tenant_active_accounts_only(client, people):
    res = client.get("/api/accounts", headers=auth(people["staff"]))
    assert res.status_code == 200
    ids = {row["id"] for row in res.json()}
    # 같은 고객사 활성 계정(본인 포함)만
    assert ids == {people["exec"], people["mgr"], people["staff"]}
    assert {row["rank"] for row in res.json()} == {"executive", "manager", "staff"}


def test_other_tenant_excluded_both_ways(client, people):
    a_ids = {row["id"] for row in client.get("/api/accounts", headers=auth(people["exec"])).json()}
    assert people["other"] not in a_ids
    b_rows = client.get("/api/accounts", headers=auth(people["other"])).json()
    assert [row["id"] for row in b_rows] == [people["other"]]


def test_requires_login(client, people):
    assert client.get("/api/accounts").status_code == 401
    assert client.get("/api/accounts", headers={"Authorization": "Bearer not-a-token"}).status_code == 401


def test_response_has_no_sensitive_fields(client, people):
    rows = client.get("/api/accounts", headers=auth(people["mgr"])).json()
    assert rows
    for row in rows:
        assert set(row) == {"id", "name", "rank"}
    body = client.get("/api/accounts", headers=auth(people["mgr"])).text
    for leaked in ("@example.com", "a-exec", "a-mgr", "a-staff", "argon2", "password", "loginId", "email", "tenantId"):
        assert leaked not in body
