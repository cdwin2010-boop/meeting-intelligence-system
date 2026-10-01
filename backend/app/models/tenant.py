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
    # 업로드 음성 보관 일수(NULL = 무기한 보관). 지금은 설정 칸만 있고 자동 삭제 작업은 없다(법무 검토 후 삭제 작업 추가 예정)
    audio_retention_days: Mapped[int | None] = mapped_column(
        Integer, nullable=True, comment="업로드 음성 보관 일수(NULL=무기한). 법무 검토 후 삭제 작업 추가 예정"
    )
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)
