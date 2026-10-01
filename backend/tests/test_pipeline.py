"""4a단계: stub 에서 이식한 STT·업무 추출 순수 모듈 테스트.
네트워크·실제 키 없이 돈다: Gemini 클라이언트는 가짜 객체로 주입하고, 키는 가짜 문자열만 쓴다."""
import json
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from google.genai import errors as genai_errors
from pydantic import SecretStr

from app.config import GeminiKeyMissingError, settings
from app.pipeline import errors as pipeline_errors
from app.pipeline.dates import resolve_due_date
from app.pipeline.extractor import (
    FAKE_PROVENANCE,
    PROMPT_TEMPLATE,
    PROMPT_VERSION,
    ActionItemCandidate,
    ActionItemList,
    ExtractionError,
    FakeExtractor,
    GeminiExtractor,
    build_prompt,
    make_gemini_extractor,
    parse_response,
    postprocess,
    response_json_schema,
)
from app.pipeline.fakes import fake_transcript
from app.pipeline.gemini_client import make_gemini_client
from app.pipeline.stt import NO_SPEECH, FakeStt, GeminiFileError, GeminiStt, is_no_speech, make_gemini_stt

KST = timezone(timedelta(hours=9))
HELD_AT = datetime(2026, 9, 29, 14, 0, tzinfo=KST)  # 화요일
M4A = b"\x00\x00\x00\x18ftypM4A \x00\x00\x00\x00" + b"\x00" * 1000
FREE_KEY = "free-test-key-DO-NOT-LEAK-1111"
PAID_KEY = "paid-test-key-DO-NOT-LEAK-2222"


def set_keys(monkeypatch, mode: str, free: str = "", paid: str = "") -> None:
    monkeypatch.setattr(settings, "gemini_key_mode", mode)
    monkeypatch.setattr(settings, "gemini_api_key", SecretStr(free))
    monkeypatch.setattr(settings, "gemini_paid_api_key", SecretStr(paid))


# ---------------- 가짜 Gemini 클라이언트 ----------------
class FakeFiles:
    def __init__(self, states):
        self.states = list(states)
        self.deleted: list[str] = []
        self.upload_args = None

    def upload(self, *, file, config):
        self.upload_args = (file, config)
        return SimpleNamespace(name="files/abc", state=self.states.pop(0))

    def get(self, *, name):
        return SimpleNamespace(name=name, state=self.states.pop(0))

    def delete(self, *, name):
        self.deleted.append(name)


class FakeModels:
    def __init__(self, text=None, error=None):
        self.text, self.error, self.calls = text, error, []

    def generate_content(self, *, model, contents, config=None):
        self.calls.append({"model": model, "contents": contents, "config": config})
        if self.error:
            raise self.error
        return SimpleNamespace(text=self.text)


def api_error(code: int, status: str, message: str):
    return genai_errors.ClientError(code, {"error": {"code": code, "status": status, "message": message}})


# ---------------- NO_SPEECH ----------------
@pytest.mark.parametrize(
    "transcript",
    ["", "   \n\t ", None, NO_SPEECH, f"  {NO_SPEECH}\n", f"{NO_SPEECH} 음성에 말소리가 없습니다.", f"{NO_SPEECH}\n(배경 소음만 있음)"],
)
def test_no_speech_detected(transcript):
    assert is_no_speech(transcript) is True


@pytest.mark.parametrize("transcript", ["[00:00:01] 화자1: 안녕하세요", fake_transcript()])
def test_real_transcript_is_not_no_speech(transcript):
    assert is_no_speech(transcript) is False


# ---------------- STT ----------------
def test_fake_stt_returns_script():
    stt = FakeStt()
    assert stt.transcribe(None, "audio/mp4") == fake_transcript()
    stt.release()


def test_gemini_stt_waits_for_active_and_deletes_remote_file(tmp_path):
    files, models = FakeFiles(["PROCESSING", "PROCESSING", "ACTIVE"]), FakeModels(text="[00:00:01] 화자1: 안녕하세요\n")
    stt = GeminiStt(SimpleNamespace(files=files, models=models), "model-x", poll_seconds=0)
    audio = tmp_path / "a.m4a"
    audio.write_bytes(M4A)
    assert stt.transcribe(audio, "audio/mp4") == "[00:00:01] 화자1: 안녕하세요"
    assert files.upload_args[1].mime_type == "audio/mp4"
    assert models.calls[0]["model"] == "model-x" and NO_SPEECH in models.calls[0]["contents"][1]
    assert files.deleted == ["files/abc"]


