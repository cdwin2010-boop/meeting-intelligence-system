"""작업 63: 근거 시각 산정(서버가 전사문과 인용문을 대조)과 기존 데이터 보정 도구. 네트워크 없음, 임시 DB 만 쓴다."""
import pytest
from sqlalchemy import select

from app.config import Settings, settings
from app.jobs import backfill_evidence_time as backfill
from app.models import ActionItem, Event
from app.pipeline import evidence_time as et
from app.pipeline.extractor import ActionItemCandidate, ExtractionResult
from app.services.processing import process_meeting
from tests.test_meeting_queries import make_meeting
from tests.test_upload_processing import BrokenExtractor, StubStt, add_account, auth, env, upload  # noqa: F401  (env 는 픽스처)

TRANSCRIPT = """[00:00] 화자1: 오늘 회의 시작하겠습니다. 안건은 신규 견적 건입니다. 서연 씨, 견적서
[00:11] 서연: 네, 다음 주 금요일까지 제출하겠습니다.
[00:15] 화자1: 민수 씨는 계약서 초안 검토를 이번 주 목요일까지 마무리해 주세요.
[00:21] 민수: 알겠습니다. 목요일까지 끝내겠습니다.
[01:05] 화자1: 그럼 12시에 같이 갑시다. 이상입니다."""

HOURS = """[00:00:03] 김도현: 개선안을 다음 주 금요일까지 정리해 주세요.
[01:02:03] 이서연: 네, 제가 정리해서 공유하겠습니다."""


def seg(text: str = TRANSCRIPT) -> list[et.Segment]:
    return et.segments_of(text, None)


# ---------------- 시각 표기 변환 ----------------
@pytest.mark.parametrize("value, expected", [
    ("00:15", 15.0), ("01:15", 75.0), ("12:34", 754.0), ("00:00:03", 3.0), ("1:02:03", 3723.0), ("[01:02:03]", 3723.0),
    (" [00:15] ", 15.0), ("75:00", 4500.0), ("bad", None), ("", None), (None, None), ("00:75", None), ("00:61:00", None), ("15", None),
])
def test_parse_seconds_formats(value, expected):
    assert et.parse_seconds(value) == expected


def test_segments_from_mmss_hhmmss_and_continuation_lines():
    parsed = et.segments_of(TRANSCRIPT, None)
    assert [s.start_sec for s in parsed] == [0.0, 11.0, 15.0, 21.0, 65.0]
    assert parsed[0].text.startswith("오늘 회의") and "화자1" not in parsed[0].text  # 화자 표기는 뺀다
    assert [s.start_sec for s in et.segments_of(HOURS, None)] == [3.0, 3723.0]
    merged = et.segments_of("[00:05] 화자1: 첫 줄\n이어지는 줄\n[00:09] 화자2: 다음", None)
    assert [s.start_sec for s in merged] == [5.0, 9.0] and "이어지는 줄" in merged[0].text


def test_segments_prefer_structured_segments_when_present():
    structured = [{"speaker": "A", "start_sec": 7.5, "end_sec": 9, "text": "구조화 구간"}, {"speaker": "B", "start_sec": "x", "text": "버림"}]
    assert [(s.start_sec, s.text) for s in et.segments_of("[00:01] 화자: 무시", structured)] == [(7.5, "구조화 구간")]


# ---------------- 대조 ----------------
def test_quote_inside_one_segment_gives_that_segment_start():
    assert et.locate(seg(), "민수 씨는 계약서 초안 검토를 이번 주 목요일까지 마무리해 주세요.") == 15.0
    assert et.locate(seg(), "알겠습니다. 목요일까지 끝내겠습니다.") == 21.0
    assert et.locate(seg(), "계약서 초안 검토") == 15.0  # 구간 안의 일부


def test_quote_spanning_segments_gives_first_segment_start():
    quote = "서연 씨, 견적서 네, 다음 주 금요일까지 제출하겠습니다."
    assert et.locate(seg(), quote) == 0.0  # 첫 구간에서 시작
    assert et.locate(seg(), "금요일까지 제출하겠습니다. 민수 씨는 계약서") == 11.0  # 두 번째 구간에서 시작


def test_whitespace_and_punctuation_differences_are_ignored():
    assert et.locate(seg(), "민수씨는   계약서초안 검토를, 이번 주 목요일까지 (마무리해 주세요)") == 15.0
    assert et.locate(seg(), "알겠습니다 목요일까지 끝내겠습니다") == 21.0


