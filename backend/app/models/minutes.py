from datetime import datetime

from sqlalchemy import ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.models.common import UTCDateTime, utcnow

# 추출된 정보가 없는 항목에 저장하는 값
NO_CONTENT = "내용없음"
# 회의록 5개 항목(저장 칸 이름 → 화면·엑셀 표시 이름). 순서가 곧 표시 순서다
MINUTES_FIELDS = {
    "purpose": "목적",
    "discussion": "주요 논의사항",
    "decisions": "결정사항",
    "risks": "리스크",
    "next_agenda": "다음 안건",
}


class MeetingMinutes(Base):
    """회의록 5개 항목(목적·주요 논의사항·결정사항·리스크·다음 안건). 회의록당 1행.
    meetings 표를 고치지 않으려고 따로 둔다(meeting_holds 와 같은 이유). 항목이 비면 "내용없음"을 저장한다.
    engine 은 이 회의록을 만든 처리 엔진(전사 엔진 이름, 현재 운영 값 gemini). 직권 수정 이력은 events 에 남는다."""

    __tablename__ = "meeting_minutes"

    meeting_id: Mapped[int] = mapped_column(ForeignKey("meetings.id"), primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"), index=True)
    purpose: Mapped[str] = mapped_column(Text, default=NO_CONTENT)
    discussion: Mapped[str] = mapped_column(Text, default=NO_CONTENT)
    decisions: Mapped[str] = mapped_column(Text, default=NO_CONTENT)
    risks: Mapped[str] = mapped_column(Text, default=NO_CONTENT)
    next_agenda: Mapped[str] = mapped_column(Text, default=NO_CONTENT)
    engine: Mapped[str] = mapped_column(String(30), default="", server_default="")
    extract_model: Mapped[str] = mapped_column(String(100), default="", server_default="")
    prompt_version: Mapped[str] = mapped_column(String(50), default="", server_default="")
    generated_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)
    # 마지막 직권 수정(없으면 NULL)
    updated_by: Mapped[int | None] = mapped_column(ForeignKey("accounts.id"), nullable=True, index=True)
    updated_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
