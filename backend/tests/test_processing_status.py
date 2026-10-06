"""작업 62: 처리 현황 API(GET /api/me/processing)와 재처리 성공 시 자동 확정 기간 재계산. 네트워크 없음, 임시 DB 만 쓴다."""
from datetime import date, timedelta

import pytest
from sqlalchemy import func, select

from app.jobs.auto_confirm import run_auto_confirm
from app.models import ActionItem, Job, Meeting, MeetingView, Tenant
from app.models.common import utcnow
from app.pipeline.extractor import ActionItemCandidate, ExtractionResult, FakeExtractor
from app.pipeline.stt import NO_SPEECH
from app.services.processing import process_meeting
from tests.test_upload_processing import SECRET_TEXT, BrokenExtractor, StubStt, add_account, auth, env, upload  # noqa: F401  (env 는 픽스처)


@pytest.fixture
def team(env):
    f = env["factory"]
    return {
        "lead": add_account(f, "고객사A", "lead", "manager", name="박총괄"),
        "other": add_account(f, "고객사A", "other", "manager", name="정관리"),
        "exe": add_account(f, "고객사A", "boss", "executive", name="최임원"),
        "staff": add_account(f, "고객사A", "kim", "staff", name="김담당"),
        "outsider": add_account(f, "고객사B", "out", "manager", name="외부"),
    }


def call(env, method, account, path, **kwargs):
    return env["client"].request(method, path, headers=auth(account) if account else {}, **kwargs)


def mine(env, account) -> list[dict]:
    res = call(env, "GET", account, "/api/me/processing")
    assert res.status_code == 200, res.text
    return res.json()


def failed_meeting(env, registrant, **kwargs) -> int:
    body = upload(env, registrant, **kwargs).json()
    assert process_meeting(body["jobId"], session_factory=env["factory"], extractor_factory=BrokenExtractor) == "failed"
    return body["meetingId"]


# ---------------- 처리 현황 API ----------------
def test_requires_login(env):
    assert env["client"].get("/api/me/processing").status_code == 401


def test_only_my_meetings_and_not_other_users_or_tenants(env, team):
    upload(env, team["lead"], title="내 회의")
    upload(env, team["other"], title="다른 관리자")
    upload(env, team["outsider"], title="다른 회사")
    assert [i["title"] for i in mine(env, team["lead"])] == ["내 회의"]
    assert [i["title"] for i in mine(env, team["other"])] == ["다른 관리자"]
    assert [i["title"] for i in mine(env, team["outsider"])] == ["다른 회사"]
    assert mine(env, team["staff"]) == []
    # 고객사가 다른 회의록은 등록자가 같아 보여도 포함하지 않는다(데이터가 어긋난 경우의 방어)
    with env["factory"]() as s:
        meeting = s.scalar(select(Meeting).where(Meeting.title == "내 회의"))
        meeting.tenant_id = team["outsider"].tenant_id
        s.commit()
    assert mine(env, team["lead"]) == []


def test_states_queued_running_completed_failed_no_content(env, team):
    f = env["factory"]
    upload(env, team["lead"], title="대기")
    running = upload(env, team["lead"], title="실행").json()
    with f() as s:
        job = s.get(Job, running["jobId"])
        job.status = "running"
        job.started_at = utcnow() - timedelta(seconds=75)
        s.commit()
    done = upload(env, team["lead"], title="완료").json()
    process_meeting(done["jobId"], session_factory=f, extractor_factory=FakeExtractor)
    failed_meeting(env, team["lead"], title="실패")
    nc = upload(env, team["lead"], title="내용없음").json()
    process_meeting(nc["jobId"], session_factory=f, stt_factory=lambda: StubStt(NO_SPEECH))
    items = {i["title"]: i for i in mine(env, team["lead"])}
    assert {k: v["status"] for k, v in items.items()} == {"대기": "queued", "실행": "running", "완료": "completed", "실패": "failed", "내용없음": "no_content"}
    assert items["실패"]["errorCode"] == "internal_error" and items["완료"]["errorCode"] is None
    assert items["실행"]["elapsedSec"] >= 75 and items["실행"]["finishedAt"] is None  # 서버가 응답 시점 기준으로 계산
    assert items["완료"]["finishedAt"] is not None
    assert set(items["실패"]) == {"meetingId", "jobId", "title", "status", "errorCode", "elapsedSec", "finishedAt", "canReprocess"}
    job_ids = [i["jobId"] for i in mine(env, team["lead"])]
    assert job_ids == sorted(job_ids, reverse=True)  # 최신순


def test_elapsed_seconds_for_finished_job_is_start_to_finish(env, team):
    body = upload(env, team["lead"]).json()
    process_meeting(body["jobId"], session_factory=env["factory"], extractor_factory=FakeExtractor)
    with env["factory"]() as s:
        job = s.get(Job, body["jobId"])
        job.started_at = utcnow() - timedelta(hours=2)
        job.finished_at = job.started_at + timedelta(seconds=93)
        s.commit()
    assert mine(env, team["lead"])[0]["elapsedSec"] == 93


