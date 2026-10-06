"""작업 61: 일시 오류 자동 재시도, 실패 회의록 재처리, 서버 시작 복구, 처리 엔진 API, 상세 응답의 처리 정보·허용 동작.
네트워크 없음(Gemini 는 가짜 클라이언트·오류 주입), 임시 DB·업로드 폴더만 쓴다."""
import logging

import httpx
import pytest
from fastapi.testclient import TestClient
from google.genai import errors as genai_errors
from pydantic import SecretStr
from sqlalchemy import func, select

from app import main as main_module
from app.config import GeminiKeyMissingError, Settings, settings
from app.main import app
from app.models import ActionItem, Event, Job, Meeting, MeetingMinutes, Transcript
from app.pipeline import retry
from app.pipeline.errors import error_code
from app.pipeline.extractor import ExtractionResult, FakeExtractor
from app.services.processing import process_meeting
from app.services.reprocess import SERVER_RESTARTED, recover_interrupted_jobs
from tests.test_meeting_queries import make_meeting
from tests.test_upload_processing import BrokenExtractor, StubStt, add_account, auth, env, upload  # noqa: F401  (env 는 픽스처)


def server_error(code: int = 503) -> genai_errors.APIError:
    cls = genai_errors.ServerError if code >= 500 else genai_errors.ClientError
    return cls(code, {"error": {"message": "boom AIzaSyFAKEFAKEFAKEFAKEFAKEFAKE12345", "status": "X"}})


class Client:
    """models.generate_content 를 흉내 내는 가짜 클라이언트: 앞에서부터 failures 를 하나씩 던지고, 다 쓰면 성공."""

    def __init__(self, failures: list[BaseException]):
        self.failures, self.calls = list(failures), 0
        self.models = self

    def generate_content(self, **kwargs):
        self.calls += 1
        if self.failures:
            raise self.failures.pop(0)
        return type("R", (), {"text": "ok"})()


@pytest.fixture
def sleeps(monkeypatch):
    recorded: list[float] = []
    monkeypatch.setattr(retry, "_sleep", recorded.append)
    monkeypatch.setattr(settings, "gemini_retry_max", 2)
    monkeypatch.setattr(settings, "gemini_retry_backoff_sec", 5.0)
    return recorded


# ---------------- 일시 오류 자동 재시도 ----------------
def test_two_503_then_success(sleeps):
    client = Client([server_error(503), server_error(503)])
    assert retry.generate_with_retry(client, model="m", contents="c").text == "ok"
    assert client.calls == 3 and sleeps == [5.0, 10.0]  # 재시도마다 2배


def test_exhausted_retries_raise_same_error_code(sleeps):
    client = Client([server_error(503)] * 3)
    with pytest.raises(genai_errors.APIError) as caught:
        retry.generate_with_retry(client, model="m", contents="c")
    assert client.calls == 3 and error_code(caught.value) == "gemini_api_503"  # 기존과 같은 오류 코드


@pytest.mark.parametrize("status", [500, 502, 504])
def test_other_5xx_are_retried(sleeps, status):
    client = Client([server_error(status)])
    retry.generate_with_retry(client, model="m", contents="c")
    assert client.calls == 2


def test_connect_error_is_retried(sleeps):
    client = Client([httpx.ConnectError("down")])
    retry.generate_with_retry(client, model="m", contents="c")
    assert client.calls == 2


@pytest.mark.parametrize("failure", [
    httpx.ReadTimeout("slow"), httpx.ConnectTimeout("slow"), TimeoutError("late"),
    server_error(429), server_error(400), server_error(401), server_error(403), server_error(404),
    GeminiKeyMissingError("GEMINI_KEY_MODE=free 인데 GEMINI_API_KEY 가 비어 있습니다."),
])
def test_non_transient_errors_are_not_retried(sleeps, failure):
    client = Client([failure])
    with pytest.raises(type(failure)):
        retry.generate_with_retry(client, model="m", contents="c")
    assert client.calls == 1 and sleeps == []


def test_retry_max_zero_turns_retry_off(sleeps, monkeypatch):
    monkeypatch.setattr(settings, "gemini_retry_max", 0)
    client = Client([server_error(503)])
    with pytest.raises(genai_errors.APIError):
        retry.generate_with_retry(client, model="m", contents="c")
    assert client.calls == 1 and sleeps == []


def test_backoff_is_capped_at_60_seconds(sleeps, monkeypatch):
    monkeypatch.setattr(settings, "gemini_retry_max", 3)
    monkeypatch.setattr(settings, "gemini_retry_backoff_sec", 40.0)
    retry.generate_with_retry(Client([server_error(503)] * 3), model="m", contents="c")
    assert sleeps == [40.0, 60.0, 60.0]


