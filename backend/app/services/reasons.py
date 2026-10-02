"""처리 사유 조회. 사유의 보관처는 사건(events.payload["reason"])이다(처리자·시각도 같은 사건 행에 있음).
사유 필수 동작: 업무 종결·삭제(item.closed·item.deleted), 회의록 보류(meeting.on_hold)·직권 종료(meeting.ended)·삭제(meeting.deleted).
재개·자동 종료는 사유 없음. 사유 도입 전에 남은 사건에는 사유가 없다(소급하지 않음)."""
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Event, Meeting


def event_reason(payload: dict[str, Any] | None) -> str | None:
    value = (payload or {}).get("reason")
    return value if isinstance(value, str) and value else None


def latest_reason(session: Session, meeting: Meeting, event_type: str) -> str | None:
    """이 회의록의 가장 최근 event_type 사건에 남은 사유."""
    payload = session.scalar(
        select(Event.payload)
        .where(
            Event.tenant_id == meeting.tenant_id,
            Event.entity_type == "meeting",
            Event.entity_id == meeting.id,
            Event.event_type == event_type,
        )
        .order_by(Event.id.desc())
        .limit(1)
    )
    return event_reason(payload)
