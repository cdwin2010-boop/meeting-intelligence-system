"""
음성 처리 작업 1건: queued → running → (STT) → no_content / (업무 추출 → 업무 생성) → completed, 예외 시 failed.

- 상태가 바뀔 때마다 사건 원장(events)에 기록한다.
- 로그에는 작업 id·상태·예외 종류만 남긴다. 전사문·키·파일 경로는 남기지 않는다.
- 실패 시 jobs.error_code 에는 분류 코드만 저장한다(app/pipeline/errors.error_code).
- STT 엔진은 추출 전에 release() 한다(로컬 GPU 엔진이면 STT·LLM 이 동시에 메모리에 올라가지 않도록).
"""
import logging
from collections.abc import Callable
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from sqlalchemy import select, update
from sqlalchemy.orm import Session, sessionmaker

from app.config import settings
from app.db import SessionLocal
from app.models import Account, ActionItem, Job, Meeting, SourceDocument, Tenant, append_event
from app.models.common import utcnow
from app.pipeline.errors import error_code
from app.pipeline.extractor import ActionItemCandidate, Extractor, FakeExtractor, make_gemini_extractor
from app.pipeline.stt import FakeStt, SttEngine, is_no_speech, make_gemini_stt

log = logging.getLogger("app.processing")

# 확장자 → 전사 엔진에 넘길 MIME 형식
AUDIO_MIME_TYPES = {
    "m4a": "audio/mp4",
    "mp4": "audio/mp4",
    "mp3": "audio/mpeg",
    "wav": "audio/wav",
    "webm": "audio/webm",
    "ogg": "audio/ogg",
}
# 등록 시 바로 확정되는 직급(부록 C-3)
CONFIRM_ON_REGISTRATION_RANKS = ("manager", "executive")


def default_stt() -> SttEngine:
    return make_gemini_stt() if settings.stt_provider == "gemini" else FakeStt()


def default_extractor() -> Extractor:
    # 추출기도 STT_PROVIDER 를 따른다(fake 면 네트워크 없이 가짜 정답표)
    return make_gemini_extractor() if settings.stt_provider == "gemini" else FakeExtractor()


def auto_confirm_at(first_created_at: datetime, auto_confirm_days: int) -> datetime:
    """자동 확정 시각 = 최초 생성일 + 고객사 N일. (업무 마감일과의 min 계산은 후속)"""
    return first_created_at + timedelta(days=auto_confirm_days)


def _assignee_id(session: Session, tenant_id: int, name: str) -> int | None:
    """같은 고객사의 활성 계정 중 이름이 정확히 같은 계정이 딱 하나면 그 id, 아니면 None(동명이인·없음)."""
    if not name:
        return None
    ids = session.scalars(
        select(Account.id).where(Account.tenant_id == tenant_id, Account.name == name, Account.is_active.is_(True))
    ).all()
    return ids[0] if len(ids) == 1 else None


def _due_date(value: str) -> date | None:
    return date.fromisoformat(value) if value else None


def _event(session: Session, job: Job, entity_type: str, entity_id: int, event_type: str, **payload) -> None:
    append_event(
        session,
        tenant_id=job.tenant_id,
        entity_type=entity_type,
        entity_id=entity_id,
        event_type=event_type,
        payload=payload,
    )


def _start(factory: sessionmaker, job_id: int) -> dict | None:
    """queued 인 작업만 running 으로 바꾼다(조건부 UPDATE: 같은 작업이 두 번 돌지 않게). 처리에 필요한 값을 돌려준다."""
    with factory() as session:
        now = utcnow()
        changed = session.execute(
            update(Job)
            .where(Job.id == job_id, Job.status == "queued")
            .values(status="running", attempts=Job.attempts + 1, started_at=now)
        ).rowcount
        if changed != 1:
            session.rollback()
            return None
        job = session.get(Job, job_id)
        meeting = session.get(Meeting, job.meeting_id)
        document = session.get(SourceDocument, meeting.source_document_id)
        _event(session, job, "job", job.id, "job.started", attempt=job.attempts)
        session.commit()
        return {"file_path": document.file_path or "", "held_at": meeting.held_at}


def _finish_no_content(factory: sessionmaker, job_id: int) -> None:
    with factory() as session:
        job = session.get(Job, job_id)
        meeting = session.get(Meeting, job.meeting_id)
        job.status, job.finished_at = "no_content", utcnow()
        meeting.status = "no_content"
        _event(session, job, "job", job.id, "job.no_content")
        _event(session, job, "meeting", meeting.id, "meeting.no_content")
        session.commit()


