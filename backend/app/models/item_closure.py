"""업무 종결 구분 기록(업무당 최대 1행): 정상 완료(completed) / 직권 종료(forced).
상태값을 늘리지 않고(action_items.status 는 그대로 closed) action_items 표도 고치지 않으려고 따로 둔다.
행이 없는 종결 업무는 "구분 없음"(이전에 종결됐거나 구분 없이 종결된 업무)이며 완료로 세지 않는다."""
from datetime import datetime

from sqlalchemy import ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.models.common import UTCDateTime, enum_check, utcnow

CLOSURE_KINDS = ("completed", "forced")


class ItemClosure(Base):
    __tablename__ = "item_closures"
    __table_args__ = (enum_check("kind", CLOSURE_KINDS, "kind_valid"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"), index=True)
    item_id: Mapped[int] = mapped_column(ForeignKey("action_items.id"), unique=True)
    kind: Mapped[str] = mapped_column(String(20))
    closed_by: Mapped[int] = mapped_column(ForeignKey("accounts.id"))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)
