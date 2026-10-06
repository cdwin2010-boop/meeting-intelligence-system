"""작업 56: 열람 기록 보강, 업무 수정 이력, 변경 이력 viewImpact, 허용 동작 목록(allowedActions)·availablePhases.
네트워크 없음, 임시 DB·업로드 폴더만 쓴다."""
from datetime import date

import pytest
from sqlalchemy import func, select

from app.auth import actions as act
from app.jobs.daily_mail import _counts as daily_counts
from app.models import ActionItem, Event, Meeting, MeetingView
from app.models.common import utcnow
from app.services import history as history_service
from tests.test_meeting_queries import make_meeting
from tests.test_upload_processing import add_account, auth, env  # noqa: F401  (env 는 픽스처)

DUE = date(2026, 10, 9)


@pytest.fixture
def team(env):
    f = env["factory"]
    return {
        "lead": add_account(f, "고객사A", "lead", "manager", name="박총괄"),  # 등록한 총괄
        "exe": add_account(f, "고객사A", "boss", "executive", name="최임원"),
        "mgr_item": add_account(f, "고객사A", "mgri", "manager", name="정담당관리"),  # 총괄 아님, 본인 담당 업무 있음
        "mgr_other": add_account(f, "고객사A", "mgro", "manager", name="한타관리"),  # 총괄 아님, 담당 업무 없음
        "staff": add_account(f, "고객사A", "kim", "staff", name="김담당"),
        "stranger": add_account(f, "고객사A", "lee", "staff", name="이무관"),
        "outsider": add_account(f, "고객사B", "out", "executive", name="외부"),
    }


def call(env, method, account, path, **kwargs):
    return env["client"].request(method, path, headers=auth(account) if account else {}, **kwargs)


def make(env, team, status="confirmed", title="회의"):
    """총괄 등록, 참석: staff·mgr_item. 업무: 1 확정 대기(staff 담당, 완성) 2 확정(mgr_item 담당) 3 종결(staff 담당)"""
    mid = make_meeting(
        env["factory"], team["lead"], participants=[team["staff"], team["mgr_item"]], transcript="화자1: 안녕", status=status,
        title=title,
        items=[
            {"title": "대기 업무", "assignee_id": team["staff"].id, "due_date": DUE},
            {"title": "확정 업무", "assignee_id": team["mgr_item"].id, "due_date": DUE, "status": "confirmed", "confirm_kind": "manager"},
            {"title": "종결 업무", "assignee_id": team["staff"].id, "due_date": DUE, "status": "closed"},
        ],
    )
    with env["factory"]() as s:
        ids = list(s.scalars(select(ActionItem.id).where(ActionItem.meeting_id == mid).order_by(ActionItem.id)))
    return mid, ids


def detail(env, account, mid) -> dict:
    res = call(env, "GET", account, f"/api/meetings/{mid}")
    assert res.status_code == 200, res.text
    return res.json()


def views(env) -> list[MeetingView]:
    with env["factory"]() as s:
        return s.scalars(select(MeetingView).order_by(MeetingView.account_id)).all()


# ---------------- 열람 기록 ----------------
def test_detail_view_is_recorded_once_per_user_and_updates_last(env, team):
    mid, _ = make(env, team)
    detail(env, team["staff"], mid)
    [first] = views(env)
    assert first.account_id == team["staff"].id and first.first_viewed_at == first.last_viewed_at
    detail(env, team["staff"], mid)
    detail(env, team["staff"], mid)
    [again] = views(env)  # 같은 사용자의 반복 열람은 행을 늘리지 않는다
    assert again.first_viewed_at == first.first_viewed_at and again.last_viewed_at >= first.last_viewed_at
    detail(env, team["lead"], mid)  # 사용자별로 따로 기록
    assert [v.account_id for v in views(env)] == sorted([team["staff"].id, team["lead"].id])