def test_retry_log_has_only_type_and_status_not_message_or_key(sleeps, caplog):
    with caplog.at_level(logging.WARNING, logger="app.gemini"):
        retry.generate_with_retry(Client([server_error(503)]), model="m", contents="c")
    text = caplog.text
    assert "ServerError" in text and "503" in text
    assert "AIza" not in text and "boom" not in text


def test_retry_settings_are_read_from_env(monkeypatch):
    monkeypatch.delenv("GEMINI_RETRY_MAX", raising=False)
    monkeypatch.delenv("GEMINI_RETRY_BACKOFF_SEC", raising=False)
    defaults = Settings(_env_file=None)
    assert defaults.gemini_retry_max == 2 and defaults.gemini_retry_backoff_sec == 5.0
    monkeypatch.setenv("GEMINI_RETRY_MAX", "4")
    monkeypatch.setenv("GEMINI_RETRY_BACKOFF_SEC", "1.5")
    configured = Settings(_env_file=None)
    assert configured.gemini_retry_max == 4 and configured.gemini_retry_backoff_sec == 1.5
    monkeypatch.setenv("GEMINI_RETRY_MAX", "-1")
    with pytest.raises(Exception):
        Settings(_env_file=None)


def test_sdk_client_has_no_own_retry():
    from google.genai import types

    from app.pipeline import gemini_client

    captured = {}

    class FakeGenai:
        class Client:
            def __init__(self, **kwargs):
                captured.update(kwargs)

    import sys
    import types as pytypes

    fake_module = pytypes.ModuleType("google.genai")
    fake_module.Client = FakeGenai.Client
    fake_module.types = types
    cfg = Settings(_env_file=None, gemini_api_key=SecretStr("k" * 20))
    real = sys.modules["google.genai"]
    sys.modules["google.genai"] = fake_module
    try:
        import google

        google.genai = fake_module  # from google import genai 가 가짜를 보게
        gemini_client.make_gemini_client(cfg)
    finally:
        sys.modules["google.genai"] = real
        google.genai = real
    assert captured["http_options"].retry_options is None  # 이중 재시도 없음


def test_successful_transcription_is_not_called_again_when_extraction_retries(env, monkeypatch, sleeps):
    """전사(이미 성공)는 추출 재시도 때 다시 호출되지 않는다. 추출이 끝내 실패하면 같은 오류 코드로 failed."""
    from app.pipeline.extractor import GeminiExtractor

    mgr = add_account(env["factory"], "고객사A", "mgr", "manager")
    res = upload(env, mgr).json()
    stt = StubStt("[00:00:01] 화자1: 안녕하세요")
    client = Client([server_error(503)] * 3)  # 추출 호출이 계속 503
    result = process_meeting(res["jobId"], session_factory=env["factory"], stt_factory=lambda: stt,
                             extractor_factory=lambda: GeminiExtractor(client, "m"))
    assert result == "failed"
    assert client.calls == 3 and sleeps == [5.0, 10.0]  # 추출 1회 + 재시도 2회
    with env["factory"]() as s:
        assert s.scalar(select(Job.error_code)) == "gemini_api_503"
        assert s.scalar(select(func.count()).select_from(Transcript)) == 1  # 전사문은 보존


# ---------------- 재처리 ----------------
@pytest.fixture
def team(env):
    f = env["factory"]
    return {
        "lead": add_account(f, "고객사A", "lead", "manager", name="박총괄"),
        "mgr2": add_account(f, "고객사A", "mgr2", "manager", name="정관리"),
        "exe": add_account(f, "고객사A", "boss", "executive", name="최임원"),
        "staff": add_account(f, "고객사A", "kim", "staff", name="김담당"),
        "stranger": add_account(f, "고객사A", "lee", "staff", name="이무관"),
        "outsider": add_account(f, "고객사B", "out", "executive", name="외부"),
    }


def failed_meeting(env, registrant, **upload_kwargs) -> int:
    """등록자(registrant)가 올린 음성을 업무 추출 실패로 failed 상태로 만든다(전사문은 저장됨)."""
    body = upload(env, registrant, **upload_kwargs).json()
    assert process_meeting(body["jobId"], session_factory=env["factory"], extractor_factory=BrokenExtractor) == "failed"
    return body["meetingId"]


def call(env, method, account, path, **kwargs):
    return env["client"].request(method, path, headers=auth(account) if account else {}, **kwargs)


def reprocess(env, account, mid):
    return call(env, "POST", account, f"/api/meetings/{mid}/reprocess")


