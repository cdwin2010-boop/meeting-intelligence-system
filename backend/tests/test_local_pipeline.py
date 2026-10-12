"""로컬 엔진 추출 경로 연결·실패 표시·유형별 프롬프트·엔진 표시(작업 81-1 파트 B). 127.0.0.1 임시 포트의 가짜 서버만 쓴다."""
import json
import logging
from datetime import datetime, timedelta, timezone

import httpx
import pytest
from sqlalchemy import select

from app.config import settings
from app.models import ActionItem, Event, Job, Meeting, MeetingClassification, MeetingMinutes
from app.models.minutes import MINUTES_FIELDS, NO_CONTENT
from app.pipeline import extractor as extractor_module
from app.pipeline import prompts
from app.pipeline.extractor import GeminiExtractor
from app.pipeline.fakes import FAKE_SCRIPT, fake_transcript
from app.pipeline.local_extractor import engine_label, prompt_version_of
from app.services.processing import process_meeting
from app.services.processing_warnings import WARNING_EVENT
from tests.test_local_llm import FakeServer, ok_body
from tests.test_transcript_txt import post_txt
from tests.test_upload_processing import add_account, auth, env, upload  # noqa: F401  (env 는 픽스처)

SECRET = "sk-local-secret-value-987"
MODEL = "Qwen2.5-7B-Instruct-Q4_K_M.gguf"
HELD = datetime(2026, 9, 29, 14, 0, tzinfo=timezone(timedelta(hours=9)))
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
def local(monkeypatch):
    """로컬 엔진 설정 + 가짜 서버 생성기."""
    servers: list[FakeServer] = []

    def make(script):
        srv = FakeServer(script)
        servers.append(srv)
        monkeypatch.setattr(settings, "llm_provider", "local")
        monkeypatch.setattr(settings, "local_llm_base_url", srv.base_url)
        monkeypatch.setattr(settings, "local_llm_model", MODEL)
        monkeypatch.setattr(settings, "local_llm_api_key", type(settings.local_llm_api_key)(SECRET))
        monkeypatch.setattr(settings, "extract_llm_timeout_seconds", 10)
        return srv

    yield make
    for srv in servers:
        srv.close()


@pytest.fixture
def mgr(env):
    return add_account(env["factory"], "고객사A", "mgr", "manager", name="박관리")


def run(env, job_id):
    return process_meeting(job_id, session_factory=env["factory"])


def job_of(env, meeting_id):
    with env["factory"]() as s:
        return s.scalars(select(Job).where(Job.meeting_id == meeting_id).order_by(Job.id.desc())).first()


def items_of(env, meeting_id):
    with env["factory"]() as s:
        return s.scalars(select(ActionItem).where(ActionItem.meeting_id == meeting_id).order_by(ActionItem.id)).all()


def minutes_of(env, meeting_id):
    with env["factory"]() as s:
        return s.get(MeetingMinutes, meeting_id)


def detail(env, account, meeting_id) -> dict:
    res = env["client"].get(f"/api/meetings/{meeting_id}", headers=auth(account))
    assert res.status_code == 200
    return res.json()


# ---------------- end-to-end ----------------
def test_txt_upload_through_local_engine(env, mgr, local, caplog):
    srv = local([(200, chunk_reply([FAKE_ITEM], purpose="목적 문장", decisions="결정 A"), 0)])
    caplog.set_level(logging.DEBUG)
    res = post_txt(env, mgr, fake_transcript().encode("utf-8"))
    assert res.status_code == 202
    meeting_id = res.json()["meetingId"]
    assert run(env, res.json()["jobId"]) == "completed"
    [request] = srv.requests
    assert request["path"] == "/v1/chat/completions" and request["body"]["model"] == MODEL
    assert request["headers"]["authorization"] == f"Bearer {SECRET}"
    assert FAKE_SCRIPT[0][2] in request["body"]["messages"][1]["content"]  # 전사문이 프롬프트에 들어간다
    [row] = items_of(env, meeting_id)
    assert row.title == "개선안 정리" and row.extract_model == MODEL and row.evidence_quote == FAKE_SCRIPT[0][2]
    assert row.evidence_start_sec == 3.0  # 서버 대조로 산정
    minutes = minutes_of(env, meeting_id)
    assert minutes.purpose == "목적 문장" and minutes.decisions == "결정 A" and minutes.risks == NO_CONTENT
    assert minutes.engine == engine_label(MODEL) == f"로컬 LLM({MODEL})"
    assert job_of(env, meeting_id).status == "completed"
    with env["factory"]() as s:
        assert s.get(Meeting, meeting_id).status in {"awaiting_confirmation", "confirmed"}
    body = json.dumps(detail(env, mgr, meeting_id), ensure_ascii=False)
    assert srv.base_url not in body and "127.0.0.1" not in body and SECRET not in body
    assert detail(env, mgr, meeting_id)["processingWarnings"] == []
    assert SECRET not in caplog.text and srv.base_url not in caplog.text and FAKE_SCRIPT[0][2] not in caplog.text