def test_similarity_threshold(monkeypatch):
    typo = "민수 씨는 계약서 초안 검토를 이번 주 목요일까지 마무리해 주세오"  # 한 글자 다름
    assert et.locate(seg(), typo) == 15.0  # 기본 80% 이상
    assert et.locate(seg(), "전혀 다른 내용의 인용문입니다 아무 관계 없음") is None
    monkeypatch.setattr(settings, "evidence_match_min", 100)
    assert et.locate(seg(), typo) is None  # 기준을 올리면 실패
    monkeypatch.setattr(settings, "evidence_match_min", 0)
    assert et.locate(seg(), "전혀 다른 내용의 인용문입니다 아무 관계 없음") is not None  # 기준 0 이면 가장 가까운 곳


def test_same_sentence_twice_uses_gemini_time_as_tiebreak():
    twice = et.segments_of("[00:10] 화자1: 확인하겠습니다.\n[00:50] 화자2: 다른 말\n[01:30] 화자1: 확인하겠습니다.", None)
    assert et.locate(twice, "확인하겠습니다") == 10.0  # 힌트 없으면 첫 번째
    assert et.locate(twice, "확인하겠습니다", hint_sec=88.0) == 90.0  # 힌트에 가까운 쪽


def test_empty_inputs():
    assert et.locate([], "무엇") is None
    assert et.locate(seg(), "  ,. ") is None


# ---------------- 최종 값 규칙 ----------------
def test_server_match_fills_when_gemini_time_missing_and_wins_over_gemini():
    quote = "민수 씨는 계약서 초안 검토를 이번 주 목요일까지 마무리해 주세요."
    assert et.resolve_evidence_sec(seg(), quote, None) == (15.0, True)
    assert et.resolve_evidence_sec(seg(), quote, 3.0) == (15.0, True)  # 서버 대조가 Gemini 값보다 우선


def test_failed_match_uses_gemini_time_only_if_inside_transcript():
    nope = "전사문에 없는 말을 지어낸 인용문입니다 정말로"
    assert et.resolve_evidence_sec(seg(), nope, 30.0) == (30.0, False)  # 전사문 길이(마지막 구간 65초) 안
    assert et.resolve_evidence_sec(seg(), nope, 9999.0) == (None, False)  # 밖이면 버린다
    assert et.resolve_evidence_sec(seg(), nope, None) == (None, False)  # 둘 다 없으면 None
    assert et.resolve_evidence_sec([], "아무 말", 12.0) == (12.0, False)  # 비교할 구간이 없으면 기존 값 그대로


def test_evidence_match_min_is_read_from_env(monkeypatch):
    monkeypatch.delenv("EVIDENCE_MATCH_MIN", raising=False)
    assert Settings(_env_file=None).evidence_match_min == 80
    monkeypatch.setenv("EVIDENCE_MATCH_MIN", "90")
    assert Settings(_env_file=None).evidence_match_min == 90
    monkeypatch.setenv("EVIDENCE_MATCH_MIN", "101")
    with pytest.raises(Exception):
        Settings(_env_file=None)


# ---------------- 처리 경로 ----------------
def candidate(quote: str, timestamp: str = "", sec: float | None = None, task: str = "업무") -> ActionItemCandidate:
    return ActionItemCandidate(task=task, assignee="", due_date="", quote_speaker="화자1", quote_timestamp=timestamp, quote_text=quote, evidence_start_sec=sec)


class FixedExtractor:
    def __init__(self, *items):
        self.items = list(items)

    def extract(self, transcript, held_at):
        return ExtractionResult(items=self.items, extract_model="x", prompt_version="x")


@pytest.fixture
def team(env):
    f = env["factory"]
    return {"lead": add_account(f, "고객사A", "lead", "manager", name="박총괄"), "staff": add_account(f, "고객사A", "kim", "staff", name="김담당")}


def saved_items(env, mid) -> list[ActionItem]:
    with env["factory"]() as s:
        return list(s.scalars(select(ActionItem).where(ActionItem.meeting_id == mid).order_by(ActionItem.id)))


QUOTE_15 = "민수 씨는 계약서 초안 검토를 이번 주 목요일까지 마무리해 주세요."


