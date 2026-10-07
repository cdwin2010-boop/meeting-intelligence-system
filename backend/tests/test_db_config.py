"""DB 연결 설정(SQLite WAL·busy_timeout·외래키), 동시 쓰기, 합계 집계 반환형, 메일 중복 방지의 unique 위반 처리."""
import threading
from datetime import datetime, timezone

import pytest
from sqlalchemy import Integer, select, text
from sqlalchemy.orm import sessionmaker

import app.db as app_db
from app.config import settings
from app.db import Base, make_engine, sqlite_pragmas
from app.models import Account, ActionItem, MailOutbox, Meeting, SourceDocument, Tenant


def _file_url(tmp_path, name="cfg.db") -> str:
    return f"sqlite:///{(tmp_path / name).as_posix()}"


def _pragma(engine, name):
    with engine.connect() as conn:
        return conn.exec_driver_sql(f"PRAGMA {name}").scalar()


# ---------------- 연결 설정 ----------------
def test_pragma_list_for_file_db_wal():
    assert sqlite_pragmas(in_memory=False, busy_timeout_ms=7000, journal_mode="WAL") == [
        "PRAGMA busy_timeout=7000", "PRAGMA journal_mode=WAL", "PRAGMA synchronous=NORMAL", "PRAGMA foreign_keys=ON",
    ]


def test_pragma_list_delete_mode_and_memory():
    delete = sqlite_pragmas(in_memory=False, busy_timeout_ms=1, journal_mode="DELETE")
    assert "PRAGMA journal_mode=DELETE" in delete and "PRAGMA synchronous=NORMAL" not in delete
    memory = sqlite_pragmas(in_memory=True, busy_timeout_ms=1, journal_mode="WAL")
    assert not any("journal_mode" in p for p in memory) and "PRAGMA foreign_keys=ON" in memory


def test_file_db_connection_has_wal_busy_timeout_and_foreign_keys(tmp_path):
    engine = make_engine(_file_url(tmp_path))
    try:
        assert _pragma(engine, "journal_mode").lower() == "wal"
        assert _pragma(engine, "busy_timeout") == settings.sqlite_busy_timeout_ms
        assert _pragma(engine, "foreign_keys") == 1
        assert _pragma(engine, "synchronous") == 1  # NORMAL
    finally:
        engine.dispose()


def test_setting_selects_delete_mode(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "sqlite_journal_mode", "DELETE")
    monkeypatch.setattr(settings, "sqlite_busy_timeout_ms", 1234)
    engine = make_engine(_file_url(tmp_path, "del.db"))
    try:
        assert _pragma(engine, "journal_mode").lower() == "delete"
        assert _pragma(engine, "busy_timeout") == 1234
    finally:
        engine.dispose()


def test_memory_db_works_without_journal_change():
    engine = make_engine("sqlite://")
    try:
        assert _pragma(engine, "foreign_keys") == 1
        assert _pragma(engine, "journal_mode").lower() == "memory"
    finally:
        engine.dispose()


def test_non_sqlite_url_runs_no_pragma(monkeypatch):
    """SQLite 가 아닌 주소면 어떤 PRAGMA 도 실행하지 않는다(엔진 생성만 가짜 SQLite 로 바꿔 연결해 확인)."""
    import sqlalchemy

    monkeypatch.setattr(app_db, "create_engine", lambda url: sqlalchemy.create_engine("sqlite://"))
    engine = make_engine("postgresql+psycopg://u:p@localhost/db")
    try:
        assert _pragma(engine, "foreign_keys") == 0  # SQLite 기본값 그대로 = 우리 PRAGMA 가 실행되지 않음
        assert _pragma(engine, "busy_timeout") != settings.sqlite_busy_timeout_ms
    finally:
        engine.dispose()


def test_journal_mode_change_blocked_by_open_connection_does_not_stop_startup(tmp_path, monkeypatch):
    """다른 연결이 열려 있어 저널 모드를 못 바꿔도 연결은 만들어지고 나머지 설정은 적용된다."""
    url = _file_url(tmp_path, "busy.db")
    first = make_engine(url)  # WAL 로 만든다
    try:
        monkeypatch.setattr(settings, "sqlite_journal_mode", "DELETE")
        holder = first.connect()
        holder.exec_driver_sql("SELECT 1")
        second = make_engine(url)
        try:
            assert _pragma(second, "foreign_keys") == 1
        finally:
            second.dispose()
            holder.close()
    finally:
        first.dispose()


