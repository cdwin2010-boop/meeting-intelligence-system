"""조직: 부서(임원 그룹 포함, 상하 계층 가능)와 계정 × 부서 × 역할 소속. 별도 직원 표는 없고 기존 계정(Account)에 소속을 붙인다.
겸직은 소속이 여러 개다. "부서장은 부서마다 1명"은 DB 제약이 아니라 서비스 계층(app/services/org.py)에서 보장한다(방언 중립)."""
from datetime import datetime

from sqlalchemy import ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.models.common import DEPARTMENT_KINDS, DEPARTMENT_ROLES, UTCDateTime, enum_check, utcnow


class Department(Base):
    __tablename__ = "departments"
    __table_args__ = (
        enum_check("kind", DEPARTMENT_KINDS, "kind_valid"),
        UniqueConstraint("tenant_id", "name", name="uq_departments_tenant_name"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"), index=True)
    name: Mapped[str] = mapped_column(String(100))
    # 상위 부서(없으면 최상위). 자기 참조
    parent_id: Mapped[int | None] = mapped_column(ForeignKey("departments.id"), nullable=True, index=True)
    kind: Mapped[str] = mapped_column(String(20), default="normal")
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)


class AccountDepartment(Base):
    """계정의 부서 소속과 역할(head·member). 같은 (계정, 부서)는 한 번만."""

    __tablename__ = "account_departments"
    __table_args__ = (
        enum_check("role", DEPARTMENT_ROLES, "role_valid"),
        UniqueConstraint("account_id", "department_id", name="uq_account_departments_account_department"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id"))
    department_id: Mapped[int] = mapped_column(ForeignKey("departments.id"), index=True)
    role: Mapped[str] = mapped_column(String(20), default="member")
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)
