"""음성 회의록 업로드. 접수(파일 저장 + 원천 문서·회의록·작업 생성) 후 즉시 202, 처리는 백그라운드."""
import uuid
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path, PurePath

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, HTTPException, UploadFile, status
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.api.schemas import CamelModel
from app.auth.deps import get_current_account
from app.config import settings
from app.db import get_session, get_session_factory
from app.models import Account, Job, Meeting, MeetingParticipant, SourceDocument, Tenant, append_event
from app.models.common import utcnow
from app.services.classification import save_classification, validate_classification
from app.services.processing import auto_confirm_at, get_job_runner

router = APIRouter(prefix="/api/meetings", tags=["meetings"])

_CHUNK = 1024 * 1024


class UploadAccepted(CamelModel):
    meeting_id: int
    job_id: int


def _bad_request(message: str) -> HTTPException:
    return HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=message)


def _parse_held_at(value: str) -> datetime:
    """오프셋이 있는 ISO 8601 만 받는다(예: 2026-10-01T14:00:00+09:00). 저장은 UTC."""
    try:
        parsed = datetime.fromisoformat(value.strip())
    except ValueError:
        raise _bad_request("heldAt 형식이 올바르지 않습니다(ISO 8601).") from None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise _bad_request("heldAt 에는 시간대 오프셋이 필요합니다(예: +09:00).")
    return parsed.astimezone(timezone.utc)


def _parse_participant_ids(values: list[str] | None) -> list[int]:
    """participantIds 는 여러 번 보내거나 쉼표로 묶어 보낼 수 있다."""
    ids: list[int] = []
    for raw in values or []:
        for part in raw.split(","):
            part = part.strip()
            if not part:
                continue
            if not part.isdigit():
                raise _bad_request("participantIds 는 계정 id(숫자)여야 합니다.")
            ids.append(int(part))
    return list(dict.fromkeys(ids))  # 중복 제거, 순서 유지


def _check_participants(session: Session, account: Account, ids: list[int]) -> None:
    """같은 고객사 계정만 참석자로 허용. 다른 고객사·없는 id 는 구분하지 않고 같은 400(존재 여부를 알려 주지 않음)."""
    if not ids:
        return
    found = session.scalars(select(Account.id).where(Account.id.in_(ids), Account.tenant_id == account.tenant_id)).all()
    if len(found) != len(ids):
        raise _bad_request("같은 고객사 계정만 참석자로 지정할 수 있습니다.")


def _extension(filename: str) -> str:
    return PurePath(filename or "").suffix.lower().lstrip(".")


def _save_upload(file: UploadFile, tenant_id: int, ext: str) -> str:
    """UPLOAD_DIR/{tenant_id}/{uuid}.{ext} 로 저장하고 UPLOAD_DIR 기준 상대 경로를 돌려준다.
    원본 파일명은 경로에 쓰지 않는다. 크기 초과·빈 파일이면 지우고 거절한다."""
    limit = settings.max_upload_mb * 1024 * 1024
    folder = Path(settings.upload_dir) / str(tenant_id)
    folder.mkdir(parents=True, exist_ok=True)
    name = f"{uuid.uuid4().hex}.{ext}"
    partial = folder / f"{name}.part"
    size = 0
    try:
        with partial.open("wb") as out:
            while chunk := file.file.read(_CHUNK):
                size += len(chunk)
                if size > limit:
                    raise HTTPException(
                        status_code=status.HTTP_413_CONTENT_TOO_LARGE,
                        detail=f"파일이 너무 큽니다(최대 {settings.max_upload_mb}MB).",
                    )
                out.write(chunk)
        if size == 0:
            raise _bad_request("빈 파일입니다.")
        partial.replace(folder / name)
    except BaseException:
        partial.unlink(missing_ok=True)
        raise
    return f"{tenant_id}/{name}"


@router.post("/upload", status_code=status.HTTP_202_ACCEPTED, response_model=UploadAccepted, response_model_by_alias=True)
def upload_meeting(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    title: str = Form(""),
    held_at: str = Form(..., alias="heldAt"),
    participant_ids: list[str] | None = Form(None, alias="participantIds"),
    meeting_type: str | None = Form(None, alias="meetingType"),
    project_id: int | None = Form(None, alias="projectId"),
    account: Account = Depends(get_current_account),
    session: Session = Depends(get_session),
    session_factory: sessionmaker = Depends(get_session_factory),
    job_runner: Callable[[int, sessionmaker], None] = Depends(get_job_runner),
) -> UploadAccepted:
    held_at_utc = _parse_held_at(held_at)
    ids = _parse_participant_ids(participant_ids)
    _check_participants(session, account, ids)

    ext = _extension(file.filename or "")
    if ext not in settings.allowed_audio_extensions:
        allowed = ", ".join(sorted(settings.allowed_audio_extensions))
        raise HTTPException(status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, detail=f"허용하지 않는 파일 형식입니다({allowed}).")

    # 회의 유형·프로젝트 연결(선택): 파일 저장과 회의록 생성 전에 검증을 끝낸다
    classified_type, classified_project = validate_classification(session, account, meeting_type, project_id)

    # 제목이 비어 있으면 원본 파일명(폴더 부분 제거)을 보조로 쓴다
    original_name = PurePath((file.filename or "").replace("\\", "/")).name
    meeting_title = (title.strip() or original_name or "제목 없음")[:300]

    relative_path = _save_upload(file, account.tenant_id, ext)
    try:
        now = utcnow()
        tenant = session.get(Tenant, account.tenant_id)
        document = SourceDocument(
            tenant_id=account.tenant_id,
            origin="audio_minutes",
            doc_type="회의록",
            title=meeting_title,
            file_path=relative_path,
            registered_by=account.id,
        )
        session.add(document)
        session.flush()
        meeting = Meeting(
            tenant_id=account.tenant_id,
            source_document_id=document.id,
            title=meeting_title,
            held_at=held_at_utc,
            status="processing",
            first_created_at=now,
            auto_confirm_at=auto_confirm_at(now, tenant.auto_confirm_days),
        )
        session.add(meeting)
        session.flush()
        for participant_id in ids:
            session.add(MeetingParticipant(meeting_id=meeting.id, account_id=participant_id))
        save_classification(session, meeting, account, classified_type, classified_project)
        job = Job(tenant_id=account.tenant_id, meeting_id=meeting.id, status="queued")
        session.add(job)
        session.flush()
        append_event(
            session,
            tenant_id=account.tenant_id,
            entity_type="meeting",
            entity_id=meeting.id,
            event_type="meeting.created",
            actor_account_id=account.id,
            payload={"origin": "audio_minutes", "job_id": job.id, "meetingType": classified_type, "projectId": classified_project},
        )
        append_event(
            session,
            tenant_id=account.tenant_id,
            entity_type="job",
            entity_id=job.id,
            event_type="job.queued",
            actor_account_id=account.id,
        )
        session.commit()
    except BaseException:
        session.rollback()
        (Path(settings.upload_dir) / relative_path).unlink(missing_ok=True)
        raise

    background_tasks.add_task(job_runner, job.id, session_factory)
    return UploadAccepted(meeting_id=meeting.id, job_id=job.id)
