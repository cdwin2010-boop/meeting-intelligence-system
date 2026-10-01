from logging.config import fileConfig

from alembic import context

import app.models  # noqa: F401  (모든 모델을 메타데이터에 등록)
from app.config import settings
from app.db import Base, make_engine
from app.models.common import UTCDateTime

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name, disable_existing_loggers=False)

# 자동 생성(autogenerate)이 비교할 모델 메타데이터
target_metadata = Base.metadata

# DB 주소: 호출부가 sqlalchemy.url 을 넘기면 그것(테스트용 임시 DB), 없으면 설정의 DATABASE_URL
_url = config.get_main_option("sqlalchemy.url") or settings.database_url

# SQLite는 ALTER TABLE 이 제한적이라 표 재생성 방식(batch)으로 변경한다. 다른 엔진은 일반 방식
_is_sqlite = _url.startswith("sqlite")


def _render_item(type_, obj, autogen_context):
    """마이그레이션 파일이 앱 코드에 묶이지 않도록 UTCDateTime 은 표준 DateTime(timezone=True)로 적는다."""
    if type_ == "type" and isinstance(obj, UTCDateTime):
        return "sa.DateTime(timezone=True)"
    return False


def run_migrations_offline() -> None:
    """DB 연결 없이 SQL 문만 출력하는 모드."""
    context.configure(
        url=_url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        render_as_batch=_is_sqlite,
        render_item=_render_item,
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """앱과 같은 방식의 엔진(SQLite 외래키 PRAGMA 포함)으로 연결해 마이그레이션을 실행한다."""
    engine = make_engine(_url)
    try:
        with engine.connect() as connection:
            context.configure(
                connection=connection,
                target_metadata=target_metadata,
                render_as_batch=_is_sqlite,
                render_item=_render_item,
            )

            with context.begin_transaction():
                context.run_migrations()
    finally:
        # 임시 DB 파일을 지울 수 있도록 연결을 닫는다(Windows 파일 잠금)
        engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
