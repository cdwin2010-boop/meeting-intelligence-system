"""즉시 변환: POST /api/meetings/{id}/process-now (작업 88-1).
정책: 등록된 파일은 대기열에서 순서대로(FIFO) 변환한다. 이 요청은 대기 중인 작업을 대기열 맨 앞(우선순위 상향)으로 올릴 뿐이고,
진행 중인 작업을 멈추거나 동시에 돌리지 않는다(GPU 1건씩). 처리기가 놀고 있으면 다음 폴링에서 바로 시작하고, 다른 작업이 진행 중이면 그 작업이 끝난 직후 시작한다.
권한: 관리자(manager)·지시자(executive)만. 담당자(staff)는 403. 볼 수 없는 회의록(다른 고객사·열람 권한 없음·시스템 관리자)은 404.
상태: 대기만 승격(이미 올려진 건은 200 멱등, 이력 중복 없음). 진행 중·완료·실패·대기 작업 없음·처리 방식이 queue 가 아님·보류/종료/삭제 잠금은 409(한국어 사유).
이력: 올린 사람·시각·사유("즉시 변환 요청")를 공통 변경 이력에 한 건 남긴다."""
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.api.schemas import CamelModel
from app.auth.access import get_visible_meeting
from app.auth.deps import get_current_account, has_rank
from app.auth.locks import reject_if_locked
from app.config import settings
from app.db import get_session
from app.models import Account
from app.services.task_queue import QueueConflict, promote_immediate, worker_alive

router = APIRouter(prefix="/api/meetings", tags=["meeting-process-now"])


class ProcessNowOut(CamelModel):
    meeting_id: int
    job_id: int | None
    status: str  # 항상 queued(대기). 진행·완료·실패는 409
    queue_ahead: int  # 이 작업보다 먼저 시작할 대기 작업 수
    prioritized: bool  # 우선 처리로 올려져 있음(항상 true)
    already_prioritized: bool  # 이미 올려져 있던 요청(멱등 응답)
    waiting_for_prior: bool  # 선행 작업 때문에 바로 시작하지 못함
    message: str
    worker_alive: bool  # 워커 생존 신호가 최근인가(false 면 워커를 확인해야 시작된다)


@router.post("/{meeting_id}/process-now", response_model=ProcessNowOut, response_model_by_alias=True)
def process_now(
    meeting_id: int,
    account: Account = Depends(get_current_account),
    session: Session = Depends(get_session),
) -> ProcessNowOut:
    meeting = get_visible_meeting(session, account, meeting_id)
    if meeting is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="회의록을 찾을 수 없습니다")
    if not has_rank(account, "manager"):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="관리자 또는 지시자만 즉시 변환을 요청할 수 있습니다")
    reject_if_locked(meeting)
    if settings.processing_mode != "queue":
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="이 서버는 대기열 없이 바로 변환하므로 즉시 변환 요청이 필요하지 않습니다")
    try:
        result = promote_immediate(session, meeting, account)
    except QueueConflict as conflict:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=conflict.message) from conflict
    session.commit()
    info = result.info
    return ProcessNowOut(
        meeting_id=meeting.id, job_id=result.job_id, status="queued", queue_ahead=info.ahead, prioritized=True,
        already_prioritized=result.already, waiting_for_prior=info.waiting_for_prior, message=info.message,
        worker_alive=worker_alive(session),
    )
