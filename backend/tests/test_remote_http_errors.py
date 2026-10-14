"""원격(OpenAI 호환) 엔진의 HTTP 400 처리(작업 B-1d): 응답 본문 기록·마스킹, 맥락 길이 초과 시 구간 나눔, 옵션 미지원 재요청, 기타 400.
127.0.0.1 임시 포트의 가짜 서버와 가짜 클라이언트만 쓴다(실제 원격·Gemini 호출 없음)."""
import json
from datetime import datetime, timezone

import pytest
from sqlalchemy import select

from app.config import settings
from app.models import Event
from app.pipeline import chunked_extract as ce
from app.pipeline.fakes import fake_transcript
from app.pipeline.engine_profile import EngineProfile
from app.pipeline.local_llm import (
    DETAIL_MAX_CHARS, PERMANENT, LocalLlmError, OpenAICompatClient, classify_bad_request, mask_detail,
)
from app.pipeline.prompts import render_chunk_prompt
from app.services import engine_test
from app.services.processing import process_meeting
from app.services.processing_warnings import DETAIL_EVENT
from tests.test_engine_test_mode import (  # noqa: F401  (픽스처 포함)
    MODEL, SECRET, chunk_reply, items_of, job_of, mgr, post_txt, remote, staff,
)
from tests.test_local_llm import FakeServer, ok_body
from tests.test_upload_processing import auth, env  # noqa: F401

MESSAGES = [{"role": "user", "content": "비밀 전사 본문 XYZ"}]
CONTEXT_BODY = {"error": {"message": "Requested tokens (9100) exceed context window of 8192", "type": "invalid_request_error", "code": 400}}
OPTION_BODY = {"error": {"message": "response_format json_object is not supported by this model"}}
OTHER_BODY = {"error": {"message": "invalid request: temperature out of range"}}
HELD_AT = datetime(2026, 10, 14, 9, 0, tzinfo=timezone.utc)


@pytest.fixture
def server():
    created: list[FakeServer] = []

    def make(script):
        srv = FakeServer(script)
        created.append(srv)
        return srv

    yield make
    for srv in created:
        srv.close()


def client_for(srv, **kw) -> OpenAICompatClient:
    return OpenAICompatClient(srv.base_url, kw.pop("model", MODEL), **kw)


# ---------------- 본문 분류·마스킹 ----------------
@pytest.mark.parametrize("body, kind", [
    ('{"error": {"message": "Requested tokens (9100) exceed context window of 8192"}}', "context"),
    ('{"detail": "maximum context length is 8192 tokens"}', "context"),
    ('{"error": "n_ctx too small"}', "context"),
    ('{"error": "response_format unsupported"}', "option"),
    ('{"error": {"message": "invalid request: temperature out of range"}}', "other"),
    ("{}", "empty"),
    ("", "empty"),
])
def test_classify_bad_request(body, kind):
    assert classify_bad_request(body) == kind


def test_mask_detail_hides_key_header_and_address_and_caps_length():
    base = "http://10.20.30.40:8080/v1"
    text = f"error at {base}/chat/completions host 10.20.30.40:8080 key {SECRET} Authorization: Bearer abc.DEF-123 " + "가" * 900
    masked = mask_detail(text, base_url=base, api_key=SECRET)
    assert SECRET not in masked and "10.20.30.40" not in masked and "abc.DEF-123" not in masked and "http://" not in masked
    assert len(masked) <= DETAIL_MAX_CHARS


# ---------------- 클라이언트: 400 분기 ----------------
def test_context_400_is_not_retried_and_keeps_body(server):
    srv = server([(400, CONTEXT_BODY, 0), (200, ok_body("never"), 0)])
    with pytest.raises(LocalLlmError) as info:
        client_for(srv).complete(MESSAGES, json_mode=True, timeout=5)
    assert len(srv.requests) == 1  # 옵션 문제가 아니므로 같은 요청을 다시 보내지 않는다
    assert info.value.kind == PERMANENT and info.value.reason == "context_exceeded"
    assert "exceed context window" in info.value.detail and info.value.job_error_code == "llm_context_exceeded"
    assert "비밀 전사 본문" not in str(info.value) + info.value.detail  # 요청 본문은 남기지 않는다


def test_option_400_retries_once_without_response_format(server):
    srv = server([(400, OPTION_BODY, 0), (200, ok_body("ok"), 0)])
    assert client_for(srv).complete(MESSAGES, json_mode=True, timeout=5) == "ok"
    assert len(srv.requests) == 2
    assert "response_format" in srv.requests[0]["body"] and "response_format" not in srv.requests[1]["body"]


