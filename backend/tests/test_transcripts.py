"""4b-1 보강: 전사문 저장(transcripts)과 고객사 음성 보관 설정 칸(tenants.audio_retention_days).
네트워크 없음. 업로드·처리 도우미와 env 픽스처는 test_upload_processing 의 것을 그대로 쓴다."""
from pathlib import Path

import pytest
from alembic import command
from sqlalchemy import inspect, select, text
from sqlalchemy.exc import IntegrityError

from app.auth.scope import scoped
from app.db import make_engine
from app.models import Event, Tenant, Transcript
from app.pipeline.fakes import fake_transcript
from app.pipeline.stt import NO_SPEECH, TranscriptResult
from app.services.processing import process_meeting
from tests.test_upload_processing import (  # noqa: F401  (env 는 픽스처)
    BrokenExtractor,
    StubStt,
    _alembic,
    add_account,
    env,
    upload,
)

_BEFORE_TRANSCRIPTS = "54850384690d"  # transcripts 마이그레이션의 down_revision

SEGMENTS = [
    {"speaker": "화자1", "start_sec": 0.0, "end_sec": 2.5, "text": "안녕하세요"},
    {"speaker": "화자2", "start_sec": 2.5, "end_sec": 6.0, "text": "다음 주 금요일까지 정리하겠습니다"},
]


class SegmentStt(StubStt):
    """구간 정보를 함께 주는 가짜 엔진"""

    provider_name = "segment-test"

    def transcribe(self, audio_path, mime_type):
        return TranscriptResult(text=self.result, segments=SEGMENTS)


def _transcripts(factory) -> list[Transcript]:
    with factory() as s:
        return s.scalars(select(Transcript)).all()


def _payloads(factory) -> list[dict]:
    with factory() as s:
        return [e.payload for e in s.scalars(select(Event).where(Event.event_type == "transcript.saved"))]


def test_completed_processing_saves_one_transcript(env):
    mgr = add_account(env["factory"], "고객사A", "mgr", "manager")
    body = upload(env, mgr).json()
    assert process_meeting(body["jobId"], session_factory=env["factory"]) == "completed"

    [saved] = _transcripts(env["factory"])
    assert saved.meeting_id == body["meetingId"] and saved.tenant_id == mgr.tenant_id
    assert saved.full_text == fake_transcript()
    assert saved.segments is None  # 가짜 엔진은 구간을 주지 않음
    assert saved.stt_provider == "fake" and saved.created_at.tzinfo is not None
    assert _payloads(env["factory"]) == [{"length": len(fake_transcript()), "segments": None, "stt_provider": "fake"}]


def test_segments_are_saved_when_engine_provides_them(env):
    staff = add_account(env["factory"], "고객사A", "kim", "staff")
    body = upload(env, staff).json()
    stt = SegmentStt("[00:00:00] 화자1: 안녕하세요")
    process_meeting(body["jobId"], session_factory=env["factory"], stt_factory=lambda: stt)

    [saved] = _transcripts(env["factory"])
    assert saved.segments == SEGMENTS and saved.stt_provider == "segment-test"
    assert _payloads(env["factory"])[0]["segments"] == 2


def test_transcript_kept_when_extraction_fails(env):
    staff = add_account(env["factory"], "고객사A", "kim", "staff")
    body = upload(env, staff).json()
    assert process_meeting(body["jobId"], session_factory=env["factory"], extractor_factory=BrokenExtractor) == "failed"
    [saved] = _transcripts(env["factory"])
    assert saved.full_text == fake_transcript()


@pytest.mark.parametrize("raw", ["", "  \n", NO_SPEECH, f"{NO_SPEECH} 말소리 없음"])
def test_no_speech_keeps_raw_transcript(env, raw):
    staff = add_account(env["factory"], "고객사A", "kim", "staff")
    body = upload(env, staff).json()
    assert process_meeting(body["jobId"], session_factory=env["factory"], stt_factory=lambda: StubStt(raw)) == "no_content"
    [saved] = _transcripts(env["factory"])
    assert saved.full_text == raw  # 판정 전 원문 그대로


def test_meeting_id_is_unique(env):
    staff = add_account(env["factory"], "고객사A", "kim", "staff")
    body = upload(env, staff).json()
    process_meeting(body["jobId"], session_factory=env["factory"])
    with env["factory"]() as s:
        s.add(Transcript(tenant_id=staff.tenant_id, meeting_id=body["meetingId"], full_text="중복", stt_provider="fake"))
        with pytest.raises(IntegrityError):
            s.flush()


def test_transcript_is_isolated_by_tenant(env):
    mgr = add_account(env["factory"], "고객사A", "mgr", "manager")
    outsider = add_account(env["factory"], "고객사B", "out", "executive")
    body = upload(env, mgr).json()
    process_meeting(body["jobId"], session_factory=env["factory"])
    with env["factory"]() as s:
        assert s.scalars(scoped(select(Transcript), outsider)).all() == []
        assert [t.meeting_id for t in s.scalars(scoped(select(Transcript), mgr))] == [body["meetingId"]]


def test_events_do_not_contain_transcript_text(env):
    mgr = add_account(env["factory"], "고객사A", "mgr", "manager")
    body = upload(env, mgr).json()
    process_meeting(body["jobId"], session_factory=env["factory"])
    with env["factory"]() as s:
        payloads = " ".join(str(e.payload) for e in s.scalars(select(Event)))
    # 대본의 어느 문장도 이벤트에 들어 있지 않다(업무 근거 인용도 이벤트에는 넣지 않음)
    for line in fake_transcript().splitlines():
        sentence = line.split(": ", 1)[1]
        leaked = sentence in payloads
        assert leaked is False


def test_audio_retention_days_defaults_to_null(env):
    staff = add_account(env["factory"], "고객사A", "kim", "staff")
    with env["factory"]() as s:
        assert s.get(Tenant, staff.tenant_id).audio_retention_days is None


def test_migration_downgrade_refuses_when_data_exists(tmp_path):
    """데이터 삭제 없는 downgrade: 비어 있으면 왕복 성공, 전사문이나 보관 일수 값이 있으면 거부하고 데이터는 그대로."""
    url = f"sqlite:///{(tmp_path / 'm.db').as_posix()}"
    cfg = _alembic(url)
    # 이 마이그레이션(2b93bc811d65)의 바로 아래 리비전으로 내려갔다가 다시 올린다(뒤에 리비전이 더 생겨도 같은 대상)
    command.upgrade(cfg, "head")
    command.downgrade(cfg, _BEFORE_TRANSCRIPTS)
    command.upgrade(cfg, "head")
    command.check(cfg)

    engine = make_engine(url)
    try:
        with engine.begin() as conn:
            conn.execute(text(
                "INSERT INTO tenants (id, name, auto_confirm_days, audio_retention_days, created_at) "
                "VALUES (1, 't', 5, 30, '2026-10-01')"))
        with pytest.raises(RuntimeError, match="downgrade 거부"):
            command.downgrade(cfg, _BEFORE_TRANSCRIPTS)
        with engine.connect() as conn:
            assert conn.execute(text("SELECT audio_retention_days FROM tenants WHERE id = 1")).scalar_one() == 30
            assert "transcripts" in set(inspect(conn).get_table_names())
    finally:
        engine.dispose()