def test_upload_processing_fills_time_from_transcript_even_when_gemini_gave_none_or_mmss(env, team):
    body = upload(env, team["lead"]).json()
    extractor = FixedExtractor(
        candidate(QUOTE_15, task="시각 없음"),
        candidate("알겠습니다. 목요일까지 끝내겠습니다.", timestamp="00:21", task="mm:ss 로 줌(기존 변환은 실패)"),
        candidate("서연 씨, 견적서 네, 다음 주 금요일까지 제출하겠습니다.", sec=40.0, task="여러 구간·틀린 시각"),
    )
    assert process_meeting(body["jobId"], session_factory=env["factory"], stt_factory=lambda: StubStt(TRANSCRIPT), extractor_factory=lambda: extractor) == "completed"
    assert [i.evidence_start_sec for i in saved_items(env, body["meetingId"])] == [15.0, 21.0, 0.0]


def test_reprocess_goes_through_same_path(env, team):
    body = upload(env, team["lead"]).json()
    assert process_meeting(body["jobId"], session_factory=env["factory"], stt_factory=lambda: StubStt(TRANSCRIPT), extractor_factory=BrokenExtractor) == "failed"
    res = env["client"].post(f"/api/meetings/{body['meetingId']}/reprocess", headers=auth(team["lead"]))
    assert res.status_code == 202
    extractor = FixedExtractor(candidate(QUOTE_15))
    assert process_meeting(res.json()["jobId"], session_factory=env["factory"], stt_factory=lambda: StubStt(TRANSCRIPT), extractor_factory=lambda: extractor) == "completed"
    assert [i.evidence_start_sec for i in saved_items(env, body["meetingId"])] == [15.0]


def test_items_without_findable_time_are_still_saved(env, team, caplog):
    import logging

    body = upload(env, team["lead"]).json()
    extractor = FixedExtractor(candidate("전사문에 없는 지어낸 인용문입니다 정말로", task="없는 인용"), candidate(QUOTE_15, task="있는 인용"))
    with caplog.at_level(logging.WARNING, logger="app.evidence"):
        process_meeting(body["jobId"], session_factory=env["factory"], stt_factory=lambda: StubStt(TRANSCRIPT), extractor_factory=lambda: extractor)
    items = saved_items(env, body["meetingId"])
    assert [(i.title, i.evidence_start_sec) for i in items] == [("없는 인용", None), ("있는 인용", 15.0)]  # 업무는 저장되고 시각만 비어 있다
    assert "failed for 1 of 2" in caplog.text and "지어낸" not in caplog.text  # 건수만 로그에(인용문 원문 금지)


def test_fake_path_results_are_unchanged(env, team):
    body = upload(env, team["lead"]).json()
    assert process_meeting(body["jobId"], session_factory=env["factory"]) == "completed"  # 기본 fake 엔진
    assert [i.evidence_start_sec for i in saved_items(env, body["meetingId"])] == [3.0, 20.0]


# ---------------- 보정 도구 ----------------
@pytest.fixture
def data(env, team):
    f = env["factory"]

    def meeting(title, status="confirmed", transcript=TRANSCRIPT, items=()):
        return make_meeting(f, team["lead"], title=title, transcript=transcript, status=status, items=list(items))

    quote = {"evidence_quote": QUOTE_15}
    main = meeting("본 회의", items=[
        {"title": "대기", **quote},
        {"title": "확정", "status": "confirmed", "confirm_kind": "manager", **quote},
        {"title": "종결", "status": "closed", **quote},
        {"title": "이미 값 있음", "evidence_start_sec": 99.0, **quote},
        {"title": "삭제된 업무", "status": "deleted", **quote},
        {"title": "수기", "extract_model": "manual", "evidence_quote": "등록자 직권 지정"},
        {"title": "인용 없음", "evidence_quote": None},
        {"title": "없는 인용", "evidence_quote": "전사문에 없는 지어낸 인용문입니다 정말로"},
    ])
    held = meeting("보류 회의", items=[{"title": "보류 업무", **quote}])
    deleted = meeting("삭제 회의", items=[{"title": "삭제 회의 업무", **quote}])
    other = meeting("다른 회의", items=[{"title": "다른 회의 업무", "evidence_quote": "알겠습니다. 목요일까지 끝내겠습니다."}])
    assert env["client"].post(f"/api/meetings/{held}/hold", headers=auth(team["lead"]), json={"reason": "r"}).status_code == 200
    assert env["client"].post(f"/api/meetings/{deleted}/delete", headers=auth(team["lead"]), json={"reason": "r"}).status_code == 200
    return {"main": main, "held": held, "deleted": deleted, "other": other}