def test_option_retry_that_hits_context_400_stops_there(server):
    srv = server([(400, OPTION_BODY, 0), (400, CONTEXT_BODY, 0), (200, ok_body("never"), 0)])
    with pytest.raises(LocalLlmError) as info:
        client_for(srv).complete(MESSAGES, json_mode=True, timeout=5)
    assert len(srv.requests) == 2 and info.value.reason == "context_exceeded"


def test_other_400_is_not_retried_and_records_reason(server):
    srv = server([(400, OTHER_BODY, 0), (200, ok_body("never"), 0)])
    with pytest.raises(LocalLlmError) as info:
        client_for(srv).complete(MESSAGES, json_mode=True, timeout=5)
    assert len(srv.requests) == 1 and info.value.reason == "http_4xx" and info.value.kind == PERMANENT
    assert "temperature out of range" in info.value.detail and info.value.job_error_code == "llm_error"


def test_normal_200_returns_content(server):
    srv = server([(200, ok_body('{"items": []}'), 0)])
    assert client_for(srv).complete(MESSAGES, json_mode=True, timeout=5) == '{"items": []}'


def test_error_body_is_masked_in_exception_detail(server):
    srv = server([])
    leak = f"bad request to {srv.base_url}/chat/completions with Bearer {SECRET} key {SECRET}"
    srv.script.append((400, {"error": {"message": leak}}, 0))
    with pytest.raises(LocalLlmError) as info:
        client_for(srv, api_key=SECRET).complete(MESSAGES, json_mode=False, timeout=5)
    detail = info.value.detail
    assert SECRET not in detail and "127.0.0.1" not in detail and srv.base_url not in detail and len(detail) <= DETAIL_MAX_CHARS


def test_model_name_keeps_case_and_only_trims_spaces(remote):
    remote([], model="  Qwen2.5-7B-Instruct-Q4_K_M.gguf  ")
    assert engine_test.load_remote_settings().model == "Qwen2.5-7B-Instruct-Q4_K_M.gguf"
    srv = FakeServer([(200, ok_body("ok"), 0)])
    try:
        OpenAICompatClient(srv.base_url, engine_test.load_remote_settings().model).complete(MESSAGES, json_mode=False, timeout=5)
        assert srv.requests[0]["body"]["model"] == "Qwen2.5-7B-Instruct-Q4_K_M.gguf"
    finally:
        srv.close()


# ---------------- 맥락 길이 자동 계산 ----------------
def test_budget_keeps_prompt_chunk_and_output_within_90_percent_of_context():
    fixed = ce.fixed_prompt_chars("")
    profile = EngineProfile(context_tokens=8192, max_output_tokens=4096)
    assert profile.output_reserve_tokens() == 2048  # 설정 4096 이 맥락의 1/4 보다 크면 줄인다(고정 지시만으로 맥락이 차는 것을 막음)
    assert EngineProfile(context_tokens=8192, max_output_tokens=1000).output_reserve_tokens() == 1000
    assert EngineProfile(context_tokens=1024, max_output_tokens=4096).output_reserve_tokens() == 512
    auto = ce.safe_chunk_chars(profile, fixed)
    assert ce.MIN_AUTO_CHUNK_CHARS < auto < settings.extract_chunk_max_chars  # 기본 6000자보다 훨씬 작다
    used = profile.tokens_of_chars(fixed) + profile.tokens_of_chars(auto) + profile.output_reserve_tokens()
    assert used <= 8192 * 0.9


def test_budget_follows_profile_values():
    fixed = ce.fixed_prompt_chars("")
    base = EngineProfile(context_tokens=8192, max_output_tokens=4096)
    assert ce.safe_chunk_chars(EngineProfile(context_tokens=32768, max_output_tokens=4096), fixed) > ce.safe_chunk_chars(base, fixed)
    assert ce.safe_chunk_chars(EngineProfile(context_tokens=8192, max_output_tokens=4096, tokens_per_char=2.0), fixed) < ce.safe_chunk_chars(base, fixed)
    assert ce.safe_chunk_chars(EngineProfile(context_tokens=8192, max_output_tokens=1000), fixed) > ce.safe_chunk_chars(base, fixed)
    assert ce.safe_chunk_chars(EngineProfile(context_tokens=1024, max_output_tokens=4096), fixed) == ce.MIN_AUTO_CHUNK_CHARS  # 하한


