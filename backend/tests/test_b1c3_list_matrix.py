"""작업 B-1c-3 A: 과거 회의 일시 회의록이 목록 기본 조건에서 빠지는 조건을 조합별로 재현한다(임시 DB 픽스처, 실데이터 없음).
결론: 상태(status)·기간 필터는 기본 조건에 없고, 누락은 오직 (1) 단계 탭 (2) 회의 일시 최신순 + 쪽 크기 때문이다."""
from datetime import datetime, timezone

import pytest

from app.models import Meeting, MeetingClosure, MeetingHold
from app.models.common import MEETING_STATUSES
from tests.test_meeting_queries import get, list_ids, make_meeting
from tests.test_b1c1_list_search_evidence import team  # noqa: F401  (픽스처)
from tests.test_upload_processing import env  # noqa: F401  (픽스처)

PAST = datetime(2025, 1, 5, tzinfo=timezone.utc)


def _make_past(env, registrant, status):
    mid = make_meeting(env["factory"], registrant, day=0, status=status, title=f"과거 {status}")
    with env["factory"]() as s:
        s.get(Meeting, mid).held_at = PAST
        s.commit()
    return mid


@pytest.mark.parametrize("status", MEETING_STATUSES)
def test_past_dated_in_every_status_is_in_default_list(env, team, status):
    """상태 필터는 기본 목록에 없다: 어떤 status 든 과거 날짜 진행중 회의록은 기본 목록(첫 쪽, 쪽 안 밀리면)에 나온다."""
    mid = _make_past(env, team["stf"], status)
    assert mid in list_ids(env, team["mgr"])
    assert mid in list_ids(env, team["stf"])  # 등록자 본인


def test_past_dated_disappears_only_by_phase_tab(env, team):
    """단계: 종료·보류·삭제된 회의록은 진행중 탭이 아니라 해당 탭에만 나온다(설계)."""
    ended = _make_past(env, team["stf"], "confirmed")
    held = _make_past(env, team["stf"], "awaiting_confirmation")
    with env["factory"]() as s:
        tenant = s.get(Meeting, ended).tenant_id
        s.add(MeetingClosure(meeting_id=ended, tenant_id=tenant, end_kind="auto", ended_at=datetime(2026, 10, 1, tzinfo=timezone.utc)))
        s.add(MeetingHold(meeting_id=held, tenant_id=tenant, on_hold=True))
        s.commit()
    assert ended not in list_ids(env, team["mgr"]) and held not in list_ids(env, team["mgr"])
    assert ended in list_ids(env, team["mgr"], phase="ended")
    assert held in list_ids(env, team["mgr"], phase="on_hold")


def test_past_dated_pushed_by_page_size_not_by_filter(env, team):
    """쪽 크기: 최신 회의 21건 뒤에 오는 과거 날짜 회의록은 첫 쪽(20건) 밖, size=100·둘째 쪽·검색·등록순에는 나온다."""
    for day in range(30, 51):
        make_meeting(env["factory"], team["mgr"], day=day)
    mid = _make_past(env, team["stf"], "awaiting_confirmation")
    assert mid not in list_ids(env, team["mgr"])
    assert mid in list_ids(env, team["mgr"], size=100)
    assert mid in [m["id"] for m in get(env, team["mgr"], "/api/meetings", page=2).json()["items"]]
    assert list_ids(env, team["mgr"], q="과거") == [mid]
