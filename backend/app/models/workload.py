"""업무 처리 현황 전일 집계 저장 표(작업 69-3). 하루 한 번 집계해 저장하고, 화면 API 는 이 저장값만 읽는다(업무 표를 직접 집계하지 않는다).
행 단위: (고객사, 집계 기준일, 계정, 부서). 겸직자는 소속 부서마다 행이 있고, 전사 합계는 계정 기준으로 중복을 제거해 읽는다.
대상 구성원은 업무가 0건이어도 행을 만든다."""
from datetime import date, datetime
from typing import Any

from sqlalchemy import JSON, Date, ForeignKey, Index, Integer, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.models.common import UTCDateTime, utcnow


class WorkloadSnapshot(Base):
    __tablename__ = "workload_snapshots"
    __table_args__ = (
        UniqueConstraint("tenant_id", "snapshot_date", "account_id", "department_id", name="uq_workload_snapshots_key"),
        Index("ix_workload_snapshots_tenant_date", "tenant_id", "snapshot_date"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"))
    # 집계 기준일(집계 실행일의 전일, 한국 시간)
    snapshot_date: Mapped[date] = mapped_column(Date)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id"), index=True)
    department_id: Mapped[int] = mapped_column(ForeignKey("departments.id"), index=True)
    completed_count: Mapped[int] = mapped_column(Integer, default=0)
    in_progress_count: Mapped[int] = mapped_column(Integer, default=0)
    overdue_count: Mapped[int] = mapped_column(Integer, default=0)
    # D-3 임박(진행 중 중 기한이 기준일 이상 기준일+3일 이하)
    due_soon_count: Mapped[int] = mapped_column(Integer, default=0)
    # 지연·임박 업무 목록(지연 먼저, 기한 오래된 순, 최대 WORKLOAD_URGENT_MAX 건): itemId·title·dueDate·kind(overdue|due_soon)·meetingId·meetingTitle
    urgent_items: Mapped[list[Any]] = mapped_column(JSON, default=list)
    generated_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)
