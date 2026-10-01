"""알림 2단계: 메일 발송함(mail_outbox), 즉시 메일, 일 1회 메일, 발송 작업.
실제 메일은 절대 보내지 않는다: FakeSender 또는 smtplib.SMTP 를 가짜로 바꾼 SmtpSender 만 쓴다."""
import smtplib
from datetime import datetime, timedelta, timezone

import pytest
from alembic import command
from pydantic import SecretStr
from sqlalchemy import select, text

from app.config import settings
from app.db import make_engine
from app.jobs import daily_mail as daily_module
from app.jobs import send_mail as send_module
from app.jobs.daily_mail import run_daily_mail
from app.jobs.send_mail import send_queued_mail
from app.models import Account, ActionItem, MailOutbox, Meeting
from app.pipeline.fakes import fake_transcript
from app.pipeline.stt import NO_SPEECH
from app.services.mail import queue_immediate_new_minutes
from app.services.mail_sender import FakeSender, MailSendError, SmtpSender
from app.services.processing import process_meeting
from tests.test_meeting_queries import make_meeting
from tests.test_upload_processing import (  # noqa: F401  (env 는 픽스처)
    BrokenExtractor,
    StubStt,
    _alembic,
    add_account,
    auth,
    env,
    upload,
)

UTC = timezone.utc
_BEFORE_MAIL = "a1cba09b5031"  # mail_outbox 마이그레이션의 down_revision
NOW = datetime(2026, 10, 1, 0, 0, tzinfo=UTC)  # 10/1 09:00 KST


@pytest.fixture
def team(env):
    f = env["factory"]
    return {
        "mgr": add_account(f, "고객사A", "mgr", "manager", name="박관리"),
        "exe": add_account(f, "고객사A", "boss", "executive", name="최임원"),
        "staff": add_account(f, "고객사A", "kim", "staff", name="김담당"),
        "lee": add_account(f, "고객사A", "lee", "staff", name="이서연"),
        "outsider": add_account(f, "고객사B", "out", "executive", name="외부"),
    }


def outbox(env, kind=None) -> list[MailOutbox]:
    with env["factory"]() as s:
        stmt = select(MailOutbox).order_by(MailOutbox.id)
        if kind:
            stmt = stmt.where(MailOutbox.kind == kind)
        return s.scalars(stmt).all()


def update_account(env, account, **values):
    with env["factory"]() as s:
        row = s.get(Account, account.id)
        for key, value in values.items():
            setattr(row, key, value)
        s.commit()


def daily(env, now=NOW) -> dict:
    with env["factory"]() as s:
        return run_daily_mail(s, now)


def send(env, sender, **kwargs) -> dict:
    with env["factory"]() as s:
        return send_queued_mail(s, sender, NOW, **kwargs)


# ---------------- 마이그레이션 ----------------
def test_migration_roundtrip_and_downgrade_guard(tmp_path):
    url = f"sqlite:///{(tmp_path / 'm.db').as_posix()}"
    cfg = _alembic(url)
    command.upgrade(cfg, "head")
    command.downgrade(cfg, _BEFORE_MAIL)
    command.upgrade(cfg, "head")
    command.check(cfg)
    engine = make_engine(url)
    try:
        with engine.begin() as conn:
            conn.execute(text("INSERT INTO tenants (id, name, auto_confirm_days, created_at) VALUES (1, 't', 5, '2026-10-01')"))
            conn.execute(text(
                "INSERT INTO accounts (id, tenant_id, login_id, password_hash, name, email, rank, is_active, created_at) "
                "VALUES (1, 1, 'a', 'x', 'a', 'a@x', 'manager', 1, '2026-10-01')"))
            conn.execute(text(
                "INSERT INTO mail_outbox (tenant_id, account_id, to_email, kind, subject, body, status, dedupe_key, attempts, created_at) "
                "VALUES (1, 1, 'a@x', 'daily_reminder', 's', 'b', 'queued', 'k', 0, '2026-10-01')"))
        with pytest.raises(RuntimeError, match="downgrade 거부"):
            command.downgrade(cfg, _BEFORE_MAIL)
        with engine.connect() as conn:
            assert conn.execute(text("SELECT COUNT(*) FROM mail_outbox")).scalar_one() == 1
    finally:
        engine.dispose()


