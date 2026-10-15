"""C-1: 긴 회의록 분기·분할·병합과 B-1c-2 결함 묶음. 모의 LLM(FakeLlmClient)만 쓴다(실제 호출 없음)."""
import json
import re
from datetime import datetime, timedelta, timezone

import pytest

from app.models.minutes import NO_CONTENT
from app.pipeline.chunked_extract import ChunkOptions, extract_chunked, profile_chunk_options
from app.pipeline.engine_profile import EngineProfile
from app.pipeline.llm_client import FakeLlmClient
from app.pipeline.local_llm import PERMANENT, LocalLlmError

HELD = datetime(2026, 10, 12, 14, 0, tzinfo=timezone(timedelta(hours=9)))


def opts(max_chars=6000, target=0, fail_stop=3, overlap_ratio=0.0, overlap_chars=0):
    return ChunkOptions(
        max_chars=max_chars, overlap_chars=overlap_chars, repair_max_chars=4000, timeout=5, retry_max=1, minutes_max_chars=4000,
        target_chars=target, overlap_ratio=overlap_ratio, fail_stop=fail_stop,
    )


def meeting(lines: int, width: int = 40) -> str:
    return "\n".join(
        f"[00:{i // 60:02d}:{i % 60:02d}] 화자{i % 3 + 1}: 발언 {i:03d} " + ("가나다라 " * (width // 5)) for i in range(lines)
    )


def reply(items=(), **minutes) -> str:
    return json.dumps({"minutes": minutes, "items": list(items)}, ensure_ascii=False)


def item(task, text, line_id="", assignee="화자1", speaker="화자1", ts=""):
    return {"task": task, "assignee": assignee, "due_date": "", "quote": {"speaker": speaker, "timestamp": ts, "text": text, "line_id": line_id}}


def prompt_lines(messages) -> list[str]:
    body = messages[-1]["content"].split("전사문:\n", 1)[1]
    return body.splitlines()


def test_within_budget_is_single_call_even_with_agenda_markers():
    text = "[00:00:01] 화자1: 첫 번째 안건은 일정입니다.\n[00:00:10] 화자2: 네.\n[00:00:20] 화자1: 두 번째 안건은 예산입니다.\n[00:00:30] 화자3: 알겠습니다."
    client = FakeLlmClient(responder=lambda m: reply([], purpose="목적"))
    result = extract_chunked(text, HELD, client, opts(max_chars=6000))
    assert len(client.calls) == 1 and len(result.chunks) == 1
    assert result.minutes["purpose"] == "목적"


def test_over_budget_splits_by_line_with_overlap_and_line_ids():
    text = meeting(40)
    client = FakeLlmClient(responder=lambda m: reply([]))
    result = extract_chunked(text, HELD, client, opts(max_chars=1500, target=600, overlap_ratio=0.2))
    assert len(client.calls) == len(result.chunks) >= 3
    original = [l.rstrip() for l in text.splitlines()]
    seen_ids: list[list[int]] = []
    for call in client.calls:
        ids = []
        for line in prompt_lines(call.messages):
            m = re.match(r"#(\d+) (.*)$", line)
            assert m, line
            n = int(m.group(1))
            assert m.group(2) == original[n - 1]  # 발화(라인) 중간은 자르지 않고 줄 번호는 전체 회의록 기준
            ids.append(n)
        seen_ids.append(ids)
    assert sorted({n for ids in seen_ids for n in ids}) == list(range(1, 41))  # 빠진 줄 없음
    assert set(seen_ids[0]) & set(seen_ids[1])  # 직전 구간 말미가 다음 구간에 겹친다


def test_overlap_duplicates_are_merged():
    text = meeting(30)
    same = item("견적서 발송", "발언 010", line_id="11")

    def responder(messages):
        return reply([same] if any(l.startswith("#11 ") for l in prompt_lines(messages)) else [])

    client = FakeLlmClient(responder=responder)
    result = extract_chunked(text, HELD, client, opts(max_chars=1200, target=500, overlap_ratio=0.3))
    assert sum(1 for c in client.calls if any(l.startswith("#11 ") for l in prompt_lines(c.messages))) >= 2  # 중첩으로 두 구간에 나온다
    assert len(result.items) == 1


def test_line_id_verification_matches_and_mismatch_is_unverified():
    text = meeting(6, width=20)
    good = item("확인 업무", "발언 002", line_id="3")
    wrong = item("다른 업무", "발언 002", line_id="5")  # 줄 번호가 틀림
    no_id = item("번호 없음", "발언 004")  # 줄 번호가 없으면 구간 안 대조
    prefixed = item("접두사", "#3 [00:00:02] 화자3: 발언 002", line_id="3")
    client = FakeLlmClient(responder=lambda m: reply([good, wrong, no_id, prefixed]))
    result = extract_chunked(text, HELD, client, opts())
    by_task = {i.candidate.task: i for i in result.items}
    assert by_task["확인 업무"].evidence_verified and by_task["확인 업무"].candidate.evidence_start_sec == 2.0
    assert not by_task["다른 업무"].evidence_verified and by_task["다른 업무"].candidate.evidence_start_sec is None
    assert by_task["번호 없음"].evidence_verified
    assert by_task["접두사"].evidence_verified  # #번호·시각·화자 표기는 걷어 내고 대조
    assert any("근거 미검증" in w for w in result.warnings)


def test_minutes_are_filled_from_other_chunks_and_none_keeps_no_content():
    text = meeting(30)
    state = {"n": 0}

    def responder(messages):
        state["n"] += 1
        return reply([], purpose="" if state["n"] == 1 else "공통 목적", discussion="")

    result = extract_chunked(text, HELD, FakeLlmClient(responder=responder), opts(max_chars=900, target=400))
    assert len(result.chunks) >= 3
    assert "공통 목적" in result.minutes["purpose"]  # 첫 구간이 비어도 다른 구간 값으로 채운다
    assert result.minutes["discussion"] == NO_CONTENT and result.minutes["risks"] == NO_CONTENT  # 모든 구간이 비면 유지


def test_list_valued_minutes_do_not_fail_the_chunk():
    payload = json.dumps(
        {"minutes": {"purpose": "목적", "discussion": ["논의 1", "논의 2"], "decisions": {"a": "결정"}, "risks": None, "next_agenda": 3},
         "items": []}, ensure_ascii=False,
    )
    result = extract_chunked(meeting(3), HELD, FakeLlmClient(responder=lambda m: payload), opts())
    assert [c.status for c in result.chunks] == ["success"]
    assert result.minutes["discussion"] == "논의 1\n논의 2" and result.minutes["decisions"] == "a: 결정"
    assert result.minutes["risks"] == NO_CONTENT and result.minutes["next_agenda"] == "3"


def test_consecutive_same_reason_failures_stop_the_rest():
    calls = {"n": 0}

    def responder(messages):
        calls["n"] += 1
        raise LocalLlmError("거절", kind=PERMANENT, reason="http_4xx")

    result = extract_chunked(meeting(40), HELD, FakeLlmClient(responder=responder), opts(max_chars=900, target=300, fail_stop=3))
    assert len(result.chunks) > 3 and calls["n"] == 3  # 세 구간 실패 후 나머지는 호출하지 않는다
    assert result.all_failed
    assert any("연속 3회" in w and "중단" in w for w in result.warnings)


def test_failure_streak_resets_on_success():
    state = {"n": 0}

    def responder(messages):
        state["n"] += 1
        if state["n"] % 3 == 0:
            return reply([], purpose="목적")
        raise LocalLlmError("거절", kind=PERMANENT, reason="http_4xx")

    result = extract_chunked(meeting(40), HELD, FakeLlmClient(responder=responder), opts(max_chars=900, target=300, fail_stop=3))
    assert not result.all_failed and state["n"] == len(result.chunks)  # 중단 없이 전부 호출


def test_single_line_larger_than_chunk_is_split_on_sentence_boundary():
    big = "[00:00:05] 화자1: " + " ".join(f"문장 {i}번을 길게 말합니다." for i in range(60))
    text = "[00:00:01] 화자2: 시작합니다.\n" + big + "\n[00:09:00] 화자2: 끝냅니다."
    client = FakeLlmClient(responder=lambda m: reply([]))
    result = extract_chunked(text, HELD, client, opts(max_chars=500, target=300))
    assert len(client.calls) >= 3 and not result.all_failed
    assert any("너무 긴 발언" in w for w in result.warnings)
    joined = " ".join(l for c in client.calls for l in prompt_lines(c.messages))
    assert "59번을 길게 말합니다." in joined and "끝냅니다." in joined


def test_context_too_small_is_reported_before_processing():
    with pytest.raises(LocalLlmError) as info:
        profile_chunk_options(
            EngineProfile(context_tokens=2048, max_output_tokens=4096), "", configured_chars=0, timeout=5, retry_max=1, retry_ceiling=2
        )
    assert info.value.job_error_code == "llm_context_too_small" and "2048" in str(info.value)
    ok = profile_chunk_options(EngineProfile(context_tokens=8192), "", configured_chars=0, timeout=5, retry_max=1, retry_ceiling=2)
    assert ok.max_chars >= 300 and 0 < ok.target_chars <= ok.max_chars and ok.fail_stop == 3
