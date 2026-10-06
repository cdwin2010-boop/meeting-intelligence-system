"""공통 변경 이력. 별도 표를 만들지 않고 기존 사건 원장(events)을 그대로 쓴다.
공통 구조 = 대상 종류(entity_type) · 대상 ID(entity_id) · 구분(event_type) · 변경자(actor_account_id) · 시각(created_at)
            · 변경 전·후(payload["before"]·payload["after"], 바뀐 칸만).
이력으로 보이는 구분은 HISTORY_KINDS 에만 등록한다. 업무 갱신·감사 로그도 같은 구조에 구분만 더해 쓴다(기능마다 표를 따로 만들지 않음)."""
from typing import Any

from sqlalchemy.orm import Session

from app.models import Event, append_event

# 사건 종류 → 화면에 보이는 구분
KIND_MINUTES_OVERRIDE = "minutes.overridden"
KIND_PARTICIPANTS_OVERRIDE = "participants.overridden"
KIND_ITEM_UPLOAD_UPDATE = "item.upload_updated"
KIND_ITEM_MANUAL_ADD = "item.manual_added"
HISTORY_KINDS: dict[str, str] = {
    KIND_MINUTES_OVERRIDE: "직권 수정",
    KIND_PARTICIPANTS_OVERRIDE: "직권 수정",
    KIND_ITEM_UPLOAD_UPDATE: "업무 갱신",
    KIND_ITEM_MANUAL_ADD: "직권 등록",
}


def record_change(
    session: Session,
    *,
    tenant_id: int,
    target_type: str,
    target_id: int,
    kind: str,
    actor_id: int | None,
    before: dict[str, Any],
    after: dict[str, Any],
    extra: dict[str, Any] | None = None,
) -> Event:
    """변경 이력 1건을 남긴다(커밋은 호출부: 변경과 같은 트랜잭션). kind 는 HISTORY_KINDS 에 있어야 한다."""
    if kind not in HISTORY_KINDS:
        raise ValueError(f"unknown history kind: {kind}")
    return append_event(
        session,
        tenant_id=tenant_id,
        entity_type=target_type,
        entity_id=target_id,
        event_type=kind,
        actor_account_id=actor_id,
        # extra: 같은 업로드 묶음(batchId)·출처(source)·열람 영향 같은 덧붙임. before/after 와 같은 층에 둔다
        payload={"before": before, "after": after, **(extra or {})},
    )