def test_unviewable_and_other_tenant_requests_are_not_recorded(env, team):
    mid, _ = make(env, team)
    assert call(env, "GET", team["stranger"], f"/api/meetings/{mid}").status_code == 404  # 관련 없는 담당자
    assert call(env, "GET", team["outsider"], f"/api/meetings/{mid}").status_code == 404  # 다른 회사
    assert call(env, "GET", None, f"/api/meetings/{mid}").status_code == 401
    assert call(env, "GET", team["staff"], "/api/meetings/999999").status_code == 404
    held, _ = make(env, team, title="보류")
    call(env, "POST", team["lead"], f"/api/meetings/{held}/hold", json={"reason": "검토"})
    assert call(env, "GET", team["staff"], f"/api/meetings/{held}").status_code == 404  # 담당자는 보류 회의록 열람 불가
    assert views(env) == []


def test_view_record_failure_does_not_break_detail(env, team, monkeypatch):
    mid, _ = make(env, team)

    def broken_now():
        raise RuntimeError("clock unavailable")

    monkeypatch.setattr(history_service, "utcnow", broken_now)
    res = call(env, "GET", team["staff"], f"/api/meetings/{mid}")
    assert res.status_code == 200 and res.json()["id"] == mid  # 기록이 실패해도 조회는 정상
    assert views(env) == []
    with env["factory"]() as s:
        assert history_service.record_meeting_view(s, team["staff"], s.get(Meeting, mid)) is False


def test_view_does_not_change_mail_or_auto_confirm_targets(env, team):
    """열람해도 확정 대기·보완 필요 안내(메일 대상)는 그대로이고, 미열람 담당 회의록 수만 열람한 담당자 몫이 줄어든다(기존 규칙)."""
    mid, _ = make(env, team, status="awaiting_confirmation")
    with env["factory"]() as s:
        before = daily_counts(s)
    detail(env, team["staff"], mid)
    detail(env, team["lead"], mid)
    with env["factory"]() as s:
        after = daily_counts(s)
    assert after[0] == before[0] and after[1] == before[1]  # 확정 대기·보완 필요 건수 불변
    assert before[2].get(team["staff"].id) == 1 and team["staff"].id not in after[2]  # 열람한 담당자는 미열람에서 빠진다
    # 자동 확정됨(미열람) 표시 규칙(me.py)은 마지막 열람 시각 기준 그대로: 확정 이후에 열람해야 사라진다
    with env["factory"]() as s:
        assert s.scalar(select(func.count()).select_from(MeetingView)) == 2


# ---------------- 업무 수정 이력 ----------------
def test_item_update_recorded_in_common_history_with_before_after(env, team):
    mid, ids = make(env, team)
    res = call(env, "PATCH", team["lead"], f"/api/action-items/{ids[0]}",
               json={"title": "수정한 업무", "assigneeId": team["mgr_item"].id, "dueUndetermined": True})
    assert res.status_code == 200
    rows = call(env, "GET", team["lead"], f"/api/meetings/{mid}/history").json()
    [row] = [r for r in rows if r["kindCode"] == "item.updated"]
    assert row["kind"] == "업무 수정" and row["targetType"] == "action_item" and row["targetId"] == ids[0]
    assert row["before"] == {"title": "대기 업무", "assigneeId": team["staff"].id, "dueDate": "2026-10-09", "dueUndetermined": False}
    assert row["after"] == {"title": "수정한 업무", "assigneeId": team["mgr_item"].id, "dueDate": None, "dueUndetermined": True}
    assert row["changedBy"]["id"] == team["lead"].id and row["beforeMissing"] is False and row["viewImpact"] is None


def test_only_changed_fields_are_recorded_and_no_change_no_history(env, team):
    mid, ids = make(env, team)
    call(env, "PATCH", team["lead"], f"/api/action-items/{ids[0]}", json={"title": "대기 업무", "dueDate": "2026-12-01"})  # 제목은 그대로
    call(env, "PATCH", team["lead"], f"/api/action-items/{ids[0]}", json={"dueDate": "2026-12-01"})  # 변경 없음
    rows = [r for r in call(env, "GET", team["lead"], f"/api/meetings/{mid}/history").json() if r["kindCode"] == "item.updated"]
    assert len(rows) == 1
    assert rows[0]["before"] == {"dueDate": "2026-10-09"} and rows[0]["after"] == {"dueDate": "2026-12-01"}