def test_remote_profile_reads_settings_and_changes_chunk_size(remote, monkeypatch):
    remote([])
    small = engine_test.make_remote_extractor("", allow_external=True)._options.max_chars
    monkeypatch.setattr(settings, "remote_llm_context_tokens", 32768)
    assert engine_test.remote_profile().context_tokens == 32768
    assert engine_test.make_remote_extractor("", allow_external=True)._options.max_chars > small
    monkeypatch.setattr(settings, "remote_llm_json_format", False)
    assert engine_test.remote_profile().supports_json_format is False


def test_remote_extractor_uses_smaller_of_setting_and_auto(remote):
    remote([])
    expected_auto = ce.safe_chunk_chars(engine_test.remote_profile(), ce.fixed_prompt_chars(""))
    extractor = engine_test.make_remote_extractor("", allow_external=True)
    assert extractor._options.max_chars == min(settings.extract_chunk_max_chars, expected_auto)
    assert extractor._options.overlap_chars <= extractor._options.max_chars // 4
    engine_test.save_remote_settings(
        base_url=engine_test.load_remote_settings().base_url, model=MODEL, api_key=None, timeout_seconds=10, chunk_max_chars=500
    )
    assert engine_test.make_remote_extractor("", allow_external=True)._options.max_chars == min(500, expected_auto)


def test_remote_request_uses_reduced_max_tokens_and_json_profile(remote, monkeypatch):
    srv = remote([(200, chunk_reply([]), 0), (200, chunk_reply([]), 0)])
    client = engine_test.build_remote_client(engine_test.load_remote_settings(), allow_external=True)
    client.complete(MESSAGES, json_mode=True, timeout=5)
    assert srv.requests[0]["body"]["max_tokens"] == engine_test.remote_output_tokens() == 2048
    assert srv.requests[0]["body"]["response_format"] == {"type": "json_object"}
    monkeypatch.setattr(settings, "remote_llm_json_format", False)  # 프로파일이 JSON 형식 미지원이면 처음부터 보내지 않는다
    engine_test.build_remote_client(engine_test.load_remote_settings(), allow_external=True).complete(MESSAGES, json_mode=True, timeout=5)
    assert "response_format" not in srv.requests[1]["body"]


# ---------------- 조용한 잘림 감지(응답의 사용 토큰·finish_reason) ----------------
def usage_body(text="{}", *, prompt_tokens=None, finish="stop", total=None):
    body = ok_body(text)
    body["choices"][0]["finish_reason"] = finish
    usage = {}
    if prompt_tokens is not None:
        usage["prompt_tokens"] = prompt_tokens
    if total is not None:
        usage["total_tokens"] = total
    if usage:
        body["usage"] = usage
    return body


def test_usage_at_98_percent_of_context_is_flagged_as_truncation_suspect(server):
    srv = server([(200, usage_body(prompt_tokens=8030), 0)])  # 8192 * 0.98 = 8028.2
    with pytest.raises(LocalLlmError) as info:
        client_for(srv, context_tokens=8192).complete(MESSAGES, json_mode=False, timeout=5)
    assert info.value.reason == "context_limit_reached" and info.value.kind == PERMANENT
    assert info.value.job_error_code == "llm_context_exceeded" and "prompt_tokens=8030" in info.value.detail


def test_usage_below_threshold_passes(server):
    srv = server([(200, usage_body("ok", prompt_tokens=8000), 0)])
    assert client_for(srv, context_tokens=8192).complete(MESSAGES, json_mode=False, timeout=5) == "ok"


def test_finish_reason_length_is_output_truncation(server):
    srv = server([(200, usage_body("잘린 응", prompt_tokens=100, finish="length"), 0)])
    with pytest.raises(LocalLlmError) as info:
        client_for(srv, context_tokens=8192).complete(MESSAGES, json_mode=False, timeout=5)
    assert info.value.reason == "output_truncated" and info.value.job_error_code == "llm_output_truncated"


def test_missing_usage_skips_check_and_is_counted(server):
    srv = server([(200, usage_body("ok"), 0)])
    client = client_for(srv, context_tokens=8192)
    assert client.complete(MESSAGES, json_mode=False, timeout=5) == "ok"
    assert client.usage_missing == 1


