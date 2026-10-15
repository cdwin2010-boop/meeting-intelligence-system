"""엔진 시험 모드·원격(OpenAI 호환, 시험용) 엔진(작업 91-1). 127.0.0.1 임시 포트의 가짜 서버만 쓴다(외부 네트워크 없음)."""
import json
import logging

import pytest
from sqlalchemy import select

from app.config import settings
from app.models import ActionItem, Event, Job, MeetingMinutes
from app.pipeline.fakes import FAKE_SCRIPT, fake_transcript
from app.services import engine_test
from app.services.processing import process_meeting
from tests.test_local_llm import FakeServer, ok_body
from tests.test_transcript_txt import HELD_AT
from tests.test_upload_processing import add_account, auth, env  # noqa: F401  (env 는 픽스처)

SECRET = "sk-remote-secret-value-4242"
MODEL = "Qwen3-8B-Q4"
TWO_AGENDAS = (
    "[00:00:01] 화자1: 첫 번째 안건은 일정 점검입니다.\n"
    "[00:00:10] 화자2: 네, 개발 일정은 이번 주 금요일까지 공유하겠습니다.\n"
    "[00:00:20] 화자1: 다음 안건은 예산 검토입니다.\n"
    "[00:00:30] 화자3: 예산안은 다음 주 월요일까지 제출하겠습니다."
)


def chunk_reply(items=(), **minutes) -> dict:
    return ok_body(json.dumps({"items": list(items), "minutes": minutes}, ensure_ascii=False))


def item(task, assignee, due, speaker, ts, text) -> dict:
    return {"task": task, "assignee": assignee, "due_date": due, "quote": {"speaker": speaker, "timestamp": ts, "text": text}}


FAKE_ITEM = item("개선안 정리", "이서연", "2026-10-09", "김도현", "00:00:03", FAKE_SCRIPT[0][2])


@pytest.fixture
def remote(monkeypatch, tmp_path):
    """시험 모드 켜짐 + 임시 설정 파일 + 가짜 원격 서버 생성기. 주소·키는 설정 파일 쪽(화면 저장과 같은 경로)에 둔다."""
    servers: list[FakeServer] = []
    monkeypatch.setattr(settings, "engine_test_mode", True)
    monkeypatch.setattr(settings, "engine_test_settings_path", tmp_path / "engine_test.json")
    monkeypatch.setattr(settings, "remote_llm_base_url", "")
    monkeypatch.setattr(settings, "remote_llm_model", "")
    monkeypatch.setattr(settings, "extract_llm_timeout_seconds", 10)

    def make(script, *, model=MODEL, timeout=10.0):
        srv = FakeServer(script)
        servers.append(srv)
        engine_test.save_remote_settings(base_url=srv.base_url, model=model, api_key=SECRET, timeout_seconds=timeout, chunk_max_chars=0)
        return srv

    yield make
    for srv in servers:
        srv.close()


@pytest.fixture
def mgr(env):
    return add_account(env["factory"], "고객사A", "mgr", "manager", name="박관리")


@pytest.fixture
def staff(env):
    return add_account(env["factory"], "고객사A", "stf", "staff", name="김담당")


def post_txt(env, account, content: bytes, engine: str | None = None, title="시험 회의"):
    data = {"title": title, "heldAt": HELD_AT, "sourceKind": "transcript_txt"}
    if engine is not None:
        data["engine"] = engine
    return env["client"].post("/api/meetings/upload", headers=auth(account), data=data, files={"file": ("회의 자료.txt", content, "text/plain")})


def run(env, job_id):
    return process_meeting(job_id, session_factory=env["factory"])


def job_of(env, meeting_id):
    with env["factory"]() as s:
        return s.scalars(select(Job).where(Job.meeting_id == meeting_id).order_by(Job.id.desc())).first()


def items_of(env, meeting_id):
    with env["factory"]() as s:
        return s.scalars(select(ActionItem).where(ActionItem.meeting_id == meeting_id).order_by(ActionItem.id)).all()


def queued_payload(env, job_id) -> dict:
    with env["factory"]() as s:
        event = s.scalars(select(Event).where(Event.entity_type == "job", Event.entity_id == job_id, Event.event_type == "job.queued")).first()
        return dict(event.payload or {})


def put_settings(env, account, **over):
    body = {"baseUrl": "http://127.0.0.1:9/v1", "model": MODEL, "apiKey": SECRET, "timeoutSeconds": 30, "chunkMaxChars": 0} | over
    return env["client"].put("/api/admin/engine-test/remote", headers=auth(account), json=body)


