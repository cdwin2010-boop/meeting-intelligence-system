"""작업 B-1c-1: 회의록 목록 누락(과거 날짜 등록) 원인 재현·정렬 옵션·검색, 근거 위치 재계산, 내부 작업 수 확인.
네트워크·실데이터 없음(임시 DB 픽스처)."""
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.config import settings
from app.main import app
from app.models import ActionItem, Event, Job, Transcript
from app.services.history import KIND_EVIDENCE_RECOMPUTE
from tests.test_meeting_queries import get, list_ids, make_meeting
from tests.test_upload_processing import add_account, auth, env, upload  # noqa: F401  (env 는 픽스처)


@pytest.fixture
def team(env):
    return {
        "mgr": add_account(env["factory"], "고객사A", "mgr", "manager", name="박관리"),
        "stf": add_account(env["factory"], "고객사A", "stf", "staff", name="김담당"),
        "exe": add_account(env["factory"], "고객사A", "exe", "executive", name="최지시"),
    }


# ---------------- 작업 A: 과거 날짜 등록 회의록 ----------------
def test_past_dated_meeting_is_listed_but_buried_by_held_at_sort(env, team):
    """원인 재현: 필터는 없고 회의 일시 최신순 + 20건 쪽 나눔이라 오래된 날짜로 등록하면 뒤쪽 쪽으로 밀린다."""
    for day in range(25):
        make_meeting(env["factory"], team["mgr"], day=day + 30)
    res = upload(env, team["stf"], held_at="2025-01-05T09:00:00+09:00", title="과거 회의")
    mid = res.json()["meetingId"]
    assert mid not in list_ids(env, team["mgr"])  # 기본 정렬 첫 쪽에는 없다(기존 기준 유지)
    assert mid in [m["id"] for m in get(env, team["mgr"], "/api/meetings", page=2).json()["items"]]  # 누락이 아니라 둘째 쪽


def test_registered_sort_puts_recent_registration_first(env, team):
    for day in range(25):
        make_meeting(env["factory"], team["mgr"], day=day + 30)
    mid = upload(env, team["stf"], held_at="2025-01-05T09:00:00+09:00", title="과거 회의").json()["meetingId"]
    assert list_ids(env, team["mgr"], sort="registered")[0] == mid
    assert mid in list_ids(env, team["stf"], sort="registered")  # 등록자 본인에게도
    default = get(env, team["mgr"], "/api/meetings").json()["items"]  # 기본 정렬은 그대로(회의 일시 최신순)
    assert [m["heldAt"] for m in default] == sorted([m["heldAt"] for m in default], reverse=True)
    assert get(env, team["mgr"], "/api/meetings", sort="bogus").status_code == 422


def test_total_counts_include_past_dated_meeting(env, team):
    for day in range(21):
        make_meeting(env["factory"], team["mgr"], day=day + 30)
    upload(env, team["stf"], held_at="2025-01-05T09:00:00+09:00", title="과거 회의")
    assert get(env, team["mgr"], "/api/meetings").json()["total"] == 22


# ---------------- 작업 B: 검색 ----------------
def make_titled(env, registrant, title, day=0):
    return make_meeting(env["factory"], registrant, day=day, title=title)


def test_search_by_title_registrant_and_date(env, team):
    a = make_titled(env, team["mgr"], "예산 검토 회의", day=0)  # 2026-09-01 01:00 UTC = 10:00 KST
    b = make_titled(env, team["stf"], "일정 점검", day=40)  # 2026-10-11
    assert list_ids(env, team["mgr"], q="예산") == [a]
    assert list_ids(env, team["mgr"], q="점검") == [b]
    assert list_ids(env, team["mgr"], q="김담당") == [b]  # 등록자 이름
    assert list_ids(env, team["mgr"], q="박관리") == [a]
    assert list_ids(env, team["mgr"], q="2026-09-01") == [a]
    assert list_ids(env, team["mgr"], q="2026.09.01") == [a]
    assert list_ids(env, team["mgr"], q="20260901") == [a]
    assert list_ids(env, team["mgr"], q="2026-10") == [b]
    assert sorted(list_ids(env, team["mgr"], q="2026")) == sorted([a, b])
    assert list_ids(env, team["mgr"], q="없는검색어") == []