def test_gemini_stt_deletes_remote_file_even_when_generation_fails(tmp_path):
    files = FakeFiles(["ACTIVE"])
    stt = GeminiStt(SimpleNamespace(files=files, models=FakeModels(error=api_error(429, "RESOURCE_EXHAUSTED", "q"))), "m")
    with pytest.raises(genai_errors.ClientError):
        stt.transcribe(tmp_path / "a.m4a", "audio/mp4")
    assert files.deleted == ["files/abc"]


def test_gemini_stt_failed_file_state_raises(tmp_path):
    files = FakeFiles(["FAILED"])
    stt = GeminiStt(SimpleNamespace(files=files, models=FakeModels(text="x")), "m")
    with pytest.raises(GeminiFileError):
        stt.transcribe(tmp_path / "a.m4a", "audio/mp4")
    assert files.deleted == ["files/abc"]


def test_gemini_stt_times_out_waiting_for_active(tmp_path):
    files = FakeFiles(["PROCESSING"] * 5)
    stt = GeminiStt(SimpleNamespace(files=files, models=FakeModels(text="x")), "m", poll_seconds=0, max_wait_seconds=0)
    with pytest.raises(TimeoutError):
        stt.transcribe(tmp_path / "a.m4a", "audio/mp4")
    assert files.deleted == ["files/abc"]


# ---------------- 상대 날짜 ----------------
@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("2026-10-09", "2026-10-09"),
        ("오늘", "2026-09-29"),
        ("내일까지", "2026-09-30"),
        ("모레", "2026-10-01"),
        ("이번 주 목요일", "2026-10-01"),
        ("이번주 월요일까지", "2026-09-28"),  # 같은 주(월~일)의 지난 요일
        ("다음 주 금요일", "2026-10-09"),
        ("차주 월", "2026-10-05"),
        ("다음 주", ""),  # 요일 없음 → 짐작하지 않음
        ("다음 달", ""),
        ("2026-13-45", ""),
        ("2026/10/09", ""),
        ("", ""),
    ],
)
def test_resolve_due_date(text, expected):
    assert resolve_due_date(text, date(2026, 9, 29)) == expected


def test_relative_date_uses_week_of_base_date_on_sunday():
    # 일요일 회의: '이번 주 목요일'은 지나간 목요일(같은 주), '다음 주 금요일'은 바로 다음 주
    sunday = date(2026, 10, 4)
    assert resolve_due_date("이번 주 목요일", sunday) == "2026-10-01"
    assert resolve_due_date("다음 주 금요일", sunday) == "2026-10-09"


# ---------------- 후처리 ----------------
def test_postprocess_normalizes_dates_drops_empty_tasks_and_fills_empty_strings():
    raw = json.dumps({"items": [
        {"task": "좋은 항목", "assignee": "이서연", "due_date": "2026-10-09",
         "quote": {"speaker": "김도현", "timestamp": "00:00:03", "text": "원문"}},
        {"task": "날짜가 말로 옴", "assignee": "정민수", "due_date": "다음 주 금요일", "quote": None},
        {"task": "없는 날짜", "assignee": None, "due_date": "2026-13-45"},
        {"task": "슬래시 날짜", "due_date": "2026/10/09", "quote": {"speaker": None}},
        {"task": "   ", "assignee": "누군가", "due_date": "2026-10-01"},
        {"assignee": "task 누락"},
    ]}, ensure_ascii=False)
    items = postprocess(ActionItemList.model_validate_json(raw), HELD_AT)
    assert [i.task for i in items] == ["좋은 항목", "날짜가 말로 옴", "없는 날짜", "슬래시 날짜"]
    assert [i.due_date for i in items] == ["2026-10-09", "2026-10-09", "", ""]  # 상대 표현은 회의일 기준 계산
    assert items[2].assignee == ""  # None → ""
    assert (items[1].quote_speaker, items[1].quote_timestamp, items[1].quote_text) == ("", "", "")
    assert items[3].assignee == "" and items[3].quote_speaker == ""  # 누락 → ""
    assert all(
        v is not None for i in items for v in (i.task, i.assignee, i.due_date, i.quote_speaker, i.quote_timestamp, i.quote_text)
    )


