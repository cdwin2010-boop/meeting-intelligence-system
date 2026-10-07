"""프로젝트와 참여자. 부서장이 등록하면 바로 active, 부서원이 등록하면 pending_approval 이고 그 부서의 부서장이 승인하면 active(승인자가 총괄).
회의록과의 연결(회의 유형·프로젝트 소속)은 별도 표로 다음 작업에서 둔다(이 표들은 회의록 표를 바꾸지 않는다)."""
from datetime import datetime

from sqlalchemy import ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.models.common import PROJECT_MEMBER_ROLES, PROJECT_STATUSES, UTCDateTime, enum_check, utcnow


class Project(Base):
    __tablename__ = "projects"
    __table_args__ = (enum_check("status", PROJECT_STATUSES, "status_valid"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"), index=True)
    # 등록 부서(승인자 = 이 부서의 부서장)
    department_id: Mapped[int] = mapped_column(ForeignKey("departments.id"), index=True)
    name: Mapped[str] = mapped_column(String(120))
    description: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(20), default="pending_approval")
    registered_by: Mapped[int] = mapped_column(ForeignKey("accounts.id"), index=True)
    # 총괄: 부서장 등록이면 등록자, 부서원 등록이면 승인한 부서장(승인 전에는 비어 있음)
    lead_account_id: Mapped[int | None] = mapped_column(ForeignKey("accounts.id"), nullable=True, index=True)
    decided_by: Mapped[int | None] = mapped_column(ForeignKey("accounts.id"), nullable=True)
    decided_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)


class ProjectMember(Base):
    __tablename__ = "project_members"
    __table_args__ = (
        enum_check("role", PROJECT_MEMBER_ROLES, "role_valid"),
        UniqueConstraint("project_id", "account_id", name="uq_project_members_project_account"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), index=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id"), index=True)
    role: Mapped[str] = mapped_column(String(20), default="member")
    added_by: Mapped[int | None] = mapped_column(ForeignKey("accounts.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)
