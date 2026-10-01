"""4b-1단계: 음성 업로드 API + 처리 작업(process_meeting) + 회의록·업무 생성.
네트워크 없음: STT_PROVIDER=fake(가짜 대본) 또는 테스트용 가짜 엔진만 쓴다. 업로드 파일은 tmp_path 아래에만 저장한다."""
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from app.auth.passwords import hash_password
from app.auth.scope import scoped
from app.auth.tokens import create_access_token
from app.config import settings
from app.db import get_session, get_session_factory, make_engine
from app.main import app
from app.models import Account, ActionItem, Event, Job, Meeting, MeetingParticipant, SourceDocument, Tenant
from app.pipeline.extractor import FakeExtractor
from app.pipeline.stt import NO_SPEECH
from app.services.processing import get_job_runner, process_meeting

_BACKEND = Path(__file__).resolve().parent.parent
M4A = b"\x00\x00\x00\x18ftypM4A \x00\x00\x00\x00" + b"\x00" * 1000
HELD_AT = "2026-09-29T14:00:00+09:00"
SECRET_TEXT = "C:/secret/path/audio.m4a AIzaSyFAKE_shape_only_000000000000 boom"


def _alembic(url: str) -> Config:
    cfg = Config(str(_BACKEND / "alembic.ini"))
    cfg.set_main_option("sqlalchemy.url", url)
    return cfg


@pytest.fixture
def env(tmp_path, monkeypatch):
    """임시 DB + 임시 업로드 폴더 + 작업 실행기 가로채기(업로드는 접수만, 처리는 테스트가 직접 부른다)."""
    url = f"sqlite:///{(tmp_path / 'upload.db').as_posix()}"
    command.upgrade(_alembic(url), "head")
    engine = make_engine(url)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    queued: list[int] = []

    def _session():
        with factory() as s:
            yield s

    monkeypatch.setattr(settings, "upload_dir", tmp_path / "uploads")
    monkeypatch.setattr(settings, "stt_provider", "fake")
    app.dependency_overrides[get_session] = _session
    app.dependency_overrides[get_session_factory] = lambda: factory
    app.dependency_overrides[get_job_runner] = lambda: (lambda job_id, _factory: queued.append(job_id))
    try:
        yield {"factory": factory, "queued": queued, "client": TestClient(app), "uploads": tmp_path / "uploads"}
    finally:
        for dep in (get_session, get_session_factory, get_job_runner):
            app.dependency_overrides.pop(dep, None)
        engine.dispose()


def add_account(factory, tenant_name: str, login_id: str, rank: str, name: str | None = None) -> Account:
    with factory() as s:
        tenant = s.scalar(select(Tenant).where(Tenant.name == tenant_name))
        if tenant is None:
            tenant = Tenant(name=tenant_name)
            s.add(tenant)
            s.flush()
        account = Account(
            tenant_id=tenant.id, login_id=login_id, password_hash=hash_password("unused-password"),
            name=name or login_id, email=f"{login_id}@example.com", rank=rank,
        )
        s.add(account)
        s.commit()
        return account


def auth(account: Account) -> dict[str, str]:
    return {"Authorization": f"Bearer {create_access_token(account.id)}"}


def upload(env, account: Account, *, filename="회의 녹음.m4a", content=M4A, held_at=HELD_AT, title="주간 회의", participants=None):
    data = {"title": title, "heldAt": held_at}
    if participants is not None:
        data["participantIds"] = participants
    return env["client"].post(
        "/api/meetings/upload", headers=auth(account), data=data, files={"file": (filename, content, "audio/mp4")}
    )


def saved_files(env) -> list[Path]:
    root = env["uploads"]
    return sorted(p for p in root.rglob("*") if p.is_file()) if root.exists() else []


def event_types(factory, tenant_id: int) -> list[str]:
    with factory() as s:
        return list(s.scalars(select(Event.event_type).where(Event.tenant_id == tenant_id).order_by(Event.id)))


