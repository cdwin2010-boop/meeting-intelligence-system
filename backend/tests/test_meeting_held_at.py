"""회의 일시 필수 입력·등록 일시 자동 기록·등록 후 수정(작업 88-4).
임시 DB·임시 업로드 폴더·fake 엔진만 쓴다(Gemini 호출 없음)."""
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from app.config import settings
from app.models import ActionItem, Event, Meeting
from app.models.common import utcnow
from app.pipeline.extractor import FakeExtractor
from app.services.history import HISTORY_KINDS, KIND_MEETING_HELD_AT
from app.services.processing import process_meeting
from tests.test_upload_processing import HELD_AT, M4A, add_account, auth, env, upload  # noqa: F401  (env 는 픽스처)


@pytest.fixture
def team(env):
    f = env["factory"]
    return {
        "staff": add_account(f, "고객사A", "staff1", "staff", name="담당자"),
        "mgr": add_account(f, "고객사A", "mgr1", "manager", name="관리자"),
        "exe": add_account(f, "고객사A", "exe1", "executive", name="지시자"),
        "other": add_account(f, "고객사B", "mgr2", "manager", name="타사관리자"),
    }


def raw_upload(env, account, **data):
    form = {"title": "회의"}
    form.update(data)
    return env["client"].post("/api/meetings/upload", headers=auth(account), data=form, files={"file": ("a.m4a", M4A, "audio/mp4")})


def iso(delta: timedelta, tz_hours: int = 9) -> str:
    tz = timezone(timedelta(hours=tz_hours))
    return (datetime.now(timezone.utc) + delta).astimezone(tz).replace(microsecond=0).isoformat()


def patch(env, account, meeting_id, **body):
    return env["client"].patch(f"/api/meetings/{meeting_id}/held-at", headers=auth(account), json=body)


def meeting_row(env, meeting_id) -> Meeting:
    with env["factory"]() as s:
        return s.get(Meeting, meeting_id)


def held_events(env, meeting_id):
    with env["factory"]() as s:
        return list(s.scalars(select(Event).where(Event.entity_type == "meeting", Event.entity_id == meeting_id, Event.event_type == KIND_MEETING_HELD_AT)))


# ---------------- 등록: 필수·미래 거부 ----------------
@pytest.mark.parametrize("data", [{}, {"heldAt": ""}, {"heldAt": "   "}])
def test_upload_without_held_at_is_422_with_korean_reason(env, team, data):
    res = raw_upload(env, team["mgr"], **data)
    assert res.status_code == 422
    assert "회의 일시" in res.json()["detail"] and "필수" in res.json()["detail"]
    with env["factory"]() as s:
        assert s.scalar(select(Meeting.id)) is None  # 아무것도 만들지 않는다


def test_future_held_at_is_422(env, team):
    res = raw_upload(env, team["mgr"], heldAt=iso(timedelta(days=1)))
    assert res.status_code == 422 and "미래" in res.json()["detail"]
    with env["factory"]() as s:
        assert s.scalar(select(Meeting.id)) is None


def test_clock_skew_within_tolerance_is_accepted_and_beyond_is_rejected(env, team, monkeypatch):
    monkeypatch.setattr(settings, "meeting_at_future_tolerance_minutes", 10)
    assert raw_upload(env, team["mgr"], heldAt=iso(timedelta(minutes=5))).status_code == 202
    assert raw_upload(env, team["mgr"], heldAt=iso(timedelta(minutes=30))).status_code == 422


def test_format_and_offset_errors_stay_400(env, team):
    assert raw_upload(env, team["mgr"], heldAt="2026-09-29T14:00:00").status_code == 400
    assert raw_upload(env, team["mgr"], heldAt="어제").status_code == 400


