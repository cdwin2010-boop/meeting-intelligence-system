"""처리 경고(작업 81-1): 일부 청크만 실패하는 등 처리는 완료됐지만 확인이 필요한 사항을 이벤트(job.processing_warning)로 남기고 읽는다.
경고 문구에는 전사 본문·주소·키를 넣지 않는다(구간 번호와 사유 분류만). 새 표는 없다."""
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Event, Job, append_event

WARNING_EVENT = "job.processing_warning"
DETAIL_EVENT = "job.llm_error_detail"  # 서버 오류 응답 본문 앞 500자(마스킹됨). 관리자 이상에게만 내려준다(작업 B-1d)
DETAIL_MAX_CHARS = 500
DETAIL_MAX_COUNT = 5


def record_processing_warnings(session: Session, job: Job, warnings) -> None:
    """작업의 처리 경고를 이벤트로 남긴다(호출부 트랜잭션에 포함, 커밋은 호출부)."""
    for message in warnings:
        append_event(
            session, tenant_id=job.tenant_id, entity_type="job", entity_id=job.id, event_type=WARNING_EVENT,
            payload={"message": str(message)},
        )


def record_llm_error_details(session: Session, job: Job, details) -> None:
    """LLM 서버가 준 오류 응답 본문 일부를 이력으로 남긴다(호출부 트랜잭션에 포함). 앞 500자·최대 5건."""
    for text in list(details)[:DETAIL_MAX_COUNT]:
        value = str(text or "").strip()[:DETAIL_MAX_CHARS]
        if value:
            append_event(
                session, tenant_id=job.tenant_id, entity_type="job", entity_id=job.id, event_type=DETAIL_EVENT, payload={"detail": value},
            )


def llm_error_details_of(session: Session, meeting_id: int) -> list[str]:
    """가장 최근 처리 작업의 오류 응답 본문 목록(없으면 빈 목록)."""
    job_id = session.scalar(select(Job.id).where(Job.meeting_id == meeting_id).order_by(Job.id.desc()).limit(1))
    if job_id is None:
        return []
    rows = session.scalars(
        select(Event.payload).where(Event.entity_type == "job", Event.entity_id == job_id, Event.event_type == DETAIL_EVENT)
        .order_by(Event.id)
    ).all()
    return [str(p.get("detail", "")) for p in rows if isinstance(p, dict) and p.get("detail")]


def processing_warnings_of(session: Session, meeting_id: int) -> list[str]:
    """가장 최근 처리 작업의 처리 경고 목록(없으면 빈 목록). 재처리하면 새 작업의 경고만 보인다."""
    job_id = session.scalar(select(Job.id).where(Job.meeting_id == meeting_id).order_by(Job.id.desc()).limit(1))
    if job_id is None:
        return []
    rows = session.scalars(
        select(Event.payload).where(Event.entity_type == "job", Event.entity_id == job_id, Event.event_type == WARNING_EVENT)
        .order_by(Event.id)
    ).all()
    return [str(p.get("message", "")) for p in rows if isinstance(p, dict) and p.get("message")]
