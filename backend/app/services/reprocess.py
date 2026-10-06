"""실패한 회의록 재처리와 서버 시작 복구. 처리 파이프라인(services/processing.process_meeting)은 그대로 다시 쓰고, 여기서는 작업만 만든다.

재처리(start_reprocess)
- 보관된 음성 원본으로 새 작업(queued)을 만들고 회의록을 processing 으로 돌린 뒤, 호출부가 같은 실행기(get_job_runner)로 process_meeting 을 돌린다.
- 중복 방지: 실패한 회의록에는 업무·5개 항목이 저장되지 않는다(업무 생성·5개 항목·상태 결정이 한 트랜잭션). 전사문은 STT 직후 저장되지만
  _save_transcript 가 같은 회의록의 전사문을 새 결과로 바꾸므로(회의록당 1건) 겹치지 않는다. 5개 항목도 회의록당 1행을 갱신한다.
- 회의록 상태는 첫 처리와 같은 규칙(등록자 직급에 따라 등록 시 확정 또는 확정 대기)이고 자동 확정 시각(auto_confirm_at)은 바꾸지 않는다.
- 이력은 공통 변경 이력(구분 "재처리")에 남긴다: 실행자·시각·이전 오류 코드. 사유는 받지 않는다.

서버 시작 복구(recover_interrupted_jobs)
- 서버가 멈추면 백그라운드 작업도 사라지므로 queued·running 으로 남은 작업과 그 회의록(processing)을 failed(server_restarted)로 바꾼다. 자동 재실행은 하지 않는다.
"""
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.models import Account, Job, Meeting, append_event
from app.models.common import utcnow
from app.services.history import KIND_MEETING_REPROCESS, record_change

SERVER_RESTARTED = "server_restarted"
ACTIVE_JOB_STATUSES = ("queued", "running")


def active_job_exists(session: Session, meeting_id: int) -> bool:
    return session.scalar(select(Job.id).where(Job.meeting_id == meeting_id, Job.status.in_(ACTIVE_JOB_STATUSES)).limit(1)) is not None


def latest_job(session: Session, meeting_id: int) -> Job | None:
    return session.scalar(select(Job).where(Job.meeting_id == meeting_id).order_by(Job.id.desc()).limit(1))


def start_reprocess(session: Session, meeting: Meeting, actor: Account) -> Job:
    """새 작업을 만들고 회의록을 처리 중으로 돌린다(커밋은 호출부). 호출부가 권한·상태·음성 파일을 먼저 확인한다."""
    previous = latest_job(session, meeting.id)
    previous_code = previous.error_code if previous is not None else None
    job = Job(tenant_id=meeting.tenant_id, meeting_id=meeting.id, status="queued")
    session.add(job)
    session.flush()
    meeting.status = "processing"
    append_event(
        session, tenant_id=meeting.tenant_id, entity_type="job", entity_id=job.id, event_type="job.queued",
        actor_account_id=actor.id, payload={"reprocess": True},
    )
    record_change(
        session, tenant_id=meeting.tenant_id, target_type="meeting", target_id=meeting.id, kind=KIND_MEETING_REPROCESS,
        actor_id=actor.id, before={"status": "failed", "errorCode": previous_code}, after={"status": "processing", "jobId": job.id},
        extra={"jobId": job.id, "previousErrorCode": previous_code},
    )
    return job


def recover_interrupted_jobs(factory: sessionmaker) -> int:
    """queued·running 로 남은 작업을 failed(server_restarted)로 바꾸고, processing 인 그 회의록도 failed 로. 바꾼 작업 수를 돌려준다."""
    changed = 0
    with factory() as session:
        jobs = session.scalars(select(Job).where(Job.status.in_(ACTIVE_JOB_STATUSES)).order_by(Job.id)).all()
        for job in jobs:
            job.status, job.error_code, job.finished_at = "failed", SERVER_RESTARTED, utcnow()
            append_event(session, tenant_id=job.tenant_id, entity_type="job", entity_id=job.id, event_type="job.failed",
                         payload={"error_code": SERVER_RESTARTED})
            meeting = session.get(Meeting, job.meeting_id)
            if meeting is not None and meeting.status == "processing":
                meeting.status = "failed"
                append_event(session, tenant_id=job.tenant_id, entity_type="meeting", entity_id=meeting.id, event_type="meeting.failed")
            changed += 1
        session.commit()
    return changed
