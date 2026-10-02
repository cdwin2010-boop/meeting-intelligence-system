"""4b-2a단계: 회의록 목록·상세·전사문 조회 API(읽기 전용).
네트워크 없음. env 픽스처(임시 DB·업로드 폴더·의존성 교체)는 test_upload_processing 의 것을 쓴다."""
from datetime import date, datetime, timedelta, timezone

import pytest
from sqlalchemy import event as sa_event

from app.models import ActionItem, Event, Meeting, MeetingParticipant, SourceDocument, Transcript, append_event
from app.models.common import utcnow
from app.services.processing import process_meeting
from tests.test_upload_processing import add_account, auth, env, upload  # noqa: F401  (env 는 픽스처)

BASE = datetime(2026, 9, 1, 1, 0, tzinfo=timezone.utc)


def make_meeting(factory, registrant, *, day: int = 0, status: str = "awaiting_confirmation", items=(), participants=(),
                 transcript: str | None = None, title: str | None = None) -> int:
    """회의록 1건을 직접 만든다. items: ActionItem 필드 dict 목록."""
    with factory() as s:
        doc = SourceDocument(tenant_id=registrant.tenant_id, origin="audio_minutes", doc_type="회의록",
                             title="doc", registered_by=registrant.id)
        s.add(doc)
        s.flush()
        now = utcnow()
        meeting = Meeting(tenant_id=registrant.tenant_id, source_document_id=doc.id, title=title or f"회의 {day}",
                          held_at=BASE + timedelta(days=day), status=status, first_created_at=now,
                          auto_confirm_at=now + timedelta(days=5))
        s.add(meeting)
        s.flush()
        for account in participants:
            s.add(MeetingParticipant(meeting_id=meeting.id, account_id=account.id))
        for fields in items:
            s.add(ActionItem(tenant_id=registrant.tenant_id, meeting_id=meeting.id, **{"title": "업무", **fields}))
        if transcript is not None:
            s.add(Transcript(tenant_id=registrant.tenant_id, meeting_id=meeting.id, full_text=transcript, stt_provider="fake"))
        append_event(s, tenant_id=registrant.tenant_id, entity_type="meeting", entity_id=meeting.id,
                     event_type="meeting.created", actor_account_id=registrant.id, payload={"secret": "payload"})
        s.commit()
        return meeting.id


def get(env, account, path: str, **params):
    return env["client"].get(path, headers=auth(account), params=params)


def list_ids(env, account, **params) -> list[int]:
    res = get(env, account, "/api/meetings", **params)
    assert res.status_code == 200
    return [m["id"] for m in res.json()["items"]]


# ---------------- 인증 ----------------
@pytest.mark.parametrize("path", ["/api/meetings", "/api/meetings/1", "/api/meetings/1/transcript"])
def test_requires_login(env, path):
    assert env["client"].get(path).status_code == 401


# ---------------- 목록 ----------------
def test_list_fields_and_sort_by_held_at_desc(env):
    mgr = add_account(env["factory"], "고객사A", "mgr", "manager", name="박관리")
    first = make_meeting(env["factory"], mgr, day=1)
    second = make_meeting(env["factory"], mgr, day=3, status="confirmed")
    third = make_meeting(env["factory"], mgr, day=2)
    outsider = add_account(env["factory"], "고객사B", "out", "executive")
    make_meeting(env["factory"], outsider, day=9)

    body = get(env, mgr, "/api/meetings").json()
    assert [m["id"] for m in body["items"]] == [second, third, first]
    assert body["total"] == 3 and body["page"] == 1 and body["size"] == 20
    item = body["items"][0]
    assert set(item) == {"id", "title", "heldAt", "registeredBy", "origin", "status", "confirmKind", "itemCount",
                         "needsCompletionCount", "autoConfirmAt", "phase"}
    assert item["phase"] == "active"
    assert item["registeredBy"] == {"id": mgr.id, "name": "박관리"}
    assert item["origin"] == "audio_minutes" and item["status"] == "confirmed"
    assert datetime.fromisoformat(item["heldAt"]) == BASE + timedelta(days=3)  # 오프셋 포함 ISO
    assert datetime.fromisoformat(item["heldAt"]).utcoffset() == timedelta(0)


def test_list_pagination(env):
    mgr = add_account(env["factory"], "고객사A", "mgr", "manager")
    ids = [make_meeting(env["factory"], mgr, day=d) for d in range(5)]
    newest_first = list(reversed(ids))
    page2 = get(env, mgr, "/api/meetings", page=2, size=2).json()
    assert [m["id"] for m in page2["items"]] == newest_first[2:4] and page2["total"] == 5
    assert list_ids(env, mgr, page=3, size=2) == newest_first[4:]
    assert list_ids(env, mgr, page=4, size=2) == []
    assert get(env, mgr, "/api/meetings", size=101).status_code == 422
    assert get(env, mgr, "/api/meetings", page=0).status_code == 422


