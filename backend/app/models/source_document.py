from datetime import datetime

from sqlalchemy import ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.models.common import SOURCE_ORIGINS, UTCDateTime, enum_check, utcnow


class SourceDocument(Base):
    """원천 문서: 음성 회의록과 고객사가 올린 회의록·작업지시서 등을 동등하게 다룬다(부록 C-2)."""

    __tablename__ = "source_documents"
    __table_args__ = (enum_check("origin", SOURCE_ORIGINS, "origin_valid"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"), index=True)
    origin: Mapped[str] = mapped_column(String(20))
    # 자유 문자열(회의록, 작업지시서 등)
    doc_type: Mapped[str] = mapped_column(String(50))
    title: Mapped[str] = mapped_column(String(300))
    file_path: Mapped[str | None] = mapped_column(String(500), nullable=True)
    registered_by: Mapped[int] = mapped_column(ForeignKey("accounts.id"), index=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)
