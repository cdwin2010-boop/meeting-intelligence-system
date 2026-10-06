from datetime import datetime

from sqlalchemy import ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.models.common import UTCDateTime, utcnow


class MeetingGuestParticipant(Base):
    """계정이 없는 참석자 이름(회의록별). 업로드 갱신에서 이름만 있고 계정을 찾지 못한 참석자를 글자로만 남긴다(표시 "이름(미등록)").
    열람 권한·담당자·알림에는 쓰지 않는다. meeting_participants(계정 참석자) 표를 고치지 않으려고 따로 둔다."""

    __tablename__ = "meeting_guest_participants"
    __table_args__ = (UniqueConstraint("meeting_id", "name", name="uq_meeting_guest_participants_meeting_name"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"), index=True)
    meeting_id: Mapped[int] = mapped_column(ForeignKey("meetings.id"), index=True)
    name: Mapped[str] = mapped_column(String(100))
    created_by: Mapped[int | None] = mapped_column(ForeignKey("accounts.id"), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)