def test_time_window_setting_and_limit_of_ten(env, team, monkeypatch):
    from app.config import settings

    for index in range(12):
        upload(env, team["lead"], title=f"회의 {index}")
    rows = mine(env, team["lead"])
    assert len(rows) == 10 and rows[0]["title"] == "회의 11"  # 최대 10건, 최신순
    with env["factory"]() as s:
        for job in s.scalars(select(Job)):
            job.started_at = job.created_at = utcnow() - timedelta(hours=30)
        s.commit()
    assert mine(env, team["lead"]) == []  # 기본 24시간 밖
    monkeypatch.setattr(settings, "processing_track_hours", 48)
    assert len(mine(env, team["lead"])) == 10  # 설정으로 범위를 넓힘


def test_deleted_meetings_are_excluded(env, team):
    body = upload(env, team["lead"], title="지울 회의").json()
    process_meeting(body["jobId"], session_factory=env["factory"], extractor_factory=FakeExtractor)
    assert len(mine(env, team["lead"])) == 1
    assert call(env, "POST", team["lead"], f"/api/meetings/{body['meetingId']}/delete", json={"reason": "삭제"}).status_code == 200
    assert mine(env, team["lead"]) == []


def test_only_latest_job_per_meeting(env, team):
    mid = failed_meeting(env, team["lead"])
    call(env, "POST", team["lead"], f"/api/meetings/{mid}/reprocess")
    rows = mine(env, team["lead"])
    assert len(rows) == 1 and rows[0]["status"] == "queued" and rows[0]["errorCode"] is None


def test_response_has_no_raw_exception_text_key_or_path(env, team):
    mid = failed_meeting(env, team["lead"])
    with env["factory"]() as s:
        s.scalar(select(Job).where(Job.meeting_id == mid)).error_code = "Bad Key AIzaSyFAKEFAKEFAKEFAKE /tmp/secret.m4a"
        s.commit()
    res = call(env, "GET", team["lead"], "/api/me/processing")
    assert res.json()[0]["errorCode"] == "internal_error"
    for leak in (SECRET_TEXT, "AIza", ".m4a", "data/uploads", "GEMINI"):
        assert leak not in res.text


def test_can_reprocess_matches_allowed_actions(env, team):
    failed_meeting(env, team["lead"])
    ok = upload(env, team["lead"], title="정상").json()
    process_meeting(ok["jobId"], session_factory=env["factory"], extractor_factory=FakeExtractor)
    flags = {i["title"]: i["canReprocess"] for i in mine(env, team["lead"])}
    assert flags == {"정상": False, "주간 회의": True}
    for item in mine(env, team["lead"]):
        allowed = call(env, "GET", team["lead"], f"/api/meetings/{item['meetingId']}").json()["allowedActions"]
        assert item["canReprocess"] == ("reprocess_meeting" in allowed)
    failed_meeting(env, team["staff"], title="담당자 업로드")  # 담당자가 올린 실패 회의록은 본인이 재처리할 수 없다(허용 동작 판정 그대로)
    assert mine(env, team["staff"])[0]["canReprocess"] is False


def test_query_leaves_no_view_record(env, team):
    upload(env, team["lead"])
    mine(env, team["lead"])
    mine(env, team["lead"])
    with env["factory"]() as s:
        assert s.scalar(select(func.count()).select_from(MeetingView)) == 0


# ---------------- 재처리 성공 시 자동 확정 기간 재계산 ----------------
def staff_failed(env, team, **kwargs) -> int:
    """담당자가 올려 확정 대기가 되는 회의록(참석 관리자 lead 가 총괄)."""
    return failed_meeting(env, team["staff"], participants=[str(team["lead"].id)], **kwargs)


def run_reprocess(env, team, mid, extractor=FakeExtractor):
    res = call(env, "POST", team["lead"], f"/api/meetings/{mid}/reprocess")
    assert res.status_code == 202
    assert process_meeting(res.json()["jobId"], session_factory=env["factory"], extractor_factory=extractor) == "completed"


def meeting(env, mid) -> Meeting:
    with env["factory"]() as s:
        return s.get(Meeting, mid)


def run_auto(env):
    with env["factory"]() as s:
        run_auto_confirm(s)
        s.commit()


def test_reprocess_success_recomputes_auto_confirm_from_success_time(env, team):
    mid = staff_failed(env, team)
    with env["factory"]() as s:
        s.get(Meeting, mid).auto_confirm_at = utcnow() - timedelta(days=1)  # 실패한 채 방치돼 이미 지남
        days = s.get(Tenant, team["lead"].tenant_id).auto_confirm_days
        s.commit()
    before_run = utcnow()
    first_created = meeting(env, mid).first_created_at
    run_reprocess(env, team, mid)
    after = meeting(env, mid)
    assert after.status == "awaiting_confirmation"
    assert before_run + timedelta(days=days) <= after.auto_confirm_at <= utcnow() + timedelta(days=days)  # 성공 시각 + 고객사 기간
    assert after.first_created_at == first_created  # 최초 생성일은 그대로


