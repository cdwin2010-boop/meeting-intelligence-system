"""프로젝트 질의응답 API(작업 89-2b): POST /api/projects/{project_id}/chat — 계약 docs/contracts/project-chat-v1.json.

회의 1건 질의(meeting_chat.py)와 같은 요청·응답(SSE)·오류·사용량 제한·GPU 사용권·저장 안 함 정책을 쓰고, 답변 근거만 그 프로젝트에 소속된
회의록(요청자가 열람 가능한 것)으로 한정한다. 범위는 서버가 프로젝트 ID 로 정하며 요청 본문으로 바꿀 수 없다.
- 404: 프로젝트가 없거나 열람 권한 없음(존재 여부를 알려 주지 않는다) 또는 질의 기능 꺼짐 / 409: 질문할 수 있는(정리된) 회의록이 없음.
"""
import os
import time
import uuid
from collections.abc import Callable

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session, sessionmaker

from app.api.meeting_chat import _LEASE_MARGIN_SECONDS, ChatIn, _event_stream, _take_rate_slot, get_qa_llm_factory
from app.auth.deps import get_current_account
from app.config import settings
from app.db import get_session
from app.meeting_qa import access
from app.meeting_qa.answer import PROJECT_SYSTEM_PROMPT, build_messages
from app.meeting_qa.llm import QaLlm, QaLlmError
from app.meeting_qa.project_context import build_project_context
from app.models import Account
from app.services import gpu_lease
from app.services import projects as svc

router = APIRouter(prefix="/api/projects", tags=["project-chat"])


@router.post("/{project_id}/chat")
def project_chat(
    project_id: int,
    body: ChatIn,
    request: Request,
    account: Account = Depends(get_current_account),
    session: Session = Depends(get_session),
    llm_factory: Callable[[], QaLlm] = Depends(get_qa_llm_factory),
) -> StreamingResponse:
    started = time.monotonic()
    request_id = uuid.uuid4().hex[:12]
    if not settings.qa_enabled:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not Found")
    project = svc.get_visible_project(session, account, project_id)
    if project is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="프로젝트를 찾을 수 없습니다")
    meetings = access.project_askable_meetings(session, account, project, limit=settings.qa_project_max_meetings)
    if not meetings:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="질문할 수 있는 정리된 회의록이 이 프로젝트에 아직 없습니다")
    wait = _take_rate_slot(account.id)
    if wait:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail="질문이 너무 잦습니다. 잠시 뒤 다시 시도해 주세요", headers={"Retry-After": str(wait)}
        )
    try:
        llm = llm_factory()
    except QaLlmError as exc:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail={"code": exc.code, "message": exc.message}) from None
    factory: sessionmaker | None = None
    lease_ref = int.from_bytes(os.urandom(4), "big") & 0x7FFFFFFF
    if llm.uses_gpu:
        factory = sessionmaker(bind=session.get_bind(), expire_on_commit=False)
        ok, info = gpu_lease.acquire(factory, gpu_lease.HOLDER_QA, lease_ref, settings.qa_timeout_seconds + _LEASE_MARGIN_SECONDS)
        if not ok:
            retry = min(info.retry_after_seconds(), 60) if info else 5
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail={"code": "gpu_busy", "message": "음성 변환 중이라 잠시 뒤 다시 질문해 주세요"},
                headers={"Retry-After": str(retry)},
            )
    try:
        ctx = build_project_context(session, meetings, account, body.question, body.quick_prompt)
        messages = build_messages(ctx, body.question, [(t.question, t.answer) for t in body.history], system=PROJECT_SYSTEM_PROMPT)
    except Exception:
        if factory is not None:
            gpu_lease.release(factory, gpu_lease.HOLDER_QA, lease_ref)
        raise
    return StreamingResponse(
        _event_stream(
            request_id=request_id, meeting_id=project.id, scope="project", llm=llm, messages=messages, ctx=ctx, factory=factory,
            lease_ref=lease_ref, started=started, disconnected=request.is_disconnected,
        ),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no", "X-Request-Id": request_id},
    )
