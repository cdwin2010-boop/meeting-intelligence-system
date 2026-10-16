"""안건 분할 추론·3단계 JSON 파서·프롬프트 템플릿(작업 75-2). 가짜 LLM 클라이언트만 쓰며 네트워크·DB 를 쓰지 않는다."""
import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.config import Settings
from app.models.minutes import MINUTES_FIELDS, NO_CONTENT
from app.pipeline import extractor, prompts
from app.pipeline.agenda_chunker import match_agenda_marker, parse_utterances, split_into_chunks
from app.pipeline.chunked_extract import (
    ChunkOptions, ChunkResponse, dedupe_items, extract_chunked, to_extraction_result,
)
from app.pipeline.extractor import ActionItemList, ExtractionResult
from app.pipeline.json_parser import extract_json_object, parse_response, run_with_repair
from app.pipeline.llm_client import FakeLlmClient, LlmClientError

KST = timezone(timedelta(hours=9))
HELD = datetime(2026, 10, 12, 14, 0, tzinfo=KST)  # 월요일
ROOT = Path(__file__).resolve().parent.parent
OPTS = ChunkOptions(max_chars=6000, overlap_chars=500, repair_max_chars=4000, timeout=30, retry_max=1, minutes_max_chars=4000)

# 안건 분할 경로(예산 초과)를 보려는 시험용: 예산 이내면 분할 없이 1회 호출한다(C-1)
OPTS_AGENDA = ChunkOptions(max_chars=200, overlap_chars=20, repair_max_chars=4000, timeout=30, retry_max=1, minutes_max_chars=4000)

TRANSCRIPT = """[00:00:01] 화자1: 오늘 회의를 시작하겠습니다.
[00:00:10] 화자2: 첫 번째 안건은 STT 정확도 개선입니다. 개선안을 다음 주 금요일까지 정리해 주세요.
[00:00:20] 화자3: 네, 제가 다음 주 금요일까지 정리하겠습니다.
[00:00:30] 화자2: 다음 안건으로 넘어가겠습니다. 디자인 시안 확정 건입니다.
[00:00:40] 화자4: 시안은 이번 주 목요일까지 확정하겠습니다."""


def item(task, assignee="", due="", speaker="", ts="", text=""):
    return {"task": task, "assignee": assignee, "due_date": due, "quote": {"speaker": speaker, "timestamp": ts, "text": text}}


def reply(items=(), **minutes):
    return json.dumps({"items": list(items), "minutes": minutes}, ensure_ascii=False)


# ---------------- 프롬프트 ----------------
def test_base_prompt_is_identical_to_current_prompt():
    assert prompts.BASE_TEMPLATE == extractor.PROMPT_TEMPLATE
    for transcript in ("", "[00:00:01] 화자1: 안녕", TRANSCRIPT):
        assert prompts.render_base_prompt(transcript, HELD) == extractor.build_prompt(transcript, HELD)


def test_chunk_prompt_variables_and_instructions():
    text = prompts.render_chunk_prompt(
        "[00:00:01] 화자1: 안녕", HELD, agenda_title="STT 개선", chunk_index=2, chunk_total=3, participants=("김도현", "이서연")
    )
    assert HELD.isoformat() in text and "(월요일)" in text
    assert "참석자: 김도현, 이서연" in text and "구간: 2/3 (안건: STT 개선)" in text
    assert "이 구간의 발언만 근거로 삼습니다" in text and "다른 구간의 내용을 추측하지 않습니다" in text
    assert "글자 그대로 복사합니다" in text and "```json" in text
    assert text.endswith("전사문:\n[00:00:01] 화자1: 안녕")
    assert extractor.PROMPT_TEMPLATE.split("\n전사문:")[0].format(started_at=HELD.isoformat(), weekday="월", transcript="") in text


def test_meeting_type_hook_changes_text_only_for_project_and_external():
    # 작업 81-1: project·external 만 관점 문단이 추가되고 그 밖의 유형은 기존 텍스트와 같다
    a = prompts.render_chunk_prompt("x", HELD, meeting_type="")
    for kind in ("regular", "irregular", "other"):
        assert prompts.render_chunk_prompt("x", HELD, meeting_type=kind) == a
    assert prompts.render_chunk_prompt("x", HELD, meeting_type="project") != a