class SpyExtractor:
    """추출기에 넘어온 회의 일시를 기록하고 가짜 정답표를 돌려준다."""

    def __init__(self):
        self.held_at = None

    def extract(self, transcript, held_at):
        self.held_at = held_at
        return FakeExtractor().extract(transcript, held_at)


class StubStt:
    def __init__(self, result):
        self.result, self.released = result, False

    def transcribe(self, audio_path, mime_type):
        return self.result

    def release(self):
        self.released = True


class BrokenExtractor:
    def extract(self, transcript, held_at):
        raise RuntimeError(SECRET_TEXT)


# ---------------- 마이그레이션 ----------------
def test_migration_roundtrip_with_jobs_and_meeting_statuses(tmp_path):
    url = f"sqlite:///{(tmp_path / 'm.db').as_posix()}"
    cfg = _alembic(url)
    command.upgrade(cfg, "head")
    command.downgrade(cfg, "base")
    command.upgrade(cfg, "head")
    command.check(cfg)

    engine = make_engine(url)
    try:
        with engine.begin() as conn:
            conn.execute(text("INSERT INTO tenants (id, name, auto_confirm_days, created_at) VALUES (1, 't', 5, '2026-10-01')"))
            conn.execute(text(
                "INSERT INTO accounts (id, tenant_id, login_id, password_hash, name, email, rank, is_active, created_at) "
                "VALUES (1, 1, 'a', 'x', 'a', 'a@x', 'staff', 1, '2026-10-01')"))
            conn.execute(text(
                "INSERT INTO source_documents (id, tenant_id, origin, doc_type, title, registered_by, created_at) "
                "VALUES (1, 1, 'audio_minutes', 'm', 't', 1, '2026-10-01')"))
            for i, st in enumerate(("failed", "no_content"), start=1):
                conn.execute(text(
                    "INSERT INTO meetings (id, tenant_id, source_document_id, title, held_at, summary, decisions, status, "
                    f"first_created_at, created_at) VALUES ({i}, 1, 1, 't', '2026-10-01', '', '[]', '{st}', '2026-10-01', '2026-10-01')"))
            conn.execute(text("INSERT INTO jobs (tenant_id, meeting_id, status, attempts, created_at) VALUES (1, 1, 'no_content', 0, '2026-10-01')"))
        with pytest.raises(IntegrityError):
            with engine.begin() as conn:
                conn.execute(text("UPDATE jobs SET status = 'weird'"))
        with pytest.raises(IntegrityError):
            with engine.begin() as conn:
                conn.execute(text("UPDATE meetings SET status = 'weird' WHERE id = 1"))
    finally:
        engine.dispose()


# ---------------- 업로드 접수 ----------------
def test_upload_accepted_202_and_records(env):
    mgr = add_account(env["factory"], "고객사A", "mgr", "manager")
    res = upload(env, mgr)
    assert res.status_code == 202
    body = res.json()
    assert set(body) == {"meetingId", "jobId"}
    assert env["queued"] == [body["jobId"]]  # 백그라운드 실행 예약

    [saved] = saved_files(env)
    assert saved.parent.name == str(mgr.tenant_id) and saved.suffix == ".m4a"
    assert "회의" not in saved.name and len(saved.stem) == 32  # 원본 파일명이 아닌 uuid
    assert saved.read_bytes() == M4A

    with env["factory"]() as s:
        meeting = s.get(Meeting, body["meetingId"])
        document = s.get(SourceDocument, meeting.source_document_id)
        job = s.get(Job, body["jobId"])
        assert meeting.status == "processing" and meeting.title == "주간 회의"
        assert meeting.held_at == datetime(2026, 9, 29, 5, 0, tzinfo=timezone.utc)  # UTC 저장
        assert meeting.first_created_at.tzinfo is not None
        assert meeting.auto_confirm_at == meeting.first_created_at + timedelta(days=5)
        assert document.origin == "audio_minutes" and document.registered_by == mgr.id
        assert document.file_path == f"{mgr.tenant_id}/{saved.name}"
        assert job.status == "queued" and job.attempts == 0
    assert event_types(env["factory"], mgr.tenant_id) == ["meeting.created", "job.queued"]


