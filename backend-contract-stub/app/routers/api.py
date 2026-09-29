from datetime import datetime, timezone
from typing import Literal

from fastapi import APIRouter, BackgroundTasks, File, Form, HTTPException, Query, Response, UploadFile

from app.config import settings
from app.db import ACTION_ITEM_SORT, JOB_SORT, store
from app.pipeline.service import run_job
from app.schemas.models import ActionItem, AdminJob, JobStatus, Meeting, UploadReceipt
from app.upload_storage import find_upload, incoming_path, job_path
from app.uploads import (
    ALLOWED_EXTENSIONS, detect_audio_kind, estimate_audio_seconds, expected_local_minutes, extension_of, parse_started_at,
)

router = APIRouter(prefix="/api")

Direction = Literal["asc", "desc"]  # 그 외 값은 FastAPI가 자동으로 422로 거절


def _check_sort(sort_key: str | None, allowed: dict[str, str]) -> None:
    # 허용 목록에 없는 sortKey는 400. (값을 SQL에 이어 붙이지 않는다)
    if sort_key is not None and sort_key not in allowed:
        raise HTTPException(status_code=400, detail=f"invalid sortKey: {sort_key}")


@router.get("/meetings/{meeting_id}", response_model=Meeting, response_model_by_alias=True)
def get_meeting(meeting_id: str):
    meeting = store.get_meeting(meeting_id)
    if meeting is None:
        raise HTTPException(status_code=404, detail="meeting not found")
    return meeting


@router.get("/meetings/{meeting_id}/action-items", response_model=list[ActionItem], response_model_by_alias=True)
def list_action_items(meeting_id: str, sort_key: str | None = Query(None, alias="sortKey"), direction: Direction = "asc"):
    _check_sort(sort_key, ACTION_ITEM_SORT)
    if store.get_meeting(meeting_id) is None:
        raise HTTPException(status_code=404, detail="meeting not found")
    return store.list_action_items(meeting_id, sort_key, direction)


@router.delete("/action-items/{item_id}", status_code=204)
def delete_action_item(item_id: str) -> Response:
    if not store.delete_action_item(item_id):
        raise HTTPException(status_code=404, detail="action item not found")
    return Response(status_code=204)


# ---------------- 음성 등록 (docs/API-CONTRACT.md "음성 등록 API") ----------------
CHUNK = 1024 * 1024  # 1MB씩 읽는다


