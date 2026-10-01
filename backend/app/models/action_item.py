from datetime import date, datetime

from sqlalchemy import Boolean, Date, Float, ForeignKey, String, Text, false
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.models.common import ACTION_ITEM_STATUSES, CONFIRM_KINDS, UTCDateTime, enum_check, utcnow


class ActionItem(Base):
    """업무(회의록 안의 할 일 1건).

    후속 과제(3단계 서비스 계층): assignee_id·confirmed_by 가 업무와 같은 tenant 의 계정인지 검사한다.
    이번 단계에서는 DB가 막지 않는다(다른 고객사 계정도 저장됨, tests/test_models.py 참고)."""

    __tablename__ = "action_items"
    __table_args__ = (
        enum_check("status", ACTION_ITEM_STATUSES, "status_valid"),
        enum_check("confirm_kind", CONFIRM_KINDS, "confirm_kind_valid", nullable=True),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"), index=True)
    meeting_id: Mapped[int] = mapped_column(ForeignKey("meetings.id"), index=True)
    # 미정 항목은 None 이 아니라 빈 문자열("")로 둔다
    title: Mapped[str] = mapped_column(String(500), default="")
    assignee_id: Mapped[int | None] = mapped_column(ForeignKey("accounts.id"), nullable=True, index=True)
    due_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    # 관리자가 기한을 "미확정"으로 고른 경우 True (경고 대상 아님)
    due_undetermined: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false())
    status: Mapped[str] = mapped_column(String(20), default="pending")
    confirm_kind: Mapped[str | None] = mapped_column(String(20), nullable=True)
    confirmed_by: Mapped[int | None] = mapped_column(ForeignKey("accounts.id"), nullable=True, index=True)
    confirmed_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    # 발화 근거: 시작 시각(초)과 인용문
    evidence_start_sec: Mapped[float | None] = mapped_column(Float, nullable=True)
    evidence_quote: Mapped[str | None] = mapped_column(Text, nullable=True)
    # 추출 출처(수동 등록이면 빈 문자열)
    extract_model: Mapped[str] = mapped_column(String(100), default="", server_default="")
    prompt_version: Mapped[str] = mapped_column(String(50), default="", server_default="")
    extracted_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)

    @property
    def needs_supplement(self) -> bool:
        """'보완 필요' 여부(저장하지 않고 계산): 업무명 빈칸, 담당자 없음, 기한 빈칸 중 하나라도 해당.
        기한을 '미확정'으로 고른 경우(due_undetermined=True)는 빈칸이 아니므로 경고하지 않는다."""
        if not (self.title or "").strip():
            return True
        if self.assignee_id is None:
            return True
        return self.due_date is None and not self.due_undetermined