def test_upload_requires_login(env):
    res = env["client"].post("/api/meetings/upload", data={"heldAt": HELD_AT}, files={"file": ("a.m4a", M4A)})
    assert res.status_code == 401
    assert saved_files(env) == []


def test_upload_rejects_extension(env):
    staff = add_account(env["factory"], "고객사A", "kim", "staff")
    res = upload(env, staff, filename="notes.txt")
    assert res.status_code == 415
    assert saved_files(env) == []


def test_upload_rejects_too_large(env, monkeypatch):
    monkeypatch.setattr(settings, "max_upload_mb", 1)
    staff = add_account(env["factory"], "고객사A", "kim", "staff")
    res = upload(env, staff, content=b"\x00" * (1024 * 1024 + 1))
    assert res.status_code == 413
    assert saved_files(env) == []  # 부분 파일도 남지 않음


def test_upload_rejects_empty_file(env):
    staff = add_account(env["factory"], "고객사A", "kim", "staff")
    assert upload(env, staff, content=b"").status_code == 400
    assert saved_files(env) == []


@pytest.mark.parametrize("held_at", ["2026-09-29T14:00:00", "2026-09-29", "어제"])
def test_upload_rejects_held_at_without_offset(env, held_at):
    staff = add_account(env["factory"], "고객사A", "kim", "staff")
    res = upload(env, staff, held_at=held_at)
    assert res.status_code == 400
    assert saved_files(env) == []


def test_upload_rejects_other_tenant_participants(env):
    staff = add_account(env["factory"], "고객사A", "kim", "staff")
    colleague = add_account(env["factory"], "고객사A", "lee", "staff")
    outsider = add_account(env["factory"], "고객사B", "out", "staff")
    res = upload(env, staff, participants=[str(colleague.id), str(outsider.id)])
    assert res.status_code == 400
    assert saved_files(env) == []
    with env["factory"]() as s:
        assert s.scalar(select(Meeting)) is None


def test_upload_saves_same_tenant_participants(env):
    staff = add_account(env["factory"], "고객사A", "kim", "staff")
    colleague = add_account(env["factory"], "고객사A", "lee", "staff")
    res = upload(env, staff, participants=f"{staff.id},{colleague.id}")
    assert res.status_code == 202
    with env["factory"]() as s:
        rows = s.scalars(select(MeetingParticipant.account_id).where(MeetingParticipant.meeting_id == res.json()["meetingId"]))
        assert sorted(rows) == sorted([staff.id, colleague.id])


# ---------------- 처리 ----------------
def test_manager_upload_is_confirmed_on_registration_with_items(env):
    mgr = add_account(env["factory"], "고객사A", "mgr", "manager")
    lee = add_account(env["factory"], "고객사A", "lee", "staff", name="이서연")
    body = upload(env, mgr).json()

    assert process_meeting(body["jobId"], session_factory=env["factory"]) == "completed"

    with env["factory"]() as s:
        meeting = s.get(Meeting, body["meetingId"])
        job = s.get(Job, body["jobId"])
        items = s.scalars(select(ActionItem).where(ActionItem.meeting_id == meeting.id).order_by(ActionItem.id)).all()
        assert meeting.status == "confirmed" and meeting.confirm_kind == "registration"
        assert meeting.confirmed_by == mgr.id and meeting.confirmed_at is not None
        assert meeting.auto_confirm_at == meeting.first_created_at + timedelta(days=5)
        assert job.status == "completed" and job.attempts == 1 and job.error_code is None
        assert job.started_at is not None and job.finished_at is not None

        assert [i.title for i in items] == ["STT 화자 분리 정확도 개선안 정리", "디자인 시안 확정"]
        assert [i.due_date for i in items] == [date(2026, 10, 9), date(2026, 10, 1)]
        assert [i.assignee_id for i in items] == [lee.id, None]  # 이서연=계정 1명, 정민수=계정 없음
        assert all(i.status == "pending" and i.confirm_kind is None for i in items)  # 업무 확정은 하지 않음
        assert all(i.extract_model == "fake" and i.prompt_version == "fake" and i.extracted_at is not None for i in items)
        assert [i.evidence_start_sec for i in items] == [3.0, 20.0]
        assert items[0].evidence_quote.startswith("화자 분리가 자꾸 틀려요")
        assert items[1].needs_supplement is True  # 담당자 없음


