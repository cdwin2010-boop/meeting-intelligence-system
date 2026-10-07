"""업무 대체 연결: 과거 업무(old)를 오늘 회의의 업무(new)가 대체한다. 상태값을 늘리지 않고(업무 표는 그대로) 별도 표로 둔다.
대체된 업무의 제외·포함 규칙은 app/models/item_conditions.py 한 곳에서만 다룬다(이 표를 다른 곳에서 직접 조회해 판정하지 않는다)."""
from datetime import datetime

from sqlalchemy import CheckConstraint, ForeignKey, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.models.common import UTCDateTime, utcnow


class ItemSupersession(Base):
    __tablename__ = "item_supersessions"
    __table_args__ = (CheckConstraint("old_item_id <> new_item_id", name="old_new_differ"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"), index=True)
    # 대체된 과거 업무(한 업무는 한 번만 대체된다)
    old_item_id: Mapped[int] = mapped_column(ForeignKey("action_items.id"), unique=True)
    # 대체하는 업무
    new_item_id: Mapped[int] = mapped_column(ForeignKey("action_items.id"), index=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), index=True)
    superseded_by: Mapped[int] = mapped_column(ForeignKey("accounts.id"))
    reason: Mapped[str] = mapped_column(Text)
    # 대체 요청에서 시작된 경우 그 요청 사건(events) id
    source_request_id: Mapped[int | None] = mapped_column(ForeignKey("events.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)