def test_old_event_without_before_is_marked_missing(env, team):
    mid, ids = make(env, team)
    with env["factory"]() as s:
        s.add(Event(tenant_id=team["lead"].tenant_id, entity_type="action_item", entity_id=ids[0], event_type="item.updated",
                    actor_account_id=team["lead"].id, payload={"after": {"title": "옛 수정"}}, created_at=utcnow()))
        s.commit()
    [row] = [r for r in call(env, "GET", team["lead"], f"/api/meetings/{mid}/history").json() if r["kindCode"] == "item.updated"]
    assert row["before"] == {} and row["beforeMissing"] is True and row["after"] == {"title": "옛 수정"}


def test_history_includes_view_impact_for_participant_changes(env, team):
    mid, _ = make(env, team)
    with env["factory"]() as s:
        s.add(Event(tenant_id=team["lead"].tenant_id, entity_type="meeting", entity_id=mid, event_type="participants.overridden",
                    actor_account_id=team["lead"].id, created_at=utcnow(),
                    payload={"before": {"participants": ["a"]}, "after": {"participants": []},
                             "viewImpact": [{"accountId": 5, "name": "정빠짐", "loginId": "x", "viewImpact": "열람 불가(참석자에서 빠져 볼 수 없게 됨)"}]}))
        s.commit()
    [row] = [r for r in call(env, "GET", team["lead"], f"/api/meetings/{mid}/history").json() if r["kindCode"] == "participants.overridden"]
    assert row["viewImpact"][0]["viewImpact"].startswith("열람 불가") and row["beforeMissing"] is False
    assert {"id", "targetType", "targetId", "kind", "kindCode", "batchId", "before", "after", "changedBy", "changedAt"} <= set(row)  # 기존 필드 유지


# ---------------- 허용 동작: 회의록 단위 ----------------
BASE = ["request_change", "download_excel", "view_history"]


def ordered(names: list[str]) -> list[str]:
    return [n for n in act.MEETING_ACTIONS if n in names]


MANAGE_ACTIVE = ["edit_minutes", "upload_update", "add_item", "edit_speakers", "hold_meeting", "end_meeting", "delete_meeting"]


def lead_expected(state: str) -> list[str]:
    resolve = ["resolve_change_request"]
    return ordered({
        "awaiting_confirmation": BASE + resolve + MANAGE_ACTIVE + ["confirm_meeting"],
        "confirmed": BASE + resolve + MANAGE_ACTIVE,
        "on_hold": BASE + resolve + ["resume_meeting", "delete_meeting"],
        "ended": BASE + resolve + ["delete_meeting"],
        "deleted": BASE + resolve,
        "processing": BASE + resolve + ["edit_minutes", "upload_update", "add_item", "edit_speakers"],
        "failed": BASE + resolve + ["edit_minutes", "upload_update", "add_item", "edit_speakers", "delete_meeting"],
    }[state])


def put_in_state(env, team, state: str):
    status = state if state in ("awaiting_confirmation", "confirmed", "processing", "failed") else "confirmed"
    mid, ids = make(env, team, status=status, title=f"상태-{state}")
    if state == "on_hold":
        assert call(env, "POST", team["lead"], f"/api/meetings/{mid}/hold", json={"reason": "검토"}).status_code == 200
    if state == "ended":
        assert call(env, "POST", team["lead"], f"/api/meetings/{mid}/end", json={"reason": "취소"}).status_code == 200
    if state == "deleted":
        assert call(env, "POST", team["lead"], f"/api/meetings/{mid}/delete", json={"reason": "삭제"}).status_code == 200
    return mid, ids


STATES = ["awaiting_confirmation", "confirmed", "on_hold", "ended", "deleted", "processing", "failed"]


@pytest.mark.parametrize("state", STATES)
@pytest.mark.parametrize("who", ["lead", "exe"])
def test_meeting_allowed_actions_for_lead_and_executive(env, team, state, who):
    mid, _ = put_in_state(env, team, state)
    assert detail(env, team[who], mid)["allowedActions"] == lead_expected(state)