def test_customer_period_setting_is_used(env, team):
    with env["factory"]() as s:
        s.get(Tenant, team["lead"].tenant_id).auto_confirm_days = 10
        s.commit()
    mid = staff_failed(env, team)
    run_reprocess(env, team, mid)
    delta = meeting(env, mid).auto_confirm_at - utcnow()
    assert timedelta(days=9, hours=23) < delta <= timedelta(days=10)


def test_not_confirmed_right_after_reprocess_but_confirmed_after_new_period(env, team):
    mid = staff_failed(env, team)
    with env["factory"]() as s:
        s.get(Meeting, mid).auto_confirm_at = utcnow() - timedelta(days=1)
        s.commit()
    run_reprocess(env, team, mid)
    run_auto(env)
    assert meeting(env, mid).status == "awaiting_confirmation"  # 재처리 직후에는 자동 확정하지 않는다
    with env["factory"]() as s:
        assert s.scalar(select(func.count()).select_from(ActionItem).where(ActionItem.meeting_id == mid, ActionItem.status == "confirmed")) == 0
        s.get(Meeting, mid).auto_confirm_at = utcnow() - timedelta(minutes=1)  # 새 기간이 지난 뒤
        s.commit()
    run_auto(env)
    assert meeting(env, mid).status == "confirmed" and meeting(env, mid).confirm_kind == "period_elapsed"


def test_item_due_date_earlier_than_new_period_still_confirms_on_due_date(env, team):
    """업무 마감일이 새 기간보다 빠르면 기존 규칙(due_reached)대로 마감일에 확정된다(회의록 시각에는 마감일을 넣지 않는다)."""

    class DueExtractor:
        def extract(self, transcript, held_at):
            item = ActionItemCandidate(
                task="마감 임박 업무", assignee="김담당", due_date=(date.today() - timedelta(days=1)).isoformat(),
                quote_speaker="화자1", quote_timestamp="00:00:01", quote_text="인용", evidence_start_sec=1.0,
            )
            return ExtractionResult(items=[item], extract_model="x", prompt_version="x")

    mid = staff_failed(env, team)
    run_reprocess(env, team, mid, extractor=DueExtractor)
    with env["factory"]() as s:
        s.scalar(select(ActionItem).where(ActionItem.meeting_id == mid)).assignee_id = team["staff"].id  # 보완 필요가 아니게
        s.commit()
    assert meeting(env, mid).auto_confirm_at > utcnow()  # 회의록 시각은 아직 먼 미래
    run_auto(env)
    with env["factory"]() as s:
        item = s.scalar(select(ActionItem).where(ActionItem.meeting_id == mid))
        assert item.status == "confirmed" and item.confirm_kind == "due_reached"
    assert meeting(env, mid).status == "awaiting_confirmation"


def test_first_processing_and_confirmed_meetings_are_unchanged(env, team):
    first = upload(env, team["staff"], participants=[str(team["lead"].id)]).json()
    before = meeting(env, first["meetingId"]).auto_confirm_at
    process_meeting(first["jobId"], session_factory=env["factory"], extractor_factory=FakeExtractor)
    assert meeting(env, first["meetingId"]).auto_confirm_at == before  # 첫 처리 불변
    confirmed = failed_meeting(env, team["lead"], title="관리자 등록")  # 관리자 등록은 처리가 끝나면 등록 시 확정
    before_confirmed = meeting(env, confirmed).auto_confirm_at
    run_reprocess(env, team, confirmed)
    after = meeting(env, confirmed)
    assert after.status == "confirmed" and after.auto_confirm_at == before_confirmed  # 이미 확정된 회의록 불변


def test_history_keeps_previous_and_new_values(env, team):
    mid = staff_failed(env, team)
    old = meeting(env, mid).auto_confirm_at
    run_reprocess(env, team, mid)
    new = meeting(env, mid).auto_confirm_at
    rows = [r for r in call(env, "GET", team["lead"], f"/api/meetings/{mid}/history").json() if r["kindCode"] == "meeting.reprocessed"]
    assert len(rows) == 2 and {r["kind"] for r in rows} == {"재처리"}  # 시작 기록 + 성공 기록
    done = next(r for r in rows if "autoConfirmAt" in r["after"])
    assert done["before"]["autoConfirmAt"].startswith(old.strftime("%Y-%m-%dT%H:%M"))
    assert done["after"]["autoConfirmAt"].startswith(new.strftime("%Y-%m-%dT%H:%M"))