def test_assignee_blank_and_whitespace_become_empty_string():
    raw = json.dumps({"items": [
        {"task": "a", "assignee": "  ", "due_date": "", "quote": {}},
        {"task": "b", "assignee": " 권 부장 ", "due_date": "", "quote": {}},
    ]}, ensure_ascii=False)
    items = parse_response(raw, HELD_AT)
    assert [i.assignee for i in items] == ["", "권 부장"]


def test_quote_is_one_to_one_with_timestamp_seconds():
    raw = json.dumps({"items": [
        {"task": "a", "assignee": "x", "due_date": "", "quote": {"speaker": "화자1", "timestamp": "01:02:03", "text": "가"}},
        {"task": "b", "assignee": "y", "due_date": "", "quote": {"speaker": "화자2", "timestamp": "12:34", "text": "나"}},
    ]}, ensure_ascii=False)
    items = parse_response(raw, HELD_AT)
    assert [(i.quote_speaker, i.quote_timestamp, i.quote_text) for i in items] == [("화자1", "01:02:03", "가"), ("화자2", "12:34", "나")]
    assert [i.evidence_start_sec for i in items] == [3723.0, None]  # 형식이 틀리면 None


def test_one_utterance_with_multiple_tasks_stays_split():
    """한 발화에서 나온 업무 여러 개는 같은 인용을 가져도 합치지 않고 각각 남긴다."""
    quote = {"speaker": "화자3", "timestamp": "00:05:10",
             "text": "EZ 견적은 오늘 보내고, 이지트 계약서는 이번 주 금요일까지 등록하겠습니다."}
    raw = json.dumps({"items": [
        {"task": "EZ 견적서 발송", "assignee": "화자3", "due_date": "오늘", "quote": quote},
        {"task": "이지트 계약서 등록", "assignee": "화자3", "due_date": "이번 주 금요일", "quote": quote},
    ]}, ensure_ascii=False)
    items = parse_response(raw, HELD_AT)
    assert [i.task for i in items] == ["EZ 견적서 발송", "이지트 계약서 등록"]
    assert [i.due_date for i in items] == ["2026-09-29", "2026-10-02"]
    assert {i.quote_text for i in items} == {quote["text"]}
    assert all(i.evidence_start_sec == 310.0 for i in items)


def test_null_items_becomes_empty_list():
    assert postprocess(ActionItemList.model_validate_json('{"items": null}'), HELD_AT) == []


def test_invalid_json_raises_extraction_error():
    with pytest.raises(ExtractionError):
        parse_response("not json", HELD_AT)


def test_response_schema_is_inlined_and_requires_every_field():
    schema = response_json_schema()
    assert "$ref" not in json.dumps(schema) and "$defs" not in schema
    item = schema["properties"]["items"]["items"]
    assert set(item["required"]) == {"task", "assignee", "due_date", "quote"}
    assert set(item["properties"]["quote"]["required"]) == {"speaker", "timestamp", "text"}


# ---------------- 추출기 ----------------
def test_fake_extractor_returns_two_items_with_fake_provenance():
    result = FakeExtractor().extract(fake_transcript(), HELD_AT)
    assert [(i.task, i.assignee, i.due_date) for i in result.items] == [
        ("STT 화자 분리 정확도 개선안 정리", "이서연", "2026-10-09"),
        ("디자인 시안 확정", "정민수", "2026-10-01"),
    ]
    assert result.extract_model == FAKE_PROVENANCE and result.prompt_version == FAKE_PROVENANCE
    assert result.extracted_at is not None and result.extracted_at.tzinfo is not None
    assert all(isinstance(i, ActionItemCandidate) for i in result.items)


