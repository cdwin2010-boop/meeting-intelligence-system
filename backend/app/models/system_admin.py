from datetime import datetime

from sqlalchemy import ForeignKey
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.models.common import UTCDateTime, utcnow


class SystemAdmin(Base):
    """시스템 관리자 지정(작업 82-1). 사용자 등급(rank)과 별개 개념이라 계정 표를 바꾸지 않고 별도 표로 둔다.
    계정당 한 행. 지정 계정의 사용권한은 staff 만 허용한다(서비스 계층 services/system_admin.py 에서 검증, 방언 중립).
    created_by 는 최초 지정 도구(scripts/create_system_admin.py)가 지정하면 None."""

    __tablename__ = "system_admins"

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"), index=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id"), unique=True)
    created_by: Mapped[int | None] = mapped_column(ForeignKey("accounts.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)
