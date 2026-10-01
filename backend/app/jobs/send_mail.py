"""
발송함(mail_outbox)의 대기 메일을 보낸다. 운영 시 OS 스케줄러로 자주(예: 몇 분마다) 실행한다:
    python -m app.jobs.send_mail [--dry-run]

- MAIL_ENABLED=false(기본): 아무것도 보내지 않고 대기 메일을 skipped(mail_disabled)로 처리한다(발송기 호출 0회).
- 성공 sent. 실패 시 attempts+1, 3회 미만이면 queued 로 남겨 다음 실행에 다시 시도, 3회째 failed.
- 실패 기록은 분류 코드만(SMTP 값·예외 원문 금지). 메일 1통마다 따로 커밋한다.
- 출력은 건수만(이메일 주소·본문 금지). failed 가 생기면 종료 코드 1.
"""
import argparse
import logging
import sys
from collections.abc import Sequence
from datetime import datetime, timezone

from sqlalchemy import select, update
from sqlalchemy.orm import Session, sessionmaker

from app.config import settings
from app.models import MailOutbox
from app.services.mail_sender import MailSender, SmtpSender, classify_send_error

log = logging.getLogger("app.jobs.send_mail")

MAX_ATTEMPTS = 3


def send_queued_mail(session: Session, sender: MailSender, now: datetime | None = None, *, dry_run: bool = False) -> dict:
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    ids = list(session.scalars(select(MailOutbox.id).where(MailOutbox.status == "queued").order_by(MailOutbox.id)))
    result = {"queued": len(ids), "sent": 0, "retrying": 0, "failed": 0, "skipped": 0}
    if dry_run:
        session.rollback()
        return result

    if not settings.mail_enabled:
        skipped = session.execute(
            update(MailOutbox)
            .where(MailOutbox.id.in_(ids), MailOutbox.status == "queued")
            .values(status="skipped", last_error_code="mail_disabled")
            .execution_options(synchronize_session=False)
        ).rowcount
        session.commit()
        result["skipped"] = skipped
        return result

    for mail_id in ids:
        mail = session.get(MailOutbox, mail_id)
        if mail is None or mail.status != "queued":
            continue
        try:
            sender.send(mail.to_email, mail.subject, mail.body)
        except Exception as exc:  # noqa: BLE001 — 한 통 실패가 나머지를 막지 않게
            code = classify_send_error(exc)
            mail.attempts += 1
            mail.last_error_code = code
            if mail.attempts >= MAX_ATTEMPTS:
                mail.status = "failed"
                result["failed"] += 1
            else:
                result["retrying"] += 1
            log.warning("mail %s send failed: %s (attempt %s)", mail_id, code, mail.attempts)
        else:
            mail.attempts += 1
            mail.status, mail.sent_at, mail.last_error_code = "sent", now, None
            result["sent"] += 1
        session.commit()
    return result


def main(
    argv: Sequence[str] | None = None,
    session_factory: sessionmaker | None = None,
    sender: MailSender | None = None,
) -> int:
    parser = argparse.ArgumentParser(description="발송함의 대기 메일 보내기")
    parser.add_argument("--dry-run", action="store_true", help="대기 건수만 출력(변경·발송 없음)")
    args = parser.parse_args(argv)
    if session_factory is None:
        from app.db import SessionLocal

        session_factory = SessionLocal
    with session_factory() as session:
        result = send_queued_mail(session, sender or SmtpSender(), dry_run=args.dry_run)

    if args.dry_run:
        print(f"[dry-run] queued={result['queued']}")
        return 0
    print(
        f"queued={result['queued']} sent={result['sent']} retrying={result['retrying']} "
        f"failed={result['failed']} skipped={result['skipped']}"
    )
    return 1 if result["failed"] else 0


if __name__ == "__main__":
    sys.exit(main())
