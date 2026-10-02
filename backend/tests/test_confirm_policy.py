"""㉟ 확정 권한 정책: 회의록 확정은 총괄 관리자(등록자, 담당자 등록이면 참석 관리자)와 지시자만,
총괄이 아닌 관리자는 본인이 담당자인 업무만 수정·확정, 담당자 등록은 확정 대기. 네트워크 없음(임시 DB)."""
from datetime import date

import pytest

from app.models import ActionItem, Meeting
from app.services.processing import process_meeting
from tests.test_meeting_queries import make_meeting
from tests.test_upload_processing import add_account, auth, env, upload  # noqa: F401  (env 는 픽스처)

FORBIDDEN_MEETING = "회의록을 총괄하는 관리자 또는 지시자만 확정할 수 있습니다"
FORBIDDEN_ITEM = "이 업무를 수정·확정할 권한이 없습니다"


def call(env, method: str, account, path: str, **kwargs):
    return env["client"].request(method, path, headers=auth(account), **kwargs)


def confirm_meeting(env, account, meeting_id: int, **params):
    return call(env, "POST", account, f"/api/meetings/{meeting_id}/confirm", params=params)


def confirm_item(env, account, item_id: int):
    return call(env, "POST", account, f"/api/action-items/{item_id}/confirm")


def patch_item(env, account, item_id: int, body: dict):
    return call(env, "PATCH", account, f"/api/action-items/{item_id}", json=body)


def item(env, item_id: int) -> ActionItem:
    with env["factory"]() as s:
        return s.get(ActionItem, item_id)


def meeting_status(env, meeting_id: int) -> str:
    with env["factory"]() as s:
        return s.get(Meeting, meeting_id).status


def items_of(env, meeting_id: int) -> list[int]:
    with env["factory"]() as s:
        return [i.id for i in s.query(ActionItem).filter(ActionItem.meeting_id == meeting_id).order_by(ActionItem.id)]


@pytest.fixture
def team(env):
    f = env["factory"]
    return {
        "lead": add_account(f, "고객사A", "lead", "manager", name="한총괄"),
        "other": add_account(f, "고객사A", "other", "manager", name="박관리"),
        "exe": add_account(f, "고객사A", "boss", "executive", name="최임원"),
        "staff": add_account(f, "고객사A", "kim", "staff", name="김담당"),
    }


@pytest.fixture
def lead_meeting(env, team):
    """관리자(한총괄)가 등록한 확정 대기 회의록: 업무 3건(박관리 담당 / 김담당 담당 / 담당자 없음)."""
    meeting_id = make_meeting(env["factory"], team["lead"], items=[
        {"title": "박관리 업무", "assignee_id": team["other"].id, "due_undetermined": True},
        {"title": "김담당 업무", "assignee_id": team["staff"].id, "due_date": date(2026, 10, 9)},
        {"title": "빈 담당", "assignee_id": None, "due_undetermined": True},
    ])
    return meeting_id, items_of(env, meeting_id)


# ---------------- 회의록 전체 확정 ----------------
def test_registrant_manager_confirms_meeting_and_all_items(env, team, lead_meeting):
    meeting_id, (others_item, staff_item, blank_item) = lead_meeting
    res = confirm_meeting(env, team["lead"], meeting_id, withItems="true")
    assert res.status_code == 200
    assert res.json()["status"] == "confirmed" and res.json()["confirmedBy"]["id"] == team["lead"].id
    # 총괄은 남의 업무도 확정(보완 필요는 건너뜀)
    assert res.json()["confirmedItemIds"] == [others_item, staff_item] and res.json()["skippedItemIds"] == [blank_item]


def test_non_registrant_manager_cannot_confirm_meeting(env, team, lead_meeting):
    meeting_id, (others_item, _, _) = lead_meeting
    res = confirm_meeting(env, team["other"], meeting_id)
    assert res.status_code == 403 and res.json() == {"detail": FORBIDDEN_MEETING}
    assert meeting_status(env, meeting_id) == "awaiting_confirmation"
    # withItems 로 본인 업무만 끼워 확정하는 것도 불가(회의록 확정 자체가 거부)
    assert confirm_meeting(env, team["other"], meeting_id, withItems="true").status_code == 403
    assert item(env, others_item).status == "pending"


