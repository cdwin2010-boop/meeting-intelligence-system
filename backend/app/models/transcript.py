from datetime import datetime
from typing import Any

from sqlalchemy import JSON, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.models.common import UTCDateTime, utcnow


class Transcript(Base):
    """회의록 1건의 전사 원문. 업무 추출이 실패하거나 말소리가 없어도 STT 결과는 그대로 남긴다.
    전사문은 회의 내용이므로 로그·예외·이벤트 payload 에 넣지 않는다(길이·구간 수만)."""

    __tablename__ = "transcripts"

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"), index=True)
    # 회의록당 전사문 1건(unique 제약이 인덱스 역할도 한다)
    meeting_id: Mapped[int] = mapped_column(ForeignKey("meetings.id"), unique=True)
    full_text: Mapped[str] = mapped_column(Text, default="")
    # 구간 목록: [{"speaker": str, "start_sec": float, "end_sec": float, "text": str}, ...]. STT가 주지 않으면 NULL
    segments: Mapped[list[dict[str, Any]] | None] = mapped_column(JSON, nullable=True)
    # 전사한 엔진 이름(fake / gemini 등)
    stt_provider: Mapped[str] = mapped_column(String(30))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)