def test_unknown_context_skips_usage_check(server):
    srv = server([(200, usage_body("ok", prompt_tokens=999999), 0)])
    assert client_for(srv).complete(MESSAGES, json_mode=False, timeout=5) == "ok"


# ---------------- 구간 나눔 재요청(가짜 클라이언트) ----------------
class ContextLimitedClient:
    """프롬프트가 limit 글자를 넘으면(또는 always) 맥락 초과 400 을 흉내 낸다. 호출 횟수를 센다."""

    def __init__(self, limit: int, always: bool = False):
        self.limit, self.always, self.calls = limit, always, 0

    def complete(self, messages, json_mode, timeout):
        self.calls += 1
        if self.always or len(messages[-1]["content"]) > self.limit:
            raise LocalLlmError("맥락 초과", kind=PERMANENT, reason="context_exceeded", detail="exceed context window of 8192")
        return json.dumps({"items": [], "minutes": {"purpose": "구간 요약"}}, ensure_ascii=False)


def long_transcript(lines: int = 12) -> str:
    return "\n".join(f"[00:{i // 60:02d}:{i % 60:02d}] 화자1: " + ("가나다라마바사아자차카타파하 " * 10) for i in range(lines))


def options(**over) -> ce.ChunkOptions:
    base = ce.ChunkOptions.from_settings()
    return ce.ChunkOptions(**{**base.__dict__, "max_chars": 100000, "overlap_chars": 10, "retry_max": 0, **over})


