"""처리 사유 필수(㊺-2): 업무 종결·삭제, 회의록 보류·직권 종료·삭제는 reason 필수(없거나 공백·2000자 초과는 422).
사유는 사건(events.payload.reason)에 처리자·시각과 함께 남고 회의록 상세(보류·종료·삭제 사유, 최근 사건)에서 보인다.
재개·자동 종료는 사유 없음, 사유 도입 전 기록은 None(소급 없음)."""
from datetime import date, datetime, timezone

import pytest
from sqlalchemy import select

from app.models import ActionItem, Event, MeetingHold
from tests.test_meeting_queries import make_meeting
from tests.test_upload_processing import add_account, auth, env  # noqa: F401  (env 는 픽스처)

DUE = date(2026, 10, 9)


@pytest.fixture
def team(env):
    f = env["factory"]
    return {
        "lead": add_account(f, "고객사A", "lead", "manager", name="박총괄"),
        "exe": add_account(f, "고객사A", "boss", "executive", name="최임원"),
        "staff": add_account(f, "고객사A", "kim", "staff", name="김담당"),
    }


def call(env, method, account, path, **kwargs):
    return env["client"].request(method, path, headers=auth(account), **kwargs)


def confirmed(**fields) -> dict:
    return {"title": "업무", "due_date": DUE, "status": "confirmed", "confirm_kind": "manager", **fields}


def item_ids(env, meeting_id) -> list[int]:
    with env["factory"]() as s:
        return list(s.scalars(select(ActionItem.id).where(ActionItem.meeting_id == meeting_id).order_by(ActionItem.id)))


def events(env, entity_type, entity_id, event_type) -> list[Event]:
    with env["factory"]() as s:
        return s.scalars(select(Event).where(Event.entity_type == entity_type, Event.entity_id == entity_id,
                                             Event.event_type == event_type).order_by(Event.id)).all()


def detail(env, account, meeting_id) -> dict:
    res = call(env, "GET", account, f"/api/meetings/{meeting_id}")
    assert res.status_code == 200
    return res.json()


def five_actions(meeting_id, item_id):
    return [
        f"/api/action-items/{item_id}/close",
        f"/api/action-items/{item_id}/delete",
        f"/api/meetings/{meeting_id}/hold",
        f"/api/meetings/{meeting_id}/end",
        f"/api/meetings/{meeting_id}/delete",
    ]


@pytest.mark.parametrize("body", [None, {}, {"reason": ""}, {"reason": "   \n\t"}, {"reason": "가" * 2001}, {"reason": None}])
def test_reason_required_for_five_actions(env, team, body):
    meeting_id = make_meeting(env["factory"], team["lead"], items=[confirmed()])
    [item_id] = item_ids(env, meeting_id)
    for path in five_actions(meeting_id, item_id):
        kwargs = {} if body is None else {"json": body}
        res = call(env, "POST", team["exe"], path, **kwargs)
        assert res.status_code == 422, (path, body, res.text)
    with env["factory"]() as s:  # 아무것도 바뀌지 않음
        assert s.get(ActionItem, item_id).status == "confirmed"
        assert s.get(MeetingHold, meeting_id) is None
        assert not s.scalars(select(Event.id).where(Event.event_type.in_((
            "item.closed", "item.deleted", "meeting.on_hold", "meeting.ended", "meeting.deleted")))).first()


def test_reason_max_length_2000_is_accepted_and_trimmed(env, team):
    meeting_id = make_meeting(env["factory"], team["lead"], items=[confirmed(), confirmed()])
    first, second = item_ids(env, meeting_id)
    reason = "가" * 2000  # 길이는 받은 그대로 센다(수정 요청 코멘트와 같은 방식)
    assert call(env, "POST", team["lead"], f"/api/action-items/{first}/close", json={"reason": reason}).status_code == 200
    [event] = events(env, "action_item", first, "item.closed")
    assert event.payload["reason"] == reason and event.actor_account_id == team["lead"].id and event.created_at
    assert call(env, "POST", team["lead"], f"/api/action-items/{second}/close", json={"reason": "  완료  "}).status_code == 200
    [event] = events(env, "action_item", second, "item.closed")
    assert event.payload["reason"] == "완료"  # 앞뒤 공백 제거


def test_item_close_and_delete_reasons_kept_and_shown_in_recent_events(env, team):
    meeting_id = make_meeting(env["factory"], team["lead"], items=[confirmed(title="닫을"), confirmed(title="지울"), confirmed(title="남길")])
    close_id, delete_id, _ = item_ids(env, meeting_id)
    call(env, "POST", team["lead"], f"/api/action-items/{close_id}/close", json={"reason": "납품 완료"})
    call(env, "POST", team["exe"], f"/api/action-items/{delete_id}/delete", json={"reason": "중복 등록"})
    [closed] = events(env, "action_item", close_id, "item.closed")
    [deleted] = events(env, "action_item", delete_id, "item.deleted")
    assert (closed.payload["reason"], closed.actor_account_id) == ("납품 완료", team["lead"].id)
    assert (deleted.payload["reason"], deleted.actor_account_id) == ("중복 등록", team["exe"].id)
    recent = detail(env, team["lead"], meeting_id)["recentEvents"]
    by_type = {e["eventType"]: e for e in recent}
    assert by_type["item.deleted"]["reason"] == "중복 등록" and by_type["item.deleted"]["actor"]["id"] == team["exe"].id
    assert by_type["item.closed"]["reason"] == "납품 완료"
    assert by_type["meeting.created"]["reason"] is None