def test_list_filters(env):
    mgr = add_account(env["factory"], "고객사A", "mgr", "manager")
    staff = add_account(env["factory"], "고객사A", "kim", "staff")
    mine = make_meeting(env["factory"], mgr, day=1, status="confirmed", items=[{"assignee_id": mgr.id, "due_undetermined": True}])
    by_staff = make_meeting(env["factory"], staff, day=2, items=[{"assignee_id": mgr.id, "due_date": date(2026, 10, 1)}])
    incomplete = make_meeting(env["factory"], staff, day=3, items=[{"assignee_id": None}])

    assert list_ids(env, mgr, status="confirmed") == [mine]
    assert list_ids(env, mgr, status="awaiting_confirmation") == [incomplete, by_staff]
    assert get(env, mgr, "/api/meetings", status="weird").status_code == 422
    assert list_ids(env, mgr, mine="registered") == [mine]
    assert list_ids(env, mgr, mine="assigned") == [by_staff, mine]
    assert list_ids(env, mgr, needsCompletion="true") == [incomplete]


def test_needs_completion_count_three_cases(env):
    mgr = add_account(env["factory"], "고객사A", "mgr", "manager")
    meeting_id = make_meeting(env["factory"], mgr, items=[
        {"assignee_id": None, "due_date": date(2026, 10, 1)},  # 담당자 없음 → 보완 필요
        {"assignee_id": mgr.id, "due_date": None, "due_undetermined": False},  # 기한 빈칸 → 보완 필요
        {"assignee_id": mgr.id, "due_date": None, "due_undetermined": True},  # 미확정 선택 → 보완 아님
        {"title": "  ", "assignee_id": mgr.id, "due_undetermined": True},  # 업무명 공백 → 보완 필요
        {"title": "삭제됨", "assignee_id": None, "status": "deleted"},  # 삭제된 업무는 세지 않음
    ])
    [item] = get(env, mgr, "/api/meetings").json()["items"]
    assert item["id"] == meeting_id and item["itemCount"] == 4 and item["needsCompletionCount"] == 3


def test_list_query_count_does_not_grow_with_meetings(env):
    mgr = add_account(env["factory"], "고객사A", "mgr", "manager")
    engine = env["factory"].kw["bind"]
    statements: list[str] = []

    def _count(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement)

    def queries_for_list() -> int:
        statements.clear()
        sa_event.listen(engine, "before_cursor_execute", _count)
        try:
            assert get(env, mgr, "/api/meetings").status_code == 200
        finally:
            sa_event.remove(engine, "before_cursor_execute", _count)
        return len(statements)

    for d in range(2):
        make_meeting(env["factory"], mgr, day=d, items=[{"assignee_id": None}, {"assignee_id": mgr.id}])
    few = queries_for_list()
    for d in range(2, 12):
        make_meeting(env["factory"], mgr, day=d, items=[{"assignee_id": None}, {"assignee_id": mgr.id}])
    many = queries_for_list()
    assert few == many
    assert many <= 3  # 계정 확인 1 + 전체 건수 1 + 목록 1


# ---------------- 열람 권한 ----------------
def test_staff_sees_only_related_meetings(env):
    mgr = add_account(env["factory"], "고객사A", "mgr", "manager")
    exe = add_account(env["factory"], "고객사A", "boss", "executive")
    staff = add_account(env["factory"], "고객사A", "kim", "staff")
    as_participant = make_meeting(env["factory"], mgr, day=1, participants=[staff])
    as_assignee = make_meeting(env["factory"], mgr, day=2, items=[{"assignee_id": staff.id}])
    as_registrant = make_meeting(env["factory"], staff, day=3)
    deleted_assignment = make_meeting(env["factory"], mgr, day=4, items=[{"assignee_id": staff.id, "status": "deleted"}])
    unrelated = make_meeting(env["factory"], mgr, day=5, transcript="관계없는 회의 전사")

    assert list_ids(env, staff) == [as_registrant, as_assignee, as_participant]
    assert get(env, staff, f"/api/meetings/{unrelated}").status_code == 404
    assert get(env, staff, f"/api/meetings/{unrelated}/transcript").status_code == 404
    assert get(env, staff, f"/api/meetings/{deleted_assignment}").status_code == 404
    assert get(env, staff, f"/api/meetings/{as_assignee}").status_code == 200

    everything = [unrelated, deleted_assignment, as_registrant, as_assignee, as_participant]
    assert list_ids(env, mgr) == everything
    assert list_ids(env, exe) == everything
    assert get(env, mgr, f"/api/meetings/{unrelated}").status_code == 200


def test_other_tenant_gets_404_same_as_missing(env):
    mgr = add_account(env["factory"], "고객사A", "mgr", "manager")
    outsider = add_account(env["factory"], "고객사B", "out", "executive")
    meeting_id = make_meeting(env["factory"], mgr, transcript="A사 전사")
    for path in (f"/api/meetings/{meeting_id}", f"/api/meetings/{meeting_id}/transcript"):
        res = get(env, outsider, path)
        missing = get(env, outsider, "/api/meetings/999999" + path[len(f"/api/meetings/{meeting_id}"):])
        assert res.status_code == 404 and res.json() == missing.json()
    assert list_ids(env, outsider) == []