def test_gemini_extractor_uses_json_schema_records_provenance_and_postprocesses():
    text = json.dumps({"items": [
        {"task": "개선안 정리", "assignee": "이서연", "due_date": "다음 주 금요일",
         "quote": {"speaker": "김도현", "timestamp": "00:00:03", "text": "원문"}},
        {"task": "", "assignee": "x", "due_date": "", "quote": {"speaker": "", "timestamp": "", "text": ""}},
    ]}, ensure_ascii=False)
    models = FakeModels(text=text)
    result = GeminiExtractor(SimpleNamespace(models=models), "test-model").extract("전사", HELD_AT)

    assert result.items == [ActionItemCandidate("개선안 정리", "이서연", "2026-10-09", "김도현", "00:00:03", "원문", 3.0)]
    assert result.extract_model == "test-model" and result.prompt_version == PROMPT_VERSION
    assert result.extracted_at is not None and result.extracted_at.tzinfo is not None
    config = models.calls[0]["config"]
    assert config.response_mime_type == "application/json" and config.response_json_schema == response_json_schema()
    assert "2026-09-29T14:00:00+09:00 (화요일)" in models.calls[0]["contents"]


def test_make_gemini_extractor_uses_model_from_settings(monkeypatch):
    monkeypatch.setattr(settings, "gemini_model", "model-from-settings")
    models = FakeModels(text='{"items": []}')
    result = make_gemini_extractor(SimpleNamespace(models=models)).extract("전사", HELD_AT)
    assert models.calls[0]["model"] == "model-from-settings" and result.extract_model == "model-from-settings"


def test_make_gemini_stt_uses_model_from_settings(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "gemini_model", "model-from-settings")
    files, models = FakeFiles(["ACTIVE"]), FakeModels(text="[00:00:01] 화자1: 네")
    make_gemini_stt(SimpleNamespace(files=files, models=models)).transcribe(tmp_path / "a.m4a", "audio/mp4")
    assert models.calls[0]["model"] == "model-from-settings"


# ---------------- 프롬프트 ----------------
def test_prompt_version_is_sha256_prefix_of_fixed_template():
    import hashlib

    assert PROMPT_VERSION == hashlib.sha256(PROMPT_TEMPLATE.encode("utf-8")).hexdigest()[:12]
    assert "{transcript}" in PROMPT_TEMPLATE and "{started_at}" in PROMPT_TEMPLATE


def test_prompt_is_identical_to_stub_v1_10_1():
    """stub(v1.10.1)과 같은 프롬프트 글자 → 같은 버전. 프롬프트를 바꾸면 이 값도 의도적으로 갱신한다."""
    assert PROMPT_VERSION == "d927151c8ac9"


def test_prompt_states_policies():
    prompt = build_prompt("[00:00:03] 화자1: 테스트", HELD_AT)
    assert "처음부터 끝까지" in prompt and "빠짐없이" in prompt
    assert "확정 약속" in prompt and "지시-수락" in prompt
    for excluded in ("이미 끝난 일", "생각해 보겠습니다", "잡담", "일반론"):
        assert excluded in prompt
    assert "각각 별도 항목" in prompt and "업체·프로젝트·고객명이 다르면 다른 항목" in prompt
    assert 'assignee 를 빈 문자열("")' in prompt and 'null 이 아니라 빈 문자열("")' in prompt
    assert "[6. 마지막 점검]" in prompt and prompt.index("[6. 마지막 점검]") < prompt.index("전사문:\n")
    assert "2026-09-29T14:00:00+09:00 (화요일)" in prompt
    assert prompt.rstrip().endswith("[00:00:03] 화자1: 테스트")


# ---------------- 키 모드 ----------------
@pytest.mark.parametrize(("mode", "expected"), [("free", FREE_KEY), ("paid", PAID_KEY)])
def test_selected_key_follows_mode(monkeypatch, mode, expected):
    set_keys(monkeypatch, mode, free=FREE_KEY, paid=PAID_KEY)
    same = settings.selected_gemini_key() == expected
    assert same is True