def test_audio_upload_with_fake_stt_through_local_engine(env, mgr, local):
    assert settings.stt_provider == "fake"
    srv = local([(200, chunk_reply([FAKE_ITEM], purpose="음성 경로"), 0)])
    res = upload(env, mgr)
    assert run(env, res.json()["jobId"]) == "completed"
    assert len(srv.requests) == 1
    assert minutes_of(env, res.json()["meetingId"]).purpose == "음성 경로"
    assert minutes_of(env, res.json()["meetingId"]).engine == f"로컬 LLM({MODEL})"


def test_valid_response_with_zero_items_is_completed(env, mgr, local):
    local([(200, chunk_reply([], purpose=""), 0)])
    res = post_txt(env, mgr, fake_transcript().encode("utf-8"))
    assert run(env, res.json()["jobId"]) == "completed"
    assert items_of(env, res.json()["meetingId"]) == []
    row = minutes_of(env, res.json()["meetingId"])
    assert all(getattr(row, key) == NO_CONTENT for key in MINUTES_FIELDS)
    assert detail(env, mgr, res.json()["meetingId"])["processingWarnings"] == []


# ---------------- 실패 표시 ----------------
@pytest.mark.parametrize("script, code", [
    ([(503, {}, 0)] * 6, "llm_unavailable"),
    ([(429, {}, 0)] * 6, "llm_unavailable"),
    ([(401, {}, 0)] * 6, "llm_error"),
])
def test_all_chunks_failed_marks_job_failed_and_reprocess_is_possible(env, mgr, local, script, code):
    srv = local(script)
    res = post_txt(env, mgr, fake_transcript().encode("utf-8"))
    meeting_id = res.json()["meetingId"]
    assert run(env, res.json()["jobId"]) == "failed"
    job = job_of(env, meeting_id)
    assert job.status == "failed" and job.error_code == code
    assert items_of(env, meeting_id) == [] and minutes_of(env, meeting_id) is None
    with env["factory"]() as s:
        assert s.get(Meeting, meeting_id).status == "failed"
    if code == "llm_error":
        assert len(srv.requests) == 1  # 영구 오류는 다시 부르지 않는다
    body = detail(env, mgr, meeting_id)
    assert body["processing"]["status"] == "failed" and body["processing"]["errorCode"] == code
    # 재처리 가능: 새 작업이 만들어지고, 서버가 정상이면 완료된다
    srv.script[:] = [(200, chunk_reply([FAKE_ITEM], purpose="재처리 성공"), 0)]
    again = env["client"].post(f"/api/meetings/{meeting_id}/reprocess", headers=auth(mgr))
    assert again.status_code == 202
    assert run(env, again.json()["jobId"]) == "completed"
    assert minutes_of(env, meeting_id).purpose == "재처리 성공"


def test_timeout_marks_failed_with_timeout_code(env, mgr, local, monkeypatch):
    local([(200, ok_body("late"), 1.5)] * 4)
    monkeypatch.setattr(settings, "extract_llm_timeout_seconds", 0.3)
    res = post_txt(env, mgr, fake_transcript().encode("utf-8"))
    assert run(env, res.json()["jobId"]) == "failed"
    assert job_of(env, res.json()["meetingId"]).error_code == "timeout"


def test_unparseable_responses_for_all_chunks_fail_as_extraction_invalid(env, mgr, local):
    local([(200, ok_body("JSON 이 아닙니다"), 0)] * 4)
    res = post_txt(env, mgr, fake_transcript().encode("utf-8"))
    assert run(env, res.json()["jobId"]) == "failed"
    assert job_of(env, res.json()["meetingId"]).error_code == "extraction_invalid"


