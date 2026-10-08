"""자료 파일(txt) 등록(작업 73-1): 해석 규칙, 인코딩, 검증, STT 건너뛰기, 추출 결과·근거 시각, 화자, 재처리, 응답 필드, 음성 회귀 불변."""
import pytest
from sqlalchemy import select

from app.config import settings
from app.models import ActionItem, Event, Job, Meeting, MeetingMinutes, SourceDocument, Transcript
from app.pipeline.extractor import FakeExtractor
from app.pipeline.fakes import fake_transcript
from app.pipeline.transcript_text import TranscriptTextError, decode_transcript, parse_transcript
from app.services.processing import process_meeting
from app.services.source_kind import TRANSCRIPT_TXT_ENGINE
from tests.test_upload_processing import (  # noqa: F401  (env 는 픽스처)
    HELD_AT, M4A, BrokenExtractor, add_account, auth, env, event_types, saved_files, upload,
)

TIMED = fake_transcript()  # "[00:00:03] 김도현: ..." 형식 그대로
TIMELESS = "\n".join(line.split("] ", 1)[1] for line in TIMED.splitlines())


class NoStt:
    """STT 가 불리면 실패한다(건너뛰었는지 확인)."""

    called = 0

    def __call__(self):
        NoStt.called += 1
        raise AssertionError("STT 를 부르면 안 됩니다")


def post_txt(env, account, content: bytes, *, filename="회의 자료.txt", source_kind="transcript_txt", title="자료 회의"):
    data = {"title": title, "heldAt": HELD_AT}
    if source_kind is not None:
        data["sourceKind"] = source_kind
    return env["client"].post("/api/meetings/upload", headers=auth(account), data=data, files={"file": (filename, content, "text/plain")})


def run(env, job_id, extractor=FakeExtractor):
    NoStt.called = 0
    return process_meeting(job_id, session_factory=env["factory"], stt_factory=NoStt(), extractor_factory=extractor)


@pytest.fixture
def mgr(env):
    return add_account(env["factory"], "고객사A", "mgr", "manager", name="박관리")


# ---------------- 해석 규칙 ----------------
def test_parse_timed_lines_keep_format():
    result = parse_transcript("[00:12] 화자1: 안녕하세요\n[01:02:03] 김대리: 네 알겠습니다")
    assert result.text == "[00:00:12] 화자1: 안녕하세요\n[01:02:03] 김대리: 네 알겠습니다"
    assert result.segments is None  # 시각이 모두 있으면 STT 결과와 같은 모양(구간 없음)


def test_parse_timeless_lines_and_continuation_and_default_speaker():
    result = parse_transcript("첫 줄은 라벨이 없습니다\n화자2: 이어서 말합니다\n그리고 이 줄은 연속입니다\n김대리: 내일까지 제출: 금요일")
    lines = result.text.splitlines()
    assert lines[0] == "화자1: 첫 줄은 라벨이 없습니다"
    assert lines[1] == "화자2: 이어서 말합니다 그리고 이 줄은 연속입니다"
    assert lines[2] == "김대리: 내일까지 제출: 금요일"
    assert [s["speaker"] for s in result.segments] == ["화자1", "화자2", "김대리"]
    assert all(s["start_sec"] is None for s in result.segments)


def test_parse_continuation_line_with_colon_is_not_a_speaker():
    result = parse_transcript("화자1: 일정 공유\n내일까지 제출: 금요일 오전")
    assert result.text.splitlines() == ["화자1: 일정 공유 내일까지 제출: 금요일 오전"]


def test_parse_mixed_and_label_less_timed_line():
    result = parse_transcript("[00:05] 화자1: 시작\n[00:09] 그래서 계속\n라벨 없는 연속")
    assert result.text.splitlines() == ["[00:00:05] 화자1: 시작", "[00:00:09] 화자1: 그래서 계속 라벨 없는 연속"]


def test_decode_utf8_bom_cp949_and_failures(monkeypatch):
    assert decode_transcript("화자1: 안녕".encode("utf-8")) == "화자1: 안녕"
    assert decode_transcript(b"\xef\xbb\xbf" + "화자1: 안녕".encode("utf-8")) == "화자1: 안녕"
    assert decode_transcript("화자1: 안녕하세요".encode("cp949")) == "화자1: 안녕하세요"
    with pytest.raises(TranscriptTextError, match="문자 인코딩을 읽을 수 없습니다"):
        decode_transcript(b"\x81\x20\xff\xfe")
    for bad in (b"", b"  \r\n\t ", b"abc\x00def"):
        with pytest.raises(TranscriptTextError):
            decode_transcript(bad)
    monkeypatch.setattr(settings, "transcript_max_chars", 5)
    with pytest.raises(TranscriptTextError, match="글자 수"):
        decode_transcript("123456".encode())