def test_hold_reason_shown_and_resume_has_no_reason(env, team):
    meeting_id = make_meeting(env["factory"], team["lead"], items=[confirmed()])
    res = call(env, "POST", team["lead"], f"/api/meetings/{meeting_id}/hold", json={"reason": "예산 재검토"})
    assert res.status_code == 200 and res.json()["onHoldReason"] == "예산 재검토"
    body = detail(env, team["exe"], meeting_id)
    assert body["onHoldReason"] == "예산 재검토" and body["onHoldBy"]["id"] == team["lead"].id
    assert body["recentEvents"][0]["eventType"] == "meeting.on_hold" and body["recentEvents"][0]["reason"] == "예산 재검토"

    # 재개는 사유를 받지 않는다(보내도 무시), 누가·언제만
    res = call(env, "POST", team["exe"], f"/api/meetings/{meeting_id}/resume", json={"reason": "무시됨"})
    assert res.status_code == 200
    [resumed] = events(env, "meeting", meeting_id, "meeting.resumed")
    assert "reason" not in resumed.payload and resumed.actor_account_id == team["exe"].id
    body = detail(env, team["exe"], meeting_id)
    assert body["onHold"] is False and body["resumedBy"]["id"] == team["exe"].id
    assert body["onHoldReason"] == "예산 재검토"  # 마지막 보류 기록(보류자·시각과 같이 남음)


def test_manager_end_reason_kept_on_meeting_and_closed_items(env, team):
    meeting_id = make_meeting(env["factory"], team["lead"], items=[{"title": "확정 전"}, confirmed()])
    pending_id, confirmed_id = item_ids(env, meeting_id)
    res = call(env, "POST", team["exe"], f"/api/meetings/{meeting_id}/end", json={"reason": "프로젝트 취소"})
    assert res.status_code == 200 and res.json()["endReason"] == "프로젝트 취소" and res.json()["endKind"] == "manager"
    [ended] = events(env, "meeting", meeting_id, "meeting.ended")
    assert ended.payload["reason"] == "프로젝트 취소" and ended.actor_account_id == team["exe"].id
    for item_id in (pending_id, confirmed_id):
        [closed] = events(env, "action_item", item_id, "item.closed")
        assert closed.payload["reason"] == "프로젝트 취소" and closed.payload["via"] == "meeting_ended"
    body = detail(env, team["lead"], meeting_id)
    assert body["endKind"] == "manager" and body["endReason"] == "프로젝트 취소" and body["endedBy"]["id"] == team["exe"].id


def test_auto_end_has_no_reason(env, team):
    meeting_id = make_meeting(env["factory"], team["lead"], items=[confirmed()])
    [item_id] = item_ids(env, meeting_id)
    call(env, "POST", team["lead"], f"/api/action-items/{item_id}/close", json={"reason": "완료"})
    [ended] = events(env, "meeting", meeting_id, "meeting.ended")
    assert ended.payload["endKind"] == "auto" and "reason" not in ended.payload and ended.actor_account_id is None
    body = detail(env, team["lead"], meeting_id)
    assert body["endKind"] == "auto" and body["endReason"] is None and body["endedBy"] is None


def test_meeting_delete_reason_shown(env, team):
    meeting_id = make_meeting(env["factory"], team["lead"])
    res = call(env, "POST", team["lead"], f"/api/meetings/{meeting_id}/delete", json={"reason": "잘못 올린 파일"})
    assert res.status_code == 200 and res.json()["deleteReason"] == "잘못 올린 파일"
    body = detail(env, team["exe"], meeting_id)
    assert body["phase"] == "deleted" and body["deleteReason"] == "잘못 올린 파일" and body["deletedBy"]["id"] == team["lead"].id


def test_records_before_reasons_have_no_reason(env, team):
    """사유 도입 전에 보류된 기록(사건에 사유 없음)은 채우지 않고 None 으로 둔다."""
    meeting_id = make_meeting(env["factory"], team["lead"])
    with env["factory"]() as s:
        s.add(MeetingHold(meeting_id=meeting_id, tenant_id=team["lead"].tenant_id, on_hold=True,
                          on_hold_by=team["lead"].id, on_hold_at=datetime.now(timezone.utc)))
        s.commit()
    body = detail(env, team["exe"], meeting_id)
    assert body["onHold"] is True and body["onHoldReason"] is None