def test_search_is_case_insensitive_and_escapes_wildcards(env, team):
    plain = make_titled(env, team["mgr"], "Weekly Sync")
    pct = make_titled(env, team["mgr"], "달성률 100% 점검", day=1)
    under = make_titled(env, team["mgr"], "a_b 회의", day=2)
    make_titled(env, team["mgr"], "axb 회의", day=3)
    assert list_ids(env, team["mgr"], q="weekly") == [plain]
    assert list_ids(env, team["mgr"], q="100%") == [pct]
    assert list_ids(env, team["mgr"], q="%") == [pct]  # % 는 와일드카드가 아니라 글자
    assert list_ids(env, team["mgr"], q="a_b") == [under]  # _ 도 글자
    assert list_ids(env, team["mgr"], q="x'; DROP TABLE meetings;--") == []
    assert get(env, team["mgr"], "/api/meetings").json()["total"] == 4  # 표가 그대로


def test_search_query_length_is_capped_and_blank_is_ignored(env, team):
    make_titled(env, team["mgr"], "회의")
    assert get(env, team["mgr"], "/api/meetings", q="가" * 101).status_code == 422
    assert get(env, team["mgr"], "/api/meetings", q="가" * 100).status_code == 200
    assert len(list_ids(env, team["mgr"], q="   ")) == 1


def test_search_keeps_permission_and_phase_rules(env, team):
    other_tenant = add_account(env["factory"], "고객사B", "mgrb", "manager", name="타사관리")
    mine = make_titled(env, team["stf"], "공개 회의")
    hidden = make_titled(env, team["mgr"], "공개 비밀 회의", day=1)  # 담당자 stf 는 참석·담당·등록자가 아님
    foreign = make_meeting(env["factory"], other_tenant, title="공개 타사")
    assert list_ids(env, team["stf"], q="공개") == [mine]
    assert sorted(list_ids(env, team["mgr"], q="공개")) == sorted([mine, hidden])
    assert foreign not in list_ids(env, team["mgr"], q="공개")
    hold = env["client"].post(f"/api/meetings/{hidden}/hold", headers=auth(team["mgr"]), json={"reason": "시험"})
    assert hold.status_code in (200, 201)  # 보류된 회의록은 기본(진행중) 검색에서 빠진다
    assert hidden not in list_ids(env, team["mgr"], q="공개")
    assert hidden in list_ids(env, team["mgr"], q="공개", phase="on_hold")
    assert list_ids(env, team["stf"], q="공개", phase="active") == [mine]  # 담당자는 보류 단계를 볼 수 없다


# ---------------- 작업 C: 근거 위치 재계산 ----------------
TRANSCRIPT = (
    "[00:00:10] 화자1: 안녕하세요 회의를 시작합니다.\n"
    "[00:01:00] 화자2: 예산안은 다음 주 월요일까지 제출하겠습니다.\n"
    "[00:02:30] 화자1: 일정은 금요일까지 공유해 주세요.\n"
    "[00:04:00] 화자3: 마무리하겠습니다."
)


def make_with_items(env, team):
    return make_meeting(env["factory"], team["mgr"], transcript=TRANSCRIPT, participants=[team["stf"]], items=[
        {"title": "예산안 제출", "evidence_quote": "예산안은 다음 주 월요일까지 제출하겠습니다", "evidence_start_sec": 5.0},  # 정확: 60초로
        {"title": "일정 공유", "evidence_quote": None, "evidence_start_sec": 200.0},  # 인용문 없음 → 앞쪽 발화(150초) 근사
        {"title": "근거 없음", "evidence_quote": None, "evidence_start_sec": None},  # 미변경
        {"title": "이미 확정", "evidence_quote": "마무리하겠습니다", "evidence_start_sec": 1.0, "status": "confirmed"},  # 확정 제외
        {"title": "이미 맞음", "evidence_quote": "안녕하세요 회의를 시작합니다", "evidence_start_sec": 10.0},  # 같은 값 → 미변경
    ])


