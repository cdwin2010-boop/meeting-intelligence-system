"""실패한 회의록 재처리: POST /api/meetings/{id}/reprocess.
권한은 회의록 확정 권한(can_confirm_meeting: 등록 관리자·지시자). 볼 수 없으면 404, 권한 없음 403, 보류·종료·삭제 409.
회의록 상태가 failed 가 아니거나 처리 중인 작업이 있거나 보관된 음성 원본이 없으면 409(서버 문구)."""
from collections.abc import Callable

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, status
from sqlalchemy.orm import Session, sessionmaker

from app.api.meeting_audio import audio_file_or_none
from app.api.schemas import CamelModel
from app.auth.access import can_confirm_meeting, get_visible_meeting
from app.auth.deps import get_current_account
from app.auth.locks import reject_if_locked
from app.db import get_session, get_session_factory
from app.models import Account
from app.services.processing import get_job_runner
from app.services.reprocess import active_job_exists, start_reprocess

router = APIRouter(prefix="/api/meetings", tags=["meeting-reprocess"])


class ReprocessAccepted(CamelModel):
    meeting_id: int
    job_id: int


def _conflict(message: str) -> HTTPException:
    return HTTPException(status_code=status.HTTP_409_CONFLICT, detail=message)


@router.post("/{meeting_id}/reprocess", status_code=status.HTTP_202_ACCEPTED, response_model=ReprocessAccepted, response_model_by_alias=True)
def reprocess_meeting(
    meeting_id: int,
    background_tasks: BackgroundTasks,
    account: Account = Depends(get_current_account),
    session: Session = Depends(get_session),
    session_factory: sessionmaker = Depends(get_session_factory),
    job_runner: Callable[[int, sessionmaker], None] = Depends(get_job_runner),
) -> ReprocessAccepted:
    meeting = get_visible_meeting(session, account, meeting_id)
    if meeting is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="회의록을 찾을 수 없습니다")
    if not can_confirm_meeting(session, account, meeting):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="회의록을 총괄하는 관리자 또는 지시자만 다시 처리할 수 있습니다")
    reject_if_locked(meeting)
    if meeting.status != "failed":
        raise _conflict("처리에 실패한 회의록만 다시 처리할 수 있습니다")
    if active_job_exists(session, meeting.id):
        raise _conflict("이미 처리 중인 작업이 있습니다")
    if audio_file_or_none(session, meeting) is None:
        raise _conflict("보관된 음성 파일이 없어 다시 처리할 수 없습니다. 음성을 다시 올려 주세요")
    job = start_reprocess(session, meeting, account)
    session.commit()
    background_tasks.add_task(job_runner, job.id, session_factory)
    return ReprocessAccepted(meeting_id=meeting.id, job_id=job.id)