# ---------------- 등록 일시: 자동 기록·별도 보관 ----------------
def test_registered_at_is_auto_recorded_in_utc_and_separate_from_held_at(env, team):
    before = utcnow()
    body = upload(env, team["mgr"], held_at="2026-09-29T14:00:00+09:00").json()
    after = utcnow()
    detail = env["client"].get(f"/api/meetings/{body['meetingId']}", headers=auth(team["mgr"])).json()
    registered = datetime.fromisoformat(detail["registeredAt"].replace("Z", "+00:00"))
    assert before - timedelta(seconds=1) <= registered <= after + timedelta(seconds=1)
    assert detail["registeredAt"] == detail["firstCreatedAt"]
    assert datetime.fromisoformat(detail["heldAt"].replace("Z", "+00:00")) == datetime(2026, 9, 29, 5, 0, tzinfo=timezone.utc)
    assert detail["heldAt"] != detail["registeredAt"]
    row = meeting_row(env, body["meetingId"])
    assert row.held_at == datetime(2026, 9, 29, 5, 0, tzinfo=timezone.utc)  # UTC 저장, 날짜 밀림 없음


def test_client_cannot_set_registered_at(env, team):
    fake = "2020-01-01T00:00:00+00:00"
    res = raw_upload(env, team["mgr"], heldAt=HELD_AT, registeredAt=fake, firstCreatedAt=fake, createdAt=fake)
    assert res.status_code == 202
    row = meeting_row(env, res.json()["meetingId"])
    assert row.first_created_at.year == utcnow().year  # 입력값은 무시된다


def test_list_items_carry_registered_at(env, team):
    upload(env, team["mgr"])
    items = env["client"].get("/api/meetings", headers=auth(team["mgr"])).json()["items"]
    assert items and "registeredAt" in items[0] and "heldAt" in items[0]


def test_local_time_input_keeps_the_same_instant_for_other_offsets(env, team):
    a = upload(env, team["mgr"], held_at="2026-09-29T00:30:00+09:00").json()["meetingId"]  # KST 자정 직후 = UTC 전날
    b = upload(env, team["mgr"], held_at="2026-09-28T15:30:00+00:00").json()["meetingId"]
    assert meeting_row(env, a).held_at == meeting_row(env, b).held_at == datetime(2026, 9, 28, 15, 30, tzinfo=timezone.utc)


# ---------------- 등록 후 수정 ----------------
def test_manager_changes_held_at_with_reason_and_history_is_recorded(env, team):
    mid = upload(env, team["mgr"], held_at="2026-09-29T14:00:00+09:00").json()["meetingId"]
    first_created = meeting_row(env, mid).first_created_at
    res = patch(env, team["mgr"], mid, heldAt="2026-09-30T10:00:00+09:00", reason="실제 회의 날짜 정정")
    assert res.status_code == 200 and res.json()["changed"] is True
    row = meeting_row(env, mid)
    assert row.held_at == datetime(2026, 9, 30, 1, 0, tzinfo=timezone.utc)
    assert row.first_created_at == first_created  # 등록 일시는 바뀌지 않는다
    [event] = held_events(env, mid)
    assert event.actor_account_id == team["mgr"].id and event.created_at is not None
    assert event.payload["reason"] == "실제 회의 날짜 정정"
    assert event.payload["before"]["heldAt"].startswith("2026-09-29T05:00:00")
    assert event.payload["after"]["heldAt"].startswith("2026-09-30T01:00:00")
    assert HISTORY_KINDS[KIND_MEETING_HELD_AT] == "회의 일시 변경"
    history = env["client"].get(f"/api/meetings/{mid}/history", headers=auth(team["mgr"])).json()
    assert [h["kind"] for h in history if h["kindCode"] == KIND_MEETING_HELD_AT] == ["회의 일시 변경"]


def test_executive_can_change_and_staff_cannot(env, team):
    mid = upload(env, team["staff"]).json()["meetingId"]
    assert patch(env, team["staff"], mid, heldAt="2026-09-30T10:00:00+09:00", reason="정정").status_code == 403
    assert held_events(env, mid) == []
    assert patch(env, team["exe"], mid, heldAt="2026-09-30T10:00:00+09:00", reason="정정").status_code == 200