# ---------------- 시험 모드 꺼짐: 서버가 거부 ----------------
def test_mode_off_rejects_remote_requests_server_side(env, mgr, tmp_path, monkeypatch):
    assert settings.engine_test_mode is False  # 기본값(납품 설정)
    monkeypatch.setattr(settings, "engine_test_settings_path", tmp_path / "engine_test.json")
    client = env["client"]
    assert client.get("/api/admin/engine-test/remote", headers=auth(mgr)).status_code == 403
    assert put_settings(env, mgr).status_code == 403
    assert client.post("/api/admin/engine-test/remote/load-test", headers=auth(mgr)).status_code == 403
    assert not (tmp_path / "engine_test.json").exists()  # 거부된 저장은 파일을 만들지 않는다
    res = post_txt(env, mgr, fake_transcript().encode("utf-8"), engine="remote")
    assert res.status_code == 403
    with env["factory"]() as s:
        assert s.scalar(select(Job.id)) is None  # 회의록·작업이 만들어지지 않는다
    engine = client.get("/api/system/engine", headers=auth(mgr)).json()
    assert engine["engineTestMode"] is False and engine["engineTestDefault"] == "default"


def test_mode_off_normal_upload_has_no_engine_record(env, mgr):
    res = post_txt(env, mgr, fake_transcript().encode("utf-8"))
    assert res.status_code == 202
    assert queued_payload(env, res.json()["jobId"]) == {}  # 현행 동작 그대로
    assert engine_test.job_engine(env["factory"], res.json()["jobId"]) == "default"


def test_remote_requested_while_mode_off_fails_without_fallback(env, mgr, remote):
    srv = remote([(200, chunk_reply([FAKE_ITEM]), 0)])
    res = post_txt(env, mgr, fake_transcript().encode("utf-8"), engine="remote")
    assert res.status_code == 202
    settings.engine_test_mode = False  # 접수 뒤 시험 모드가 꺼졌다(fixture 가 monkeypatch 로 되돌린다)
    try:
        assert run(env, res.json()["jobId"]) == "failed"
    finally:
        settings.engine_test_mode = True
    assert job_of(env, res.json()["meetingId"]).error_code == "remote_disabled"
    assert srv.requests == [] and items_of(env, res.json()["meetingId"]) == []


# ---------------- 권한 ----------------
def test_staff_cannot_use_remote_settings_or_select_engine(env, staff, mgr, remote):
    remote([])
    client = env["client"]
    assert client.get("/api/admin/engine-test/remote", headers=auth(staff)).status_code == 403
    assert put_settings(env, staff).status_code == 403
    assert client.post("/api/admin/engine-test/remote/load-test", headers=auth(staff)).status_code == 403
    assert post_txt(env, staff, fake_transcript().encode("utf-8"), engine="remote").status_code == 403
    assert client.get("/api/admin/engine-test/remote", headers=auth(mgr)).status_code == 200


def test_staff_upload_without_choice_uses_configured_default(env, staff, remote, monkeypatch):
    remote([])
    monkeypatch.setattr(settings, "engine_test_default", "remote")
    res = post_txt(env, staff, fake_transcript().encode("utf-8"))
    assert res.status_code == 202
    assert queued_payload(env, res.json()["jobId"]) == {"engine": "remote"}


def test_unknown_engine_value_is_422(env, mgr, remote):
    remote([])
    assert post_txt(env, mgr, fake_transcript().encode("utf-8"), engine="gemini").status_code == 422


# ---------------- 설정 저장·키 마스킹 ----------------
def test_settings_roundtrip_masks_api_key_everywhere(env, mgr, remote, caplog, tmp_path):
    remote([])
    caplog.set_level(logging.DEBUG)
    put = put_settings(env, mgr, baseUrl="http://127.0.0.1:12345/v1/", model="모델-A", timeoutSeconds=45, chunkMaxChars=3000)
    assert put.status_code == 200
    body = put.json()
    assert body["apiKeySet"] is True and SECRET not in json.dumps(body) and body["apiKeyMasked"] not in SECRET
    assert body["baseUrl"] == "http://127.0.0.1:12345/v1" and body["model"] == "모델-A"
    assert body["timeoutSeconds"] == 45 and body["chunkMaxChars"] == 3000 and body["configured"] is True
    assert "시험용 데이터만" in body["notice"]
    got = env["client"].get("/api/admin/engine-test/remote", headers=auth(mgr)).json()
    assert got == body and SECRET not in json.dumps(got)
    # 키를 비워 보내면 기존 키 유지(파일에는 그대로 있다)
    again = put_settings(env, mgr, apiKey="")
    assert again.json()["apiKeySet"] is True
    assert engine_test.load_remote_settings().api_key == SECRET
    # 이력에는 바뀐 항목 이름만 남고 값(주소·키)은 없다
    with env["factory"]() as s:
        payloads = [json.dumps(e.payload) for e in s.scalars(select(Event).where(Event.event_type == "engine_test.settings_changed"))]
    assert payloads and all(SECRET not in p and "127.0.0.1" not in p for p in payloads)
    assert SECRET not in caplog.text and "127.0.0.1:12345" not in caplog.text