def test_context_400_splits_chunk_in_halves_until_it_fits():
    full = len(render_chunk_prompt(long_transcript(), HELD_AT))
    fixed = len(render_chunk_prompt("", HELD_AT))
    client = ContextLimitedClient(limit=fixed + (full - fixed) // 2 + 80)  # 절반이면 들어가는 크기
    result = ce.extract_chunked(long_transcript(), HELD_AT, client, options())
    assert not result.all_failed
    assert client.calls == 3  # 원래 구간 1회(실패) + 절반 2회
    assert any("구간을 나눠 다시 요청" in w for w in result.warnings)
    assert result.chunks[0].status == ce.STATUS_OK and result.chunks[0].calls == 3
    assert result.error_details == ["exceed context window of 8192"]


class RaisingClient:
    """호출마다 같은 사유(reason)로 실패하거나, 큰 요청만 실패한다."""

    def __init__(self, reason: str, *, limit: int | None = None):
        self.reason, self.limit, self.calls = reason, limit, 0

    def complete(self, messages, json_mode, timeout):
        self.calls += 1
        if self.limit is None or len(messages[-1]["content"]) > self.limit:
            raise LocalLlmError("x", kind=PERMANENT, reason=self.reason, detail="finish_reason=length, prompt_tokens=8100")
        return json.dumps({"items": [], "minutes": {"purpose": "요약"}}, ensure_ascii=False)


def test_truncation_suspect_splits_like_context_error():
    client = RaisingClient("context_limit_reached", limit=4000)
    result = ce.extract_chunked(long_transcript(), HELD_AT, client, options())
    assert not result.all_failed and client.calls >= 3
    assert any("구간을 나눠 다시 요청" in w for w in result.warnings)


def test_unresolved_truncation_fails_chunk_with_reason_and_is_bounded():
    stuck = RaisingClient("output_truncated")
    out = ce.extract_chunked(long_transcript(), HELD_AT, stuck, options())
    assert out.all_failed and stuck.calls == 1 + 2 + 4
    assert any("잘렸을 수 있어" in w for w in out.warnings)


def test_split_retries_are_capped_per_chunk_and_in_total():
    client = ContextLimitedClient(0, always=True)
    result = ce.extract_chunked(long_transcript(), HELD_AT, client, options())
    assert result.all_failed and client.calls == 1 + 2 + 4  # 깊이 2: 최대 4조각
    assert any("맥락 길이를 초과" in w for w in result.warnings)
    # 구간이 여러 개여도 나눔은 전체 8회까지만 늘어난다
    many = ContextLimitedClient(0, always=True)
    out = ce.extract_chunked(long_transcript(30), HELD_AT, many, options(max_chars=600, overlap_chars=10))
    assert out.all_failed and len(out.chunks) > 4
    assert many.calls <= len(out.chunks) + ce.CONTEXT_SPLIT_BUDGET


def test_single_long_utterance_is_split_by_text():
    one = "[00:00:01] 화자1: " + ("가나다라마바사아자차카타파하. " * 40)
    fixed = len(render_chunk_prompt("", HELD_AT))
    client = ContextLimitedClient(limit=fixed + len(one) // 2 + 120)
    result = ce.extract_chunked(one, HELD_AT, client, options())
    assert not result.all_failed and client.calls >= 3


# ---------------- 처리 흐름(가짜 원격 서버): 이력 저장·화면 노출 ----------------
def run(env, job_id):
    return process_meeting(job_id, session_factory=env["factory"])


def details_of(env, job_id) -> list[str]:
    with env["factory"]() as s:
        rows = s.scalars(select(Event).where(Event.entity_type == "job", Event.entity_id == job_id, Event.event_type == DETAIL_EVENT)).all()
    return [str(r.payload["detail"]) for r in rows]


EIGHT_LINES = "\n".join(f"[00:00:{i:02d}] 화자{i % 2 + 1}: 이것은 {i}번째 발언 내용입니다. " + "내용 " * 10 for i in range(1, 9))


def test_context_400_on_remote_splits_and_completes(env, mgr, remote):
    srv = remote([(400, CONTEXT_BODY, 0), (200, chunk_reply([], purpose="앞부분"), 0), (200, chunk_reply([], purpose="뒷부분"), 0)])
    res = post_txt(env, mgr, EIGHT_LINES.encode("utf-8"), engine="remote")
    assert run(env, res.json()["jobId"]) == "completed"
    assert len(srv.requests) == 3
    first_len = len(srv.requests[0]["body"]["messages"][-1]["content"])
    assert all(len(r["body"]["messages"][-1]["content"]) < first_len for r in srv.requests[1:])
    detail = env["client"].get(f"/api/meetings/{res.json()['meetingId']}", headers=auth(mgr)).json()
    assert any("구간을 나눠 다시 요청" in w for w in detail["processingWarnings"])


def test_other_400_fails_records_masked_body_and_shows_to_manager(env, mgr, remote):
    srv = remote([])
    leak = f"bad request at {srv.base_url}/chat/completions Bearer {SECRET} key {SECRET} 맥락과 무관한 사유"
    srv.script.extend([(400, {"error": {"message": leak}}, 0)] * 6)
    res = post_txt(env, mgr, fake_transcript().encode("utf-8"), engine="remote")
    assert run(env, res.json()["jobId"]) == "failed"
    assert len(srv.requests) == 1  # 재시도 없음, 자동 전환 없음
    assert job_of(env, res.json()["meetingId"]).error_code == "llm_error"
    assert items_of(env, res.json()["meetingId"]) == []
    saved = details_of(env, res.json()["jobId"])
    assert len(saved) == 1 and len(saved[0]) <= DETAIL_MAX_CHARS
    assert SECRET not in saved[0] and "127.0.0.1" not in saved[0] and "맥락과 무관한 사유" in saved[0]
    body = env["client"].get(f"/api/meetings/{res.json()['meetingId']}", headers=auth(mgr)).json()
    assert body["llmErrorDetails"] == saved and DETAIL_EVENT not in json.dumps(body)  # 사건 목록(history)에는 나오지 않는다
    assert body["processing"]["errorCode"] == "llm_error"


def test_context_400_everywhere_fails_with_context_code_and_bounded_requests(env, mgr, remote):
    srv = remote([(400, CONTEXT_BODY, 0)] * 30)
    res = post_txt(env, mgr, EIGHT_LINES.encode("utf-8"), engine="remote")
    assert run(env, res.json()["jobId"]) == "failed"
    assert len(srv.requests) <= 1 + 2 + 4  # 구간당 깊이 2(최대 7회)
    assert job_of(env, res.json()["meetingId"]).error_code == "llm_context_exceeded"
    assert details_of(env, res.json()["jobId"])


def test_error_body_is_hidden_from_staff(env, mgr, staff, remote, monkeypatch):
    monkeypatch.setattr(settings, "engine_test_default", "remote")
    srv = remote([])
    srv.script.extend([(400, {"error": {"message": "사유 문구 ABC"}}, 0)] * 6)
    res = post_txt(env, staff, fake_transcript().encode("utf-8"))
    assert run(env, res.json()["jobId"]) == "failed"
    assert details_of(env, res.json()["jobId"])  # 이력에는 있다
    own = env["client"].get(f"/api/meetings/{res.json()['meetingId']}", headers=auth(staff))
    assert own.status_code == 200 and own.json()["llmErrorDetails"] == []
