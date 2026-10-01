from datetime import datetime

from sqlalchemy import ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.models.common import UTCDateTime, enum_check, utcnow

MAIL_KINDS = ("immediate_new_minutes", "daily_reminder")
MAIL_STATUSES = ("queued", "sent", "failed", "skipped")


class MailOutbox(Base):
    """메일 발송함. 만드는 쪽은 여기에 쌓기만 하고, 발송 작업(app/jobs/send_mail.py)이 따로 보낸다.
    dedupe_key 로 같은 메일이 두 번 쌓이지 않게 한다. 본문에는 제목·건수·로그인 링크만(업무 내용·전사문 금지)."""

    __tablename__ = "mail_outbox"
    __table_args__ = (
        enum_check("kind", MAIL_KINDS, "kind_valid"),
        enum_check("status", MAIL_STATUSES, "status_valid"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"), index=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id"), index=True)
    to_email: Mapped[str] = mapped_column(String(255))
    kind: Mapped[str] = mapped_column(String(30))
    subject: Mapped[str] = mapped_column(String(300))
    body: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20), default="queued")
    dedupe_key: Mapped[str] = mapped_column(String(200), unique=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    # 실패 분류 코드만(예: smtp_auth). SMTP 값·예외 원문은 넣지 않는다
    last_error_code: Mapped[str | None] = mapped_column(String(50), nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)
    sent_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