# ---------------- 동시 쓰기 ----------------
def test_two_threads_write_concurrently_without_database_locked(tmp_path):
    engine = make_engine(_file_url(tmp_path, "conc.db"))
    try:
        with engine.begin() as conn:
            conn.exec_driver_sql("CREATE TABLE t (id INTEGER PRIMARY KEY, who TEXT, n INTEGER)")
        errors: list[BaseException] = []

        def writer(who: str):
            try:
                for n in range(60):
                    with engine.begin() as conn:
                        conn.execute(text("INSERT INTO t (who, n) VALUES (:w, :n)"), {"w": who, "n": n})
            except BaseException as exc:  # noqa: BLE001
                errors.append(exc)

        threads = [threading.Thread(target=writer, args=(name,)) for name in ("a", "b")]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert errors == []
        with engine.connect() as conn:
            assert conn.exec_driver_sql("SELECT COUNT(*) FROM t").scalar() == 120
    finally:
        engine.dispose()


# ---------------- 합계 집계·메일 중복 방지 ----------------
@pytest.fixture()
def db(tmp_path):
    engine = make_engine(_file_url(tmp_path, "m.db"))
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    with factory() as session:
        tenant = Tenant(name="t")
        session.add(tenant)
        session.flush()
        account = Account(tenant_id=tenant.id, login_id="a", password_hash="x", name="가", email="a@example.com", rank="manager")
        session.add(account)
        session.flush()
        doc = SourceDocument(tenant_id=tenant.id, origin="audio_minutes", doc_type="회의록", title="m", registered_by=account.id)
        session.add(doc)
        session.flush()
        meeting = Meeting(tenant_id=tenant.id, source_document_id=doc.id, title="m", held_at=datetime(2026, 10, 1, tzinfo=timezone.utc))
        session.add(meeting)
        session.flush()
        session.commit()
        ids = {"tenant": tenant.id, "account": account.id, "meeting": meeting.id}
    yield factory, ids
    engine.dispose()


def test_needs_count_is_int(db):
    from app.api.meeting_queries import _item_counts

    factory, ids = db
    with factory() as session:
        session.add(ActionItem(tenant_id=ids["tenant"], meeting_id=ids["meeting"], title="", status="pending"))  # 보완 필요
        session.add(ActionItem(tenant_id=ids["tenant"], meeting_id=ids["meeting"], title="x", status="pending",
                               assignee_id=ids["account"], due_undetermined=True))
        session.commit()
        counts = _item_counts()
        row = session.execute(select(counts.c.item_count, counts.c.needs_count)).one()
        assert (row.item_count, row.needs_count) == (2, 1)
        assert type(row.needs_count) is int
    assert isinstance(counts.c.needs_count.type, Integer)


def test_queue_mail_skips_on_unique_violation_and_keeps_other_changes(db, monkeypatch):
    from app.services.mail import queue_mail

    factory, ids = db
    with factory() as session:
        account = session.get(Account, ids["account"])
        assert queue_mail(session, account=account, kind="immediate_new_minutes", subject="s", body="b", dedupe_key="same") is True
        session.commit()
    with factory() as session:
        account = session.get(Account, ids["account"])
        other = MailOutbox(tenant_id=ids["tenant"], account_id=ids["account"], to_email="a@example.com", kind="immediate_new_minutes", subject="s2",
                           body="b", status="queued", dedupe_key="other")
        session.add(other)
        session.flush()
        # 동시 실행 흉내: 미리 조회에서는 없다고 나오지만 넣는 순간 unique 위반
        real_scalar = session.scalar
        calls = []

        def stale_first(*args, **kwargs):  # 첫 조회(미리 확인)만 "없음", 위반 뒤 재확인은 실제 조회
            calls.append(1)
            return None if len(calls) == 1 else real_scalar(*args, **kwargs)

        monkeypatch.setattr(session, "scalar", stale_first)
        assert queue_mail(session, account=account, kind="immediate_new_minutes", subject="s", body="b", dedupe_key="same") is False
        monkeypatch.setattr(session, "scalar", real_scalar)
        session.commit()
        keys = sorted(session.scalars(select(MailOutbox.dedupe_key)))
        assert keys == ["other", "same"]  # 위반한 건 건너뛰고, 같은 트랜잭션의 다른 변경은 그대로 커밋