def meeting_row(env, mid) -> Meeting:
    with env["factory"]() as s:
        return s.get(Meeting, mid)


def test_failed_meeting_keeps_transcript_but_no_items_or_minutes(env, team):
    mid = failed_meeting(env, team["lead"])
    with env["factory"]() as s:
        assert s.scalar(select(func.count()).select_from(Transcript).where(Transcript.meeting_id == mid)) == 1
        assert s.scalar(select(func.count()).select_from(ActionItem).where(ActionItem.meeting_id == mid)) == 0
        assert s.get(MeetingMinutes, mid) is None
    assert meeting_row(env, mid).status == "failed"


@pytest.mark.parametrize("who", ["lead", "exe"])
def test_reprocess_allowed_for_lead_and_executive_end_to_end(env, team, who):
    mid = failed_meeting(env, team["lead"])
    before = meeting_row(env, mid)
    res = reprocess(env, team[who], mid)
    assert res.status_code == 202 and res.json()["meetingId"] == mid
    job_id = res.json()["jobId"]
    assert env["queued"][-1] == job_id and meeting_row(env, mid).status == "processing"  # 같은 실행기로 새 작업 예약
    assert process_meeting(job_id, session_factory=env["factory"], extractor_factory=FakeExtractor) == "completed"  # 같은 파이프라인 함수

    after = meeting_row(env, mid)
    assert after.status == "confirmed" and after.confirm_kind == "registration"  # 관리자 등록 = 등록 시 확정(첫 처리와 같은 규칙)
    assert after.auto_confirm_at == before.auto_confirm_at and after.first_created_at == before.first_created_at  # 자동 확정 시각 불변
    with env["factory"]() as s:
        assert s.scalar(select(func.count()).select_from(ActionItem).where(ActionItem.meeting_id == mid)) == 2
        assert s.scalar(select(func.count()).select_from(Transcript).where(Transcript.meeting_id == mid)) == 1  # 전사문 중복 없음
        assert s.scalar(select(func.count()).select_from(MeetingMinutes).where(MeetingMinutes.meeting_id == mid)) == 1
        assert s.scalar(select(func.count()).select_from(Job).where(Job.meeting_id == mid)) == 2  # 처음 작업(failed) + 새 작업
    # 재처리가 끝난 뒤 다시 누르면 failed 가 아니라 409
    assert reprocess(env, team[who], mid).status_code == 409


def test_reprocess_status_follows_registrant_rank_awaiting_for_staff_registered(env, team):
    f = env["factory"]
    mid = failed_meeting(env, team["staff"], participants=[str(team["lead"].id)])  # 담당자가 올림, 참석 관리자가 총괄
    res = reprocess(env, team["lead"], mid)
    assert res.status_code == 202
    process_meeting(res.json()["jobId"], session_factory=f, extractor_factory=FakeExtractor)
    after = meeting_row(env, mid)
    assert after.status == "awaiting_confirmation" and after.confirm_kind is None


def test_reprocess_denied_for_other_manager_staff_stranger_and_other_tenant(env, team):
    mid = failed_meeting(env, team["lead"])
    assert reprocess(env, team["mgr2"], mid).status_code == 403  # 총괄 아닌 관리자
    assert reprocess(env, team["stranger"], mid).status_code == 404  # 볼 수 없는 담당자
    assert reprocess(env, team["outsider"], mid).status_code == 404  # 다른 회사
    assert reprocess(env, None, mid).status_code == 401
    assert meeting_row(env, mid).status == "failed"
    with env["factory"]() as s:
        assert s.scalar(select(func.count()).select_from(Job)) == 1  # 새 작업이 만들어지지 않았다


def test_reprocess_denied_for_participating_staff(env, team):
    mid = failed_meeting(env, team["lead"], participants=[str(team["staff"].id)])
    assert reprocess(env, team["staff"], mid).status_code == 403


def test_reprocess_requires_failed_status(env, team):
    ok = upload(env, team["lead"]).json()
    process_meeting(ok["jobId"], session_factory=env["factory"], extractor_factory=FakeExtractor)
    assert reprocess(env, team["lead"], ok["meetingId"]).status_code == 409  # confirmed
    mid = make_meeting(env["factory"], team["lead"], status="no_content", title="내용 없음")
    assert reprocess(env, team["lead"], mid).status_code == 409


def test_reprocess_rejected_for_deleted_meeting(env, team):
    mid = failed_meeting(env, team["lead"])
    assert call(env, "POST", team["lead"], f"/api/meetings/{mid}/delete", json={"reason": "삭제"}).status_code == 200
    res = reprocess(env, team["lead"], mid)
    assert res.status_code == 409 and "삭제" in res.json()["detail"]


