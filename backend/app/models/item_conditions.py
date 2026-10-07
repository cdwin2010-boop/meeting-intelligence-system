"""업무(ActionItem) 상태를 가르는 SQL 조건의 단일 창구. 업무 상태·대체 여부를 쿼리 조건으로 직접 비교하지 말고 여기 함수를 쓴다
(tests/test_item_conditions.py 의 가드가 검사한다). 모든 함수는 조건식을 돌려주며, 별칭(aliased) 업무에도 쓸 수 있게 item 인자를 받는다.

의미별 이름과 "대체된 업무"(item_supersessions 의 old_item_id) 처리:
- not_deleted_item   삭제되지 않은 업무(확정 대기·확정·종결). 업무 원장·엑셀·이력·집계처럼 "보여 주는" 범위라 대체된 업무를 포함한다
- deleted_item       삭제된 업무(not_deleted_item 의 반대). 대체된 업무는 삭제된 것이 아니다
- open_item          열려 있는 업무(확정 대기·확정). 종결·삭제도 아니고 대체되지도 않음. 할 일·메일·보류/종료 때 처리 대상이라 대체된 업무는 제외
- inactive_item      끝난 업무(종결·삭제) 또는 대체된 업무. 안내를 숨길 때 쓰므로 대체된 업무를 포함한다
- pending_item       확정 대기 업무(대체된 업무는 제외: 자동 확정·할 일·메일에서 빠진다)
- confirmed_item     확정된 업무(대체된 업무는 제외)
- live_item          대체되지 않은 업무(상태와 무관). 집계에서 대체된 업무만 빼야 할 때 쓴다
(open_item 은 상태 CHECK 제약(pending·confirmed·closed·deleted) 아래에서 "종결·삭제가 아님" 과 같다.)
"""
from sqlalchemy import ColumnElement, and_, exists, or_
from sqlalchemy.orm import aliased

from app.models.action_item import ActionItem
from app.models.common import INACTIVE_ITEM_STATUSES
from app.models.supersession import ItemSupersession


def _superseded(item: type[ActionItem] = ActionItem) -> ColumnElement[bool]:
    """대체된 업무인가(item_supersessions 에 old_item_id 로 있음)."""
    link = aliased(ItemSupersession)
    return exists().where(link.old_item_id == item.id)


def live_item(item: type[ActionItem] = ActionItem) -> ColumnElement[bool]:
    return ~_superseded(item)


def not_deleted_item(item: type[ActionItem] = ActionItem) -> ColumnElement[bool]:
    return item.status != "deleted"


def deleted_item(item: type[ActionItem] = ActionItem) -> ColumnElement[bool]:
    return item.status == "deleted"


def open_item(item: type[ActionItem] = ActionItem) -> ColumnElement[bool]:
    return and_(item.status.not_in(INACTIVE_ITEM_STATUSES), live_item(item))


def inactive_item(item: type[ActionItem] = ActionItem) -> ColumnElement[bool]:
    return or_(item.status.in_(INACTIVE_ITEM_STATUSES), _superseded(item))


def pending_item(item: type[ActionItem] = ActionItem) -> ColumnElement[bool]:
    return and_(item.status == "pending", live_item(item))


def confirmed_item(item: type[ActionItem] = ActionItem) -> ColumnElement[bool]:
    return and_(item.status == "confirmed", live_item(item))
