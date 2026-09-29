from datetime import datetime, timezone
from typing import Literal

from fastapi import APIRouter, HTTPException, Query, Response

from app.db import ACTION_ITEM_SORT, JOB_SORT, store
from app.schemas.models import ActionItem, AdminJob, Meeting

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


# ---------------- 관리자 API (v1.7 스텁: 인증 없음 → 운영에서는 서버 권한 검증 필수) ----------------
@router.get("/admin/jobs", response_model=list[AdminJob], response_model_by_alias=True)
def list_jobs(sort_key: str | None = Query(None, alias="sortKey"), direction: Direction = "asc"):
    _check_sort(sort_key, JOB_SORT)
    return store.list_jobs(sort_key, direction)


@router.post("/admin/jobs/{job_id}/retry", response_model=AdminJob, response_model_by_alias=True)
def retry_job(job_id: str):
    if store.get_job(job_id) is None:
        raise HTTPException(status_code=404, detail="job not found")
    if not store.retry_if_failed(job_id):  # 조건부 UPDATE: 확인과 변경을 한 번에
        raise HTTPException(status_code=409, detail="job is not failed")
    return store.get_job(job_id)


@router.post("/admin/jobs/{job_id}/kill", response_model=AdminJob, response_model_by_alias=True)
def kill_job(job_id: str):
    if store.get_job(job_id) is None:
        raise HTTPException(status_code=404, detail="job not found")
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    if not store.kill_if_processing(job_id, f"{now} [ERROR] killed by administrator; partial output discarded"):
        raise HTTPException(status_code=409, detail="job is not processing")
    return store.get_job(job_id)
