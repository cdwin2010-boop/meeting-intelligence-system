"""v2 핵심 테이블 7개: 마이그레이션 왕복, CHECK 제약, 보완 필요 계산, 추가 전용 원장.
모든 테스트는 테스트마다 새 임시 SQLite 파일에 Alembic 마이그레이션으로 표를 만든다(backend/data 와 무관)."""
import inspect as pyinspect
from datetime import date, datetime, timezone
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import func, inspect, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db import make_engine
from app.models import Account, ActionItem, Event, Meeting, SourceDocument, Tenant, append_event
from app.models import event as event_module

_ALEMBIC_INI = Path(__file__).resolve().parent.parent / "alembic.ini"
_TABLES = {
    "tenants",
    "accounts",
    "source_documents",
    "meetings",
    "meeting_participants",
    "action_items",
    "events",
}


def _alembic_config(url: str) -> Config:
    cfg = Config(str(_ALEMBIC_INI))
    cfg.set_main_option("sqlalchemy.url", url)
    return cfg


@pytest.fixture
def db_url(tmp_path):
    return f"sqlite:///{(tmp_path / 'models.db').as_posix()}"


@pytest.fixture
def session(db_url):
    command.upgrade(_alembic_config(db_url), "head")
    engine = make_engine(db_url)
    with Session(engine) as s:
        yield s
    engine.dispose()


def _table_names(url: str) -> set[str]:
    engine = make_engine(url)
    try:
        return set(inspect(engine).get_table_names())
    finally:
        engine.dispose()


def _seed(session: Session, tenant_name: str = "고객사A", login_id: str = "mgr-a") -> tuple[Tenant, Account, Meeting]:
    """고객사 1·관리자 계정 1·원천 문서 1·회의록 1을 만든다."""
    tenant = Tenant(name=tenant_name)
    session.add(tenant)
    session.flush()
    account = Account(
        tenant_id=tenant.id, login_id=login_id, password_hash="x", name="관리자", email=f"{login_id}@example.com", rank="manager"
    )
    session.add(account)
    session.flush()
    doc = SourceDocument(tenant_id=tenant.id, origin="audio_minutes", doc_type="회의록", title="주간 회의", registered_by=account.id)
    session.add(doc)
    session.flush()
    meeting = Meeting(
        tenant_id=tenant.id, source_document_id=doc.id, title="주간 회의", held_at=datetime(2026, 10, 1, 1, 0, tzinfo=timezone.utc)
    )
    session.add(meeting)
    session.flush()
    return tenant, account, meeting


def test_migration_roundtrip(db_url):
    """upgrade head → downgrade base → upgrade head 가 모두 성공하고, 모델과 마이그레이션 사이에 차이가 없다."""
    cfg = _alembic_config(db_url)

    command.upgrade(cfg, "head")
    assert _TABLES <= _table_names(db_url)

    command.downgrade(cfg, "base")
    assert _table_names(db_url) & _TABLES == set()

    command.upgrade(cfg, "head")
    assert _TABLES <= _table_names(db_url)

    # 모델을 바꾸고 마이그레이션을 안 만들면 여기서 실패한다
    command.check(cfg)


def test_account_rank_rejects_invalid_value(session):
    tenant, _, _ = _seed(session)
    session.add(Account(tenant_id=tenant.id, login_id="bad", password_hash="x", name="잘못", email="bad@example.com", rank="boss"))
    with pytest.raises(IntegrityError):
        session.flush()


def test_cross_tenant_assignee_not_blocked_yet(session):
    """현재 동작 기록: 다른 고객사 계정을 업무 담당자로 넣어도 DB는 막지 않는다.
    후속 과제(3단계 서비스 계층): 업무 tenant 와 담당자 tenant 가 다르면 거절한다."""
    tenant_a, _, meeting_a = _seed(session)
    _, account_b, _ = _seed(session, tenant_name="고객사B", login_id="mgr-b")
    item = ActionItem(tenant_id=tenant_a.id, meeting_id=meeting_a.id, title="견적서 송부", assignee_id=account_b.id, due_undetermined=True)
    session.add(item)
    session.flush()
    assert item.id is not None


@pytest.mark.parametrize(
    ("assignee", "due_date", "due_undetermined", "expected"),
    [
        pytest.param(False, date(2026, 10, 10), False, True, id="no-assignee"),
        pytest.param(True, None, False, True, id="due-blank"),
        pytest.param(True, None, True, False, id="due-undetermined-no-warning"),
    ],
)
def test_needs_supplement(session, assignee, due_date, due_undetermined, expected):
    tenant, account, meeting = _seed(session)
    item = ActionItem(
        tenant_id=tenant.id,
        meeting_id=meeting.id,
        title="견적서 송부",
        assignee_id=account.id if assignee else None,
        due_date=due_date,
        due_undetermined=due_undetermined,
    )
    session.add(item)
    session.flush()
    assert item.needs_supplement is expected


def test_append_event_adds_one_row(session):
    tenant, account, meeting = _seed(session)
    before = session.scalar(select(func.count()).select_from(Event))

    event = append_event(
        session,
        tenant_id=tenant.id,
        entity_type="meeting",
        entity_id=meeting.id,
        event_type="meeting_created",
        actor_account_id=account.id,
        payload={"title": meeting.title},
    )

    assert session.scalar(select(func.count()).select_from(Event)) == before + 1
    assert event.created_at.tzinfo is not None


def test_event_module_has_no_update_or_delete_functions():
    names = [name for name, obj in pyinspect.getmembers(event_module, pyinspect.isfunction) if obj.__module__ == event_module.__name__]
    assert names == ["append_event"]
    assert not any(word in name.lower() for name in names for word in ("update", "delete", "remove", "edit"))
