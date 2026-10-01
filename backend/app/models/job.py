from datetime import datetime

from sqlalchemy import ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.models.common import JOB_STATUSES, UTCDateTime, enum_check, utcnow


class Job(Base):
    """음성 처리 작업(전사 → 업무 추출) 1건. 회의록 1건에 대응한다."""

    __tablename__ = "jobs"
    __table_args__ = (enum_check("status", JOB_STATUSES, "status_valid"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"), index=True)
    meeting_id: Mapped[int] = mapped_column(ForeignKey("meetings.id"), index=True)
    status: Mapped[str] = mapped_column(String(20), default="queued")
    # 실패 분류 코드만 저장(예: timeout, extraction_invalid). 예외 원문·키·파일 경로는 넣지 않는다
    error_code: Mapped[str | None] = mapped_column(String(50), nullable=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    started_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)