def test_staff_upload_awaits_confirmation(env):
    staff = add_account(env["factory"], "고객사A", "kim", "staff")
    body = upload(env, staff).json()
    process_meeting(body["jobId"], session_factory=env["factory"])
    with env["factory"]() as s:
        meeting = s.get(Meeting, body["meetingId"])
        assert meeting.status == "awaiting_confirmation"
        assert meeting.confirm_kind is None and meeting.confirmed_by is None and meeting.confirmed_at is None
        assert meeting.auto_confirm_at == meeting.first_created_at + timedelta(days=5)


def test_auto_confirm_days_follow_tenant(env):
    staff = add_account(env["factory"], "고객사A", "kim", "staff")
    with env["factory"]() as s:
        s.get(Tenant, staff.tenant_id).auto_confirm_days = 10
        s.commit()
    body = upload(env, staff).json()
    with env["factory"]() as s:
        meeting = s.get(Meeting, body["meetingId"])
        assert meeting.auto_confirm_at == meeting.first_created_at + timedelta(days=10)


@pytest.mark.parametrize("transcript", ["", "  \n", NO_SPEECH, f"{NO_SPEECH} 말소리 없음"])
def test_no_speech_marks_no_content(env, transcript):
    staff = add_account(env["factory"], "고객사A", "kim", "staff")
    body = upload(env, staff).json()
    stt = StubStt(transcript)
    result = process_meeting(body["jobId"], session_factory=env["factory"], stt_factory=lambda: stt,
                             extractor_factory=BrokenExtractor)
    assert result == "no_content" and stt.released is True
    with env["factory"]() as s:
        assert s.get(Job, body["jobId"]).status == "no_content"
        assert s.get(Meeting, body["meetingId"]).status == "no_content"
        assert s.scalar(select(ActionItem)) is None
    assert event_types(env["factory"], staff.tenant_id)[-2:] == ["job.no_content", "meeting.no_content"]


def test_extractor_error_marks_failed_without_leaking_message(env):
    staff = add_account(env["factory"], "고객사A", "kim", "staff")
    body = upload(env, staff).json()
    result = process_meeting(body["jobId"], session_factory=env["factory"], extractor_factory=BrokenExtractor)
    assert result == "failed"
    with env["factory"]() as s:
        job = s.get(Job, body["jobId"])
        assert job.status == "failed" and s.get(Meeting, body["meetingId"]).status == "failed"
        assert job.error_code == "internal_error"
        payloads = " ".join(str(e.payload) for e in s.scalars(select(Event)))
    for secret in ("boom", "AIza", "secret/path"):
        leaked = secret in (job.error_code or "") or secret in payloads
        assert leaked is False
    assert event_types(env["factory"], staff.tenant_id)[-2:] == ["job.failed", "meeting.failed"]


def test_missing_upload_file_marks_failed(env):
    staff = add_account(env["factory"], "고객사A", "kim", "staff")
    body = upload(env, staff).json()
    for path in saved_files(env):
        path.unlink()
    assert process_meeting(body["jobId"], session_factory=env["factory"]) == "failed"
    with env["factory"]() as s:
        assert s.get(Job, body["jobId"]).error_code == "upload_missing"