def test_reprocess_rejected_when_job_is_active(env, team):
    mid = failed_meeting(env, team["lead"])
    with env["factory"]() as s:
        s.add(Job(tenant_id=team["lead"].tenant_id, meeting_id=mid, status="running"))
        s.commit()
    res = reprocess(env, team["lead"], mid)
    assert res.status_code == 409 and "처리 중" in res.json()["detail"]
    assert call(env, "GET", team["lead"], f"/api/meetings/{mid}").json()["allowedActions"].count("reprocess_meeting") == 0


def test_reprocess_rejected_without_audio_file(env, team):
    mid = failed_meeting(env, team["lead"])
    for path in env["uploads"].rglob("*"):
        if path.is_file():
            path.unlink()
    res = reprocess(env, team["lead"], mid)
    assert res.status_code == 409 and "음성 파일이 없어" in res.json()["detail"]
    assert meeting_row(env, mid).status == "failed"


def test_reprocess_leaves_common_history_with_actor_and_previous_code(env, team):
    mid = failed_meeting(env, team["lead"])
    res = reprocess(env, team["exe"], mid)
    rows = [r for r in call(env, "GET", team["lead"], f"/api/meetings/{mid}/history").json() if r["kindCode"] == "meeting.reprocessed"]
    assert len(rows) == 1
    row = rows[0]
    assert row["kind"] == "재처리" and row["changedBy"]["id"] == team["exe"].id and row["changedAt"]
    assert row["before"] == {"status": "failed", "errorCode": "internal_error"} and row["after"] == {"status": "processing", "jobId": res.json()["jobId"]}
    with env["factory"]() as s:
        event = s.scalar(select(Event).where(Event.event_type == "meeting.reprocessed"))
        assert event.payload["previousErrorCode"] == "internal_error"  # 사유는 받지 않는다(payload 에 reason 없음)
        assert "reason" not in event.payload


# ---------------- 서버 시작 복구 ----------------
def test_recover_marks_running_and_queued_failed_and_leaves_completed(env, team):
    f = env["factory"]
    done = upload(env, team["lead"]).json()
    process_meeting(done["jobId"], session_factory=f, extractor_factory=FakeExtractor)  # completed
    queued = upload(env, team["lead"], title="대기").json()  # queued(실행기는 가로채져 아직 안 돎)
    running = upload(env, team["lead"], title="실행").json()
    with f() as s:
        s.get(Job, running["jobId"]).status = "running"
        s.commit()
    queued_before = list(env["queued"])
    assert recover_interrupted_jobs(f) == 2
    with f() as s:
        jobs = {j.id: j for j in s.scalars(select(Job))}
        assert jobs[done["jobId"]].status == "completed" and jobs[done["jobId"]].error_code is None
        for item in (queued, running):
            assert jobs[item["jobId"]].status == "failed" and jobs[item["jobId"]].error_code == SERVER_RESTARTED
            assert jobs[item["jobId"]].finished_at is not None
            assert s.get(Meeting, item["meetingId"]).status == "failed"
        assert s.get(Meeting, done["meetingId"]).status == "confirmed"
    assert env["queued"] == queued_before  # 자동 재실행 없음
    assert recover_interrupted_jobs(f) == 0  # 멱등


def test_recovered_meeting_can_be_reprocessed(env, team):
    f = env["factory"]
    body = upload(env, team["lead"]).json()
    recover_interrupted_jobs(f)
    detail = call(env, "GET", team["lead"], f"/api/meetings/{body['meetingId']}").json()
    assert detail["status"] == "failed" and detail["processing"]["errorCode"] == "server_restarted"
    assert "reprocess_meeting" in detail["allowedActions"]
    assert reprocess(env, team["lead"], body["meetingId"]).status_code == 202


def test_startup_warns_when_fake_and_recovers(env, team, monkeypatch, caplog):
    monkeypatch.setattr(main_module, "SessionLocal", env["factory"])  # 임시 DB 로만(운영 DB 에는 닿지 않는다)
    monkeypatch.setattr(settings, "stt_provider", "fake")
    body = upload(env, team["lead"]).json()
    with caplog.at_level(logging.WARNING, logger="app.main"):
        with TestClient(app):
            pass
    assert "가짜(fake) 처리 모드로 실행 중" in caplog.text
    assert meeting_row(env, body["meetingId"]).status == "failed"  # 시작 때 중단된 작업 정리


def test_startup_has_no_fake_warning_for_gemini(env, monkeypatch, caplog):
    monkeypatch.setattr(main_module, "SessionLocal", env["factory"])
    monkeypatch.setattr(settings, "stt_provider", "gemini")
    with caplog.at_level(logging.WARNING, logger="app.main"):
        with TestClient(app):
            pass
    assert "가짜(fake)" not in caplog.text


