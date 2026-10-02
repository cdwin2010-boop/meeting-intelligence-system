"""화자 매핑(GET·PUT /api/meetings/{id}/speakers)과 담당자 자동 채움, 전사문 표시 반영.
네트워크 없음. env 픽스처와 도우미는 기존 테스트 모듈의 것을 쓴다(임시 DB만 사용)."""
import pytest
from sqlalchemy import select

from app.models import ActionItem, Event, MeetingSpeaker, append_event
from tests.test_meeting_queries import make_meeting
from tests.test_upload_processing import add_account, auth, env  # noqa: F401  (env 는 픽스처)

TRANSCRIPT = "\n".join([
    "[00:00:03] 화자1: 이번 주까지 정리해 주세요.",
    "[00:00:10] 화자2: 네, 제가 하겠습니다.",
    "[00:00:20] 화자3: 화자2 님 말씀대로 진행하죠.",  # 말 내용 속 '화자2' 는 바꾸지 않아야 한다
])


def call(env, method: str, account, path: str, **kwargs):
    return env["client"].request(method, path, headers=auth(account), **kwargs)


def put(env, account, meeting_id: int, speakers: list[dict]):
    return call(env, "PUT", account, f"/api/meetings/{meeting_id}/speakers", json={"speakers": speakers})


@pytest.fixture
def team(env):
    f = env["factory"]
    return {
        "mgr": add_account(f, "고객사A", "mgr", "manager", name="박관리"),
        "exe": add_account(f, "고객사A", "boss", "executive", name="최임원"),
        "staff": add_account(f, "고객사A", "kim", "staff", name="김담당"),
        "lee": add_account(f, "고객사A", "lee", "staff", name="이서연"),
        "outsider": add_account(f, "고객사B", "out", "executive", name="외부"),
    }


def meeting_with_items(env, registrant, extracted: list[str], **kwargs) -> tuple[int, list[int]]:
    """전사문과 업무(담당자 비어 있음)를 만들고, 처리 단계처럼 item.created 사건에 추출 담당자 글자를 남긴다."""
    f = env["factory"]
    meeting_id = make_meeting(f, registrant, transcript=TRANSCRIPT, items=[{"title": f"업무{i}"} for i in range(len(extracted))], **kwargs)
    with f() as s:
        ids = list(s.scalars(select(ActionItem.id).where(ActionItem.meeting_id == meeting_id).order_by(ActionItem.id)))
        for item_id, name in zip(ids, extracted):
            append_event(s, tenant_id=registrant.tenant_id, entity_type="action_item", entity_id=item_id,
                         event_type="item.created", payload={"assignee_name": name})
        s.commit()
    return meeting_id, ids


def assignees(env, item_ids: list[int]) -> list[int | None]:
    with env["factory"]() as s:
        return [s.get(ActionItem, i).assignee_id for i in item_ids]


# ---------------- 저장·조회 ----------------
def test_save_and_get_mapping(env, team):
    meeting_id, _ = meeting_with_items(env, team["mgr"], [])
    res = put(env, team["mgr"], meeting_id, [
        {"label": "화자1", "accountId": team["exe"].id},
        {"label": "화자3", "name": "  협력사 홍길동 "},
    ])
    assert res.status_code == 200
    body = res.json()
    assert body["labels"] == ["화자1", "화자2", "화자3"]
    assert body["speakers"] == [
        {"label": "화자1", "accountId": team["exe"].id, "accountName": "최임원", "name": None,
         "displayName": "최임원", "unregistered": False},
        {"label": "화자3", "accountId": None, "accountName": None, "name": "협력사 홍길동",
         "displayName": "협력사 홍길동(미등록)", "unregistered": True},
    ]
    got = call(env, "GET", team["mgr"], f"/api/meetings/{meeting_id}/speakers").json()
    assert got["speakers"] == body["speakers"] and got["autoAssignedItemIds"] == []

    # 전체 교체: 보낸 것만 남는다
    assert put(env, team["mgr"], meeting_id, [{"label": "화자2", "accountId": team["lee"].id}]).status_code == 200
    labels = [s["label"] for s in call(env, "GET", team["mgr"], f"/api/meetings/{meeting_id}/speakers").json()["speakers"]]
    assert labels == ["화자2"]