def _finish_completed(factory: sessionmaker, job_id: int, candidates: list[ActionItemCandidate], provenance: dict) -> None:
    """업무 생성 + 회의록 상태 결정 + 작업 완료를 한 트랜잭션으로 기록한다."""
    with factory() as session:
        job = session.get(Job, job_id)
        meeting = session.get(Meeting, job.meeting_id)
        document = session.get(SourceDocument, meeting.source_document_id)
        registrant = session.get(Account, document.registered_by)

        for candidate in candidates:
            item = ActionItem(
                tenant_id=job.tenant_id,
                meeting_id=meeting.id,
                title=candidate.task,
                assignee_id=_assignee_id(session, job.tenant_id, candidate.assignee),
                due_date=_due_date(candidate.due_date),
                due_undetermined=False,  # 빈 기한은 "보완 필요"(미확정은 관리자가 고르는 값)
                status="pending",  # 업무 확정은 이번 단계에서 하지 않는다
                evidence_start_sec=candidate.evidence_start_sec,
                evidence_quote=candidate.quote_text or None,
                **provenance,
            )
            session.add(item)
            session.flush()
            _event(session, job, "action_item", item.id, "item.created", assignee_name=candidate.assignee)

        now = utcnow()
        if registrant is not None and registrant.rank in CONFIRM_ON_REGISTRATION_RANKS:
            # 중간관리자·지시자 등록 = 등록 시 확정(부록 C-3)
            meeting.status, meeting.confirm_kind = "confirmed", "registration"
            meeting.confirmed_by, meeting.confirmed_at = registrant.id, now
            _event(session, job, "meeting", meeting.id, "meeting.confirmed", confirm_kind="registration")
        else:
            meeting.status = "awaiting_confirmation"
            _event(session, job, "meeting", meeting.id, "meeting.awaiting_confirmation")
        if meeting.auto_confirm_at is None:
            tenant = session.get(Tenant, job.tenant_id)
            meeting.auto_confirm_at = auto_confirm_at(meeting.first_created_at, tenant.auto_confirm_days)

        job.status, job.finished_at = "completed", now
        _event(session, job, "job", job.id, "job.completed", items=len(candidates))
        session.commit()


def _finish_failed(factory: sessionmaker, job_id: int, code: str) -> None:
    with factory() as session:
        job = session.get(Job, job_id)
        meeting = session.get(Meeting, job.meeting_id)
        job.status, job.error_code, job.finished_at = "failed", code, utcnow()
        meeting.status = "failed"
        _event(session, job, "job", job.id, "job.failed", error_code=code)
        _event(session, job, "meeting", meeting.id, "meeting.failed")
        session.commit()


def process_meeting(
    job_id: int,
    *,
    session_factory: sessionmaker | None = None,
    stt_factory: Callable[[], SttEngine] | None = None,
    extractor_factory: Callable[[], Extractor] | None = None,
) -> str | None:
    """작업 1건을 처리하고 최종 상태를 돌려준다. queued 가 아니면 아무것도 하지 않고 None."""
    factory = session_factory or SessionLocal
    started = _start(factory, job_id)
    if started is None:
        return None

    try:
        audio_path = Path(settings.upload_dir) / started["file_path"]
        if not started["file_path"] or not audio_path.is_file():
            raise FileNotFoundError("uploaded audio is missing")
        mime_type = AUDIO_MIME_TYPES.get(audio_path.suffix.lower().lstrip("."), "application/octet-stream")

        stt = (stt_factory or default_stt)()
        try:
            transcript = stt.transcribe(audio_path, mime_type)
        finally:
            stt.release()

        if is_no_speech(transcript):
            _finish_no_content(factory, job_id)
            return "no_content"

        # 추출기에는 회의 일시를 현지 시각(APP_TIMEZONE)으로 넘긴다. UTC 그대로면 날짜·요일이 하루 어긋날 수 있다
        local_held_at = started["held_at"].astimezone(ZoneInfo(settings.app_timezone))
        result = (extractor_factory or default_extractor)().extract(transcript, local_held_at)
        provenance = {
            "extract_model": result.extract_model,
            "prompt_version": result.prompt_version,
            "extracted_at": result.extracted_at,
        }
        _finish_completed(factory, job_id, result.items, provenance)
        return "completed"
    except Exception as exc:  # noqa: BLE001 — 백그라운드 작업은 예외를 밖으로 던지지 않고 failed 로 기록한다
        code = error_code(exc)
        log.warning("job %s failed: %s (%s)", job_id, code, type(exc).__name__)
        _finish_failed(factory, job_id, code)
        return "failed"


def run_job_in_background(job_id: int, session_factory: sessionmaker) -> None:
    """업로드 API 가 BackgroundTasks 로 부르는 기본 실행기."""
    process_meeting(job_id, session_factory=session_factory)


def get_job_runner() -> Callable[[int, sessionmaker], None]:
    """FastAPI 의존성: 업로드 후 작업을 실행할 함수(테스트에서 바꿔 끼워 직접 process_meeting 을 부를 수 있게)."""
    return run_job_in_background