# ---------------- 분할기 ----------------
def test_explicit_markers_split_and_titles():
    chunks = split_into_chunks(parse_utterances(TRANSCRIPT), max_chars=6000, overlap_chars=500)
    assert [c.title for c in chunks] == ["", "STT 정확도 개선", "디자인 시안 확정 건"]
    assert [(c.first_index, c.last_index) for c in chunks] == [(0, 0), (1, 2), (3, 4)]
    assert chunks[1].start_sec == 10.0 and chunks[1].end_sec == 20.0
    assert [c.index for c in chunks] == [0, 1, 2]


@pytest.mark.parametrize(
    "text, title",
    [
        ("첫 번째 안건은 예산 검토입니다", "예산 검토"),
        ("다음 안건: 일정 점검", "일정 점검"),
        ("안건 3. 인력 충원", "인력 충원"),
        ("1. 보안 점검 결과", "보안 점검 결과"),
        ("첫째, 비용 절감 방안", "비용 절감 방안"),
        ("마지막 안건은 기타 논의입니다", "기타 논의"),
    ],
)
def test_marker_patterns(text, title):
    assert match_agenda_marker(text) == title


def test_non_markers_and_extra_markers():
    assert match_agenda_marker("안건이 많네요") is None
    assert match_agenda_marker("그 일정은 괜찮습니다") is None
    assert match_agenda_marker("토의사항: 장비 교체", extra_markers=("토의사항",)) == "장비 교체"


def _long(n, size=80):
    return parse_utterances("\n".join(f"[00:{i // 60:02d}:{i % 60:02d}] 화자{i % 3 + 1}: {'가나다라마' * (size // 5)}{i}" for i in range(n)))


def test_length_split_without_markers_with_overlap_and_limits():
    us = _long(60)
    chunks = split_into_chunks(us, max_chars=1000, overlap_chars=200)
    assert len(chunks) > 3
    for c in chunks:
        assert len(c.text) <= 1000
        assert all(u.index == us[u.index].index for u in c.utterances)  # 발언 단위 그대로(중간 분할 없음)
        assert all(u.text == us[u.index].text for u in c.utterances)
    # 순서·위치 보존, 모든 발언이 어느 청크엔가 들어간다
    covered = sorted({u.index for c in chunks for u in c.utterances})
    assert covered == list(range(60))
    firsts = [c.first_index for c in chunks]
    assert firsts == sorted(firsts)
    for prev, nxt in zip(chunks, chunks[1:]):
        assert nxt.overlap_count > 0
        overlapped = nxt.utterances[: nxt.overlap_count]
        assert sum(len(u.line()) + 1 for u in overlapped) <= 200
        assert [u.index for u in overlapped] == [u.index for u in prev.utterances[-nxt.overlap_count:]]


def test_oversized_utterance_is_split_on_sentences_and_marked():
    sentence = "이것은 아주 긴 발언의 한 문장입니다. "
    us = parse_utterances("[00:00:01] 화자1: 짧은 발언\n[00:00:02] 화자2: " + sentence * 60 + "\n[00:00:03] 화자1: 끝")
    chunks = split_into_chunks(us, max_chars=400, overlap_chars=100)
    assert any(c.split_utterance for c in chunks)
    pieces = [u for c in chunks for u in c.utterances if u.split]
    assert pieces and all(u.index == 1 and u.speaker == "화자2" for u in pieces)
    assert all(len(c.text) <= 400 for c in chunks)
    assert all(u.text.endswith("입니다.") for u in pieces)  # 문장 경계에서 잘렸다


def test_chunker_is_deterministic_and_handles_empty():
    us = _long(40)
    first = split_into_chunks(us, max_chars=800, overlap_chars=150)
    second = split_into_chunks(_long(40), max_chars=800, overlap_chars=150)
    assert first == second
    assert split_into_chunks([], max_chars=100, overlap_chars=10) == []
    assert parse_utterances("") == []


