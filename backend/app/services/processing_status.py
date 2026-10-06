"""내가 올린 회의록의 처리 현황(왼쪽 메뉴 "처리 현황"). 읽기 전용이라 열람 기록(meeting_views)을 남기지 않는다.
- 대상: 현재 사용자가 등록한 회의록 중 최근 PROCESSING_TRACK_HOURS(기본 24)시간 안에 처리가 시작된 것(시작 전 대기 작업은 만든 시각), 삭제 단계 제외,
  회의록마다 가장 최근 작업 1건, 최신 작업 순으로 최대 10건. 같은 고객사 안의 본인 등록분만(다른 사용자·다른 회사는 포함하지 않는다).
- 경과 초(elapsedSec)는 서버가 응답 시점 기준으로 계산한다: 대기·처리 중은 지금까지, 끝난 작업은 시작~종료.
- 오류 코드는 분류 코드만(원본 예외 문구·키·파일 경로·설정값은 내려주지 않는다). canReprocess 는 허용 동작 판정(reprocess_meeting)을 그대로 부른다.
"""
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.auth.actions import REPROCESS_MEETING, meeting_allowed_actions
from app.config import settings
from app.models import Account, Job, Meeting, SourceDocument
from app.models.common import utcnow
from app.services.reprocess import safe_error_code

PROCESSING_LIST_LIMIT = 10
_RUNNING = ("queued", "running")


@dataclass
class ProcessingEntry:
    meeting_id: int
    job_id: int
    title: str
    status: str
    error_code: str | None
    elapsed_sec: int
    finished_at: datetime | None
    can_reprocess: bool


def _elapsed(job: Job, now: datetime) -> int:
    start = job.started_at or job.created_at
    end = now if job.status in _RUNNING else (job.finished_at or start)
    return max(0, int((end - start).total_seconds()))


def my_processing(session: Session, account: Account, now: datetime | None = None) -> list[ProcessingEntry]:
    now = now or utcnow()
    cutoff = now - timedelta(hours=settings.processing_track_hours)
    latest = select(func.max(Job.id).label("job_id")).group_by(Job.meeting_id).subquery()
    rows = session.execute(
        select(Job, Meeting)
        .join(latest, latest.c.job_id == Job.id)
        .join(Meeting, Meeting.id == Job.meeting_id)
        .join(SourceDocument, SourceDocument.id == Meeting.source_document_id)
        .where(
            Meeting.tenant_id == account.tenant_id,
            SourceDocument.registered_by == account.id,
            ~Meeting.deleted,
            func.coalesce(Job.started_at, Job.created_at) >= cutoff,
        )
        .order_by(Job.id.desc())
        .limit(PROCESSING_LIST_LIMIT)
    ).all()
    return [
        ProcessingEntry(
            meeting_id=meeting.id, job_id=job.id, title=meeting.title, status=job.status, error_code=safe_error_code(job.error_code),
            elapsed_sec=_elapsed(job, now), finished_at=job.finished_at,
            can_reprocess=REPROCESS_MEETING in meeting_allowed_actions(session, account, meeting),
        )
        for job, meeting in rows
    ]
