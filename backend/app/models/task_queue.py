from datetime import datetime

from sqlalchemy import CheckConstraint, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.models.common import TASK_QUEUE_STATUSES, UTCDateTime, enum_check, utcnow


class TaskQueue(Base):
    """처리 작업 큐(작업 75-1). 워커가 PENDING 을 하나씩 가져가 처리한다. 이 표는 services/task_queue.py 에서만 다룬다.
    회의록당 활성(PENDING·PROCESSING) 작업은 하나뿐이어야 하며 DB 제약이 아니라 서비스 계층(enqueue)에서 보장한다."""

    __tablename__ = "task_queue"
    __table_args__ = (
        enum_check("status", TASK_QUEUE_STATUSES, "status_valid"),
        CheckConstraint("progress BETWEEN 0 AND 100", name="progress_range"),
        Index("ix_task_queue_status_created", "status", "created_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"), index=True)
    meeting_id: Mapped[int] = mapped_column(ForeignKey("meetings.id"), index=True)
    status: Mapped[str] = mapped_column(String(20), default="PENDING")
    progress: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    # 짧은 요약만(전사 본문·개인 정보 금지)
    error_msg: Mapped[str | None] = mapped_column(Text, nullable=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    started_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow, onupdate=utcnow)