def test_long_agenda_is_split_by_length_inside_agenda():
    body = "\n".join(f"[00:01:{i % 60:02d}] 화자2: {'내용' * 40}{i}" for i in range(30))
    us = parse_utterances("[00:00:05] 화자1: 첫 번째 안건은 장비 교체입니다.\n" + body)
    chunks = split_into_chunks(us, max_chars=900, overlap_chars=100)
    assert len(chunks) > 1 and {c.title for c in chunks} == {"장비 교체"}
    assert [c.part for c in chunks] == list(range(1, len(chunks) + 1)) and all(c.parts == len(chunks) for c in chunks)


# ---------------- 파서 ----------------
@pytest.mark.parametrize(
    "raw",
    [
        '{"items": [{"task": "보고서 작성"}]}',
        '```json\n{"items": [{"task": "보고서 작성"}]}\n```',
        '네, 결과입니다.\n```json\n{"items": [{"task": "보고서 작성"}]}\n```\n이상입니다.',
        '아래와 같습니다 {"items": [{"task": "보고서 작성"}]} 감사합니다',
        '﻿{"items": [{"task": "보고서 작성"}]}',
    ],
)
def test_parser_accepts_common_shapes(raw):
    value, errors, repaired = parse_response(raw, ActionItemList)
    assert errors == [] and repaired is False
    assert [i.task for i in value.items] == ["보고서 작성"]


def test_braces_and_escapes_inside_strings():
    raw = r'설명 {"items": [{"task": "함수 {a} 와 \"b\" 처리 \\ 끝", "assignee": "김}"}]} 뒤'
    value, errors, _ = parse_response(raw, ActionItemList)
    assert errors == [] and value.items[0].task == '함수 {a} 와 "b" 처리 \\ 끝' and value.items[0].assignee == "김}"


def test_truncated_response_is_closed_conservatively_and_flagged():
    cut = '{"items": [{"task": "첫 업무", "assignee": "화자1"}, {"task": "둘째 업'
    found = extract_json_object(cut)
    assert found.repaired is True
    value, errors, repaired = parse_response(cut, ActionItemList)
    assert repaired is True and errors == [] and [i.task for i in value.items] == ["첫 업무"]  # 덜 쓰인 요소는 버린다


def test_trailing_comma_is_not_silently_fixed():
    value, errors, _ = parse_response('{"items": [{"task": "a"},]}', ActionItemList)
    assert value is None and errors  # 내용 변형 없이 실패 → 교정 재시도 대상


def test_unknown_fields_are_ignored_and_none_becomes_empty():
    value, errors, _ = parse_response('{"items": [{"task": "a", "priority": "high", "assignee": null}], "extra": 1}', ActionItemList)
    assert errors == [] and value.items[0].assignee == ""


def test_schema_mismatch_retries_once_with_repair_prompt_then_succeeds():
    client = FakeLlmClient([r'{"items": "문자열"}', '```json\n{"items": [{"task": "고침"}]}\n```'])
    out = run_with_repair(client, [{"role": "user", "content": "원본"}], ActionItemList, timeout=5, repair_max_chars=50)
    assert out.status == "repaired" and out.calls == 2 and out.value.items[0].task == "고침"
    retry_prompt = client.calls[1].messages[-1]["content"]
    assert "JSON 만 다시 출력" in retry_prompt or "JSON 만" in retry_prompt
    assert all(call.json_mode for call in client.calls)
    assert client.calls[1].messages[:-1] == client.calls[0].messages  # 원본 메시지 + 교정 지시 1개


def test_retry_also_fails_marks_failed_without_exception_and_caps_calls():
    client = FakeLlmClient(["엉터리", "또 엉터리", "세 번째(쓰이면 안 됨)"])
    out = run_with_repair(client, [{"role": "user", "content": "x"}], ActionItemList, timeout=5, repair_max_chars=50, retry_max=99)
    assert out.status == "failed" and out.value is None and out.calls == 2 and len(client.calls) == 2 and out.errors


def test_repair_prompt_truncates_original_response():
    client = FakeLlmClient(["가" * 500 + "깨짐", '{"items": []}'])
    run_with_repair(client, [{"role": "user", "content": "x"}], ActionItemList, timeout=5, repair_max_chars=100)
    assert ("가" * 100) in client.calls[1].messages[-1]["content"] and ("가" * 101) not in client.calls[1].messages[-1]["content"]