def test_partial_chunk_failure_completes_with_processing_warning(env, mgr, local):
    first = item("일정 공유", "화자2", "2026-10-02", "화자2", "00:00:10", "네, 개발 일정은 이번 주 금요일까지 공유하겠습니다.")
    srv = local([(200, chunk_reply([first], discussion="일정 점검"), 0), (503, {}, 0), (503, {}, 0)])
    res = post_txt(env, mgr, TWO_AGENDAS.encode("utf-8"))
    meeting_id = res.json()["meetingId"]
    assert run(env, res.json()["jobId"]) == "completed"
    assert len(srv.requests) == 3
    assert [i.title for i in items_of(env, meeting_id)] == ["일정 공유"]
    warnings = detail(env, mgr, meeting_id)["processingWarnings"]
    assert len(warnings) == 1 and "구간 2/2" in warnings[0] and "실패" in warnings[0]
    assert "127.0.0.1" not in warnings[0] and SECRET not in warnings[0] and "예산" not in warnings[0]
    with env["factory"]() as s:
        stored = s.scalars(select(Event).where(Event.event_type == WARNING_EVENT)).all()
        assert len(stored) == 1 and stored[0].entity_type == "job"
    # 처리 경고 이벤트는 최근 사건 목록(recentEvents)에 섞이지 않는다
    assert WARNING_EVENT not in [e["eventType"] for e in detail(env, mgr, meeting_id)["recentEvents"]]


# ---------------- Gemini 경로 새 규칙 ----------------
class FakeGenai:
    def __init__(self, items_error=None, minutes_error=None, items=None):
        self.items_error, self.minutes_error, self.items = items_error, minutes_error, items if items is not None else []
        self.models = self

    def generate_content(self, *, model, contents, config):
        if "5개 항목" in contents:
            if self.minutes_error is not None:
                raise self.minutes_error
            return type("R", (), {"text": json.dumps({"purpose": "p", "discussion": "d", "decisions": "", "risks": "", "next_agenda": ""})})()
        if self.items_error is not None:
            raise self.items_error
        return type("R", (), {"text": json.dumps({"items": self.items})})()


def run_gemini(env, mgr, client, monkeypatch):
    monkeypatch.setattr(settings, "gemini_retry_max", 0)
    res = upload(env, mgr)
    out = process_meeting(res.json()["jobId"], session_factory=env["factory"],
                          extractor_factory=lambda: GeminiExtractor(client, "m"))
    return out, res.json()["meetingId"]


@pytest.mark.parametrize("error", [httpx.ReadTimeout("slow"), httpx.ConnectError("down")])
def test_gemini_transient_minutes_failure_marks_job_failed(env, mgr, monkeypatch, error):
    out, meeting_id = run_gemini(env, mgr, FakeGenai(minutes_error=error, items=[]), monkeypatch)
    assert out == "failed"
    assert job_of(env, meeting_id).error_code in {"timeout", "internal_error"}
    assert items_of(env, meeting_id) == [] and minutes_of(env, meeting_id) is None


def test_gemini_transient_items_failure_marks_job_failed(env, mgr, monkeypatch):
    out, meeting_id = run_gemini(env, mgr, FakeGenai(items_error=httpx.ReadTimeout("slow")), monkeypatch)
    assert out == "failed" and job_of(env, meeting_id).error_code == "timeout"


def test_gemini_normal_zero_items_and_empty_minutes_stay_completed(env, mgr, monkeypatch):
    out, meeting_id = run_gemini(env, mgr, FakeGenai(items=[]), monkeypatch)
    assert out == "completed" and items_of(env, meeting_id) == []
    assert minutes_of(env, meeting_id).purpose == "p" and minutes_of(env, meeting_id).decisions == NO_CONTENT


def test_gemini_non_transient_minutes_failure_stays_completed(env, mgr, monkeypatch):
    out, meeting_id = run_gemini(env, mgr, FakeGenai(minutes_error=RuntimeError("boom")), monkeypatch)
    assert out == "completed"
    row = minutes_of(env, meeting_id)
    assert all(getattr(row, key) == NO_CONTENT for key in MINUTES_FIELDS)


# ---------------- 담당자 규칙 ----------------
def test_unknown_assignee_stays_empty_string(env, mgr, local):
    unknown = item("담당 불명 일", "", "", "", "", "전사문에 없는 인용문입니다")
    local([(200, chunk_reply([unknown]), 0)])
    res = post_txt(env, mgr, fake_transcript().encode("utf-8"))
    assert run(env, res.json()["jobId"]) == "completed"
    [row] = items_of(env, res.json()["meetingId"])
    assert row.assignee_id is None and row.title == "담당 불명 일" and row.evidence_start_sec is None
    with env["factory"]() as s:
        created = s.scalars(select(Event).where(Event.event_type == "item.created")).one()
        assert created.payload["assignee_name"] == ""


