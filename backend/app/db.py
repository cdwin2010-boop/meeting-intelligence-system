import logging
from pathlib import Path

from sqlalchemy import MetaData, create_engine, event
from sqlalchemy.engine import make_url
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from app.config import settings

log = logging.getLogger("app.db")

# 제약 이름 규칙: 이름이 고정돼야 Alembic이 엔진과 무관하게 같은 제약을 찾아 바꾸거나 지울 수 있다
NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    """모든 ORM 모델의 부모. 모델은 app/models 에 있다."""

    metadata = MetaData(naming_convention=NAMING_CONVENTION)


def sqlite_pragmas(*, in_memory: bool, busy_timeout_ms: int, journal_mode: str) -> list[str]:
    """SQLite 연결마다 실행할 PRAGMA 목록(순서 유지). 연결 없이 판정할 수 있게 순수 함수로 둔다.
    - busy_timeout: 다른 연결이 쓰는 중이면 이 시간(ms)까지 기다린다(요청 스레드와 백그라운드 처리가 함께 쓰므로).
    - journal_mode: 파일 DB 에서만 바꾼다(메모리 DB 는 WAL 을 지원하지 않음). WAL 이면 synchronous=NORMAL 도 함께 쓴다.
    - foreign_keys: SQLite 는 연결마다 꺼져 있으므로 항상 켠다."""
    pragmas = [f"PRAGMA busy_timeout={int(busy_timeout_ms)}"]
    if not in_memory:
        pragmas.append(f"PRAGMA journal_mode={journal_mode}")
        if journal_mode == "WAL":
            pragmas.append("PRAGMA synchronous=NORMAL")
    pragmas.append("PRAGMA foreign_keys=ON")
    return pragmas


def make_engine(url: str):
    parsed = make_url(url)
    is_sqlite = parsed.get_backend_name() == "sqlite"
    in_memory = not parsed.database or parsed.database == ":memory:" or parsed.database.startswith("file::memory:")

    # SQLite 파일 DB면 폴더가 없을 때 만들어 둔다(SQLite는 폴더를 자동으로 만들지 않음)
    if is_sqlite and not in_memory:
        Path(parsed.database).parent.mkdir(parents=True, exist_ok=True)

    engine = create_engine(url)

    # SQLite 전용 연결 설정. 다른 엔진에는 어떤 PRAGMA 도 실행하지 않는다.
    # 기존 운영 DB 파일은 서버를 다음에 시작할 때(첫 연결) WAL 로 바뀐다(파일에 기록되는 설정이라 이후에도 유지됨).
    if is_sqlite:
        pragmas = sqlite_pragmas(
            in_memory=in_memory, busy_timeout_ms=settings.sqlite_busy_timeout_ms, journal_mode=settings.sqlite_journal_mode
        )

        @event.listens_for(engine, "connect")
        def _configure_sqlite(dbapi_conn, _record):
            cursor = dbapi_conn.cursor()
            try:
                for pragma in pragmas:
                    try:
                        cursor.execute(pragma)
                    except Exception as exc:  # noqa: BLE001 — 저널 모드 전환 실패(다른 연결이 열려 있음 등)로 서버 시작을 막지 않는다
                        if "journal_mode" not in pragma:
                            raise
                        log.warning("SQLite journal_mode 전환 실패(그대로 진행): %s", type(exc).__name__)
            finally:
                cursor.close()
            # pysqlite 는 SAVEPOINT 를 쓰는 begin_nested() 와 트랜잭션 시작 시점이 어긋난다(바깥 트랜잭션이 아직 없으면 SAVEPOINT 해제가
            # 곧 커밋이 됨). 드라이버의 자동 BEGIN 을 끄고 SQLAlchemy 가 직접 BEGIN 을 내보내게 하는 공식 방법
            dbapi_conn.isolation_level = None

        @event.listens_for(engine, "begin")
        def _begin_sqlite(conn):
            conn.connection.dbapi_connection.execute("BEGIN")  # 드라이버에 직접 보낸다(SQLAlchemy 문장 이벤트·집계에 잡히지 않음)

    return engine


engine = make_engine(settings.database_url)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def get_session():
    """FastAPI 의존성: 요청마다 세션을 열고 끝나면 닫는다."""
    with SessionLocal() as session:
        yield session


def get_session_factory() -> sessionmaker:
    """FastAPI 의존성: 요청이 끝난 뒤 도는 백그라운드 처리가 새 세션을 열 때 쓰는 공장(테스트에서 바꿔 끼움)."""
    return SessionLocal