def test_client_errors_do_not_raise():
    client = FakeLlmClient([LlmClientError("down"), LlmClientError("down")])
    out = run_with_repair(client, [{"role": "user", "content": "x"}], ActionItemList, timeout=5, repair_max_chars=50)
    assert out.status == "failed" and out.calls == 2
    unexpected = FakeLlmClient([ValueError("boom")])
    assert run_with_repair(unexpected, [{"role": "user", "content": "x"}], ActionItemList, timeout=5, repair_max_chars=50).status == "failed"


# ---------------- 조립 ----------------
def by_title(mapping, default=None):
    """프롬프트에 든 안건 제목으로 응답을 고르는 대역."""
    def responder(messages):
        content = "\n".join(m["content"] for m in messages)  # 교정 재시도 때도 원본 프롬프트가 들어 있다
        for title, value in mapping.items():
            if f"(안건: {title})" in content:
                return value
        if default is None:
            raise LlmClientError("no response")
        return default
    return responder


def run(responder, transcript=TRANSCRIPT, opts=OPTS):
    client = FakeLlmClient(responder=responder)
    return extract_chunked(transcript, HELD, client, opts), client


def test_one_failed_chunk_does_not_stop_the_rest_and_statuses_are_reported():
    good = reply(
        [item("개선안 정리", "화자3", "다음 주 금요일", "화자2", "00:00:10", "개선안을 다음 주 금요일까지 정리해 주세요.")],
        discussion="화자 분리 개선",
    )
    result, client = run(by_title({"STT 정확도 개선": good, "디자인 시안 확정 건": "깨진 응답"}, default=reply()), opts=OPTS_AGENDA)
    assert [c.status for c in result.chunks] == ["success", "success", "failed"]
    assert result.chunks[2].calls == 2 and not result.all_failed
    assert len(result.items) == 1 and result.items[0].candidate.due_date == "2026-10-23"  # 다음 주 금요일 확정
    assert any("구간 3/3 실패" in w for w in result.warnings)
    assert max(c.calls for c in result.chunks) <= 2
    assert all(call.json_mode for call in client.calls)


def test_repaired_status_and_truncated_flag():
    answers = iter(['{"items": "잘못"}', reply([], purpose="목적")])
    result, _ = run(lambda m: next(answers), transcript="[00:00:01] 화자1: 오늘 회의를 시작합니다.")
    assert [c.status for c in result.chunks] == ["repaired"] and result.chunks[0].calls == 2
    assert any("교정 재시도" in w for w in result.warnings)
    truncated, _ = run(lambda m: '{"items": [], "minutes": {"purpose": "일부", "discussion": "잘림', transcript="[00:00:01] 화자1: 안녕")
    assert truncated.chunks[0].truncated_recovered is True and any("잘려 복구" in w for w in truncated.warnings)


def test_overlap_duplicates_are_removed_keeping_verified_one():
    quote = "개선안을 다음 주 금요일까지 정리해 주세요."
    same = reply([item("STT 개선안 정리", "화자3", "", "화자2", "00:00:10", quote)])
    transcript = "\n".join(f"[00:00:{i:02d}] 화자{i % 3 + 1}: " + ("일반 발언입니다. " * 6) for i in range(10)) + "\n[00:00:20] 화자2: " + quote
    opts = ChunkOptions(max_chars=420, overlap_chars=200, repair_max_chars=4000, timeout=5, retry_max=1, minutes_max_chars=4000)
    result, _ = run(lambda m: same, transcript, opts)
    assert len(result.chunks) > 1
    assert len([i for i in result.items if i.candidate.task == "STT 개선안 정리"]) == 1
    assert any("중복 업무" in w for w in result.warnings)


def test_dedupe_criteria():
    from app.pipeline.chunked_extract import ChunkedItem
    from app.pipeline.extractor import ActionItemCandidate

    def cand(task, who, quote):
        return ChunkedItem(ActionItemCandidate(task, who, "", "", "", quote, None), True, 0, "")

    kept, removed = dedupe_items([cand("디자인 시안 확정", "화자4", "q1"), cand("디자인 시안 확정!", "화자4", "q2")])  # 업무명 거의 같음
    assert len(kept) == 1 and removed == 1
    kept, _ = dedupe_items([cand("디자인 시안 확정", "화자4", "q"), cand("디자인 시안 확정", "화자5", "q")])  # 담당자 다름
    assert len(kept) == 2
    kept, _ = dedupe_items([cand("예산안 검토", "화자1", "예산안을 검토해 주세요"), cand("보고서 작성", "화자1", "보고서를 써 주세요")])
    assert len(kept) == 2