# ---------------- 즉시 메일 ----------------
def test_immediate_mail_recipients(env, team):
    no_email = add_account(env["factory"], "고객사A", "noemail", "manager")
    update_account(env, no_email, email="  ")
    inactive = add_account(env["factory"], "고객사A", "off", "manager")
    update_account(env, inactive, is_active=False)
    # 등록자 = staff(본인 제외). 참석자: 등록자·mgr·lee·이메일 없음·비활성. 담당자: 가짜 추출의 '이서연'(=lee, 중복)
    ids = ",".join(str(a.id) for a in (team["staff"], team["mgr"], team["lee"], no_email, inactive))
    body = upload(env, team["staff"], participants=ids).json()
    assert process_meeting(body["jobId"], session_factory=env["factory"]) == "completed"

    mails = outbox(env, "immediate_new_minutes")
    assert sorted(m.account_id for m in mails) == sorted([team["mgr"].id, team["lee"].id])
    assert {m.dedupe_key for m in mails} == {f"meeting:{body['meetingId']}:immediate:{a}" for a in (team["mgr"].id, team["lee"].id)}
    assert all(m.status == "queued" and m.attempts == 0 and m.tenant_id == team["staff"].tenant_id for m in mails)


def test_immediate_mail_body_has_only_title_and_link(env, team, monkeypatch):
    monkeypatch.setattr(settings, "app_base_url", "https://minutes.example.com/")
    body = upload(env, team["staff"], participants=str(team["mgr"].id), title="주간 회의").json()
    process_meeting(body["jobId"], session_factory=env["factory"])
    [mail] = [m for m in outbox(env) if m.account_id == team["mgr"].id]
    assert "주간 회의" in mail.subject and "주간 회의" in mail.body
    assert "https://minutes.example.com" in mail.body
    with env["factory"]() as s:
        item_titles = list(s.scalars(select(ActionItem.title)))
    forbidden = [*item_titles, "이서연", "정민수", *[line.split(": ", 1)[1] for line in fake_transcript().splitlines()]]
    leaked = [word for word in forbidden if word in mail.body or word in mail.subject]
    assert leaked == []


def test_registration_confirmed_meeting_also_gets_immediate_mail(env, team):
    body = upload(env, team["mgr"], participants=f"{team['exe'].id}").json()
    process_meeting(body["jobId"], session_factory=env["factory"])
    with env["factory"]() as s:
        assert s.get(Meeting, body["meetingId"]).status == "confirmed"
    # 등록자(mgr) 제외: 참석자 exe + 가짜 추출의 담당자 '이서연'(lee)
    assert sorted(m.account_id for m in outbox(env)) == sorted([team["exe"].id, team["lee"].id])


@pytest.mark.parametrize("outcome", ["failed", "no_content"])
def test_no_immediate_mail_for_failed_or_no_content(env, team, outcome):
    body = upload(env, team["staff"], participants=str(team["mgr"].id)).json()
    if outcome == "failed":
        process_meeting(body["jobId"], session_factory=env["factory"], extractor_factory=BrokenExtractor)
    else:
        process_meeting(body["jobId"], session_factory=env["factory"], stt_factory=lambda: StubStt(NO_SPEECH))
    assert outbox(env) == []


def test_immediate_mail_only_once_on_reprocessing(env, team):
    body = upload(env, team["staff"], participants=str(team["mgr"].id)).json()
    process_meeting(body["jobId"], session_factory=env["factory"])
    before = len(outbox(env))
    with env["factory"]() as s:
        assert queue_immediate_new_minutes(s, s.get(Meeting, body["meetingId"])) == 0
        s.commit()
    assert len(outbox(env)) == before == 2  # mgr(참석자) + lee(담당자)