# ---------------- 업로드 검증 ----------------
def test_upload_txt_accepted_and_stored(env, mgr):
    res = post_txt(env, mgr, TIMED.encode("utf-8"))
    assert res.status_code == 202
    [saved] = saved_files(env)
    assert saved.suffix == ".txt" and len(saved.stem) == 32 and saved.read_bytes() == TIMED.encode("utf-8")
    with env["factory"]() as s:
        meeting = s.get(Meeting, res.json()["meetingId"])
        document = s.get(SourceDocument, meeting.source_document_id)
        assert document.file_path.endswith(".txt") and document.origin == "audio_minutes"
        created = s.scalars(select(Event).where(Event.event_type == "meeting.created")).one()
        assert created.payload["sourceKind"] == "transcript_txt"
    assert event_types(env["factory"], mgr.tenant_id) == ["meeting.created", "job.queued"]


def test_upload_txt_cp949_accepted(env, mgr):
    assert post_txt(env, mgr, "화자1: 안녕하세요 오늘 회의 시작합니다".encode("cp949")).status_code == 202


@pytest.mark.parametrize("content, message", [
    (b"", "빈 파일"), (b"   \n ", "빈 파일"), (b"abc\x00\x01def", "텍스트 파일이 아닙니다"), (b"\x81\x20\xff\xfe", "문자 인코딩을 읽을 수 없습니다"),
])
def test_upload_txt_validation_errors_422_and_nothing_saved(env, mgr, content, message):
    res = post_txt(env, mgr, content)
    assert res.status_code == 422 and message in res.json()["detail"]
    assert saved_files(env) == []
    with env["factory"]() as s:
        assert s.scalar(select(Meeting.id)) is None


def test_upload_txt_size_and_char_limits(env, mgr, monkeypatch):
    monkeypatch.setattr(settings, "transcript_upload_max_bytes", 50)
    res = post_txt(env, mgr, ("화자1: " + "가" * 60).encode("utf-8"))
    assert res.status_code == 422 and "너무 큽니다" in res.json()["detail"]
    monkeypatch.setattr(settings, "transcript_upload_max_bytes", 2 * 1024 * 1024)
    monkeypatch.setattr(settings, "transcript_max_chars", 20)
    res = post_txt(env, mgr, ("화자1: " + "가" * 30).encode("utf-8"))
    assert res.status_code == 422 and "글자 수" in res.json()["detail"]
    assert saved_files(env) == []


def test_upload_txt_kind_requires_txt_extension_and_unknown_kind_422(env, mgr):
    assert post_txt(env, mgr, TIMED.encode(), filename="회의.m4a").status_code == 422
    assert post_txt(env, mgr, TIMED.encode(), filename="회의.docx").status_code == 422
    assert post_txt(env, mgr, TIMED.encode(), source_kind="video").status_code == 422
    assert saved_files(env) == []


def test_audio_kind_with_txt_file_is_still_415(env, mgr):
    assert post_txt(env, mgr, TIMED.encode(), source_kind="audio").status_code == 415
    assert post_txt(env, mgr, TIMED.encode(), source_kind=None).status_code == 415  # sourceKind 없음 = 음성(기존 동작)


# ---------------- 파이프라인 ----------------
def test_pipeline_skips_stt_and_extracts(env, mgr):
    job_id = post_txt(env, mgr, TIMED.encode()).json()["jobId"]
    assert run(env, job_id) == "completed"
    assert NoStt.called == 0
    with env["factory"]() as s:
        meeting = s.scalars(select(Meeting)).one()
        transcript = s.scalar(select(Transcript).where(Transcript.meeting_id == meeting.id))
        assert transcript.stt_provider == TRANSCRIPT_TXT_ENGINE and transcript.full_text == TIMED
        assert s.get(Job, job_id).status == "completed"
        assert meeting.status == "confirmed"  # 관리자 등록 = 등록 시 확정(기존 규칙 그대로)
        items = list(s.scalars(select(ActionItem).where(ActionItem.meeting_id == meeting.id).order_by(ActionItem.id)))
        assert len(items) == 2
        assert [i.evidence_start_sec for i in items] == [3.0, 20.0]  # 시각 있는 줄 → 서버가 근거 시각을 산정
        minutes = s.get(MeetingMinutes, meeting.id)
        assert minutes.purpose and minutes.engine == "fake"  # 생성 엔진은 추출기