@pytest.mark.parametrize("state", STATES)
@pytest.mark.parametrize("who", ["mgr_item", "mgr_other"])
def test_meeting_allowed_actions_for_non_lead_managers(env, team, state, who):
    mid, _ = put_in_state(env, team, state)
    assert detail(env, team[who], mid)["allowedActions"] == ordered(BASE + ["resolve_change_request"])  # 총괄 아닌 관리자: 쓰기 동작 없음


@pytest.mark.parametrize("state", ["awaiting_confirmation", "confirmed", "ended", "processing", "failed"])
def test_meeting_allowed_actions_for_staff(env, team, state):
    mid, _ = put_in_state(env, team, state)
    assert detail(env, team["staff"], mid)["allowedActions"] == ordered(BASE)


@pytest.mark.parametrize("state", ["on_hold", "deleted"])
def test_staff_cannot_open_held_or_deleted_meeting(env, team, state):
    mid, _ = put_in_state(env, team, state)
    assert call(env, "GET", team["staff"], f"/api/meetings/{mid}").status_code == 404


# ---------------- 허용 동작: 업무 단위 ----------------
def item_actions(env, account, mid) -> dict[str, list[str]]:
    return {i["title"]: i["allowedActions"] for i in detail(env, account, mid)["actionItems"]}


def test_item_allowed_actions_matrix_in_confirmed_meeting(env, team):
    mid, _ = make(env, team)
    for who in ("lead", "exe"):
        assert item_actions(env, team[who], mid) == {
            "대기 업무": ["confirm_item", "delete_item", "set_assignee", "set_due", "request_change"],
            "확정 업무": ["close_item", "delete_item", "set_assignee", "set_due", "request_change"],
            "종결 업무": ["delete_item", "request_change"],
        }
    assert item_actions(env, team["mgr_item"], mid) == {  # 본인 담당 업무만 수정·확정·종결, 삭제는 총괄·지시자만
        "대기 업무": ["request_change"],
        "확정 업무": ["close_item", "set_assignee", "set_due", "request_change"],
        "종결 업무": ["request_change"],
    }
    assert item_actions(env, team["mgr_other"], mid) == {t: ["request_change"] for t in ("대기 업무", "확정 업무", "종결 업무")}
    assert item_actions(env, team["staff"], mid) == {t: ["request_change"] for t in ("대기 업무", "확정 업무", "종결 업무")}


@pytest.mark.parametrize("state", ["on_hold", "ended", "deleted"])
def test_item_write_actions_removed_in_locked_meetings(env, team, state):
    mid, _ = put_in_state(env, team, state)
    for who in ("lead", "exe", "mgr_item"):
        assert set(map(tuple, item_actions(env, team[who], mid).values())) == {("request_change",)}, who


def test_deleted_items_are_not_listed(env, team):
    mid, ids = make(env, team)
    call(env, "POST", team["lead"], f"/api/action-items/{ids[0]}/delete", json={"reason": "중복"})
    assert "대기 업무" not in item_actions(env, team["lead"], mid)


def test_other_responses_have_null_item_allowed_actions(env, team):
    _, ids = make(env, team)
    res = call(env, "PATCH", team["lead"], f"/api/action-items/{ids[0]}", json={"title": "x"})
    assert res.json()["allowedActions"] is None  # 상세 응답에서만 채운다