def test_minutes_merge_with_titles_and_no_content():
    a = reply([], purpose="STT 개선", discussion="화자 분리 오류\n개선안 논의")
    b = reply([], discussion="시안 일정", decisions="목요일 확정")
    result, _ = run(by_title({"STT 정확도 개선": a, "디자인 시안 확정 건": b}, default=reply()), opts=OPTS_AGENDA)
    assert set(result.minutes) == set(MINUTES_FIELDS)
    assert result.minutes["purpose"] == "[STT 정확도 개선]\nSTT 개선"
    assert result.minutes["discussion"] == "[STT 정확도 개선]\n화자 분리 오류\n개선안 논의\n[디자인 시안 확정 건]\n시안 일정"
    assert result.minutes["risks"] == NO_CONTENT and result.minutes["next_agenda"] == NO_CONTENT


def test_minutes_length_is_capped():
    big = reply([], discussion="가" * 500)
    opts = ChunkOptions(max_chars=6000, overlap_chars=500, repair_max_chars=4000, timeout=5, retry_max=1, minutes_max_chars=100)
    result, _ = run(lambda m: big, "[00:00:01] 화자1: 안녕", opts)
    assert len(result.minutes["discussion"]) <= 100 and result.minutes["discussion"].endswith("…")


def test_quote_not_in_chunk_is_unverified_and_time_cleared():
    ok = item("개선안 정리", "화자3", "", "화자2", "00:00:10", "개선안을 다음 주 금요일까지 정리해 주세요.")
    fake = item("없는 일", "화자3", "", "화자2", "00:00:15", "이 말은 전사문에 없습니다")
    result, _ = run(lambda m: reply([ok, fake]), "[00:00:10] 화자2: 개선안을 다음 주   금요일까지 정리해 주세요.\n[00:00:20] 화자3: 네.")
    verified = {i.candidate.task: i for i in result.items}
    assert verified["개선안 정리"].evidence_verified is True and verified["개선안 정리"].candidate.evidence_start_sec == 10.0  # 공백 정규화 비교
    assert verified["없는 일"].evidence_verified is False and verified["없는 일"].candidate.evidence_start_sec is None
    assert result.unverified_count == 1 and any("근거 미검증" in w for w in result.warnings)


def test_assignee_keeps_speaker_label_and_is_never_empty():
    items = [
        item("A 업무", "", "", "화자7", "00:00:10", "첫 발언"),  # 모델이 비움 → 인용 발화자
        item("B 업무", "화자5", "", "화자5", "00:00:20", "둘째 발언"),  # 화자N 유지
        item("C 업무", "", "", "", "", "셋째 발언"),  # 인용문이 있는 발언의 화자
        item("D 업무", "", "", "", "", "전사문에 없음"),  # 식별 불가: 빈 문자열
    ]
    transcript = "[00:00:10] 화자7: 첫 발언\n[00:00:20] 화자5: 둘째 발언\n[00:00:30] 화자2: 셋째 발언"
    result, _ = run(lambda m: reply(items), transcript)
    who = {i.candidate.task: i.candidate.assignee for i in result.items}
    assert who == {"A 업무": "화자7", "B 업무": "화자5", "C 업무": "화자2", "D 업무": ""}
    # 원문 근거로 식별할 수 없으면 빈 문자열(현행 Gemini 경로와 같은 표준, 작업 81-1)
    assert [i.candidate.assignee for i in result.items if i.candidate.task == "D 업무"] == [""]