def items_by_title(env, mid):
    with env["factory"]() as s:
        return {i.title: i for i in s.scalars(select(ActionItem).where(ActionItem.meeting_id == mid))}


def recompute(env, account, mid, **body):
    return env["client"].post(f"/api/meetings/{mid}/recompute-evidence", headers=auth(account), json=body)


def test_recompute_preview_counts_without_saving(env, team):
    mid = make_with_items(env, team)
    res = recompute(env, team["mgr"], mid, dryRun=True)
    assert res.status_code == 200
    assert res.json() == {"targets": 4, "changed": 1, "approx": 1, "unchanged": 2, "excluded": 1, "applied": False}
    assert items_by_title(env, mid)["예산안 제출"].evidence_start_sec == 5.0  # 저장되지 않았다
    with env["factory"]() as s:
        assert s.scalar(select(Event.id).where(Event.event_type == KIND_EVIDENCE_RECOMPUTE)) is None


def test_recompute_apply_updates_items_records_history_and_marks_approx(env, team):
    mid = make_with_items(env, team)
    res = recompute(env, team["mgr"], mid, reason="근거 위치 오류 보정")
    assert res.status_code == 200 and res.json()["applied"] is True
    body = res.json()
    assert (body["changed"], body["approx"], body["unchanged"], body["excluded"]) == (1, 1, 2, 1)
    items = items_by_title(env, mid)
    assert items["예산안 제출"].evidence_start_sec == 60.0
    assert items["일정 공유"].evidence_start_sec == 150.0  # 200초 이전 가장 가까운 발화
    assert items["근거 없음"].evidence_start_sec is None
    assert items["이미 확정"].evidence_start_sec == 1.0  # 확정 업무는 바꾸지 않는다
    assert items["이미 맞음"].evidence_start_sec == 10.0
    with env["factory"]() as s:
        events = s.scalars(select(Event).where(Event.event_type == KIND_EVIDENCE_RECOMPUTE).order_by(Event.id)).all()
        assert len(events) == 3  # 업무 2건 + 회의록 요약 1건
        item_events = [e for e in events if e.entity_type == "action_item"]
        assert {e.payload["after"]["evidenceStartSec"] for e in item_events} == {60.0, 150.0}
        assert all(e.actor_account_id == team["mgr"].id and e.payload["reason"] == "근거 위치 오류 보정" for e in events)
        assert any(e.payload["after"].get("approx") is True and e.payload["before"] == {"evidenceStartSec": 200.0} for e in item_events)
        summary = [e for e in events if e.entity_type == "meeting"][0]
        assert summary.payload["after"] == {"targets": 4, "changed": 1, "approx": 1, "unchanged": 2, "excluded": 1}
        assert s.scalar(select(Transcript.full_text).where(Transcript.meeting_id == mid)) == TRANSCRIPT  # 전사문 불변
    detail = get(env, team["mgr"], f"/api/meetings/{mid}").json()
    approx = {i["title"]: i["evidenceApprox"] for i in detail["actionItems"]}
    assert approx == {"예산안 제출": False, "일정 공유": True, "근거 없음": False, "이미 확정": False, "이미 맞음": False}
    history = get(env, team["mgr"], f"/api/meetings/{mid}/history").json()
    rows = history if isinstance(history, list) else history.get("items", [])
    assert any(h.get("kind") == "근거 위치 재계산" for h in rows)


