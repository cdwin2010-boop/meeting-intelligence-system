from datetime import datetime

from sqlalchemy import CheckConstraint, ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.models.common import UTCDateTime, utcnow


class MeetingSpeaker(Base):
    """회의록별 화자 매핑: 전사문의 화자 표기(label) → 같은 고객사 계정(account_id) 또는 미등록 이름 글자(display_name).

    - 둘 중 정확히 하나만 채운다(계정이면 display_name 은 "", 미등록이면 account_id 는 NULL).
    - 미등록 이름은 전사문 표시용 글자일 뿐 계정·담당자로 쓰지 않는다(표시 형식 "이름(미등록)").
    - updated_by 는 마지막으로 저장한 계정. 직급 우선(낮은 직급은 높은 직급이 정한 매핑을 덮지 못함) 판정에 쓴다."""

    __tablename__ = "meeting_speakers"
    __table_args__ = (
        UniqueConstraint("meeting_id", "label", name="uq_meeting_speakers_meeting_label"),
        CheckConstraint(
            "(account_id IS NOT NULL AND display_name = '') OR (account_id IS NULL AND display_name <> '')",
            name="account_or_name",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"), index=True)
    meeting_id: Mapped[int] = mapped_column(ForeignKey("meetings.id"), index=True)
    label: Mapped[str] = mapped_column(String(100))
    account_id: Mapped[int | None] = mapped_column(ForeignKey("accounts.id"), nullable=True, index=True)
    display_name: Mapped[str] = mapped_column(String(50), default="", server_default="")
    updated_by: Mapped[int] = mapped_column(ForeignKey("accounts.id"))
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)
