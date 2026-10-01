from datetime import datetime
from typing import Any

from sqlalchemy import JSON, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.models.common import UTCDateTime, enum_check, utcnow

NOTICE_KINDS = ("confirmed_notice",)
NOTICE_ENTITY_TYPES = ("meeting", "action_item")


class MeetingView(Base):
    """계정별 회의록 열람 기록(상세 조회 시). 첫 열람 시각은 유지하고 마지막 열람 시각만 갱신한다."""

    __tablename__ = "meeting_views"

    meeting_id: Mapped[int] = mapped_column(ForeignKey("meetings.id"), primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id"), primary_key=True, index=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"), index=True)
    first_viewed_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)
    last_viewed_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)


class Notice(Base):
    """시스템 안 안내(메일 아님). 지금은 확정 안내만: 회의록·업무가 확정되면 관리자 이상 관련자에게 1건씩."""

    __tablename__ = "notices"
    __table_args__ = (
        enum_check("kind", NOTICE_KINDS, "kind_valid"),
        enum_check("entity_type", NOTICE_ENTITY_TYPES, "entity_type_valid"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"), index=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id"), index=True)  # 수신자
    kind: Mapped[str] = mapped_column(String(30))
    entity_type: Mapped[str] = mapped_column(String(20))
    entity_id: Mapped[int] = mapped_column(Integer)
    meeting_id: Mapped[int] = mapped_column(ForeignKey("meetings.id"), index=True)
    # 짧은 값만(confirmKind, title 등). 전사문·인용문은 넣지 않는다
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)
    seen_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