# ---------------- 일 1회 메일 ----------------
def test_daily_mail_for_managers_and_assignees(env, team):
    awaiting = make_meeting(env["factory"], team["staff"], items=[
        {"assignee_id": None},  # 보완 필요
        {"assignee_id": team["lee"].id, "due_undetermined": True},  # lee 담당(미열람)
    ])
    make_meeting(env["factory"], team["mgr"], day=1, status="confirmed", items=[
        {"assignee_id": team["lee"].id, "due_undetermined": True, "status": "confirmed"},  # 확정된 업무는 세지 않음
        {"assignee_id": team["staff"].id, "status": "deleted"},  # 삭제된 업무는 세지 않음
    ])
    result = daily(env)
    mails = {m.account_id: m for m in outbox(env, "daily_reminder")}
    assert sorted(mails) == sorted([team["mgr"].id, team["exe"].id, team["lee"].id])  # staff·외부 없음
    assert result == {"created": 3, "alreadyQueued": 0}
    assert "확정 대기 회의록 1건, 보완 필요 업무 1건" in mails[team["mgr"].id].body
    assert "아직 열어 보지 않은 담당 회의록 1건" in mails[team["lee"].id].body
    assert mails[team["lee"].id].dedupe_key == f"daily:{team['lee'].id}:2026-10-01"
    assert all("회의 0" not in m.body for m in mails.values())  # 제목 나열 없음
    assert awaiting


def test_daily_mail_excludes_viewed_and_confirmed(env, team):
    meeting_id = make_meeting(env["factory"], team["mgr"], status="confirmed",
                              items=[{"assignee_id": team["lee"].id, "due_undetermined": True}])
    env["client"].get(f"/api/meetings/{meeting_id}", headers=auth(team["lee"]))  # lee 가 열람
    assert daily(env) == {"created": 0, "alreadyQueued": 0}

    other = make_meeting(env["factory"], team["mgr"], day=1, status="confirmed",
                         items=[{"assignee_id": team["staff"].id, "due_undetermined": True}])
    with env["factory"]() as s:
        item = s.scalar(select(ActionItem).where(ActionItem.meeting_id == other))
        item.status = "confirmed"
        s.commit()
    assert daily(env) == {"created": 0, "alreadyQueued": 0}
    assert outbox(env) == []


def test_daily_mail_once_per_day_and_new_next_day(env, team):
    make_meeting(env["factory"], team["staff"])  # 확정 대기 1건 → 관리자 2명
    assert daily(env)["created"] == 2
    assert daily(env, NOW + timedelta(hours=14)) == {"created": 0, "alreadyQueued": 2}  # 같은 KST 날(23:00)
    assert daily(env, NOW + timedelta(hours=15))["created"] == 2  # KST 다음 날 00:00
    assert sorted({m.dedupe_key.rsplit(":", 1)[1] for m in outbox(env)}) == ["2026-10-01", "2026-10-02"]


def test_daily_mail_isolated_by_tenant(env, team):
    make_meeting(env["factory"], team["staff"])  # 고객사A 확정 대기
    daily(env)
    assert team["outsider"].id not in {m.account_id for m in outbox(env)}
    assert {m.tenant_id for m in outbox(env)} == {team["mgr"].tenant_id}


def test_daily_mail_cli_prints_counts_only(env, team, capsys):
    make_meeting(env["factory"], team["staff"])
    assert daily_module.main([], session_factory=env["factory"]) == 0
    out = capsys.readouterr().out.strip()
    assert out == "created=2 alreadyQueued=0" and "@" not in out


# ---------------- 발송 ----------------
def _queue_one(env, team) -> int:
    """즉시 메일 1통만 쌓는다: 참석자 없이 등록 → 수신자는 가짜 추출의 담당자 '이서연'(lee)뿐."""
    body = upload(env, team["staff"]).json()
    process_meeting(body["jobId"], session_factory=env["factory"])
    [mail] = outbox(env)
    return mail.id


def test_mail_disabled_skips_without_calling_sender(env, team, monkeypatch):
    monkeypatch.setattr(settings, "mail_enabled", False)
    mail_id = _queue_one(env, team)
    sender = FakeSender()
    assert send(env, sender) == {"queued": 1, "sent": 0, "retrying": 0, "failed": 0, "skipped": 1}
    assert sender.calls == 0
    [mail] = outbox(env)
    assert mail.id == mail_id and mail.status == "skipped" and mail.last_error_code == "mail_disabled"