@router.post("/meetings", status_code=202, response_model=UploadReceipt, response_model_by_alias=True)
async def upload_meeting(
    background: BackgroundTasks,
    file: UploadFile | None = File(None),
    title: str | None = Form(None),
    started_at: str | None = Form(None, alias="startedAt"),
    engine: str = Form("gemini-api"),
    force_local: bool = Form(False, alias="forceLocal"),
):
    # 검사 순서: 입력값(400) → 확장자(415) → 크기(413/400) → 파일 내용(415) → GPU 가드(409)
    #           → 로컬 엔진 미지원(501, gemini 모드) → API 키 없음(503, gemini 모드).
    # 하나라도 걸리면 회의·작업을 저장하지 않는다.
    title = (title or "").strip()
    if not 1 <= len(title) <= 200:
        raise HTTPException(status_code=400, detail="title is required (1-200 chars)")
    if not started_at or (started := parse_started_at(started_at)) is None:
        raise HTTPException(status_code=400, detail="startedAt must be an ISO 8601 datetime")
    if engine not in ("gemini-api", "faster-whisper"):
        raise HTTPException(status_code=400, detail="engine must be gemini-api or faster-whisper")
    if file is None or not file.filename:
        raise HTTPException(status_code=400, detail="file is required")

    ext = extension_of(file.filename)
    if ext not in ALLOWED_EXTENSIONS:
        raise HTTPException(status_code=415, detail="unsupported file extension")

    # 크기는 Content-Length 를 믿지 않고 실제로 읽으면서 센다.
    # 받은 청크는 임시 파일(UPLOAD_DIR/.incoming-*)에 쓰고, 검사에 하나라도 걸리면 그 파일을 지운다.
    limit = settings.max_upload_mb * 1024 * 1024
    temp = incoming_path(ext)
    try:
        size, head = 0, b""
        with temp.open("wb") as out:
            while chunk := await file.read(CHUNK):
                if not head:
                    head = chunk[:16]
                size += len(chunk)
                if size > limit:
                    raise HTTPException(status_code=413, detail=f"file is larger than {settings.max_upload_mb} MB")
                out.write(chunk)
        if size == 0:
            raise HTTPException(status_code=400, detail="file is empty")

        # 이름만 .mp3 인 가짜 파일을 걸러낸다. 확장자와 실제 종류가 달라도 거절.
        if detect_audio_kind(head) != ext:
            raise HTTPException(status_code=415, detail="file content is not a valid audio file")

        audio_seconds = estimate_audio_seconds(size)
        if engine == "faster-whisper" and not force_local:
            expected = expected_local_minutes(audio_seconds)
            if expected >= settings.gpu_guard_threshold_minutes:
                # 409 + 본문 code: 프론트가 경고창(Gemini로 전환 / 그래도 로컬 / 취소)을 띄운다.
                raise HTTPException(status_code=409, detail={"code": "gpu_guard", "expectedMinutes": expected})

        # Gemini 파이프라인에는 아직 로컬 엔진이 없다 (GPU 가드 경고가 먼저)
        if settings.stt_provider == "gemini" and engine == "faster-whisper":
            raise HTTPException(status_code=501, detail="local engine (faster-whisper) is not supported yet")
        # 키가 없어도 서버는 켜지되, 처리할 수 없는 작업은 받지 않는다
        if settings.uses_gemini and not settings.has_gemini_key:
            raise HTTPException(status_code=503, detail="Gemini API key is not configured on the server")

        meeting_id, job_id = store.create_upload(title, started.isoformat(), engine, audio_seconds)
        temp.replace(job_path(job_id, ext))  # 작업 ID가 정해진 뒤 이름을 바꿔 보관 (경로는 응답에 넣지 않는다)
    finally:
        temp.unlink(missing_ok=True)  # 검사 실패 등으로 남은 임시 파일 정리 (이름을 바꿨으면 이미 없음)

    background.add_task(run_job, job_id)  # 응답을 보낸 뒤 뒤에서 처리 (기다리지 않는다)
    return {"meetingId": meeting_id, "jobId": job_id, "status": "queued"}


@router.get("/jobs/{job_id}", response_model=JobStatus, response_model_by_alias=True)
def get_job_status(job_id: str):
    status = store.get_job_status(job_id)
    if status is None:
        raise HTTPException(status_code=404, detail="job not found")
    return status


# ---------------- 관리자 API (v1.7 스텁: 인증 없음 → 운영에서는 서버 권한 검증 필수) ----------------
@router.get("/admin/jobs", response_model=list[AdminJob], response_model_by_alias=True)
def list_jobs(sort_key: str | None = Query(None, alias="sortKey"), direction: Direction = "asc"):
    _check_sort(sort_key, JOB_SORT)
    return store.list_jobs(sort_key, direction)


@router.post("/admin/jobs/{job_id}/retry", response_model=AdminJob, response_model_by_alias=True)
def retry_job(job_id: str, background: BackgroundTasks):
    job = store.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="job not found")
    is_upload = bool(store.job_meeting_id(job_id))
    # 업로드 작업인데 음성 파일이 없어졌으면 다시 처리할 수 없다 (실패 상태가 아니면 아래 조건부 UPDATE가 409)
    if is_upload and job["status"] == "failed" and find_upload(job_id) is None:
        raise HTTPException(status_code=409, detail="uploaded audio file is missing; upload it again")
    if not store.retry_if_failed(job_id):  # 조건부 UPDATE: 확인과 변경을 한 번에
        raise HTTPException(status_code=409, detail="job is not failed")
    if is_upload:  # 업로드로 만들어진 작업만 다시 처리한다 (시드 작업은 그대로 queued)
        background.add_task(run_job, job_id)
    return store.get_job(job_id)


@router.post("/admin/jobs/{job_id}/kill", response_model=AdminJob, response_model_by_alias=True)
def kill_job(job_id: str):
    if store.get_job(job_id) is None:
        raise HTTPException(status_code=404, detail="job not found")
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    if not store.kill_if_processing(job_id, f"{now} [ERROR] killed by administrator; partial output discarded"):
        raise HTTPException(status_code=409, detail="job is not processing")
    return store.get_job(job_id)