@pytest.mark.parametrize(
    "over",
    [{"baseUrl": "ftp://example.test/v1"}, {"baseUrl": "not a url"}, {"baseUrl": "http://x.test/v1?k=1"}, {"model": "  "},
     {"timeoutSeconds": 0}, {"timeoutSeconds": 99999}, {"chunkMaxChars": 100}],
)
def test_settings_validation_rejects_bad_values(env, mgr, remote, over):
    remote([])
    assert put_settings(env, mgr, **over).status_code == 422


# ---------------- 원격 추출 end-to-end ----------------
def test_remote_extraction_records_engine_and_hides_address_and_key(env, mgr, remote, caplog):
    srv = remote([(200, chunk_reply([FAKE_ITEM], purpose="목적"), 0)])
    caplog.set_level(logging.DEBUG)
    res = post_txt(env, mgr, fake_transcript().encode("utf-8"), engine="remote")
    assert res.status_code == 202 and queued_payload(env, res.json()["jobId"]) == {"engine": "remote"}
    assert run(env, res.json()["jobId"]) == "completed"
    [request] = srv.requests
    assert request["path"] == "/v1/chat/completions" and request["body"]["model"] == MODEL
    assert request["body"]["response_format"] == {"type": "json_object"}
    assert request["headers"]["authorization"] == f"Bearer {SECRET}"
    meeting_id = res.json()["meetingId"]
    [row] = items_of(env, meeting_id)
    assert row.title == "개선안 정리" and row.evidence_quote == FAKE_SCRIPT[0][2] and row.evidence_start_sec == 3.0
    with env["factory"]() as s:
        assert s.get(MeetingMinutes, meeting_id).engine == f"원격(시험용) · {MODEL}"
    detail = env["client"].get(f"/api/meetings/{meeting_id}", headers=auth(mgr)).json()
    text = json.dumps(detail, ensure_ascii=False)
    assert detail["minutes"]["engine"] == f"원격(시험용) · {MODEL}"
    assert SECRET not in text and "127.0.0.1" not in text
    assert SECRET not in caplog.text and srv.base_url not in caplog.text


def test_default_engine_is_used_when_not_remote(env, mgr, remote):
    srv = remote([(200, chunk_reply([FAKE_ITEM]), 0)])
    res = post_txt(env, mgr, fake_transcript().encode("utf-8"), engine="default")
    assert queued_payload(env, res.json()["jobId"]) == {"engine": "default"}
    assert run(env, res.json()["jobId"]) == "completed"
    assert srv.requests == []  # 원격 서버를 부르지 않는다(기존 엔진 = fake)


def test_long_name_model_label_fits_column(env, mgr, remote):
    remote([(200, chunk_reply([FAKE_ITEM]), 0)], model="Qwen3-Coder-480B-A35B-Instruct-FP8")
    res = post_txt(env, mgr, fake_transcript().encode("utf-8"), engine="remote")
    assert run(env, res.json()["jobId"]) == "completed"
    with env["factory"]() as s:
        engine = s.get(MeetingMinutes, res.json()["meetingId"]).engine
    assert engine.startswith("원격(시험용) · ") and len(engine) <= 30


# ---------------- 실패: 자동 전환 없음 ----------------
def test_connection_failure_fails_without_fallback(env, mgr, remote):
    srv = remote([])
    srv.close()  # 서버가 없다 → 연결 실패
    res = post_txt(env, mgr, fake_transcript().encode("utf-8"), engine="remote")
    assert run(env, res.json()["jobId"]) == "failed"
    job = job_of(env, res.json()["meetingId"])
    assert job.status == "failed" and job.error_code == "remote_unavailable"
    assert items_of(env, res.json()["meetingId"]) == []  # 기존(fake/Gemini) 엔진으로 넘어가 결과가 생기지 않는다
    with env["factory"]() as s:
        assert s.get(MeetingMinutes, res.json()["meetingId"]) is None