def test_executive_confirms_any_meeting_and_item(env, team, lead_meeting):
    meeting_id, (others_item, staff_item, _) = lead_meeting
    assert confirm_item(env, team["exe"], staff_item).status_code == 200
    assert patch_item(env, team["exe"], others_item, {"title": "임원 정정"}).status_code == 200
    assert confirm_meeting(env, team["exe"], meeting_id).status_code == 200
    assert meeting_status(env, meeting_id) == "confirmed"


# ---------------- 업무 수정·확정 ----------------
def test_non_registrant_manager_can_confirm_and_edit_own_item(env, team, lead_meeting):
    _, (own_item, _, _) = lead_meeting
    assert patch_item(env, team["other"], own_item, {"title": "내 업무 고침"}).status_code == 200
    res = confirm_item(env, team["other"], own_item)
    assert res.status_code == 200 and res.json()["status"] == "confirmed"
    assert item(env, own_item).confirmed_by == team["other"].id


def test_non_registrant_manager_cannot_touch_others_items(env, team, lead_meeting):
    _, (_, staff_item, blank_item) = lead_meeting
    for target in (staff_item, blank_item):  # 남의 업무, 담당자 없는 업무
        res = confirm_item(env, team["other"], target)
        assert res.status_code == 403 and res.json() == {"detail": FORBIDDEN_ITEM}
        assert patch_item(env, team["other"], target, {"assigneeId": team["other"].id}).status_code == 403
    assert item(env, staff_item).status == "pending" and item(env, blank_item).assignee_id is None


def test_registrant_manager_edits_every_item(env, team, lead_meeting):
    _, (others_item, staff_item, blank_item) = lead_meeting
    assert patch_item(env, team["lead"], blank_item, {"assigneeId": team["staff"].id}).status_code == 200
    assert patch_item(env, team["lead"], others_item, {"title": "총괄 수정"}).status_code == 200
    assert confirm_item(env, team["lead"], staff_item).status_code == 200


def test_staff_still_cannot_write(env, team, lead_meeting):
    meeting_id, (_, staff_item, _) = lead_meeting
    # 담당자 직급은 본인 담당 업무라도 수정·확정 불가(기존 규칙 유지)
    assert confirm_item(env, team["staff"], staff_item).status_code == 403
    assert patch_item(env, team["staff"], staff_item, {"title": "x"}).status_code == 403


# ---------------- 담당자 등록 회의록 ----------------
def test_staff_registration_awaits_and_participant_manager_is_lead(env, team):
    participants = f"{team['staff'].id},{team['lead'].id}"
    body = upload(env, team["staff"], participants=participants).json()
    process_meeting(body["jobId"], session_factory=env["factory"])
    meeting_id = body["meetingId"]
    assert meeting_status(env, meeting_id) == "awaiting_confirmation"  # 담당자 등록 → 확정 대기

    assert confirm_meeting(env, team["staff"], meeting_id).status_code == 403  # 등록자라도 담당자 직급은 불가
    assert confirm_meeting(env, team["other"], meeting_id).status_code == 403  # 참석하지 않은 관리자
    assert confirm_meeting(env, team["lead"], meeting_id).status_code == 200  # 참석한 관리자 = 총괄
    assert meeting_status(env, meeting_id) == "confirmed"


def test_staff_registered_items_follow_participant_lead(env, team):
    meeting_id = make_meeting(env["factory"], team["staff"], participants=[team["lead"]], items=[
        {"title": "빈 담당", "assignee_id": None, "due_undetermined": True},
    ])
    [blank_item] = items_of(env, meeting_id)
    assert patch_item(env, team["other"], blank_item, {"assigneeId": team["other"].id}).status_code == 403
    assert patch_item(env, team["lead"], blank_item, {"assigneeId": team["staff"].id}).status_code == 200
    assert confirm_item(env, team["lead"], blank_item).status_code == 200
    # 담당자 등록 회의록도 지시자는 확정 가능
    assert confirm_meeting(env, team["exe"], meeting_id).status_code == 200


def test_participant_manager_is_not_lead_when_registrant_is_manager_or_executive(env, team):
    for registrant in (team["lead"], team["exe"]):
        meeting_id = make_meeting(env["factory"], registrant, participants=[team["other"]])
        assert confirm_meeting(env, team["other"], meeting_id).status_code == 403
