"""업무(ActionItem) 상태를 가르는 SQL 조건의 단일 창구. 업무 상태를 쿼리 조건으로 직접 비교하지 말고 여기 함수를 쓴다
(tests/test_item_conditions_guard.py 가 검사한다). 모든 함수는 조건식을 돌려주며, 별칭(aliased) 업무에도 쓸 수 있게 item 인자를 받는다.

의미별 이름:
- not_deleted_item   삭제되지 않은 업무(확정 대기·확정·종결). 목록·원장·엑셀·집계의 기본 범위
- deleted_item       삭제된 업무(not_deleted_item 의 반대)
- open_item          열려 있는 업무(확정 대기·확정). 종결·삭제는 아님. 할 일·메일·보류/종료 때 처리 대상
- inactive_item      끝난 업무(종결·삭제). open_item 의 반대. 안내에서 뺄 때 쓴다
- pending_item       확정 대기 업무
- confirmed_item     확정된 업무
(open_item 은 상태 CHECK 제약(pending·confirmed·closed·deleted) 아래에서 "종결·삭제가 아님" 과 같다.)

장차 "대체된 업무"를 모두 같은 방식으로 빼려면 아래 _superseded() 한 곳만 바꾼다. 지금은 대체 기능이 없어 항상 거짓이다.
"""
from sqlalchemy import ColumnElement, and_, false, or_

from app.models.action_item import ActionItem
from app.models.common import INACTIVE_ITEM_STATUSES


def _superseded(item: type[ActionItem] = ActionItem) -> ColumnElement[bool]:
    """대체된 업무인가(아직 대체 기능 없음 → 거짓). 대체 연결 표가 생기면 여기서 EXISTS 조건을 돌려준다."""
    return false()


def _live(item: type[ActionItem]) -> ColumnElement[bool]:
    """모든 '살아 있는 업무' 조건에 공통으로 붙는 조건(대체되지 않음)."""
    return ~_superseded(item)


def not_deleted_item(item: type[ActionItem] = ActionItem) -> ColumnElement[bool]:
    return and_(item.status != "deleted", _live(item))


def deleted_item(item: type[ActionItem] = ActionItem) -> ColumnElement[bool]:
    return or_(item.status == "deleted", _superseded(item))


def open_item(item: type[ActionItem] = ActionItem) -> ColumnElement[bool]:
    return and_(item.status.not_in(INACTIVE_ITEM_STATUSES), _live(item))


def inactive_item(item: type[ActionItem] = ActionItem) -> ColumnElement[bool]:
    return or_(item.status.in_(INACTIVE_ITEM_STATUSES), _superseded(item))


def pending_item(item: type[ActionItem] = ActionItem) -> ColumnElement[bool]:
    return and_(item.status == "pending", _live(item))


def confirmed_item(item: type[ActionItem] = ActionItem) -> ColumnElement[bool]:
    return and_(item.status == "confirmed", _live(item))
