"""메일 발송기. 실제 SMTP 발송(SmtpSender)과 테스트용 가짜(FakeSender)를 같은 모양으로 둔다.
SMTP 주소·계정·비밀번호는 로그·예외 메시지·DB 에 넣지 않는다(실패는 분류 코드만)."""
import smtplib
import ssl
from email.message import EmailMessage
from typing import Protocol

from app.config import Settings, settings as default_settings


class MailSender(Protocol):
    def send(self, to_email: str, subject: str, body: str) -> None: ...


class MailSendError(RuntimeError):
    """발송 실패. code 는 분류 코드(smtp_auth 등)만 담는다."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def classify_send_error(exc: BaseException) -> str:
    """예외 → 분류 코드(원문·SMTP 값 없음)."""
    if isinstance(exc, MailSendError):
        return exc.code
    if isinstance(exc, smtplib.SMTPAuthenticationError):
        return "smtp_auth"
    if isinstance(exc, smtplib.SMTPRecipientsRefused):
        return "smtp_recipient_refused"
    if isinstance(exc, smtplib.SMTPSenderRefused):
        return "smtp_sender_refused"
    if isinstance(exc, (smtplib.SMTPConnectError, smtplib.SMTPServerDisconnected, ConnectionError)):
        return "smtp_connect"
    if isinstance(exc, TimeoutError):
        return "smtp_timeout"
    if isinstance(exc, smtplib.SMTPException):
        return "smtp_error"
    if isinstance(exc, OSError):
        return "smtp_connect"
    return "send_error"


class SmtpSender:
    """smtplib + STARTTLS. 설정이 비어 있으면 연결하지 않고 smtp_not_configured 로 실패한다."""

    def __init__(self, cfg: Settings | None = None, timeout: float = 30.0):
        self._cfg = cfg or default_settings
        self._timeout = timeout

    def send(self, to_email: str, subject: str, body: str) -> None:
        cfg = self._cfg
        if not cfg.smtp_host or not cfg.smtp_from:
            raise MailSendError("smtp_not_configured")
        message = EmailMessage()
        message["From"] = cfg.smtp_from
        message["To"] = to_email
        message["Subject"] = subject
        message.set_content(body)
        with smtplib.SMTP(cfg.smtp_host, cfg.smtp_port, timeout=self._timeout) as smtp:
            smtp.starttls(context=ssl.create_default_context())
            if cfg.smtp_user:
                smtp.login(cfg.smtp_user, cfg.smtp_password.get_secret_value())
            smtp.send_message(message)


class FakeSender:
    """테스트용: 보낸 메일을 기억만 한다. fail_times 만큼 먼저 실패한다(error_code 지정 가능)."""

    def __init__(self, fail_times: int = 0, error: BaseException | None = None):
        self.sent: list[tuple[str, str, str]] = []
        self.calls = 0
        self._fail_times = fail_times
        self._error = error or MailSendError("smtp_connect")

    def send(self, to_email: str, subject: str, body: str) -> None:
        self.calls += 1
        if self.calls <= self._fail_times:
            raise self._error
        self.sent.append((to_email, subject, body))