@pytest.mark.parametrize(
    ("mode", "free", "paid", "missing_name"),
    [
        ("free", "", PAID_KEY, "GEMINI_API_KEY"),  # 유료 키가 있어도 무료 모드면 무료 키 누락 오류(전환 없음)
        ("paid", FREE_KEY, "", "GEMINI_PAID_API_KEY"),  # 무료 키가 있어도 유료 모드면 유료 키 누락 오류(전환 없음)
    ],
)
def test_missing_key_for_selected_mode_raises_without_fallback(monkeypatch, mode, free, paid, missing_name):
    set_keys(monkeypatch, mode, free=free, paid=paid)
    with pytest.raises(GeminiKeyMissingError) as info:
        settings.selected_gemini_key()
    message = str(info.value)
    assert missing_name in message and f"GEMINI_KEY_MODE={mode}" in message
    leaked = FREE_KEY in message or PAID_KEY in message
    assert leaked is False


def test_make_client_refuses_before_creating_client_when_key_missing(monkeypatch):
    from google import genai

    set_keys(monkeypatch, "paid", free=FREE_KEY, paid="")
    created = []
    monkeypatch.setattr(genai, "Client", lambda **kwargs: created.append(kwargs))
    with pytest.raises(GeminiKeyMissingError):
        make_gemini_client()
    assert created == []


@pytest.mark.parametrize(("mode", "expected"), [("paid", PAID_KEY), ("free", FREE_KEY)])
def test_client_receives_only_the_selected_key(monkeypatch, mode, expected):
    from google import genai

    set_keys(monkeypatch, mode, free=FREE_KEY, paid=PAID_KEY)
    captured: dict = {}
    monkeypatch.setattr(genai, "Client", lambda **kwargs: captured.update(kwargs))
    monkeypatch.setattr(settings, "gemini_timeout_sec", 900.0)
    make_gemini_client()
    same = captured["api_key"] == expected
    assert same is True
    assert captured["http_options"].timeout == 900_000  # 밀리초


def test_default_settings_values():
    from app.config import Settings

    s = Settings(_env_file=None, gemini_api_key="", gemini_paid_api_key="", gemini_key_mode="free")
    assert s.gemini_model == "gemini-3.8-flash" and s.gemini_timeout_sec == 900.0 and s.gemini_key_mode == "free"


# ---------------- 오류 요약·키 가리기 ----------------
def test_429_in_free_mode_suggests_paid_mode_without_key(monkeypatch):
    set_keys(monkeypatch, "free", free=FREE_KEY, paid=PAID_KEY)
    message = pipeline_errors.describe_error(api_error(429, "RESOURCE_EXHAUSTED", f"quota for {FREE_KEY}"))
    assert "무료 키 한도" in message and "GEMINI_KEY_MODE=paid" in message
    leaked = FREE_KEY in message or PAID_KEY in message
    assert leaked is False


def test_429_in_paid_mode_has_no_switch_hint(monkeypatch):
    set_keys(monkeypatch, "paid", free=FREE_KEY, paid=PAID_KEY)
    message = pipeline_errors.describe_error(api_error(429, "RESOURCE_EXHAUSTED", "q"))
    assert "(429)" in message and "GEMINI_KEY_MODE" not in message and "무료 키" not in message


def test_redact_hides_both_keys(monkeypatch):
    set_keys(monkeypatch, "free", free=FREE_KEY, paid=PAID_KEY)
    text = pipeline_errors.redact(f"a={FREE_KEY} b={PAID_KEY} c=AIzaSyFAKE_shape_only_000000000000")
    leaked = FREE_KEY in text or PAID_KEY in text or "AIza" in text
    assert leaked is False and text.count("***") == 3


@pytest.mark.parametrize("mode", ["free", "paid"])
def test_error_message_hides_both_keys_in_either_mode(monkeypatch, mode):
    set_keys(monkeypatch, mode, free=FREE_KEY, paid=PAID_KEY)
    message = pipeline_errors.describe_error(api_error(400, "INVALID_ARGUMENT", f"bad request {FREE_KEY} {PAID_KEY}"))
    leaked = FREE_KEY in message or PAID_KEY in message
    assert leaked is False and "bad request" in message


def test_describe_key_missing_error_names_setting_only(monkeypatch):
    set_keys(monkeypatch, "paid", free=FREE_KEY, paid="")
    try:
        settings.selected_gemini_key()
    except GeminiKeyMissingError as exc:
        message = pipeline_errors.describe_error(exc)
    assert "GEMINI_PAID_API_KEY" in message
    leaked = FREE_KEY in message
    assert leaked is False