# ---------------- 허용 동작 ↔ 실제 API 일치 ----------------
def test_listed_meeting_actions_succeed_and_unlisted_are_rejected(env, team):
    lead = team["lead"]
    mid, ids = make(env, team, status="awaiting_confirmation", title="일치-확정대기")
    allowed = detail(env, lead, mid)["allowedActions"]
    assert {"edit_minutes", "upload_update", "add_item", "edit_speakers", "confirm_meeting", "resolve_change_request"} <= set(allowed)
    export = call(env, "GET", lead, f"/api/meetings/{mid}/export")
    assert export.status_code == 200 and "download_excel" in allowed
    assert call(env, "GET", lead, f"/api/meetings/{mid}/history").status_code == 200 and "view_history" in allowed
    assert call(env, "PATCH", lead, f"/api/meetings/{mid}/minutes", json={"purpose": "p"}).status_code == 200
    assert call(env, "POST", lead, f"/api/meetings/{mid}/action-items", json={"title": "새 업무"}).status_code == 201
    assert call(env, "PUT", lead, f"/api/meetings/{mid}/speakers", json={"speakers": []}).status_code == 200
    up = call(env, "POST", lead, f"/api/meetings/{mid}/update-upload/preview", files={"file": ("a.xlsx", export.content, "application/octet-stream")})
    assert up.status_code == 200
    created = call(env, "POST", lead, f"/api/meetings/{mid}/change-requests", json={"comment": "고쳐 주세요"})
    assert created.status_code == 201 and "request_change" in allowed
    assert call(env, "POST", lead, f"/api/meetings/{mid}/change-requests/{created.json()['requestId']}/resolve",
                json={"decision": "accepted"}).status_code == 200
    assert call(env, "POST", lead, f"/api/meetings/{mid}/confirm").status_code == 200
    assert "confirm_meeting" not in detail(env, lead, mid)["allowedActions"]  # 확정 뒤에는 목록에서 빠진다
    # 보류 → 재개 → 종료 → 삭제 (각 단계의 허용 목록과 API 가 일치)
    assert call(env, "POST", lead, f"/api/meetings/{mid}/hold", json={"reason": "r"}).status_code == 200
    assert "resume_meeting" in detail(env, lead, mid)["allowedActions"]
    assert call(env, "PATCH", lead, f"/api/meetings/{mid}/minutes", json={"purpose": "q"}).status_code == 409  # 보류 중에는 목록에 없다
    assert call(env, "POST", lead, f"/api/meetings/{mid}/resume").status_code == 200
    assert "end_meeting" in detail(env, lead, mid)["allowedActions"]
    assert call(env, "POST", lead, f"/api/meetings/{mid}/end", json={"reason": "r"}).status_code == 200
    ended = detail(env, lead, mid)["allowedActions"]
    assert ended == ordered(BASE + ["resolve_change_request", "delete_meeting"])
    for method, path, body in (
        ("PATCH", "minutes", {"purpose": "z"}), ("POST", "action-items", {"title": "t"}), ("PUT", "speakers", {"speakers": []}),
        ("POST", "hold", {"reason": "r"}),  # (이미 종료된 회의록의 직권 종료는 멱등 200 이라 거부 목록에서 뺀다)
    ):
        assert call(env, method, lead, f"/api/meetings/{mid}/{path}", json=body).status_code == 409, path
    assert call(env, "POST", lead, f"/api/meetings/{mid}/delete", json={"reason": "r"}).status_code == 200
    assert detail(env, lead, mid)["allowedActions"] == ordered(BASE + ["resolve_change_request"])


def test_unlisted_actions_are_rejected_for_other_roles(env, team):
    mid, ids = make(env, team, status="awaiting_confirmation", title="일치-타역할")
    for who in ("mgr_item", "mgr_other", "staff"):
        allowed = detail(env, team[who], mid)["allowedActions"]
        for name, method, path, body in (
            ("confirm_meeting", "POST", "confirm", None), ("hold_meeting", "POST", "hold", {"reason": "r"}),
            ("end_meeting", "POST", "end", {"reason": "r"}), ("delete_meeting", "POST", "delete", {"reason": "r"}),
            ("edit_minutes", "PATCH", "minutes", {"purpose": "x"}), ("add_item", "POST", "action-items", {"title": "t"}),
            ("edit_speakers", "PUT", "speakers", {"speakers": []}),
        ):
            assert name not in allowed
            res = call(env, method, team[who], f"/api/meetings/{mid}/{path}", **({"json": body} if body is not None else {}))
            assert res.status_code == 403, (who, name, res.status_code)
    created = call(env, "POST", team["lead"], f"/api/meetings/{mid}/change-requests", json={"comment": "요청"}).json()["requestId"]
    assert "resolve_change_request" not in detail(env, team["staff"], mid)["allowedActions"]
    assert call(env, "POST", team["staff"], f"/api/meetings/{mid}/change-requests/{created}/resolve", json={"decision": "accepted"}).status_code == 403
    assert "resolve_change_request" in detail(env, team["mgr_other"], mid)["allowedActions"]
    assert call(env, "POST", team["mgr_other"], f"/api/meetings/{mid}/change-requests/{created}/resolve", json={"decision": "rejected"}).status_code == 200