# ---------------- 엔진 API ----------------
def test_engine_requires_login_and_exposes_only_engine(env, team, monkeypatch):
    monkeypatch.setattr(settings, "gemini_api_key", SecretStr("AIzaSyFAKEFAKEFAKEFAKEFAKEFAKE12345"))
    monkeypatch.setattr(settings, "gemini_paid_api_key", SecretStr("AIzaSyPAIDPAIDPAIDPAIDPAIDPAID1234"))
    monkeypatch.setattr(settings, "gemini_key_mode", "paid")
    monkeypatch.setattr(settings, "gemini_model", "model-name-xyz")
    assert env["client"].get("/api/system/engine").status_code == 401
    monkeypatch.setattr(settings, "stt_provider", "fake")
    res = call(env, "GET", team["staff"], "/api/system/engine")
    assert res.status_code == 200 and res.json() == {"engine": "fake", "isFake": True}
    monkeypatch.setattr(settings, "stt_provider", "gemini")
    res = call(env, "GET", team["staff"], "/api/system/engine")
    assert res.json() == {"engine": "gemini", "isFake": False}
    for secret in ("AIza", "paid", "free", "model-name-xyz"):
        assert secret not in res.text


# ---------------- 상세 응답: 처리 정보·허용 동작 ----------------
def test_detail_processing_info_has_code_only_never_exception_text(env, team):
    mid = failed_meeting(env, team["lead"])  # BrokenExtractor 의 예외 문구에는 SECRET_TEXT 가 들어 있다
    res = call(env, "GET", team["lead"], f"/api/meetings/{mid}")
    body = res.json()
    assert body["processing"]["status"] == "failed" and body["processing"]["errorCode"] == "internal_error"
    assert body["processing"]["startedAt"] and body["processing"]["finishedAt"]
    assert set(body["processing"]) == {"status", "errorCode", "startedAt", "finishedAt"}
    from tests.test_upload_processing import SECRET_TEXT

    assert SECRET_TEXT not in res.text


def test_detail_processing_sanitizes_unexpected_error_code(env, team):
    mid = failed_meeting(env, team["lead"])
    with env["factory"]() as s:
        job = s.scalar(select(Job).where(Job.meeting_id == mid))
        job.error_code = "Bad Key AIzaSyFAKEFAKEFAKEFAKE"
        s.commit()
    body = call(env, "GET", team["lead"], f"/api/meetings/{mid}").json()
    assert body["processing"]["errorCode"] == "internal_error" and "AIza" not in str(body)


def test_detail_processing_is_none_without_job_and_follows_latest_job(env, team):
    mid = make_meeting(env["factory"], team["lead"], transcript="x")
    assert call(env, "GET", team["lead"], f"/api/meetings/{mid}").json()["processing"] is None
    failed = failed_meeting(env, team["lead"], title="두 번째")
    reprocess(env, team["lead"], failed)
    body = call(env, "GET", team["lead"], f"/api/meetings/{failed}").json()
    assert body["status"] == "processing" and body["processing"]["status"] == "queued" and body["processing"]["errorCode"] is None


@pytest.mark.parametrize("state, who, expected", [
    ("failed", "lead", True), ("failed", "exe", True), ("failed", "mgr2", False),
    ("confirmed", "lead", False), ("awaiting_confirmation", "lead", False), ("processing", "lead", False), ("no_content", "lead", False),
])
def test_reprocess_allowed_action_table(env, team, state, who, expected):
    mid = make_meeting(env["factory"], team["lead"], status=state, transcript="x", title=f"표-{state}")
    with env["factory"]() as s:
        from app.models import SourceDocument

        doc = s.get(SourceDocument, s.get(Meeting, mid).source_document_id)
        doc.file_path = None
        s.commit()
    assert ("reprocess_meeting" in call(env, "GET", team[who], f"/api/meetings/{mid}").json()["allowedActions"]) is expected


def test_reprocess_not_allowed_in_deleted_failed_meeting_and_for_staff(env, team):
    mid = failed_meeting(env, team["lead"], participants=[str(team["staff"].id)])
    assert "reprocess_meeting" not in call(env, "GET", team["staff"], f"/api/meetings/{mid}").json()["allowedActions"]
    call(env, "POST", team["lead"], f"/api/meetings/{mid}/delete", json={"reason": "삭제"})
    assert "reprocess_meeting" not in call(env, "GET", team["lead"], f"/api/meetings/{mid}").json()["allowedActions"]
