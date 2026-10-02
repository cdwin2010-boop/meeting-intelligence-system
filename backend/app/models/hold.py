from datetime import datetime

from sqlalchemy import Boolean, ForeignKey, exists, false
from sqlalchemy.orm import Mapped, column_property, mapped_column

from app.db import Base
from app.models.common import UTCDateTime, utcnow
from app.models.meeting import Meeting


class MeetingHold(Base):
    """회의록 보류 상태(회의록당 1행, 처음 보류할 때 만든다). 보류 중이면 딸린 업무도 모두 보류로 본다(업무 상태는 그대로 둠).
    meetings 표를 고치지 않으려고 따로 둔다(SQLite 는 외래키 칸을 더하려면 meetings 표를 다시 만들어야 해서, 데이터가 있으면 실패할 수 있음).
    마지막 보류·재개를 한 사람과 시각만 남기고, 이력은 사건(meeting.on_hold·meeting.resumed)에 남는다."""

    __tablename__ = "meeting_holds"

    meeting_id: Mapped[int] = mapped_column(ForeignKey("meetings.id"), primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"), index=True)
    on_hold: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false())
    on_hold_by: Mapped[int | None] = mapped_column(ForeignKey("accounts.id"), nullable=True, index=True)
    on_hold_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    resumed_by: Mapped[int | None] = mapped_column(ForeignKey("accounts.id"), nullable=True, index=True)
    resumed_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow, onupdate=utcnow)


# Meeting.on_hold: 읽기 전용 계산 칸(인스턴스 값 + SQL 조건). 조회 조건에는 Meeting.on_hold.is_(False) 처럼 쓴다
Meeting.on_hold = column_property(
    exists().where(MeetingHold.meeting_id == Meeting.id, MeetingHold.on_hold.is_(True)),
)