def test_listed_item_actions_succeed_and_unlisted_are_rejected(env, team):
    lead = team["lead"]
    mid, ids = make(env, team)
    pending, confirmed, closed = ids
    actions = item_actions(env, lead, mid)
    assert call(env, "PATCH", lead, f"/api/action-items/{pending}", json={"assigneeId": team["staff"].id}).status_code == 200
    assert call(env, "PATCH", lead, f"/api/action-items/{pending}", json={"dueDate": "2026-12-24"}).status_code == 200  # set_due
    assert "set_assignee" in actions["대기 업무"] and "set_due" in actions["대기 업무"]
    assert call(env, "POST", lead, f"/api/action-items/{pending}/confirm").status_code == 200 and "confirm_item" in actions["대기 업무"]
    assert call(env, "POST", lead, f"/api/action-items/{confirmed}/close", json={"reason": "끝"}).status_code == 200 and "close_item" in actions["확정 업무"]
    # 목록에 없는 동작은 서버도 거부: 종결된 업무 수정(409)·확정 대기 업무 종결(409)
    assert "set_due" not in actions["종결 업무"] and call(env, "PATCH", lead, f"/api/action-items/{closed}", json={"dueDate": "2026-12-24"}).status_code == 409
    mid2, ids2 = make(env, team, title="일치-업무2")
    assert "close_item" not in item_actions(env, lead, mid2)["대기 업무"]
    assert call(env, "POST", lead, f"/api/action-items/{ids2[0]}/close", json={"reason": "r"}).status_code == 409
    # 타 관리자·담당자: 목록에 없고 서버가 403
    for who in ("mgr_other", "staff"):
        assert call(env, "POST", team[who], f"/api/action-items/{ids2[0]}/confirm").status_code == 403
        assert call(env, "PATCH", team[who], f"/api/action-items/{ids2[0]}", json={"title": "x"}).status_code == 403
    assert call(env, "POST", team["mgr_item"], f"/api/action-items/{ids2[1]}/close", json={"reason": "내 업무"}).status_code == 200  # 본인 담당
    assert call(env, "POST", team["mgr_item"], f"/api/action-items/{ids2[0]}/delete", json={"reason": "r"}).status_code == 403  # 삭제는 총괄·지시자만
    assert "delete_item" in item_actions(env, lead, mid2)["대기 업무"]
    assert call(env, "POST", lead, f"/api/action-items/{ids2[0]}/delete", json={"reason": "삭제"}).status_code == 200


# ---------------- availablePhases ----------------
def test_available_phases_in_list_response(env, team):
    make(env, team)
    for who in ("lead", "exe", "mgr_other"):
        body = call(env, "GET", team[who], "/api/meetings").json()
        assert body["availablePhases"] == ["active", "ended", "on_hold", "deleted"], who
        assert {"items", "total", "page", "size"} <= set(body)
    body = call(env, "GET", team["staff"], "/api/meetings").json()
    assert body["availablePhases"] == ["active", "ended"]
    for phase in ("on_hold", "deleted"):
        assert call(env, "GET", team["staff"], "/api/meetings", params={"phase": phase}).status_code == 403  # 같은 판정
        assert call(env, "GET", team["lead"], "/api/meetings", params={"phase": phase}).status_code == 200
    assert act.phase_allowed(team["staff"], "ended") and not act.phase_allowed(team["staff"], "deleted")


def test_action_names_are_unique_constants():
    names = list(act.MEETING_ACTIONS) + [n for n in act.ITEM_ACTIONS if n not in act.MEETING_ACTIONS]
    assert len(names) == len(set(names)) and all(n == n.lower() and " " not in n for n in names)
