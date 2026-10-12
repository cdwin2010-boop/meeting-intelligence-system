from datetime import datetime

from sqlalchemy import Boolean, ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.models.common import SYSTEM_SETTING_KEYS, UTCDateTime, enum_check, utcnow


class SystemSetting(Base):
    """시스템 관리자만 바꾸는 설정(작업 82-1 파트 C). 고객사마다 (key) 한 행. 허용 키는 CHECK 로 제한한다(문자열 + CHECK, 방언 중립).
    행이 없으면 .env 의 현재 값이 초기값 역할을 한다(services/system_settings.py)."""

    __tablename__ = "system_settings"
    __table_args__ = (
        enum_check("key", SYSTEM_SETTING_KEYS, "key_valid"),
        UniqueConstraint("tenant_id", "key", name="uq_system_settings_tenant_key"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"), index=True)
    key: Mapped[str] = mapped_column(String(60))
    value_bool: Mapped[bool] = mapped_column(Boolean)
    updated_by: Mapped[int | None] = mapped_column(ForeignKey("accounts.id"), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)