def test_relative_dates_use_meeting_datetime_and_adapter_is_compatible():
    items = [
        item("오늘 일", "화자1", "오늘", "화자1", "00:00:10", "발언"),
        item("이번 주 목요일 일", "화자1", "이번 주 목요일", "화자1", "00:00:10", "발언"),
        item("모호한 일", "화자1", "다음 주", "화자1", "00:00:10", "발언"),
    ]
    result, _ = run(lambda m: reply(items, purpose="목적"), "[00:00:10] 화자1: 발언")
    due = {i.candidate.task: i.candidate.due_date for i in result.items}
    assert due == {"오늘 일": "2026-10-12", "이번 주 목요일 일": "2026-10-15", "모호한 일": ""}
    out = to_extraction_result(result, extract_model="local-test", prompt_version="v1")
    assert isinstance(out, ExtractionResult)
    assert [c.task for c in out.items] == [i.candidate.task for i in result.items]
    assert out.extract_model == "local-test" and out.extracted_at is not None and out.minutes["purpose"] == "목적"
    assert set(out.minutes) == set(MINUTES_FIELDS)
    # 현행 저장 경로가 쓰는 필드가 모두 있다
    first = out.items[0]
    for name in ("task", "assignee", "due_date", "quote_speaker", "quote_timestamp", "quote_text", "evidence_start_sec"):
        assert hasattr(first, name)


def test_empty_transcript_and_all_failed():
    empty, client = run(lambda m: reply(), "")
    assert empty.items == [] and empty.chunks == [] and client.calls == []
    assert set(empty.minutes.values()) == {NO_CONTENT}
    failed, _ = run(lambda m: "x", "[00:00:01] 화자1: 안녕")
    assert failed.all_failed and failed.items == [] and set(failed.minutes.values()) == {NO_CONTENT}


def test_response_model_accepts_missing_minutes():
    value, errors, _ = parse_response('{"items": []}', ChunkResponse)
    assert errors == [] and value.minutes.purpose == ""


# ---------------- 설정·가드 ----------------
def test_settings_defaults_and_validation():
    s = Settings(_env_file=None)
    assert (s.extract_chunk_max_chars, s.extract_chunk_overlap_chars) == (6000, 500)
    assert (s.extract_response_max_chars_for_repair, s.extract_llm_timeout_seconds, s.extract_parse_retry_max) == (4000, 300, 1)
    for name in ("extract_chunk_max_chars", "extract_chunk_overlap_chars", "extract_response_max_chars_for_repair",
                 "extract_llm_timeout_seconds", "extract_parse_retry_max"):
        for bad in (0, -1):
            with pytest.raises(ValidationError):
                Settings(_env_file=None, **{name: bad})
    with pytest.raises(ValidationError):
        Settings(_env_file=None, extract_chunk_max_chars=1000, extract_chunk_overlap_chars=1000)


def test_retry_max_is_clamped_to_one_in_code():
    from app.config import settings

    original = settings.extract_parse_retry_max
    try:
        object.__setattr__(settings, "extract_parse_retry_max", 5)
        assert ChunkOptions.from_settings().retry_max == 1
    finally:
        object.__setattr__(settings, "extract_parse_retry_max", original)


def test_new_modules_are_not_imported_by_existing_pipeline():
    new = {"prompts", "agenda_chunker", "json_parser", "llm_client", "chunked_extract"}
    # 작업 81-1: 로컬 엔진 경로 연결 모듈만 이 부품을 쓴다(현행 Gemini 경로·API 는 쓰지 않는다)
    # 작업 91-1: 원격(시험용) 엔진 연결 모듈(services/engine_test.py)도 같은 부품을 로컬 추출기와 함께 쓴다
    # 작업 L-5: 엔진 시험 변수 검증(engine_lab/params.py)도 구간 상한 계산에 chunked_extract 를 쓴다
    wiring = {"local_extractor", "local_llm", "extraction_engine", "engine_test", "params"}
    pattern = re.compile(r"from\s+app\.pipeline(?:\.(\w+))?\s+import\s+([^\n(]+)|import\s+app\.pipeline\.(\w+)")
    offenders = []
    for path in (ROOT / "app").rglob("*.py"):
        if (path.stem in new and path.parent.name == "pipeline") or path.stem in wiring:
            continue
        text = path.read_text(encoding="utf-8")
        for m in pattern.finditer(text):
            module = m.group(1) or m.group(3) or ""
            names = {n.strip().split(" as ")[0] for n in (m.group(2) or "").split(",")}
            if module in new or (module == "" and names & new):
                offenders.append(path.relative_to(ROOT).as_posix())
    assert offenders == [], offenders
