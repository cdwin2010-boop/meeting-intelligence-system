"""회의록 분류: 회의 유형 5종과 프로젝트 소속. 기존 meetings 표를 바꾸지 않으려고(SQLite 에서 표 재생성이 실패한 전례) 별도 연결 표로 둔다.
한 회의록당 한 행이고, 행이 없으면 "미지정"이다. 프로젝트 회의(project)만 project_id 를 가진다."""
from datetime import datetime

from sqlalchemy import ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.models.common import MEETING_TYPES, UTCDateTime, enum_check, utcnow


class MeetingClassification(Base):
    __tablename__ = "meeting_classifications"
    __table_args__ = (enum_check("meeting_type", MEETING_TYPES, "meeting_type_valid"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"), index=True)
    meeting_id: Mapped[int] = mapped_column(ForeignKey("meetings.id"), unique=True)
    meeting_type: Mapped[str] = mapped_column(String(20))
    project_id: Mapped[int | None] = mapped_column(ForeignKey("projects.id"), nullable=True, index=True)
    created_by: Mapped[int | None] = mapped_column(ForeignKey("accounts.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)
