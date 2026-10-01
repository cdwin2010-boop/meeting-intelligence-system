from datetime import datetime

from sqlalchemy import Boolean, ForeignKey, String, true
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.models.common import ACCOUNT_RANKS, UTCDateTime, enum_check, utcnow


class Account(Base):
    """로그인 계정. rank: staff(담당자) < manager(중간관리자) < executive(지시자)."""

    __tablename__ = "accounts"
    __table_args__ = (enum_check("rank", ACCOUNT_RANKS, "rank_valid"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"), index=True)
    login_id: Mapped[str] = mapped_column(String(100), unique=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    name: Mapped[str] = mapped_column(String(100))
    email: Mapped[str] = mapped_column(String(255))
    rank: Mapped[str] = mapped_column(String(20))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default=true())
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)
