"""CORS: 허용 출처만 브라우저 호출 허용, '*' 설정은 시작 거부. 서버를 띄우지 않고 TestClient 로 확인한다."""
import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.config import Settings
from app.main import app

ALLOWED = "http://localhost:3000"


def preflight(origin: str, method: str = "PATCH", headers: str = "Authorization, Content-Type"):
    return TestClient(app).options(
        "/api/meetings",
        headers={"Origin": origin, "Access-Control-Request-Method": method, "Access-Control-Request-Headers": headers},
    )


@pytest.mark.parametrize("origin", ["http://localhost:3000", "http://127.0.0.1:3000"])
def test_preflight_from_allowed_origin(origin):
    res = preflight(origin)
    assert res.status_code == 200
    assert res.headers["access-control-allow-origin"] == origin
    allowed_headers = {h.strip().lower() for h in res.headers["access-control-allow-headers"].split(",")}
    assert {"authorization", "content-type"} <= allowed_headers
    assert {m.strip() for m in res.headers["access-control-allow-methods"].split(",")} == {"GET", "POST", "PATCH", "OPTIONS"}
    assert "access-control-allow-credentials" not in res.headers


def test_simple_request_from_allowed_origin_gets_header():
    res = TestClient(app).get("/api/health", headers={"Origin": ALLOWED})
    assert res.status_code == 200 and res.headers["access-control-allow-origin"] == ALLOWED


@pytest.mark.parametrize("origin", ["http://evil.example.com", "http://localhost:3001"])
def test_disallowed_origin_gets_no_allow_origin(origin):
    assert "access-control-allow-origin" not in preflight(origin).headers
    res = TestClient(app).get("/api/health", headers={"Origin": origin})
    assert "access-control-allow-origin" not in res.headers


def test_disallowed_method_preflight_rejected():
    res = preflight(ALLOWED, method="DELETE")
    assert res.status_code == 400


@pytest.mark.parametrize("value", ["*", "http://localhost:3000,*", " * "])
def test_wildcard_origin_refuses_start(value):
    with pytest.raises(ValidationError):
        Settings(_env_file=None, cors_origins=value)


def test_default_origins_and_parsing():
    assert Settings(_env_file=None).cors_origin_list == ["http://localhost:3000", "http://127.0.0.1:3000"]
    parsed = Settings(_env_file=None, cors_origins=" https://a.example.com/ , ,https://b.example.com").cors_origin_list
    assert parsed == ["https://a.example.com", "https://b.example.com"]
