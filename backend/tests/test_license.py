"""라이선스(사용 허가) 검증 모듈·발급 도구·상태 조회 API(작업 80-1). 임시 키쌍만 쓰며 저장소에 키 파일이 없다."""
import base64
import importlib.util
import json
import secrets
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from app.auth.passwords import hash_password
from app.auth.tokens import create_access_token
from app.config import Settings, settings
from app.db import get_session, make_engine
from app.licensing import verifier
from app.licensing.verifier import canonical_bytes, verify_license, verify_license_bytes
from app.main import app
from app.models import Account, Tenant
from app.services import license_status

ROOT = Path(__file__).resolve().parent.parent
NOW = datetime(2026, 10, 12, 0, 0, tzinfo=timezone.utc)
FP = "a" * 64


def _pem_private(key: Ed25519PrivateKey) -> bytes:
    return key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
    )


def _pem_public(key: Ed25519PrivateKey) -> bytes:
    return key.public_key().public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)


def _tool(name: str):
    spec = importlib.util.spec_from_file_location(f"{name}_tool", ROOT / "scripts" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def keypair():
    return Ed25519PrivateKey.generate()


@pytest.fixture
def issue(keypair):
    """발급 도구와 같은 함수로 .lic 문서를 만든다."""
    tool = _tool("issue_license")

    def _issue(*, days=365, max_accounts=10, fingerprint=None, key=None, **extra):
        doc = tool.build_license(
            key or keypair, licensee="테스트 고객사", max_accounts=max_accounts,
            expires_at=NOW + timedelta(days=days), issued_at=NOW - timedelta(days=1),
            license_id="LIC-1", fingerprint=fingerprint,
        )
        doc["payload"].update(extra)
        return doc

    return _issue


def _bytes(doc) -> bytes:
    return json.dumps(doc, ensure_ascii=False).encode("utf-8")


def check(doc_bytes, keypair, *, active=3, fp=FP, warn=30, bind=False, now=NOW, public=None):
    return verify_license_bytes(doc_bytes, public if public is not None else _pem_public(keypair), now, fp, active, warn, bind)


# ---------------- 상태 판정 ----------------
def test_valid_license(issue, keypair):
    st = check(_bytes(issue()), keypair)
    assert st.state == "valid" and st.licensee == "테스트 고객사" and st.max_accounts == 10
    assert st.days_left == 365 and st.over_limit is False and st.hardware_match is None and st.warnings == ()


def test_expiring_soon_boundary(issue, keypair):
    assert check(_bytes(issue(days=30)), keypair).state == "expiring_soon"
    assert check(_bytes(issue(days=31)), keypair).state == "valid"
    assert check(_bytes(issue(days=10)), keypair, warn=5).state == "valid"


def test_expired(issue, keypair):
    st = check(_bytes(issue(days=-1)), keypair)
    assert st.state == "expired" and st.days_left < 0 and "만료" in st.message


def test_over_limit_is_flag_not_state(issue, keypair):
    st = check(_bytes(issue(max_accounts=2)), keypair, active=5)
    assert st.state == "valid" and st.over_limit is True and st.active_accounts == 5 and st.warnings
    assert check(_bytes(issue(max_accounts=5)), keypair, active=5).over_limit is False


def test_hardware_binding_on_and_off(issue, keypair):
    bound = _bytes(issue(fingerprint=FP))
    assert check(bound, keypair, bind=True, fp=FP).state == "valid"
    assert check(bound, keypair, bind=True, fp=FP).hardware_match is True
    mismatch = check(bound, keypair, bind=True, fp="b" * 64)
    assert mismatch.state == "hardware_mismatch" and mismatch.hardware_match is False
    assert check(bound, keypair, bind=False, fp="b" * 64).state == "valid"  # 바인딩 끔
    unbound = _bytes(issue())
    assert check(unbound, keypair, bind=True, fp="b" * 64).state == "valid"  # 핑거프린트 없는 라이선스


def test_missing_malformed_and_public_key_missing(issue, keypair, tmp_path):
    assert check(None, keypair).state == "missing"
    assert verify_license(tmp_path / "none.lic", tmp_path / "none.pem", NOW, FP, 0).state == "missing"
    doc = _bytes(issue())
    assert verify_license_bytes(doc, None, NOW, FP, 0).state == "public_key_missing"
    assert check(doc, keypair, public=b"not a pem").state == "public_key_missing"
    other_algo = b"-----BEGIN PUBLIC KEY-----\nAAAA\n-----END PUBLIC KEY-----\n"
    assert check(doc, keypair, public=other_algo).state == "public_key_missing"
    for bad in (b"{not json", b"[]", b'{"payload": {}}', b'{"payload": 1, "signature": "x"}',
                b'{"payload": {}, "signature": "***"}', "한".encode("cp949"), b""):
        assert check(bad, keypair).state == "malformed", bad


def test_valid_signature_but_bad_fields_is_malformed(keypair):
    base = {"version": 1, "license_id": "L", "licensee": "x", "max_accounts": 1,
            "issued_at": "2026-01-01T00:00:00Z", "expires_at": "2027-01-01T00:00:00Z"}
    variants = [
        {**base, "version": 2}, {**base, "max_accounts": -1}, {**base, "max_accounts": "5"}, {**base, "max_accounts": True},
        {**base, "expires_at": "내일"}, {**base, "licensee": ""}, {**{k: v for k, v in base.items() if k != "licensee"}},
        {**base, "features": ["export"]},  # 기능 플래그 필드는 허용하지 않는다
    ]
    for payload in variants:
        doc = {"payload": payload, "signature": base64.b64encode(keypair.sign(canonical_bytes(payload))).decode()}
        assert check(_bytes(doc), keypair).state == "malformed", payload
    ok = {"payload": base, "signature": base64.b64encode(keypair.sign(canonical_bytes(base))).decode()}
    assert check(_bytes(ok), keypair, now=datetime(2026, 6, 1, tzinfo=timezone.utc)).state == "valid"


# ---------------- 서명 ----------------
def test_tampered_payload_and_other_key_are_rejected(issue, keypair):
    doc = issue()
    doc["payload"]["max_accounts"] = 9999
    assert check(_bytes(doc), keypair).state == "invalid_signature"
    forged = issue(key=Ed25519PrivateKey.generate())
    assert check(_bytes(forged), keypair).state == "invalid_signature"
    broken = issue()
    broken["signature"] = base64.b64encode(b"\x00" * 64).decode()
    assert check(_bytes(broken), keypair).state == "invalid_signature"


def test_invalid_states_do_not_expose_payload(issue, keypair):
    doc = issue()
    doc["payload"]["licensee"] = "변조"
    st = check(_bytes(doc), keypair)
    assert st.state == "invalid_signature" and st.licensee is None and st.max_accounts is None


def test_canonicalization_is_key_order_and_whitespace_stable(issue, keypair):
    doc = issue()
    reordered = {"signature": doc["signature"], "payload": dict(reversed(list(doc["payload"].items())))}
    text = json.dumps(reordered, indent=4, ensure_ascii=False)
    assert check(text.encode("utf-8"), keypair).state == "valid"
    a = {"b": 1, "a": "한"}
    b = {"a": "한", "b": 1}
    assert canonical_bytes(a) == canonical_bytes(b) == '{"a":"한","b":1}'.encode("utf-8")


def test_verify_never_raises_on_garbage(keypair):
    for garbage in (b"\xff\xfe\x00", b"null", b"123", b'{"payload": {"expires_at": []}, "signature": "AA=="}'):
        assert check(garbage, keypair).state in {"malformed", "invalid_signature"}


# ---------------- 핑거프린트 ----------------
def test_fingerprint_is_hash_and_hides_raw_value(monkeypatch):
    digest, _warnings = verifier.device_fingerprint()
    assert len(digest) == 64 and all(c in "0123456789abcdef" for c in digest)
    assert verifier.device_fingerprint()[0] == digest  # 같은 장비에서 안정적


def test_fingerprint_fallback_warns_without_leaking(monkeypatch):
    monkeypatch.setattr(verifier, "winreg", None)
    monkeypatch.setattr(verifier.Path, "read_text", lambda *a, **k: (_ for _ in ()).throw(OSError("secret-raw-value")))
    digest, warnings = verifier.device_fingerprint()
    assert len(digest) == 64 and warnings and "secret-raw-value" not in "".join(warnings)


# ---------------- 발급 도구 왕복 ----------------
def test_issue_tool_roundtrip_and_options(tmp_path, keypair, capsys):
    key_path = tmp_path / "issuer.pem"
    key_path.write_bytes(_pem_private(keypair))
    pub_path = tmp_path / "pub.pem"
    pub_path.write_bytes(_pem_public(keypair))
    out = tmp_path / "customer.lic"
    tool = _tool("issue_license")
    future = (datetime.now(timezone.utc) + timedelta(days=200)).strftime("%Y-%m-%d")
    code = tool.main(["--private-key", str(key_path), "--licensee", "한국고객", "--max-accounts", "7", "--expires", future,
                      "--out", str(out), "--fingerprint", FP.upper()])
    assert code == 0
    printed = capsys.readouterr().out
    assert "BEGIN" not in printed and "PRIVATE" not in printed
    st = verify_license(out, pub_path, datetime.now(timezone.utc), FP, 1, 30, True)
    assert st.state == "valid" and st.licensee == "한국고객" and st.max_accounts == 7 and st.hardware_match is True
    payload = json.loads(out.read_text(encoding="utf-8"))["payload"]
    assert payload["hardware_fingerprint"] == FP and set(payload) <= verifier.PAYLOAD_KEYS
    # 이미 있으면 거부, --force 면 덮어쓴다
    args = ["--private-key", str(key_path), "--licensee", "x", "--max-accounts", "1", "--expires", future, "--out", str(out)]
    assert tool.main(args) == 2
    assert tool.main(args + ["--force"]) == 0
    assert tool.main(["--private-key", str(key_path), "--licensee", "x", "--max-accounts", "-1", "--expires", future,
                      "--out", str(tmp_path / "z.lic")]) == 2
    assert tool.main(["--private-key", str(tmp_path / "missing.pem"), "--licensee", "x", "--max-accounts", "1",
                      "--expires", future, "--out", str(tmp_path / "y.lic")]) == 2
    assert not (tmp_path / "y.lic").exists()


def test_show_fingerprint_tool_prints_only_hash(capsys):
    assert _tool("show_fingerprint").main() == 0
    out = capsys.readouterr().out.strip()
    assert len(out) == 64 and out == verifier.device_fingerprint()[0]


def test_check_license_tool(tmp_path, keypair, monkeypatch, capsys):
    pub = tmp_path / "pub.pem"
    pub.write_bytes(_pem_public(keypair))
    lic = tmp_path / "c.lic"
    doc = _tool("issue_license").build_license(
        keypair, licensee="점검사", max_accounts=3, expires_at=datetime.now(timezone.utc) + timedelta(days=100)
    )
    lic.write_text(json.dumps(doc), encoding="utf-8")
    monkeypatch.setattr(settings, "license_path", lic)
    monkeypatch.setattr(settings, "license_public_key_path", pub)
    tool = _tool("check_license")
    assert tool.main(["--accounts", "5", "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["status"] == "valid" and data["overLimit"] is True and data["licensee"] == "점검사"
    monkeypatch.setattr(settings, "license_path", tmp_path / "none.lic")
    assert tool.main(["--accounts", "1"]) == 1


# ---------------- 설정 ----------------
def test_settings_defaults_and_validation():
    s = Settings(_env_file=None)
    assert s.license_path == Path("data/license.lic") and s.license_public_key_path == Path("config/license_public.pem")
    assert s.license_warn_days == 30 and s.license_require_hardware_match is False
    with pytest.raises(ValidationError):
        Settings(_env_file=None, license_warn_days=-1)


def test_repo_has_no_key_or_license_files():
    suffixes = {".pem", ".key", ".lic"}
    tracked = subprocess.run(["git", "ls-files"], cwd=ROOT.parent, capture_output=True, text=True, check=True).stdout
    assert [f for f in tracked.splitlines() if Path(f).suffix in suffixes] == []


# ---------------- API ----------------
@pytest.fixture
def db(tmp_path):
    url = f"sqlite:///{(tmp_path / 'lic.db').as_posix()}"
    cfg = Config(str(ROOT / "alembic.ini"))
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


def _add(factory, login_id, rank, is_active=True) -> int:
    with factory() as s:
        tenant = s.scalar(select(Tenant).where(Tenant.name == "고객사"))
        if tenant is None:
            tenant = Tenant(name="고객사")
            s.add(tenant)
            s.flush()
        account = Account(tenant_id=tenant.id, login_id=login_id, password_hash=hash_password(secrets.token_urlsafe(12)),
                          name=login_id, email=f"{login_id}@example.com", rank=rank, is_active=is_active)
        s.add(account)
        s.commit()
        return account.id


def _headers(account_id: int) -> dict:
    return {"Authorization": f"Bearer {create_access_token(account_id)}"}


@pytest.fixture
def licensed(tmp_path, keypair, monkeypatch):
    """설정을 임시 .lic·공개키로 돌리고 캐시를 비운다."""
    pub = tmp_path / "pub.pem"
    pub.write_bytes(_pem_public(keypair))
    lic = tmp_path / "api.lic"
    monkeypatch.setattr(settings, "license_path", lic)
    monkeypatch.setattr(settings, "license_public_key_path", pub)
    license_status._file_cache.clear()

    def write(days=200, max_accounts=10, fingerprint=None):
        doc = _tool("issue_license").build_license(
            keypair, licensee="API 고객사", max_accounts=max_accounts,
            expires_at=datetime.now(timezone.utc) + timedelta(days=days), fingerprint=fingerprint,
        )
        lic.write_text(json.dumps(doc), encoding="utf-8")

    return write


def test_api_permissions(db, licensed):
    client = TestClient(app)
    staff, manager, executive = _add(db, "s", "staff"), _add(db, "m", "manager"), _add(db, "e", "executive")
    assert client.get("/api/system/license").status_code == 401
    assert client.get("/api/system/license", headers=_headers(staff)).status_code == 403
    assert client.get("/api/system/license", headers=_headers(manager)).status_code == 200
    assert client.get("/api/system/license", headers=_headers(executive)).status_code == 200


def test_api_response_states_and_no_sensitive_values(db, licensed, monkeypatch):
    client = TestClient(app)
    manager = _add(db, "m", "manager")
    _add(db, "off", "staff", is_active=False)
    h = _headers(manager)
    body = client.get("/api/system/license", headers=h).json()
    assert body["status"] == "missing" and body["activeAccounts"] == 1 and body["licensee"] is None
    licensed(days=200, max_accounts=1)
    body = client.get("/api/system/license", headers=h).json()
    assert body["status"] == "valid" and body["licensee"] == "API 고객사" and body["maxAccounts"] == 1
    assert body["activeAccounts"] == 1 and body["overLimit"] is False and body["daysLeft"] in (199, 200)
    _add(db, "extra", "staff")
    body = client.get("/api/system/license", headers=h).json()
    assert body["overLimit"] is True and body["warnings"] and body["status"] == "valid"  # 차단하지 않고 표시만
    assert set(body) == {"status", "licensee", "maxAccounts", "activeAccounts", "overLimit", "expiresAt", "daysLeft",
                         "hardwareMatch", "message", "warnings"}
    text = json.dumps(body)
    assert "signature" not in text.lower() and license_status.current_fingerprint()[0] not in text
    # 파일을 바꾸면 수정 시각 기준으로 다시 읽는다
    licensed(days=5, max_accounts=10)
    assert client.get("/api/system/license", headers=h).json()["status"] == "expiring_soon"
    licensed(days=100, max_accounts=10, fingerprint="c" * 64)
    monkeypatch.setattr(settings, "license_require_hardware_match", True)
    body = client.get("/api/system/license", headers=h).json()
    assert body["status"] == "hardware_mismatch" and body["hardwareMatch"] is False and "c" * 64 not in json.dumps(body)


def test_existing_engine_response_is_unchanged(db, licensed):
    client = TestClient(app)
    staff = _add(db, "s", "staff")
    body = client.get("/api/system/engine", headers=_headers(staff)).json()
    assert set(body) == {"engine", "isFake", "processingMode", "queuePending", "queueProcessing"}


def test_license_module_is_not_imported_by_processing_pipeline():
    """라이선스는 표시·조회용이며 처리 흐름을 막지 않는다(처리·작업 모듈이 import 하지 않음)."""
    for rel in ("app/services/processing.py", "app/worker_loop.py", "app/api/meetings.py", "app/api/accounts.py"):
        assert "licens" not in (ROOT / rel).read_text(encoding="utf-8").lower(), rel
