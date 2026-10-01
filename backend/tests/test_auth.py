"""3단계: ID 로그인, 토큰 검증, 직급 권한, 고객사 범위(scoped), 계정 생성 스크립트.
비밀번호·토큰 값은 assert 실패 메시지에도 찍히지 않도록 직접 비교하지 않는다."""
import base64
import json
import secrets
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import jwt
import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.auth.passwords import hash_password, verify_password
from app.auth.scope import scoped
from app.auth.tokens import create_access_token
from app.config import DEV_SECRET_KEY, Settings
from app.db import get_session, make_engine
from app.main import app
from app.models import Account, Meeting, SourceDocument, Tenant

_BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_BACKEND / "scripts"))
import create_account as create_account_script  # noqa: E402

LOGIN_FAILED = "ID 또는 비밀번호가 올바르지 않습니다"


@pytest.fixture
def db(tmp_path):
    """테스트마다 새 임시 DB에 마이그레이션을 적용하고, API 가 이 DB를 쓰도록 의존성을 바꾼다."""
    url = f"sqlite:///{(tmp_path / 'auth.db').as_posix()}"
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
def password():
    # 실행마다 무작위 비밀번호(저장소에 고정 비밀번호를 두지 않음)
    return secrets.token_urlsafe(16)


def _add_account(factory, *, tenant_name: str, login_id: str, rank: str, password: str, is_active: bool = True) -> Account:
    with factory() as s:
        tenant = s.scalar(select(Tenant).where(Tenant.name == tenant_name))
        if tenant is None:
            tenant = Tenant(name=tenant_name)
            s.add(tenant)
            s.flush()
        account = Account(
            tenant_id=tenant.id,
            login_id=login_id,
            password_hash=hash_password(password),
            name=f"{login_id}-이름",
            email=f"{login_id}@example.com",
            rank=rank,
            is_active=is_active,
        )
        s.add(account)
        s.commit()
        return account


def _login(client: TestClient, login_id: str, password: str):
    return client.post("/api/auth/login", json={"loginId": login_id, "password": password})


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


# ---- 로그인 ----


def test_login_success_returns_token(client, db, password):
    _add_account(db, tenant_name="고객사A", login_id="kim", rank="staff", password=password)
    res = _login(client, "kim", password)
    assert res.status_code == 200
    body = res.json()
    assert set(body) == {"accessToken", "tokenType"}
    assert body["tokenType"] == "bearer"


def test_login_failures_are_identical(client, db, password):
    """틀린 비밀번호·없는 ID·비활성 계정은 같은 상태 코드와 같은 메시지."""
    _add_account(db, tenant_name="고객사A", login_id="kim", rank="staff", password=password)
    _add_account(db, tenant_name="고객사A", login_id="off", rank="staff", password=password, is_active=False)

    responses = [
        _login(client, "kim", password + "x"),  # 틀린 비밀번호
        _login(client, "nobody", password),  # 없는 ID
        _login(client, "off", password),  # 비활성 계정(비밀번호는 맞음)
    ]
    assert [r.status_code for r in responses] == [401, 401, 401]
    assert all(r.json() == {"detail": LOGIN_FAILED} for r in responses)


def test_password_hash_differs_from_plaintext(password):
    hashed = hash_password(password)
    same = hashed == password
    contains = password in hashed
    assert same is False
    assert contains is False
    assert verify_password(hashed, password) is True
    assert verify_password(hashed, password + "x") is False


# ---- 토큰 ----


def test_me_returns_account(client, db, password):
    account = _add_account(db, tenant_name="고객사A", login_id="lee", rank="manager", password=password)
    token = _login(client, "lee", password).json()["accessToken"]
    res = client.get("/api/auth/me", headers=_auth(token))
    assert res.status_code == 200
    assert res.json() == {"id": account.id, "name": "lee-이름", "rank": "manager", "tenantId": account.tenant_id}


def test_token_carries_only_account_id(client, db, password):
    account = _add_account(db, tenant_name="고객사A", login_id="kim", rank="staff", password=password)
    token = _login(client, "kim", password).json()["accessToken"]
    claims = jwt.decode(token, options={"verify_signature": False})
    assert set(claims) == {"sub", "iat", "exp"}
    assert claims["sub"] == str(account.id)


def test_me_requires_token(client, db):
    assert client.get("/api/auth/me").status_code == 401


def test_expired_token_rejected(client, db, password):
    account = _add_account(db, tenant_name="고객사A", login_id="kim", rank="staff", password=password)
    expired = create_access_token(account.id, expires_delta=timedelta(seconds=-1))
    assert client.get("/api/auth/me", headers=_auth(expired)).status_code == 401


def _b64(data: dict) -> str:
    return base64.urlsafe_b64encode(json.dumps(data).encode()).rstrip(b"=").decode()