# ---------------- 유형별 프롬프트 ----------------
def test_regular_types_are_identical_to_current_prompt():
    base = prompts.render_chunk_prompt("본문", HELD, agenda_title="A", chunk_index=1, chunk_total=2)
    for kind in ("regular", "irregular", "other", "", "unknown-type"):
        assert prompts.render_chunk_prompt("본문", HELD, agenda_title="A", chunk_index=1, chunk_total=2, meeting_type=kind) == base
    # 현행 추출 프롬프트(골든) 불변
    assert prompts.render_base_prompt("본문", HELD) == extractor_module.build_prompt("본문", HELD)
    assert prompts.BASE_TEMPLATE == extractor_module.PROMPT_TEMPLATE


def test_project_and_external_add_a_paragraph_without_changing_schema():
    base = prompts.render_chunk_prompt("본문", HELD)
    project = prompts.render_chunk_prompt("본문", HELD, meeting_type="project")
    external = prompts.render_chunk_prompt("본문", HELD, meeting_type="external")
    assert project != base and external != base and project != external
    assert "마일스톤" in project and "담당" in project and "기한" in project
    assert "고객" in external and "견적" in external and "계약" in external and "후속 요청" in external
    assert "주요 논의사항" in external and "결정사항" in external
    for text in (project, external):
        assert prompts.RESPONSE_FORMAT in text and text.endswith("전사문:\n본문")  # 출력 스키마·전사문 위치 불변
        for field in ("purpose", "discussion", "decisions", "risks", "next_agenda"):
            assert field in text
    assert prompts.META_TYPE_NOTES.keys() == {"project", "external"}
    assert len({prompt_version_of(k) for k in ("", "regular", "project", "external")}) == 3


def test_meeting_type_from_classification_reaches_the_prompt(env, mgr, local):
    srv = local([(200, chunk_reply([], purpose="x"), 0)])
    res = post_txt(env, mgr, fake_transcript().encode("utf-8"))
    with env["factory"]() as s:
        meeting = s.get(Meeting, res.json()["meetingId"])
        s.add(MeetingClassification(tenant_id=meeting.tenant_id, meeting_id=meeting.id, meeting_type="external", created_by=mgr.id))
        s.commit()
    assert run(env, res.json()["jobId"]) == "completed"
    assert "외부 미팅" in srv.requests[0]["body"]["messages"][1]["content"]
    # 분류가 없으면(미지정) 추가 문단이 없다
    srv2 = local([(200, chunk_reply([], purpose="x"), 0)])
    res2 = post_txt(env, mgr, fake_transcript().encode("utf-8"))
    assert run(env, res2.json()["jobId"]) == "completed"
    content = srv2.requests[0]["body"]["messages"][1]["content"]
    assert "외부 미팅" not in content and "프로젝트 회의" not in content


# ---------------- 엔진 표시·기존 응답 ----------------
def test_engine_endpoint_adds_fields_only(env, mgr, local):
    body = env["client"].get("/api/system/engine", headers=auth(mgr)).json()
    assert set(body) == {"engine", "isFake", "processingMode", "queuePending", "queueProcessing", "llmProvider", "llmModel"}
    assert body["engine"] == "fake" and body["llmProvider"] == "fake" and body["llmModel"] == ""
    srv = local([])
    body = env["client"].get("/api/system/engine", headers=auth(mgr)).json()
    assert body["llmProvider"] == "local" and body["llmModel"] == MODEL and body["engine"] == "fake"
    text = json.dumps(body)
    assert srv.base_url not in text and SECRET not in text and "127.0.0.1" not in text


def test_default_settings_keep_existing_extractor_selection(env, mgr, monkeypatch):
    """LLM_PROVIDER 기본(auto)이면 기존처럼 STT_PROVIDER 를 따른다: fake 추출기가 쓰이고 로컬 호출이 없다."""
    assert settings.llm_provider == "auto"
    res = upload(env, mgr)
    assert run(env, res.json()["jobId"]) == "completed"
    assert len(items_of(env, res.json()["meetingId"])) == 2
    assert minutes_of(env, res.json()["meetingId"]).engine == "fake"
    assert detail(env, mgr, res.json()["meetingId"])["processingWarnings"] == []