@pytest.mark.parametrize("body", [
    {"heldAt": "2026-09-30T10:00:00+09:00"},
    {"heldAt": "2026-09-30T10:00:00+09:00", "reason": ""},
    {"heldAt": "2026-09-30T10:00:00+09:00", "reason": "   "},
])
def test_reason_is_required(env, team, body):
    mid = upload(env, team["mgr"]).json()["meetingId"]
    assert patch(env, team["mgr"], mid, **body).status_code == 422
    assert held_events(env, mid) == []


def test_edit_rejects_missing_and_future_held_at(env, team):
    mid = upload(env, team["mgr"]).json()["meetingId"]
    assert patch(env, team["mgr"], mid, reason="정정").status_code == 422
    assert patch(env, team["mgr"], mid, heldAt="", reason="정정").status_code == 422
    res = patch(env, team["mgr"], mid, heldAt=iso(timedelta(days=2)), reason="정정")
    assert res.status_code == 422 and "미래" in res.json()["detail"]
    assert held_events(env, mid) == []


def test_same_value_is_idempotent_without_history(env, team):
    mid = upload(env, team["mgr"], held_at="2026-09-29T14:00:00+09:00").json()["meetingId"]
    res = patch(env, team["mgr"], mid, heldAt="2026-09-29T14:00:00+09:00", reason="변경 없음")
    assert res.status_code == 200 and res.json()["changed"] is False
    assert held_events(env, mid) == []


def test_other_tenant_and_unknown_are_404_and_deleted_meeting_is_locked(env, team):
    mid = upload(env, team["mgr"]).json()["meetingId"]
    assert patch(env, team["other"], mid, heldAt="2026-09-30T10:00:00+09:00", reason="정정").status_code == 404
    assert patch(env, team["mgr"], 9999, heldAt="2026-09-30T10:00:00+09:00", reason="정정").status_code == 404
    done = env["client"].post(f"/api/meetings/{mid}/delete", headers=auth(team["exe"]), json={"reason": "삭제"})
    if done.status_code == 200:
        assert patch(env, team["exe"], mid, heldAt="2026-09-30T10:00:00+09:00", reason="정정").status_code in (404, 409)


def test_edit_does_not_recalculate_existing_due_dates(env, team):
    body = upload(env, team["mgr"], held_at="2026-09-29T14:00:00+09:00").json()
    process_meeting(body["jobId"], session_factory=env["factory"], extractor_factory=FakeExtractor)
    mid = body["meetingId"]

    def due_dates():
        with env["factory"]() as s:
            return sorted((i.id, i.due_date) for i in s.scalars(select(ActionItem).where(ActionItem.meeting_id == mid)))

    before = due_dates()
    assert before  # fake 추출기가 만든 업무가 있다
    assert patch(env, team["mgr"], mid, heldAt="2026-09-20T10:00:00+09:00", reason="정정").status_code == 200
    assert due_dates() == before  # 이미 계산된 기한은 그대로
    assert meeting_row(env, mid).auto_confirm_at is not None


def test_allowed_actions_lists_edit_held_at_for_manager_up_only(env, team):
    mid = upload(env, team["staff"]).json()["meetingId"]

    def actions(account):
        return env["client"].get(f"/api/meetings/{mid}", headers=auth(account)).json()["allowedActions"]

    assert "edit_held_at" in actions(team["mgr"]) and "edit_held_at" in actions(team["exe"])
    assert "edit_held_at" not in actions(team["staff"])


# ---------------- 상대 날짜 기준일 ----------------
def test_relative_dates_use_meeting_date_not_registration_time(env, team):
    """업로드한 날(오늘)이 아니라 회의 일시(현지 날짜)를 기준일로 쓴다."""
    body = upload(env, team["mgr"], held_at="2026-09-28T08:00:00+09:00").json()
    process_meeting(body["jobId"], session_factory=env["factory"], extractor_factory=FakeExtractor)
    with env["factory"]() as s:
        dues = [i.due_date for i in s.scalars(select(ActionItem).where(ActionItem.meeting_id == body["meetingId"])) if i.due_date]
    assert dues and all(d.isoformat() >= "2026-09-28" for d in dues)
    assert all(d.isoformat() < "2026-10-13" for d in dues)  # 오늘(등록일)이 아니라 회의일 근처