def test_pipeline_timeless_has_null_evidence_and_speakers(env, mgr):
    res = post_txt(env, mgr, TIMELESS.encode())
    assert run(env, res.json()["jobId"]) == "completed"
    with env["factory"]() as s:
        assert [i.evidence_start_sec for i in s.scalars(select(ActionItem))] == [None, None]
        transcript = s.scalar(select(Transcript))
        assert transcript.segments and {x["speaker"] for x in transcript.segments} == {"김도현", "이서연", "정민수"}
    # 화자 목록(매핑 대상)이 구간의 화자에서 만들어진다
    speakers = env["client"].get(f"/api/meetings/{res.json()['meetingId']}/speakers", headers=auth(mgr))
    assert speakers.status_code == 200
    assert {"김도현", "이서연", "정민수"} <= {label for label in speakers.json()["labels"]}


def test_pipeline_timed_speakers_listed(env, mgr):
    res = post_txt(env, mgr, TIMED.encode())
    run(env, res.json()["jobId"])
    speakers = env["client"].get(f"/api/meetings/{res.json()['meetingId']}/speakers", headers=auth(mgr))
    assert {"김도현", "이서연", "정민수"} <= set(speakers.json()["labels"])


def test_reprocess_uses_stored_transcript_without_stt(env, mgr):
    res = post_txt(env, mgr, TIMED.encode())
    meeting_id = res.json()["meetingId"]
    assert run(env, res.json()["jobId"], extractor=BrokenExtractor) == "failed"
    with env["factory"]() as s:
        assert s.get(Meeting, meeting_id).status == "failed"
        assert s.scalar(select(Transcript.stt_provider)) == TRANSCRIPT_TXT_ENGINE  # 실패해도 전사문은 보존
    again = env["client"].post(f"/api/meetings/{meeting_id}/reprocess", headers=auth(mgr))
    assert again.status_code == 202
    assert run(env, again.json()["jobId"]) == "completed"
    assert NoStt.called == 0
    with env["factory"]() as s:
        assert s.scalar(select(Transcript.full_text)) == TIMED
        assert len(list(s.scalars(select(ActionItem)))) == 2


# ---------------- 응답·재생 ----------------
def test_detail_fields_and_audio_requests(env, mgr):
    res = post_txt(env, mgr, TIMED.encode())
    meeting_id = res.json()["meetingId"]
    run(env, res.json()["jobId"])
    detail = env["client"].get(f"/api/meetings/{meeting_id}", headers=auth(mgr)).json()
    assert detail["sourceKind"] == "transcript_txt" and detail["hasAudio"] is False
    audio = env["client"].get(f"/api/meetings/{meeting_id}/audio-url", headers=auth(mgr))
    assert audio.status_code == 404 and audio.json()["detail"] == "음성 파일이 없습니다"
    transcript = env["client"].get(f"/api/meetings/{meeting_id}/transcript", headers=auth(mgr)).json()
    assert transcript["sttProvider"] == TRANSCRIPT_TXT_ENGINE


def test_audio_upload_regression_unchanged(env, mgr):
    res = upload(env, mgr)
    assert res.status_code == 202
    [saved] = saved_files(env)
    assert saved.suffix == ".m4a"
    with env["factory"]() as s:
        created = s.scalars(select(Event).where(Event.event_type == "meeting.created")).one()
        assert "sourceKind" not in created.payload  # 기존 음성 회의록의 사건 모양은 그대로
    detail = env["client"].get(f"/api/meetings/{res.json()['meetingId']}", headers=auth(mgr)).json()
    assert detail["sourceKind"] == "audio" and detail["hasAudio"] is True
    assert env["client"].get(f"/api/meetings/{res.json()['meetingId']}/audio-url", headers=auth(mgr)).status_code == 200


def test_visibility_and_permissions_unchanged(env, mgr):
    staff = add_account(env["factory"], "고객사A", "kim", "staff")
    outsider = add_account(env["factory"], "고객사B", "out", "executive")
    res = post_txt(env, staff, TIMED.encode())  # 담당자도 음성과 같이 올릴 수 있다
    assert res.status_code == 202
    run(env, res.json()["jobId"])
    meeting_id = res.json()["meetingId"]
    assert env["client"].get(f"/api/meetings/{meeting_id}", headers=auth(outsider)).status_code == 404
    assert env["client"].get(f"/api/meetings/{meeting_id}/transcript", headers=auth(outsider)).status_code == 404
    with env["factory"]() as s:
        assert s.get(Meeting, meeting_id).status == "awaiting_confirmation"  # 담당자 등록 = 확정 대기(기존 규칙)


def test_transcript_body_not_in_events(env, mgr):
    res = post_txt(env, mgr, TIMED.encode())
    run(env, res.json()["jobId"])
    with env["factory"]() as s:
        for event in s.scalars(select(Event)):
            assert "디자인 시안" not in str(event.payload)
