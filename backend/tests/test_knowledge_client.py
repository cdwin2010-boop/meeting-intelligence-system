"""지식(KMS) 연동 호출 인터페이스(작업 83-1 파트 B): 꺼짐 기본값, 호출 없음 고정, 입력 검증, 자동 전달 코드 없음."""
import ast
import http.client
import socket
import urllib.request
from pathlib import Path

import httpx
import pytest

from app.config import settings
from app.integrations import knowledge as k
from app.integrations.knowledge import (
    FakeKnowledgeClient, KnowledgeClient, KnowledgeQueryError, NullKnowledgeClient, ReferenceCard, make_knowledge_client,
)

ROOT = Path(__file__).resolve().parent.parent
CARD = ReferenceCard(title="외주 계약 검토 규정", date="2026-03-15", summary="외주 계약은 법무 검토 후 체결한다", url="https://kms.example.internal/docs/77", source="kms")


@pytest.fixture
def no_network(monkeypatch):
    """어떤 경로로든 네트워크 호출을 시도하면 즉시 실패시킨다."""
    attempts: list[str] = []

    def boom(name):
        def _raise(*args, **kwargs):
            attempts.append(name)
            raise AssertionError(f"네트워크 호출 시도: {name}")

        return _raise

    monkeypatch.setattr(socket.socket, "connect", boom("socket.connect"))
    monkeypatch.setattr(socket, "create_connection", boom("socket.create_connection"))
    monkeypatch.setattr(httpx.Client, "send", boom("httpx.Client.send"))
    monkeypatch.setattr(httpx.AsyncClient, "send", boom("httpx.AsyncClient.send"))
    monkeypatch.setattr(urllib.request, "urlopen", boom("urllib.urlopen"))
    monkeypatch.setattr(http.client.HTTPConnection, "request", boom("http.client"))
    return attempts


def test_interface_is_abstract():
    with pytest.raises(TypeError):
        KnowledgeClient()  # type: ignore[abstract]


def test_null_client_returns_disabled_result_without_any_call(no_network):
    client = NullKnowledgeClient()
    result = client.search_references("외주 계약", "ext-user-1")
    assert result.status == k.STATUS_DISABLED and result.disabled is True
    assert result.cards == () and result.message == k.MESSAGE_DISABLED
    assert no_network == []


@pytest.mark.parametrize(
    "enabled, endpoint, status",
    [
        (False, "", k.STATUS_DISABLED),
        (False, "http://127.0.0.1:9000", k.STATUS_DISABLED),  # 스위치가 꺼져 있으면 주소가 있어도 호출하지 않는다
        (True, "", k.STATUS_DISABLED),  # 주소가 없으면 호출하지 않는다
        (True, "   ", k.STATUS_DISABLED),
        (True, "http://127.0.0.1:9000", k.STATUS_NOT_READY),  # 켜져 있어도 호출 구현이 아직 없다(선반영)
    ],
)
def test_factory_never_calls_out(enabled, endpoint, status, no_network):
    client = make_knowledge_client(enabled=enabled, endpoint=endpoint)
    assert isinstance(client, NullKnowledgeClient)
    result = client.search_references("검색어", "ext-user-1", limit=3)
    assert result.status == status and result.cards == () and result.disabled
    assert no_network == []


def test_switch_off_means_no_call_from_settings_default(no_network):
    assert settings.knowledge_integration_enabled is False
    result = k.client_from_settings().search_references("검색어", "ext-user-1")
    assert result.status == k.STATUS_DISABLED
    assert no_network == []


def test_fake_client_returns_fixed_cards_and_records_calls():
    fake = FakeKnowledgeClient((CARD, CARD, CARD, CARD))
    result = fake.search_references("  외주 계약  ", "ext-user-1", limit=2)
    assert result.status == k.STATUS_OK and result.disabled is False and len(result.cards) == 2
    assert fake.calls == [("외주 계약", "ext-user-1", 2)]  # 검색어는 공백을 지운 값만 전달
    assert FakeKnowledgeClient().search_references("q", "u").cards == ()


@pytest.mark.parametrize(
    "query, sub, limit",
    [("", "u", 3), ("   ", "u", 3), ("q" * 201, "u", 3), ("q", "", 3), ("q", "  ", 3), ("q", "u", 0), ("q", "u", 11), ("q", "u", True)],
)
def test_invalid_requests_are_rejected_before_any_call(query, sub, limit, no_network):
    for client in (NullKnowledgeClient(), FakeKnowledgeClient()):
        with pytest.raises(KnowledgeQueryError):
            client.search_references(query, sub, limit)
    assert no_network == []


def test_default_limit_is_three():
    assert k.DEFAULT_LIMIT == 3
    fake = FakeKnowledgeClient((CARD,) * 5)
    assert len(fake.search_references("q", "u").cards) == 3


def test_module_has_no_http_implementation():
    tree = ast.parse((ROOT / "app" / "integrations" / "knowledge.py").read_text(encoding="utf-8"))
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported |= {a.name.split(".")[0] for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])
    assert imported.isdisjoint({"httpx", "requests", "urllib", "http", "socket", "aiohttp", "asyncio"}), imported


def test_no_automatic_caller_exists_in_the_app():
    """안건·업무·회의록 내용이 자동으로 이 인터페이스에 전달되는 코드는 없다: app/integrations 밖에서 import 하는 곳이 없다."""
    offenders = []
    for path in (ROOT / "app").rglob("*.py"):
        rel = path.relative_to(ROOT / "app").as_posix()
        if rel.startswith("integrations/"):
            continue
        text = path.read_text(encoding="utf-8")
        if "app.integrations" in text or "from app import integrations" in text:
            offenders.append(rel)
    assert offenders == [], offenders
