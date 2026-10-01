from datetime import datetime

from sqlalchemy import Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.models.common import UTCDateTime, utcnow


class Tenant(Base):
    """고객사(회사). 모든 업무 데이터는 tenant_id 로 고객사에 묶인다."""

    __tablename__ = "tenants"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200), unique=True)
    # 자동 확정 기간 N일(기본 5, 고객사별 5/10)
    auto_confirm_days: Mapped[int] = mapped_column(Integer, default=5, server_default="5")
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)