def test_job_runs_only_once(env):
    staff = add_account(env["factory"], "고객사A", "kim", "staff")
    body = upload(env, staff).json()
    assert process_meeting(body["jobId"], session_factory=env["factory"]) == "completed"
    assert process_meeting(body["jobId"], session_factory=env["factory"]) is None
    with env["factory"]() as s:
        assert len(s.scalars(select(ActionItem)).all()) == 2


def test_extractor_receives_local_kst_time_on_utc_day_boundary(env):
    """KST 월요일 08:00 = UTC 일요일 23:00. 추출기는 KST(월요일)를 받아야 '다음 주 금요일'이 10-09 가 된다.
    (UTC 를 그대로 넘기면 일요일 기준으로 10-02 가 되어 하루가 아니라 한 주가 어긋난다)"""
    staff = add_account(env["factory"], "고객사A", "kim", "staff")
    body = upload(env, staff, held_at="2026-09-28T08:00:00+09:00").json()
    spy = SpyExtractor()
    process_meeting(body["jobId"], session_factory=env["factory"], extractor_factory=lambda: spy)

    assert spy.held_at.utcoffset() == timedelta(hours=9)
    assert spy.held_at.date() == date(2026, 9, 28)  # UTC 로는 9/27
    with env["factory"]() as s:
        meeting = s.get(Meeting, body["meetingId"])
        assert meeting.held_at.date() == date(2026, 9, 27)  # 저장은 UTC
        dues = [i.due_date for i in s.scalars(select(ActionItem).order_by(ActionItem.id))]
    assert dues == [date(2026, 10, 9), date(2026, 10, 1)]


def test_homonym_assignee_is_not_mapped(env):
    mgr = add_account(env["factory"], "고객사A", "mgr", "manager")
    add_account(env["factory"], "고객사A", "lee1", "staff", name="이서연")
    add_account(env["factory"], "고객사A", "lee2", "staff", name="이서연")
    body = upload(env, mgr).json()
    process_meeting(body["jobId"], session_factory=env["factory"])
    with env["factory"]() as s:
        assert [i.assignee_id for i in s.scalars(select(ActionItem).order_by(ActionItem.id))] == [None, None]


def test_assignee_in_other_tenant_is_not_mapped(env):
    mgr = add_account(env["factory"], "고객사A", "mgr", "manager")
    add_account(env["factory"], "고객사B", "lee", "staff", name="이서연")
    body = upload(env, mgr).json()
    process_meeting(body["jobId"], session_factory=env["factory"])
    with env["factory"]() as s:
        assert s.scalars(select(ActionItem.assignee_id).order_by(ActionItem.id)).all() == [None, None]


def test_events_are_recorded_in_order(env):
    mgr = add_account(env["factory"], "고객사A", "mgr", "manager")
    body = upload(env, mgr).json()
    process_meeting(body["jobId"], session_factory=env["factory"])
    assert event_types(env["factory"], mgr.tenant_id) == [
        "meeting.created",
        "job.queued",
        "job.started",
        "transcript.saved",
        "item.created",
        "item.created",
        "meeting.confirmed",
        "job.completed",
    ]


def test_other_tenant_cannot_see_meeting_or_items(env):
    mgr = add_account(env["factory"], "고객사A", "mgr", "manager")
    outsider = add_account(env["factory"], "고객사B", "out", "executive")
    body = upload(env, mgr).json()
    process_meeting(body["jobId"], session_factory=env["factory"])
    with env["factory"]() as s:
        assert s.scalars(scoped(select(Meeting), outsider)).all() == []
        assert s.scalars(scoped(select(ActionItem), outsider)).all() == []
        assert s.scalars(scoped(select(Job), outsider)).all() == []
        assert [m.id for m in s.scalars(scoped(select(Meeting), mgr))] == [body["meetingId"]]
