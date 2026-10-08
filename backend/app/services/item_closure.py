"""업무 종결 구분(item_closures) 읽기·쓰기. 이 표는 이 파일에서만 다룬다."""
from collections.abc import Iterable

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import ActionItem
from app.models.item_closure import ItemClosure


def record_closure(session: Session, item: ActionItem, kind: str, account_id: int) -> None:
    """종결과 같은 트랜잭션에서 구분 행을 더한다(커밋은 호출자). 이미 행이 있으면 바꾸지 않는다."""
    if session.scalar(select(ItemClosure.id).where(ItemClosure.item_id == item.id)) is not None:
        return
    session.add(ItemClosure(tenant_id=item.tenant_id, item_id=item.id, kind=kind, closed_by=account_id))


def closure_kinds(session: Session, item_ids: Iterable[int]) -> dict[int, str]:
    """업무 id → 종결 구분(행이 없는 업무는 빠짐 = 구분 없음)."""
    ids = list(item_ids)
    if not ids:
        return {}
    return dict(session.execute(select(ItemClosure.item_id, ItemClosure.kind).where(ItemClosure.item_id.in_(ids))).all())