def test_not_configured_remote_fails(env, mgr, monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "engine_test_mode", True)
    monkeypatch.setattr(settings, "engine_test_settings_path", tmp_path / "none.json")
    monkeypatch.setattr(settings, "remote_llm_base_url", "")
    res = post_txt(env, mgr, fake_transcript().encode("utf-8"), engine="remote")
    assert run(env, res.json()["jobId"]) == "failed"
    assert job_of(env, res.json()["meetingId"]).error_code == "remote_not_configured"


def test_timeout_marks_failed_with_timeout_code(env, mgr, remote):
    remote([(200, ok_body("late"), 1.5)] * 4, timeout=0.3)
    res = post_txt(env, mgr, fake_transcript().encode("utf-8"), engine="remote")
    assert run(env, res.json()["jobId"]) == "failed"
    assert job_of(env, res.json()["meetingId"]).error_code == "timeout"


def test_invalid_json_retries_twice_then_fails(env, mgr, remote):
    srv = remote([(200, ok_body("JSON 이 아닙니다"), 0)] * 6)
    res = post_txt(env, mgr, fake_transcript().encode("utf-8"), engine="remote")
    assert run(env, res.json()["jobId"]) == "failed"
    assert len(srv.requests) == 3  # 최초 1회 + 재시도 최대 2회
    assert job_of(env, res.json()["meetingId"]).error_code == "extraction_invalid"


def test_invalid_json_recovers_on_second_retry(env, mgr, remote):
    srv = remote([(200, ok_body("깨진 응답"), 0), (200, ok_body("{ 깨진"), 0), (200, chunk_reply([FAKE_ITEM], purpose="복구"), 0)])
    res = post_txt(env, mgr, fake_transcript().encode("utf-8"), engine="remote")
    assert run(env, res.json()["jobId"]) == "completed"
    assert len(srv.requests) == 3
    assert [i.title for i in items_of(env, res.json()["meetingId"])] == ["개선안 정리"]


# ---------------- 청크·병합 ----------------
def test_long_meeting_is_chunked_and_duplicates_are_merged(env, mgr, remote):
    first = item("일정 공유", "화자2", "2026-10-02", "화자2", "00:00:10", "네, 개발 일정은 이번 주 금요일까지 공유하겠습니다.")
    second = item("예산안 제출", "화자3", "2026-10-05", "화자3", "00:00:30", "예산안은 다음 주 월요일까지 제출하겠습니다.")
    # 청크마다 같은 업무(first)가 다시 나와도 하나로 합쳐진다
    srv = remote([(200, chunk_reply([first], discussion="일정 점검"), 0), (200, chunk_reply([first, second], discussion="예산 검토"), 0)])
    engine_test.save_remote_settings(
        base_url=engine_test.load_remote_settings().base_url, model=MODEL, api_key=None, timeout_seconds=10, chunk_max_chars=130
    )  # 예산 초과 경로(안건 분할)를 타게 한다(C-1)
    res = post_txt(env, mgr, TWO_AGENDAS.encode("utf-8"), engine="remote")
    assert run(env, res.json()["jobId"]) == "completed"
    assert len(srv.requests) == 2
    titles = [i.title for i in items_of(env, res.json()["meetingId"])]
    assert titles == ["일정 공유", "예산안 제출"]


def test_remote_chunk_limit_setting_splits_transcript(env, mgr, remote):
    srv = remote([(200, chunk_reply([]), 0)] * 8)
    engine_test.save_remote_settings(
        base_url=engine_test.load_remote_settings().base_url, model=MODEL, api_key=None, timeout_seconds=10, chunk_max_chars=500
    )
    long_text = "\n".join(f"[00:{i // 60:02d}:{i % 60:02d}] 화자1: " + ("가나다라마바사아자차카타파하 " * 6) for i in range(30))
    res = post_txt(env, mgr, long_text.encode("utf-8"), engine="remote")
    assert run(env, res.json()["jobId"]) == "completed"
    assert len(srv.requests) >= 2  # 청크 한도(500자)에 따라 여러 번 나눠 호출


# ---------------- 로딩 테스트 ----------------
def models_body(*ids) -> dict:
    return {"data": [{"id": i} for i in ids]}