# ---------------- 상세 ----------------
def test_detail_fields_missing_fields_and_events(env):
    mgr = add_account(env["factory"], "고객사A", "mgr", "manager", name="박관리")
    lee = add_account(env["factory"], "고객사A", "lee", "staff", name="이서연")
    meeting_id = make_meeting(env["factory"], mgr, participants=[lee, mgr], items=[
        {"title": "", "assignee_id": None, "due_date": None},
        {"title": "시안 확정", "assignee_id": lee.id, "due_date": None, "due_undetermined": True,
         "evidence_start_sec": 20.0, "evidence_quote": "확정해 주세요"},
        {"title": "보고서", "assignee_id": lee.id, "due_date": date(2026, 10, 9)},
        {"title": "삭제됨", "status": "deleted"},
    ])
    res = get(env, mgr, f"/api/meetings/{meeting_id}")
    assert res.status_code == 200
    body = res.json()
    assert body["registeredBy"] == {"id": mgr.id, "name": "박관리"} and body["origin"] == "audio_minutes"
    assert body["participants"] == [{"id": mgr.id, "name": "박관리"}, {"id": lee.id, "name": "이서연"}]
    assert body["confirmedBy"] is None and body["decisions"] == [] and body["summary"] == ""
    assert datetime.fromisoformat(body["firstCreatedAt"]).utcoffset() == timedelta(0)

    items = body["actionItems"]
    assert [i["title"] for i in items] == ["", "시안 확정", "보고서"]  # 삭제된 업무 제외
    assert items[0]["missingFields"] == ["title", "assignee", "dueDate"] and items[0]["needsCompletion"] is True
    assert items[0]["assignee"] is None and items[0]["dueDate"] is None
    assert items[1]["missingFields"] == [] and items[1]["needsCompletion"] is False and items[1]["dueUndetermined"] is True
    assert items[1]["assignee"] == {"id": lee.id, "name": "이서연"}
    assert items[1]["evidenceStartSec"] == 20.0 and items[1]["evidenceQuote"] == "확정해 주세요"
    assert items[2]["dueDate"] == "2026-10-09" and items[2]["missingFields"] == []

    [created] = body["recentEvents"]
    assert created == {"eventType": "meeting.created", "actor": {"id": mgr.id, "name": "박관리"},
                       "createdAt": created["createdAt"], "reason": None}  # payload 는 내보내지 않음(처리 사유만, 없으면 None)
    assert "payload" not in res.text


def test_detail_events_after_processing_are_latest_first_and_limited(env):
    mgr = add_account(env["factory"], "고객사A", "mgr", "manager")
    body = upload(env, mgr).json()
    process_meeting(body["jobId"], session_factory=env["factory"])
    detail = get(env, mgr, f"/api/meetings/{body['meetingId']}").json()
    assert [e["eventType"] for e in detail["recentEvents"]] == [
        "job.completed", "meeting.confirmed", "item.created", "item.created",
        "transcript.saved", "job.started", "job.queued", "meeting.created",
    ]
    assert detail["status"] == "confirmed" and detail["confirmKind"] == "registration"
    assert detail["confirmedBy"]["id"] == mgr.id

    # 사건이 20건을 넘으면 최근 20건만
    with env["factory"]() as s:
        for _ in range(25):
            append_event(s, tenant_id=mgr.tenant_id, entity_type="meeting", entity_id=body["meetingId"], event_type="x.test")
        s.commit()
    events = get(env, mgr, f"/api/meetings/{body['meetingId']}").json()["recentEvents"]
    assert len(events) == 20 and {e["eventType"] for e in events} == {"x.test"}


# ---------------- 전사문 ----------------
def test_transcript_returned_and_404_when_missing(env):
    mgr = add_account(env["factory"], "고객사A", "mgr", "manager")
    with_transcript = make_meeting(env["factory"], mgr, transcript="[00:00:01] 화자1: 안녕하세요")
    without = make_meeting(env["factory"], mgr, day=1)

    res = get(env, mgr, f"/api/meetings/{with_transcript}/transcript")
    assert res.status_code == 200
    # displayText·speakers 는 화자 매핑 반영용 추가 필드(매핑이 없으면 원문 그대로·빈 목록)
    assert res.json() == {"fullText": "[00:00:01] 화자1: 안녕하세요", "segments": None, "sttProvider": "fake",
                          "displayText": "[00:00:01] 화자1: 안녕하세요", "speakers": []}
    missing = get(env, mgr, f"/api/meetings/{without}/transcript")
    assert missing.status_code == 404 and missing.json() == {"detail": "전사문이 없습니다"}


def test_transcript_from_processing(env):
    staff = add_account(env["factory"], "고객사A", "kim", "staff")
    body = upload(env, staff).json()
    process_meeting(body["jobId"], session_factory=env["factory"])
    res = get(env, staff, f"/api/meetings/{body['meetingId']}/transcript")  # 등록자라 열람 가능
    assert res.status_code == 200 and res.json()["fullText"].startswith("[00:00:03] 김도현:")