def columns(item: ActionItem) -> dict:
    return {c.name: getattr(item, c.name) for c in ActionItem.__table__.columns if c.name != "evidence_start_sec"}


def snapshot(env) -> dict[int, ActionItem]:
    with env["factory"]() as s:
        return {i.id: i for i in s.scalars(select(ActionItem))}


def by_title(items: dict[int, ActionItem], title: str) -> ActionItem:
    return next(i for i in items.values() if i.title == title)


def test_dry_run_prints_plan_and_saves_nothing(env, data, capsys):
    before = snapshot(env)
    assert backfill.main([], session_factory=env["factory"]) == 0
    out = capsys.readouterr().out
    assert "[dry-run]" in out and "저장하지 않음" in out and "대조 실패" in out and "15초" in out
    assert QUOTE_15[:30] in out and QUOTE_15[:31] not in out  # 인용문 앞 30자만
    assert {i: x.evidence_start_sec for i, x in snapshot(env).items()} == {i: x.evidence_start_sec for i, x in before.items()}
    with env["factory"]() as s:
        assert s.scalars(select(Event).where(Event.event_type == "evidence.backfilled")).all() == []


def test_apply_fills_only_missing_time_and_nothing_else(env, data, capsys):
    before = snapshot(env)
    backfill.main(["--apply"], session_factory=env["factory"])
    after = snapshot(env)
    for item_id, old in before.items():
        assert columns(after[item_id]) == columns(old)  # 근거 시각 외 칸은 그대로
    filled = {a.title: a.evidence_start_sec for a in after.values()}
    assert filled["대기"] == 15.0 and filled["확정"] == 15.0 and filled["종결"] == 15.0  # 확정·종결 업무도 시각만 채움
    assert filled["다른 회의 업무"] == 21.0
    assert filled["이미 값 있음"] == 99.0  # 이미 값이 있으면 불변
    assert filled["없는 인용"] is None  # 대조 실패는 비워 둠
    for title in ("삭제된 업무", "수기", "인용 없음", "보류 업무", "삭제 회의 업무"):
        assert filled[title] is None, title  # 삭제된 업무·수기·인용 없음·보류/삭제 회의록 제외
    assert "[적용]" in capsys.readouterr().out


def test_apply_records_history_with_system_actor_and_before_after(env, data, team):
    backfill.main(["--apply", "--meeting-id", str(data["main"])], session_factory=env["factory"])
    with env["factory"]() as s:
        events = s.scalars(select(Event).where(Event.event_type == "evidence.backfilled").order_by(Event.id)).all()
        assert len(events) == 3 and all(e.actor_account_id is None and e.entity_type == "action_item" for e in events)
        assert events[0].payload == {"before": {"evidenceStartSec": None}, "after": {"evidenceStartSec": 15.0}}
    rows = env["client"].get(f"/api/meetings/{data['main']}/history", headers=auth(team["lead"])).json()
    shown = [r for r in rows if r["kindCode"] == "evidence.backfilled"]
    assert len(shown) == 3 and {r["kind"] for r in shown} == {"근거 시각 보정"} and shown[0]["changedBy"] is None


def test_meeting_id_limits_scope(env, data):
    backfill.main(["--apply", "--meeting-id", str(data["other"])], session_factory=env["factory"])
    after = snapshot(env)
    assert by_title(after, "다른 회의 업무").evidence_start_sec == 21.0
    assert by_title(after, "대기").evidence_start_sec is None  # 다른 회의록은 건드리지 않는다


def test_apply_twice_is_idempotent(env, data, capsys):
    backfill.main(["--apply"], session_factory=env["factory"])
    capsys.readouterr()
    backfill.main(["--apply"], session_factory=env["factory"])
    out = capsys.readouterr().out
    with env["factory"]() as s:
        assert len(s.scalars(select(Event).where(Event.event_type == "evidence.backfilled")).all()) == 4  # 두 번째 실행은 대상만 다시 보고 새 기록은 없다
    assert "대상 1건" in out  # 남은 대상은 대조 실패 1건뿐
