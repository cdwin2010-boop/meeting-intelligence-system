from logging.config import fileConfig

from alembic import context

from app.config import settings
from app.db import Base, engine

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# 자동 생성(autogenerate)이 비교할 모델 메타데이터. 모델은 2단계에서 app 아래에 추가한다
target_metadata = Base.metadata

# SQLite는 ALTER TABLE 이 제한적이라 표 재생성 방식(batch)으로 변경한다. 다른 엔진은 일반 방식
_is_sqlite = engine.dialect.name == "sqlite"


def run_migrations_offline() -> None:
    """DB 연결 없이 SQL 문만 출력하는 모드. 주소는 설정(DATABASE_URL)에서 가져온다."""
    context.configure(
        url=settings.database_url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        render_as_batch=_is_sqlite,
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """앱과 같은 엔진(SQLite 외래키 PRAGMA 포함)으로 연결해 마이그레이션을 실행한다."""
    with engine.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            render_as_batch=_is_sqlite,
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
