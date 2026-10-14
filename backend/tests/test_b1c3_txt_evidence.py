"""작업 B-1c-3 B: 자료 파일(txt)로 올린 회의록의 근거 위치. 줄바꿈 있음/없음 × 시각 있음/없음 조합을 픽스처로 재현한다(네트워크·실데이터 없음)."""
from dataclasses import dataclass

import pytest

from app.pipeline.evidence_time import apply_evidence_times, segments_of
from app.pipeline.transcript_text import has_timestamps, parse_transcript
from tests.test_b1c1_list_search_evidence import recompute, team  # noqa: F401  (픽스처)
from tests.test_meeting_queries import get, make_meeting
from tests.test_upload_processing import env  # noqa: F401  (픽스처)

QUOTE = "예산안은 다음 주 검토합니다"
FORMATS = {
    "lines_timed": "[00:01] 김담당: 보고서를 금요일까지 제출하겠습니다.\n[00:20] 박관리: 예산안은 다음 주 검토합니다.\n[01:05] 김담당: 알겠습니다.",
    "oneline_timed": "[00:01] 김담당: 보고서를 금요일까지 제출하겠습니다. [00:20] 박관리: 예산안은 다음 주 검토합니다. [01:05] 김담당: 알겠습니다.",
    "hms_timed": "[00:00:01] 김담당: 보고서를 금요일까지 제출하겠습니다.\n[00:00:20] 박관리: 예산안은 다음 주 검토합니다.",
    "lines_untimed": "김담당: 보고서를 금요일까지 제출하겠습니다.\n박관리: 예산안은 다음 주 검토합니다.\n김담당: 알겠습니다.",
    "oneline_untimed": "김담당: 보고서를 금요일까지 제출하겠습니다. 박관리: 예산안은 다음 주 검토합니다. 김담당: 알겠습니다.",
}


@dataclass
class Cand:
    quote_text: str
    evidence_start_sec: float | None = None
    quote_timestamp: str = ""


@pytest.mark.parametrize("name", [n for n in FORMATS if "untimed" not in n])
def test_timed_txt_locates_quote_start(name):
    result = parse_transcript(FORMATS[name])
    assert has_timestamps(result)
    (item,) = apply_evidence_times([Cand(QUOTE)], result.text, result.segments)
    assert item.evidence_start_sec == 20.0


@pytest.mark.parametrize("name", ["lines_untimed", "oneline_untimed"])
def test_untimed_txt_has_no_time_but_segments_keep_quote(name):
    """시각 없는 txt: 근거 시각은 없다(None). 구간 글자는 남아 있어 화면이 인용문으로 위치를 찾을 수 있다."""
    result = parse_transcript(FORMATS[name])
    assert not has_timestamps(result)
    assert segments_of(result.text, result.segments) == []  # 시각 구간 없음
    (item,) = apply_evidence_times([Cand(QUOTE, evidence_start_sec=None)], result.text, result.segments)
    assert item.evidence_start_sec is None
    assert result.segments and QUOTE in "".join(s["text"] for s in result.segments)  # 화면 인용문 검색 대상


def test_recompute_on_untimed_transcript_is_noop_without_error(env, team):
    text = FORMATS["lines_untimed"]
    mid = make_meeting(
        env["factory"], team["mgr"], transcript=text,
        items=[{"title": "예산 검토", "evidence_quote": QUOTE, "evidence_start_sec": None}],
    )
    res = recompute(env, team["mgr"], mid, dryRun=True)
    assert res.status_code == 200
    body = res.json()
    assert (body["targets"], body["changed"], body["approx"], body["unchanged"]) == (1, 0, 0, 1)
    detail = get(env, team["mgr"], f"/api/meetings/{mid}").json()
    assert detail["actionItems"][0]["evidenceStartSec"] is None
    assert detail["actionItems"][0]["evidenceQuote"] == QUOTE  # 화면은 이 인용문으로 위치를 찾는다