def test_transcript_reflects_mapping_without_touching_original(env, team):
    meeting_id, _ = meeting_with_items(env, team["mgr"], [])
    put(env, team["mgr"], meeting_id, [
        {"label": "화자2", "accountId": team["lee"].id},
        {"label": "화자3", "name": "홍길동"},
    ])
    body = call(env, "GET", team["mgr"], f"/api/meetings/{meeting_id}/transcript").json()
    assert body["fullText"] == TRANSCRIPT  # 원문 그대로
    assert body["displayText"].splitlines() == [
        "[00:00:03] 화자1: 이번 주까지 정리해 주세요.",
        "[00:00:10] 이서연: 네, 제가 하겠습니다.",
        "[00:00:20] 홍길동(미등록): 화자2 님 말씀대로 진행하죠.",
    ]
    assert [(s["label"], s["displayName"]) for s in body["speakers"]] == [("화자2", "이서연"), ("화자3", "홍길동(미등록)")]


# ---------------- 담당자 자동 채움 ----------------
def test_account_mapping_fills_matching_empty_assignees(env, team):
    # 추출 담당자 글자: 화자 표기 / 계정 이름 / 매핑 안 된 표기 / 빈 값
    meeting_id, ids = meeting_with_items(env, team["mgr"], ["화자2", "최임원", "화자1", ""])
    res = put(env, team["mgr"], meeting_id, [
        {"label": "화자2", "accountId": team["lee"].id},
        {"label": "화자3", "accountId": team["exe"].id},
    ])
    assert res.status_code == 200
    assert res.json()["autoAssignedItemIds"] == [ids[0], ids[1]]
    assert assignees(env, ids) == [team["lee"].id, team["exe"].id, None, None]
    with env["factory"]() as s:
        updates = s.scalars(select(Event).where(Event.event_type == "item.updated").order_by(Event.id)).all()
        assert [(e.entity_id, e.payload["after"]["assigneeId"], e.payload["via"]) for e in updates] == [
            (ids[0], team["lee"].id, "speaker_mapping"), (ids[1], team["exe"].id, "speaker_mapping")]
        assert all(e.actor_account_id == team["mgr"].id for e in updates)


def test_auto_fill_never_overwrites_or_touches_confirmed(env, team):
    meeting_id, ids = meeting_with_items(env, team["mgr"], ["화자2", "화자2"])
    with env["factory"]() as s:
        first, second = s.get(ActionItem, ids[0]), s.get(ActionItem, ids[1])
        first.assignee_id = team["staff"].id  # 이미 담당자 있음
        second.status = "confirmed"
        s.commit()
    res = put(env, team["mgr"], meeting_id, [{"label": "화자2", "accountId": team["lee"].id}])
    assert res.json()["autoAssignedItemIds"] == []
    assert assignees(env, ids) == [team["staff"].id, None]


def test_unregistered_name_is_text_only(env, team):
    # 추출 담당자 글자가 미등록 이름이나 그 표기와 같아도 담당자로 쓰지 않는다
    meeting_id, ids = meeting_with_items(env, team["mgr"], ["화자3", "홍길동"])
    res = put(env, team["mgr"], meeting_id, [{"label": "화자3", "name": "홍길동"}])
    assert res.status_code == 200 and res.json()["autoAssignedItemIds"] == []
    assert assignees(env, ids) == [None, None]
    with env["factory"]() as s:
        row = s.scalar(select(MeetingSpeaker).where(MeetingSpeaker.meeting_id == meeting_id))
        assert row.account_id is None and row.display_name == "홍길동"


def test_ambiguous_name_is_not_filled(env, team):
    # 두 화자가 서로 다른 계정인데 추출 글자가 어느 쪽과도 같으면(계정 이름 중복 등) 채우지 않는다
    dup = add_account(env["factory"], "고객사A", "lee2", "staff", name="이서연")
    meeting_id, ids = meeting_with_items(env, team["mgr"], ["이서연"])
    put(env, team["mgr"], meeting_id, [
        {"label": "화자1", "accountId": team["lee"].id},
        {"label": "화자2", "accountId": dup.id},
    ])
    assert assignees(env, ids) == [None]


# ---------------- 입력 검사 ----------------
@pytest.mark.parametrize("who", ["outsider", "missing"])
def test_other_tenant_or_missing_account_rejected(env, team, who):
    meeting_id, ids = meeting_with_items(env, team["mgr"], ["화자1"])
    account_id = team["outsider"].id if who == "outsider" else 99999
    res = put(env, team["mgr"], meeting_id, [{"label": "화자1", "accountId": account_id}])
    assert res.status_code == 400 and res.json() == {"detail": "같은 고객사의 활성 계정만 지정할 수 있습니다"}
    with env["factory"]() as s:
        assert s.scalars(select(MeetingSpeaker)).all() == []
    assert assignees(env, ids) == [None]