def test_send_success(env, team, monkeypatch):
    monkeypatch.setattr(settings, "mail_enabled", True)
    _queue_one(env, team)
    sender = FakeSender()
    assert send(env, sender)["sent"] == 1
    [mail] = outbox(env)
    assert mail.status == "sent" and mail.sent_at == NOW and mail.attempts == 1
    assert sender.sent[0][0] == team["lee"].email


def test_send_retries_then_fails_after_three_attempts(env, team, monkeypatch, capsys):
    monkeypatch.setattr(settings, "mail_enabled", True)
    _queue_one(env, team)
    sender = FakeSender(fail_times=10, error=MailSendError("smtp_connect"))
    assert send(env, sender)["retrying"] == 1
    assert (outbox(env)[0].status, outbox(env)[0].attempts) == ("queued", 1)
    assert send(env, sender)["retrying"] == 1
    assert send_module.main([], session_factory=env["factory"], sender=sender) == 1  # 3회째 failed → 종료 코드 1
    mail = outbox(env)[0]
    assert (mail.status, mail.attempts, mail.last_error_code) == ("failed", 3, "smtp_connect")
    assert capsys.readouterr().out.strip() == "queued=1 sent=0 retrying=0 failed=1 skipped=0"
    assert send(env, sender)["queued"] == 0  # failed 는 다시 보내지 않음


def test_smtp_sender_uses_starttls_and_hides_smtp_values(env, team, monkeypatch, capsys):
    secret_host, secret_user, secret_pw = "smtp.secret-host.example", "secret-user", "pw-SECRET-123"
    monkeypatch.setattr(settings, "mail_enabled", True)
    monkeypatch.setattr(settings, "smtp_host", secret_host)
    monkeypatch.setattr(settings, "smtp_user", secret_user)
    monkeypatch.setattr(settings, "smtp_password", SecretStr(secret_pw))
    monkeypatch.setattr(settings, "smtp_from", "noreply@example.com")
    calls: list[str] = []

    class FakeSMTP:
        def __init__(self, host, port, timeout):
            calls.append("connect")

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def starttls(self, context=None):
            calls.append("starttls")

        def login(self, user, password):
            calls.append("login")
            raise smtplib.SMTPAuthenticationError(535, f"auth failed for {user} {password} at {secret_host}".encode())

        def send_message(self, message):
            calls.append("send")

    monkeypatch.setattr(smtplib, "SMTP", FakeSMTP)
    _queue_one(env, team)
    for _ in range(3):
        send_module.main([], session_factory=env["factory"], sender=SmtpSender())
    assert calls[:3] == ["connect", "starttls", "login"]
    mail = outbox(env)[0]
    assert mail.status == "failed" and mail.last_error_code == "smtp_auth"
    stored = " ".join(str(v) for v in (mail.last_error_code, mail.body, mail.subject, mail.to_email))
    printed = capsys.readouterr().out
    leaked = [v for v in (secret_host, secret_user, secret_pw) if v in stored or v in printed]
    assert leaked == []


def test_smtp_sender_not_configured_does_not_connect(monkeypatch):
    monkeypatch.setattr(settings, "smtp_host", "")
    monkeypatch.setattr(smtplib, "SMTP", lambda *a, **k: (_ for _ in ()).throw(AssertionError("should not connect")))
    with pytest.raises(MailSendError) as info:
        SmtpSender().send("a@example.com", "s", "b")
    assert info.value.code == "smtp_not_configured"


def test_send_dry_run_counts_only(env, team, monkeypatch, capsys):
    monkeypatch.setattr(settings, "mail_enabled", True)
    _queue_one(env, team)
    sender = FakeSender()
    assert send_module.main(["--dry-run"], session_factory=env["factory"], sender=sender) == 0
    assert capsys.readouterr().out.strip() == "[dry-run] queued=1"
    assert sender.calls == 0 and outbox(env)[0].status == "queued"