@pytest.mark.parametrize("kind", ["other-key", "payload-swapped", "alg-none", "garbage"])
def test_tampered_token_rejected(client, db, password, kind):
    victim = _add_account(db, tenant_name="고객사A", login_id="boss", rank="executive", password=password)
    attacker = _add_account(db, tenant_name="고객사A", login_id="kim", rank="staff", password=password)
    exp = datetime.now(timezone.utc) + timedelta(minutes=5)

    if kind == "other-key":
        token = jwt.encode({"sub": str(victim.id), "exp": exp}, "x" * 48, algorithm="HS256")
    elif kind == "payload-swapped":
        # 정상 토큰의 서명은 그대로 두고 본문의 계정 id만 바꾼다
        header, _, signature = create_access_token(attacker.id).split(".")
        token = ".".join([header, _b64({"sub": str(victim.id), "exp": int(exp.timestamp())}), signature])
    elif kind == "alg-none":
        token = ".".join([_b64({"alg": "none", "typ": "JWT"}), _b64({"sub": str(victim.id), "exp": int(exp.timestamp())}), ""])
    else:
        token = "not-a-token"

    assert client.get("/api/auth/me", headers=_auth(token)).status_code == 401


def test_deactivated_account_token_rejected(client, db, password):
    account = _add_account(db, tenant_name="고객사A", login_id="kim", rank="manager", password=password)
    token = _login(client, "kim", password).json()["accessToken"]
    with db() as s:
        s.get(Account, account.id).is_active = False
        s.commit()
    assert client.get("/api/auth/me", headers=_auth(token)).status_code == 401


# ---- 직급 권한 ----


@pytest.mark.parametrize(("rank", "expected"), [("staff", 403), ("manager", 200), ("executive", 200)])
def test_probe_manager_requires_manager_or_above(client, db, password, rank, expected):
    _add_account(db, tenant_name="고객사A", login_id=f"user-{rank}", rank=rank, password=password)
    token = _login(client, f"user-{rank}", password).json()["accessToken"]
    assert client.get("/api/_probe/manager", headers=_auth(token)).status_code == expected


# ---- 고객사 범위 ----


def _add_meeting(factory, account: Account, title: str) -> None:
    with factory() as s:
        doc = SourceDocument(tenant_id=account.tenant_id, origin="uploaded", doc_type="회의록", title=title, registered_by=account.id)
        s.add(doc)
        s.flush()
        s.add(
            Meeting(
                tenant_id=account.tenant_id,
                source_document_id=doc.id,
                title=title,
                held_at=datetime(2026, 10, 1, tzinfo=timezone.utc),
            )
        )
        s.commit()


def test_scoped_hides_other_tenant_data(db, password):
    """사용 예: 모든 업무 조회는 scoped(select(모델), current_account) 로 감싼다."""
    a = _add_account(db, tenant_name="고객사A", login_id="a-mgr", rank="manager", password=password)
    b = _add_account(db, tenant_name="고객사B", login_id="b-mgr", rank="manager", password=password)
    _add_meeting(db, a, "A 회의")
    _add_meeting(db, b, "B 회의")

    with db() as s:
        titles_a = s.scalars(scoped(select(Meeting), a)).all()
        titles_b = s.scalars(scoped(select(Meeting), b)).all()
    assert [m.title for m in titles_a] == ["A 회의"]
    assert [m.title for m in titles_b] == ["B 회의"]


def test_scoped_rejects_model_without_tenant(db, password):
    a = _add_account(db, tenant_name="고객사A", login_id="a-mgr", rank="manager", password=password)
    with pytest.raises(ValueError):
        scoped(select(Tenant), a)


# ---- 설정 ----


@pytest.mark.parametrize("key", ["", DEV_SECRET_KEY, "too-short"])
def test_non_dev_refuses_default_or_weak_secret(key):
    with pytest.raises(ValidationError):
        Settings(_env_file=None, app_env="prod", secret_key=key)


def test_dev_allows_default_secret():
    assert Settings(_env_file=None, app_env="dev", secret_key="").secret_key.get_secret_value() == DEV_SECRET_KEY


# ---- 계정 생성 스크립트 ----


def test_create_account_script_creates_tenant_and_hashes(db, password):
    with db() as s:
        account = create_account_script.create_account(
            s, tenant_name="새고객사", login_id="park", name="박담당", email="park@example.com", rank="staff", password=password
        )
        s.commit()
        stored_same = account.password_hash == password
        assert stored_same is False
        assert s.scalar(select(Tenant).where(Tenant.name == "새고객사")) is not None

        with pytest.raises(ValueError):
            create_account_script.create_account(
                s, tenant_name="새고객사", login_id="park", name="중복", email="dup@example.com", rank="staff", password=password
            )


def test_create_account_script_has_no_password_argument():
    """비밀번호는 getpass 로만 받는다(인자·환경변수 금지)."""
    source = (_BACKEND / "scripts" / "create_account.py").read_text(encoding="utf-8")
    assert "--password" not in source
    assert "environ" not in source
    assert "getpass.getpass(" in source
