from datetime import datetime
from typing import Any

from sqlalchemy import JSON, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.models.common import CONFIRM_KINDS, MEETING_STATUSES, UTCDateTime, enum_check, utcnow


class Meeting(Base):
    """회의록(원장). 확정은 회의록 단위, 업무 확정은 action_items 에서 따로 한다(부록 C-5)."""

    __tablename__ = "meetings"
    __table_args__ = (
        enum_check("status", MEETING_STATUSES, "status_valid"),
        enum_check("confirm_kind", CONFIRM_KINDS, "confirm_kind_valid", nullable=True),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"), index=True)
    source_document_id: Mapped[int] = mapped_column(ForeignKey("source_documents.id"), index=True)
    title: Mapped[str] = mapped_column(String(300))
    held_at: Mapped[datetime] = mapped_column(UTCDateTime())
    summary: Mapped[str] = mapped_column(Text, default="")
    decisions: Mapped[list[Any]] = mapped_column(JSON, default=list)
    status: Mapped[str] = mapped_column(String(30), default="processing")
    confirm_kind: Mapped[str | None] = mapped_column(String(20), nullable=True)
    confirmed_by: Mapped[int | None] = mapped_column(ForeignKey("accounts.id"), nullable=True, index=True)
    confirmed_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    # 자동 확정 기준: 최초 생성일 + N일(수정해도 초기화하지 않음)
    first_created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)
    auto_confirm_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    # 고객별 정책 확장 슬롯(예: 관리자 확정 후 발송)
    policy_snapshot: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)


class MeetingParticipant(Base):
    """회의 참석자. 회의(tenant 소속)를 통해 고객사가 정해지므로 tenant_id 는 두지 않는다(지시 사양대로 2열)."""

    __tablename__ = "meeting_participants"

    meeting_id: Mapped[int] = mapped_column(ForeignKey("meetings.id"), primary_key=True)
    # 복합 PK 첫 열(meeting_id)은 PK 인덱스로 찾고, account_id 는 따로 인덱스를 둔다
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id"), primary_key=True, index=True)