def test_recompute_second_run_and_stale_approx_is_cleared(env, team):
    mid = make_with_items(env, team)
    recompute(env, team["mgr"], mid, reason="1차")
    second = recompute(env, team["mgr"], mid, reason="2차").json()
    assert second["changed"] == 0 and second["approx"] == 1  # 인용문 없는 업무는 계속 근사로 남는다
    with env["factory"]() as s:  # 인용문이 생기면 같은 값(150초)이라도 근사 표시는 풀린다
        item = s.scalar(select(ActionItem).where(ActionItem.meeting_id == mid, ActionItem.title == "일정 공유"))
        item.evidence_quote = "일정은 금요일까지 공유해 주세요"
        s.commit()
    third = recompute(env, team["mgr"], mid, reason="3차").json()
    assert third["changed"] == 0 and third["approx"] == 0
    detail = get(env, team["mgr"], f"/api/meetings/{mid}").json()
    assert {i["title"]: i["evidenceApprox"] for i in detail["actionItems"]}["일정 공유"] is False


def test_recompute_permission_reason_and_lock(env, team):
    mid = make_with_items(env, team)
    assert recompute(env, team["stf"], mid, reason="x").status_code == 403
    assert recompute(env, team["stf"], mid, dryRun=True).status_code == 403
    assert recompute(env, team["mgr"], mid).status_code == 422
    assert recompute(env, team["mgr"], mid, reason="   ").status_code == 422
    other = add_account(env["factory"], "고객사B", "mgrb", "manager", name="타사")
    assert recompute(env, other, mid, reason="x").status_code == 404
    assert recompute(env, team["exe"], mid, reason="지시자 실행").status_code == 200
    hold = env["client"].post(f"/api/meetings/{mid}/hold", headers=auth(team["mgr"]), json={"reason": "보류"})
    assert hold.status_code in (200, 201)
    assert recompute(env, team["mgr"], mid, reason="보류 중").status_code == 409
    assert recompute(env, team["mgr"], mid, dryRun=True).status_code == 200  # 건수 확인은 잠금과 무관


def test_recompute_without_transcript_changes_nothing(env, team):
    mid = make_meeting(env["factory"], team["mgr"], items=[{"title": "업무", "evidence_quote": "말", "evidence_start_sec": 3.0}])
    res = recompute(env, team["mgr"], mid, reason="전사문 없음").json()
    assert (res["changed"], res["approx"], res["unchanged"]) == (0, 0, 1)
    assert items_by_title(env, mid)["업무"].evidence_start_sec == 3.0


# ---------------- 작업 D: 내부 작업 수 ----------------
def add_jobs(env, team, statuses):
    for status in statuses:
        mid = make_meeting(env["factory"], team["mgr"], title="비밀 제목 XYZ")
        with env["factory"]() as s:
            s.add(Job(tenant_id=team["mgr"].tenant_id, meeting_id=mid, status=status))
            s.commit()


def peer_client(host: str) -> TestClient:
    return TestClient(app, client=(host, 50000))


def test_queue_status_allows_loopback_and_returns_numbers_only(env, team):
    add_jobs(env, team, ["running", "queued", "queued", "completed", "failed"])
    for host in ("127.0.0.1", "::1"):
        res = peer_client(host).get("/internal/queue-status")
        assert res.status_code == 200
        assert res.json() == {"active": 1, "queued": 2}
        assert "XYZ" not in res.text and "박관리" not in res.text


def test_queue_status_rejects_non_loopback_and_ignores_forwarded_headers(env, team):
    add_jobs(env, team, ["running"])
    external = peer_client("203.0.113.9")
    assert external.get("/internal/queue-status").status_code == 403
    spoof = {"X-Forwarded-For": "127.0.0.1", "X-Real-IP": "127.0.0.1", "Forwarded": "for=127.0.0.1"}
    assert external.get("/internal/queue-status", headers=spoof).status_code == 403
    assert env["client"].get("/internal/queue-status").status_code == 403  # 기본 TestClient(peer 이름 testclient)도 루프백이 아니다


def test_queue_status_can_be_turned_off(env, team, monkeypatch):
    monkeypatch.setattr(settings, "internal_queue_status_enabled", False)
    assert peer_client("127.0.0.1").get("/internal/queue-status").status_code == 404