def test_inactive_account_rejected(env, team):
    with env["factory"]() as s:
        s.get(type(team["lee"]), team["lee"].id).is_active = False
        s.commit()
    meeting_id, _ = meeting_with_items(env, team["mgr"], [])
    assert put(env, team["mgr"], meeting_id, [{"label": "화자1", "accountId": team["lee"].id}]).status_code == 400


@pytest.mark.parametrize("entry", [
    {"label": "화자9", "name": "홍길동"},                       # 전사문에 없는 표기
    {"label": "화자1"},                                         # 계정·이름 둘 다 없음
    {"label": "화자1", "name": "   "},                          # 공백 이름
    {"label": "화자1", "accountId": 1, "name": "홍길동"},        # 둘 다 지정
    {"label": "화자1", "name": "가" * 51},                      # 너무 긴 이름
])
def test_invalid_entries_rejected_and_nothing_saved(env, team, entry):
    meeting_id, _ = meeting_with_items(env, team["mgr"], [])
    res = put(env, team["mgr"], meeting_id, [{"label": "화자2", "name": "먼저 온 정상 값"}, entry])
    assert res.status_code == 400
    with env["factory"]() as s:
        assert s.scalars(select(MeetingSpeaker)).all() == []


def test_duplicate_label_rejected(env, team):
    meeting_id, _ = meeting_with_items(env, team["mgr"], [])
    res = put(env, team["mgr"], meeting_id, [{"label": "화자1", "name": "가"}, {"label": "화자1", "name": "나"}])
    assert res.status_code == 400


def test_meeting_without_transcript_is_404(env, team):
    meeting_id = make_meeting(env["factory"], team["mgr"])
    assert put(env, team["mgr"], meeting_id, []).status_code == 404


# ---------------- 권한 ----------------
def test_requires_login(env, team):
    meeting_id, _ = meeting_with_items(env, team["mgr"], [])
    assert env["client"].get(f"/api/meetings/{meeting_id}/speakers").status_code == 401
    assert env["client"].put(f"/api/meetings/{meeting_id}/speakers", json={"speakers": []}).status_code == 401


def test_staff_registrant_allowed_but_other_staff_forbidden(env, team):
    # 담당자 직급 등록자는 가능, 같은 회의 참석자(담당자 직급)는 볼 수는 있어도 403
    meeting_id, _ = meeting_with_items(env, team["staff"], [], participants=[team["lee"]])
    assert put(env, team["staff"], meeting_id, [{"label": "화자1", "name": "홍길동"}]).status_code == 200
    assert call(env, "GET", team["lee"], f"/api/meetings/{meeting_id}/speakers").status_code == 403
    assert put(env, team["lee"], meeting_id, []).status_code == 403
    # 참석자는 전사문(매핑 반영)은 그대로 볼 수 있다
    assert call(env, "GET", team["lee"], f"/api/meetings/{meeting_id}/transcript").status_code == 200


def test_other_tenant_and_unrelated_staff_get_404(env, team):
    meeting_id, _ = meeting_with_items(env, team["mgr"], [])
    assert call(env, "GET", team["outsider"], f"/api/meetings/{meeting_id}/speakers").status_code == 404
    assert put(env, team["outsider"], meeting_id, []).status_code == 404
    assert put(env, team["lee"], meeting_id, []).status_code == 404  # 관련 없는 담당자 직급은 회의록 자체를 못 봄


def test_lower_rank_cannot_overwrite_higher_rank_mapping(env, team):
    meeting_id, _ = meeting_with_items(env, team["staff"], [])
    assert put(env, team["exe"], meeting_id, [{"label": "화자1", "accountId": team["exe"].id}]).status_code == 200
    res = put(env, team["mgr"], meeting_id, [{"label": "화자1", "name": "다른 사람"}])
    assert res.status_code == 409
    assert put(env, team["staff"], meeting_id, []).status_code == 409  # 등록자(담당자 직급)도 마찬가지
    assert put(env, team["exe"], meeting_id, [{"label": "화자1", "name": "정정"}]).status_code == 200  # 같은 직급은 덮어씀
    # 관리자가 정한 매핑은 지시자가 덮을 수 있다
    meeting2, _ = meeting_with_items(env, team["mgr"], [], day=1)
    assert put(env, team["mgr"], meeting2, [{"label": "화자1", "name": "가"}]).status_code == 200
    assert put(env, team["exe"], meeting2, [{"label": "화자1", "name": "나"}]).status_code == 200