def test_load_test_success_reports_model_time_and_json(env, mgr, remote):
    srv = remote([(200, models_body(MODEL, "다른-모델"), 0), (200, ok_body('{"ok": true}'), 0)])
    res = env["client"].post("/api/admin/engine-test/remote/load-test", headers=auth(mgr))
    assert res.status_code == 200
    body = res.json()
    assert body["ok"] is True and body["modelListed"] is True and body["modelFound"] is True
    assert body["currentModel"] == MODEL and body["availableModels"] == [MODEL, "다른-모델"]
    assert body["jsonParsed"] is True and isinstance(body["responseMs"], int) and body["failureReason"] == ""
    assert [r["path"] for r in srv.requests] == ["/v1/models", "/v1/chat/completions"]
    assert SECRET not in json.dumps(body) and "127.0.0.1" not in json.dumps(body)


def test_load_test_models_failure(env, mgr, remote):
    remote([(500, {}, 0)])
    body = env["client"].post("/api/admin/engine-test/remote/load-test", headers=auth(mgr)).json()
    assert body["ok"] is False and body["failureStage"] == "models" and "연결할 수 없습니다" in body["failureReason"]


def test_load_test_connection_failure(env, mgr, remote):
    remote([]).close()
    body = env["client"].post("/api/admin/engine-test/remote/load-test", headers=auth(mgr)).json()
    assert body["ok"] is False and body["failureReason"] == "원격 엔진에 연결할 수 없습니다"


def test_load_test_invalid_json_reports_reason(env, mgr, remote):
    remote([(200, models_body(MODEL), 0), (200, ok_body("그냥 글자"), 0)])
    body = env["client"].post("/api/admin/engine-test/remote/load-test", headers=auth(mgr)).json()
    assert body["ok"] is False and body["failureStage"] == "inference" and body["jsonParsed"] is False
    assert "JSON" in body["failureReason"] and body["modelFound"] is True


def test_load_test_timeout(env, mgr, remote, monkeypatch):
    remote([(200, models_body(MODEL), 0), (200, ok_body("{}"), 2.0)], timeout=0.4)
    body = env["client"].post("/api/admin/engine-test/remote/load-test", headers=auth(mgr)).json()
    assert body["ok"] is False and "시간이 초과" in body["failureReason"]


def test_load_test_rejected_while_processing_is_running(env, mgr, remote):
    srv = remote([(200, models_body(MODEL), 0), (200, ok_body("{}"), 0)])
    res = post_txt(env, mgr, fake_transcript().encode("utf-8"))
    with env["factory"]() as s:
        s.get(Job, res.json()["jobId"]).status = "running"
        s.commit()
    out = env["client"].post("/api/admin/engine-test/remote/load-test", headers=auth(mgr))
    assert out.status_code == 409 and "진행 중" in out.json()["detail"]
    assert srv.requests == []  # 원격 서버에 아무것도 보내지 않는다


def test_load_test_requires_saved_settings(env, mgr, monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "engine_test_mode", True)
    monkeypatch.setattr(settings, "engine_test_settings_path", tmp_path / "none.json")
    monkeypatch.setattr(settings, "remote_llm_base_url", "")
    assert env["client"].post("/api/admin/engine-test/remote/load-test", headers=auth(mgr)).status_code == 422


# ---------------- 재처리 ----------------
def test_reprocess_with_engine_choice_records_it(env, mgr, remote):
    remote([(200, chunk_reply([FAKE_ITEM]), 0)])
    res = post_txt(env, mgr, fake_transcript().encode("utf-8"))
    meeting_id = res.json()["meetingId"]
    with env["factory"]() as s:  # 첫 처리가 실패한 상태로 만든다(재처리는 실패한 회의록만)
        s.get(Job, res.json()["jobId"]).status = "failed"
        from app.models import Meeting

        s.get(Meeting, meeting_id).status = "failed"
        s.commit()
    again = env["client"].post(f"/api/meetings/{meeting_id}/reprocess", headers=auth(mgr), json={"engine": "remote"})
    assert again.status_code == 202
    assert queued_payload(env, again.json()["jobId"]) == {"reprocess": True, "engine": "remote"}
    assert run(env, again.json()["jobId"]) == "completed"
    with env["factory"]() as s:
        assert s.get(MeetingMinutes, meeting_id).engine == f"원격(시험용) · {MODEL}"
    # 이전 작업 기록(첫 job)은 그대로 남는다
    with env["factory"]() as s:
        assert s.get(Job, res.json()["jobId"]).status == "failed"
