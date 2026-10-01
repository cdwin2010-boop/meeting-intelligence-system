"""사건 원장(추가 전용). 기록은 append_event() 로만 추가하고, 수정·삭제 함수는 두지 않는다."""
from datetime import datetime
from typing import Any

from sqlalchemy import JSON, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, Session, mapped_column

from app.db import Base
from app.models.common import UTCDateTime, utcnow


class Event(Base):
    __tablename__ = "events"

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"), index=True)
    # 대상: 예) entity_type="meeting", entity_id=회의록 id
    entity_type: Mapped[str] = mapped_column(String(50))
    entity_id: Mapped[int] = mapped_column(Integer)
    event_type: Mapped[str] = mapped_column(String(50))
    # 시스템 사건(자동 확정 등)은 행위자 없음
    actor_account_id: Mapped[int | None] = mapped_column(ForeignKey("accounts.id"), nullable=True, index=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)


def append_event(
    session: Session,
    *,
    tenant_id: int,
    entity_type: str,
    entity_id: int,
    event_type: str,
    actor_account_id: int | None = None,
    payload: dict[str, Any] | None = None,
) -> Event:
    """사건 1건을 원장에 추가한다. 커밋은 호출부에 맡긴다(업무 변경과 같은 트랜잭션으로 묶기 위해)."""
    event = Event(
        tenant_id=tenant_id,
        entity_type=entity_type,
        entity_id=entity_id,
        event_type=event_type,
        actor_account_id=actor_account_id,
        payload=payload or {},
    )
    session.add(event)
    session.flush()
    return event
