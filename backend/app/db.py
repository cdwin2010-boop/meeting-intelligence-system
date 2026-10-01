from pathlib import Path

from sqlalchemy import MetaData, create_engine, event
from sqlalchemy.engine import make_url
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from app.config import settings


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


def make_engine(url: str):
    parsed = make_url(url)
    is_sqlite = parsed.get_backend_name() == "sqlite"

    # SQLite 파일 DB면 폴더가 없을 때 만들어 둔다(SQLite는 폴더를 자동으로 만들지 않음)
    if is_sqlite and parsed.database and parsed.database != ":memory:":
        Path(parsed.database).parent.mkdir(parents=True, exist_ok=True)

    engine = create_engine(url)

    # SQLite는 연결마다 외래키 검사가 꺼져 있으므로 이때만 켠다. 다른 엔진에는 아무 것도 하지 않는다
    if is_sqlite:
        @event.listens_for(engine, "connect")
        def _enable_sqlite_fk(dbapi_conn, _record):
            cursor = dbapi_conn.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()

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
