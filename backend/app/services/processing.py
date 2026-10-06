"""
음성 처리 작업 1건: queued → running → (STT → 전사문 저장) → no_content / (업무 추출 → 업무 생성) → completed, 예외 시 failed.

- 상태가 바뀔 때마다 사건 원장(events)에 기록한다.
- 로그에는 작업 id·상태·예외 종류만 남긴다. 전사문·키·파일 경로는 남기지 않는다.
- 전사문은 STT 직후 transcripts 에 저장한다(말소리 없음·추출 실패여도 원문 보존). 이벤트에는 길이·구간 수만 남긴다.
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
from app.models import Account, ActionItem, Job, Meeting, MeetingMinutes, SourceDocument, Tenant, Transcript, append_event
from app.models.minutes import MINUTES_FIELDS, NO_CONTENT
from app.models.common import utcnow
from app.pipeline.errors import error_code
from app.pipeline.evidence_time import apply_evidence_times
from app.services.mail import queue_immediate_new_minutes
from app.services.notices import create_confirm_notices
from app.services.reprocess import recompute_auto_confirm_after_reprocess
from app.pipeline.extractor import ActionItemCandidate, ExtractionResult, Extractor, FakeExtractor, make_gemini_extractor
from app.pipeline.stt import FakeStt, SttEngine, TranscriptResult, as_transcript_result, is_no_speech, make_gemini_stt

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


def _save_transcript(factory: sessionmaker, job_id: int, result: TranscriptResult, provider: str) -> None:
    """전사 원문을 저장한다(회의록당 1건). 같은 회의록에 이미 있으면 새 결과로 바꾼다(재처리 대비)."""
    with factory() as session:
        job = session.get(Job, job_id)
        transcript = session.scalar(select(Transcript).where(Transcript.meeting_id == job.meeting_id))
        if transcript is None:
            transcript = Transcript(tenant_id=job.tenant_id, meeting_id=job.meeting_id)
            session.add(transcript)
        transcript.full_text = result.text
        transcript.segments = result.segments
        transcript.stt_provider = provider
        session.flush()
        # 전사문 전문은 넣지 않는다(길이·구간 수만)
        _event(
            session, job, "transcript", transcript.id, "transcript.saved",
            length=len(result.text),
            segments=len(result.segments) if result.segments is not None else None,
            stt_provider=provider,
        )
        session.commit()


def _finish_no_content(factory: sessionmaker, job_id: int) -> None:
    with factory() as session:
        job = session.get(Job, job_id)
        meeting = session.get(Meeting, job.meeting_id)
        job.status, job.finished_at = "no_content", utcnow()
        meeting.status = "no_content"
        _event(session, job, "job", job.id, "job.no_content")
        _event(session, job, "meeting", meeting.id, "meeting.no_content")
        session.commit()


def _save_minutes(session: Session, job: Job, meeting: Meeting, result: ExtractionResult | None, engine: str) -> None:
    """회의록 5개 항목 저장(업무와 같은 트랜잭션). 항목이 없거나 응답이 없으면 "내용없음"(업무 저장에는 영향 없음).
    같은 회의록을 다시 처리하면 새 결과로 바꾼다(직권 수정 이력은 events 에 남아 있다).
    생성 사건은 따로 남기지 않는다(엔진·모델·생성 시각은 meeting_minutes 행에 있고, 기존 사건 순서도 바뀌지 않는다)."""
    drafted = result.minutes if result is not None and result.minutes else {}
    values = {key: (drafted.get(key) or NO_CONTENT) for key in MINUTES_FIELDS}
    row = session.get(MeetingMinutes, meeting.id)
    if row is None:
        row = MeetingMinutes(meeting_id=meeting.id, tenant_id=job.tenant_id)
        session.add(row)
    for key, value in values.items():
        setattr(row, key, value)
    row.engine = engine
    row.extract_model = result.extract_model if result is not None else ""
    row.prompt_version = result.minutes_prompt_version if result is not None else ""
    row.generated_at = utcnow()


def _finish_completed(
    factory: sessionmaker, job_id: int, candidates: list[ActionItemCandidate], provenance: dict,
    minutes: ExtractionResult | None = None, engine: str = "",
) -> None:
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

        _save_minutes(session, job, meeting, minutes, engine)

        now = utcnow()
        if registrant is not None and registrant.rank in CONFIRM_ON_REGISTRATION_RANKS:
            # 중간관리자·지시자 등록 = 등록 시 확정(부록 C-3)
            meeting.status, meeting.confirm_kind = "confirmed", "registration"
            meeting.confirmed_by, meeting.confirmed_at = registrant.id, now
            _event(session, job, "meeting", meeting.id, "meeting.confirmed", confirm_kind="registration")
            # 확정 안내: 등록자 본인은 제외하고 관리자 이상 참석자에게(같은 트랜잭션)
            create_confirm_notices(
                session, meeting=meeting, entity_type="meeting", entity_id=meeting.id,
                confirm_kind="registration", title=meeting.title, actor_id=registrant.id,
            )
        else:
            meeting.status = "awaiting_confirmation"
            _event(session, job, "meeting", meeting.id, "meeting.awaiting_confirmation")
        # 즉시 메일: 확정 대기/확정이 된 지금, 같은 트랜잭션에서 발송함에 쌓는다(재처리해도 dedupe_key 로 1회)
        queue_immediate_new_minutes(session, meeting)
        recompute_auto_confirm_after_reprocess(session, job, meeting, now)  # 재처리 성공이면 자동 확정 기간을 이 시각부터 다시(첫 처리·확정된 회의록은 불변)
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
            stt_result = as_transcript_result(stt.transcribe(audio_path, mime_type))
        finally:
            stt.release()
        provider = getattr(stt, "provider_name", None) or settings.stt_provider

        # 말소리 없음 판정·업무 추출보다 먼저 원문을 보존한다
        _save_transcript(factory, job_id, stt_result, provider)
        transcript = stt_result.text

        if is_no_speech(transcript):
            _finish_no_content(factory, job_id)
            return "no_content"

        # 추출기에는 회의 일시를 현지 시각(APP_TIMEZONE)으로 넘긴다. UTC 그대로면 날짜·요일이 하루 어긋날 수 있다
        local_held_at = started["held_at"].astimezone(ZoneInfo(settings.app_timezone))
        result = (extractor_factory or default_extractor)().extract(transcript, local_held_at)
        # 근거 시각은 Gemini 응답이 아니라 저장된 전사문과 인용문을 서버가 대조해 정한다(실패한 업무도 시각만 비우고 그대로 저장)
        items = apply_evidence_times(result.items, stt_result.text, stt_result.segments)
        provenance = {
            "extract_model": result.extract_model,
            "prompt_version": result.prompt_version,
            "extracted_at": result.extracted_at,
        }
        _finish_completed(factory, job_id, items, provenance, minutes=result, engine=provider)
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
