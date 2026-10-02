"""회의록 종료·삭제 기록(회의록당 1행)과 회의록 단계(진행중·종료·보류·삭제) 계산.
meetings 표를 고치지 않으려고 따로 둔다(SQLite 는 외래키 칸을 더하려면 meetings 표를 다시 만들어야 해서, 데이터가 있으면 실패할 수 있음).
회의록 status(처리 중·확정 대기·확정 등)와는 별개의 축이다."""
from datetime import datetime

from sqlalchemy import ForeignKey, String, and_, exists
from sqlalchemy.orm import Mapped, column_property, mapped_column

from app.db import Base
from app.models.common import UTCDateTime, enum_check, utcnow
from app.models.hold import MeetingHold  # noqa: F401  (Meeting.on_hold 를 먼저 등록)
from app.models.meeting import Meeting

# 종료 구분: 자동 종료(업무가 모두 종결) / 관리자 직권 종료
END_KINDS = ("auto", "manager")
# 회의록 단계(목록 필터·응답 값)
PHASES = ("active", "ended", "on_hold", "deleted")


class MeetingClosure(Base):
    __tablename__ = "meeting_closures"
    __table_args__ = (enum_check("end_kind", END_KINDS, "end_kind_valid", nullable=True),)

    meeting_id: Mapped[int] = mapped_column(ForeignKey("meetings.id"), primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"), index=True)
    # 종료: 구분·처리자(자동 종료는 없음)·시각. ended_at 이 있으면 종료
    end_kind: Mapped[str | None] = mapped_column(String(20), nullable=True)
    ended_by: Mapped[int | None] = mapped_column(ForeignKey("accounts.id"), nullable=True, index=True)
    ended_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    # 삭제(표시만, 회의록·업무·수정 요청 행은 그대로): deleted_at 이 있으면 삭제
    deleted_by: Mapped[int | None] = mapped_column(ForeignKey("accounts.id"), nullable=True, index=True)
    deleted_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow, onupdate=utcnow)


# 읽기 전용 계산 칸(인스턴스 값 + SQL 조건)
Meeting.ended = column_property(
    exists().where(MeetingClosure.meeting_id == Meeting.id, MeetingClosure.ended_at.is_not(None)),
)
Meeting.deleted = column_property(
    exists().where(MeetingClosure.meeting_id == Meeting.id, MeetingClosure.deleted_at.is_not(None)),
)


def meeting_phase(meeting: Meeting) -> str:
    """삭제 > 보류 > 종료 > 진행중 순으로 판정한다."""
    if meeting.deleted:
        return "deleted"
    if meeting.on_hold:
        return "on_hold"
    if meeting.ended:
        return "ended"
    return "active"


def phase_condition(phase: str):
    """Meeting 조회에 붙일 단계 조건(meeting_phase 와 같은 우선순위)."""
    if phase == "deleted":
        return Meeting.deleted
    if phase == "on_hold":
        return and_(Meeting.on_hold, ~Meeting.deleted)
    if phase == "ended":
        return and_(Meeting.ended, ~Meeting.on_hold, ~Meeting.deleted)
    if phase == "active":
        return and_(~Meeting.ended, ~Meeting.on_hold, ~Meeting.deleted)
    raise ValueError(f"알 수 없는 단계: {phase}")


def active_meeting_condition():
    """진행중 회의록(보류·종료·삭제 아님). 할 일·자동 확정·메일이 이 조건으로 대상을 고른다."""
    return phase_condition("active")
