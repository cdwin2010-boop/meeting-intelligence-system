"""회의록 질의응답 API(작업 89-1, 회의 1건): POST /api/meetings/{meeting_id}/chat — 계약 docs/contracts/meeting-chat-v1.json.

- 응답 전 오류(JSON): 403(삭제된 회의록) / 404(없거나 열람 권한 없음) / 409(정리된 회의록 없음) / 422 / 429 / 503(답변 엔진 없음·GPU 사용 중, Retry-After).
- 성공은 text/event-stream: meta → delta* → citations → done (실패하면 error). 검증이 끝난 답변만 delta 로 내보낸다(근거 없는 문장이 먼저 나가지 않는다).
- 질문·답변 내용은 어디에도 저장하지 않는다(DB·이벤트·로그). 로그에는 요청ID·회의록ID·상태·소요시간만 남긴다.
- 연결이 끊기거나 제한 시간을 넘으면 제너레이터를 닫아 추론(HTTP 스트림)을 취소하고 GPU 사용권을 푼다.
"""
import asyncio
import logging
import time
import uuid
from collections import defaultdict, deque
import os
from collections.abc import AsyncIterator, Awaitable, Callable
from threading import Lock

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import StreamingResponse
from pydantic import Field, field_validator
from sqlalchemy.orm import Session, sessionmaker

from app.api.schemas import CamelModel
from app.auth.access import get_visible_meeting
from app.auth.deps import get_current_account
from app.config import settings
from app.db import get_session
from app.meeting_qa import access
from app.meeting_qa.answer import build_messages, citation_item, finalize, sse
from app.meeting_qa.context import MODE_SUMMARY, QUICK_PROMPTS, build_context
from app.meeting_qa.llm import QaLlm, QaLlmError, make_qa_llm
from app.models import Account
from app.services import gpu_lease

log = logging.getLogger("app.meeting_qa")
router = APIRouter(prefix="/api/meetings", tags=["meeting-chat"])

QUESTION_MAX = 500
HISTORY_MAX_TURNS = 4
TURN_TEXT_MAX = 1000
_DELTA_CHARS = 24
_LEASE_MARGIN_SECONDS = 15


class ChatTurn(CamelModel):
    question: str = Field(max_length=TURN_TEXT_MAX)
    answer: str = Field(max_length=TURN_TEXT_MAX)


class ChatIn(CamelModel):
    question: str
    history: list[ChatTurn] = Field(default_factory=list, max_length=HISTORY_MAX_TURNS)
    quick_prompt: str | None = None

    @field_validator("question")
    @classmethod
    def _question_length(cls, value: str) -> str:
        text = value.strip()
        if not 1 <= len(text) <= QUESTION_MAX:
            raise ValueError(f"질문은 1~{QUESTION_MAX}자여야 합니다")
        return text

    @field_validator("quick_prompt")
    @classmethod
    def _quick_prompt(cls, value: str | None) -> str | None:
        if value is not None and value not in QUICK_PROMPTS:
            raise ValueError("quickPrompt 는 decisions, my_tasks, schedule 또는 null 이어야 합니다")
        return value


# ---- 사용량 제한(계정당 분당, 메모리 내): 질문 내용은 보관하지 않고 시각만 센다 ----
_hits: dict[int, deque[float]] = defaultdict(deque)
_hits_lock = Lock()


def reset_rate_limit() -> None:
    with _hits_lock:
        _hits.clear()


def _take_rate_slot(account_id: int) -> int:
    """슬롯을 얻으면 0, 초과면 다시 시도할 때까지 남은 초."""
    limit, window, now = settings.qa_rate_limit_per_minute, 60.0, time.monotonic()
    with _hits_lock:
        q = _hits[account_id]
        while q and now - q[0] >= window:
            q.popleft()
        if len(q) >= limit:
            return max(1, int(window - (now - q[0])) + 1)
        q.append(now)
        return 0


def get_qa_llm_factory() -> Callable[[], QaLlm]:
    """답변 엔진을 만드는 함수(의존성: 테스트에서 가짜 LLM 을 돌려주는 함수로 바꾼다). 권한 판정 뒤에 부른다."""
    return make_qa_llm


_END = object()


async def _event_stream(
    *, request_id: str, meeting_id: int, scope: str = "meeting", llm: QaLlm, messages, ctx, factory: sessionmaker | None, lease_ref: int, started: float,
    disconnected: Callable[[], Awaitable[bool]] | None = None,
) -> AsyncIterator[str]:
    """SSE 이벤트 생성. 추론은 스레드풀에서 조각 단위로 받고, 조각 사이마다 연결 끊김·제한 시간을 확인해 닫는다(추론 취소)."""
    state = "ok"
    iterator = None
    try:
        yield sse("meta", {
            "requestId": request_id, "contextMode": ctx.mode or MODE_SUMMARY,
            "model": getattr(llm, "model_label", "") or settings.effective_llm_provider,
            "confirmedPendingIncluded": ctx.pending_included,
        })
        deadline = started + settings.qa_timeout_seconds
        parts: list[str] = []
        size = 0
        cap = int(settings.qa_max_output_tokens / settings.qa_tokens_per_char) * 2 + 200
        try:
            iterator = iter(llm.stream(messages, max_tokens=settings.qa_max_output_tokens, timeout=settings.qa_timeout_seconds))
            while True:
                if disconnected is not None and await disconnected():
                    state = "cancelled"
                    return
                if time.monotonic() > deadline:
                    raise QaLlmError("llm_timeout", "응답 시간이 초과되었습니다")
                piece = await run_in_threadpool(next, iterator, _END)
                if piece is _END:
                    break
                parts.append(piece)
                size += len(piece)
                if size > cap:
                    break
        except QaLlmError as exc:
            state = exc.code
            yield sse("error", {"code": exc.code, "message": exc.message})
            return
        final = finalize("".join(parts), ctx)
        for i in range(0, len(final.text), _DELTA_CHARS):
            yield sse("delta", {"text": final.text[i : i + _DELTA_CHARS]})
        yield sse("citations", {"items": [citation_item(s) for s in final.cited]})
        state = "no_answer" if final.no_answer else "grounded"
        yield sse("done", {"grounded": not final.no_answer, "noAnswer": final.no_answer, "elapsedMs": int((time.monotonic() - started) * 1000)})
    except (GeneratorExit, asyncio.CancelledError):
        state = "cancelled"  # 연결 끊김: finally 가 추론 스트림을 닫고 사용권을 푼다
        raise
    finally:
        if iterator is not None and hasattr(iterator, "close"):
            try:
                iterator.close()  # HTTP 스트림을 닫아 추론을 취소한다
            except Exception as exc:  # noqa: BLE001
                log.warning("qa request=%s stream close failed: %s", request_id, type(exc).__name__)
        if factory is not None:
            try:
                gpu_lease.release(factory, gpu_lease.HOLDER_QA, lease_ref)
            except Exception as exc:  # noqa: BLE001 — 만료로 풀린다
                log.warning("qa request=%s lease release failed: %s", request_id, type(exc).__name__)
        log.info("qa request=%s %s=%s status=%s elapsed_ms=%d", request_id, scope, meeting_id, state, int((time.monotonic() - started) * 1000))


@router.post("/{meeting_id}/chat")
def chat(
    meeting_id: int,
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
    meeting = get_visible_meeting(session, account, meeting_id)
    if meeting is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="회의록을 찾을 수 없습니다")
    state = access.ask_state(session, meeting)
    if state == access.STATE_DELETED:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="삭제된 회의록에는 질문할 수 없습니다")
    if state == access.STATE_NO_MINUTES:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="정리된 회의록이 없어 질문할 수 없습니다")
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
    lease_ref = int.from_bytes(os.urandom(4), "big") & 0x7FFFFFFF  # 질의마다 다른 점유 표식(질의끼리도 겹치지 않는다)
    if llm.uses_gpu:
        factory = sessionmaker(bind=session.get_bind(), expire_on_commit=False)
        ttl = settings.qa_timeout_seconds + _LEASE_MARGIN_SECONDS
        ok, info = gpu_lease.acquire(factory, gpu_lease.HOLDER_QA, lease_ref, ttl)
        if not ok:
            retry = min(info.retry_after_seconds(), 60) if info else 5
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail={"code": "gpu_busy", "message": "음성 변환 중이라 잠시 뒤 다시 질문해 주세요"},
                headers={"Retry-After": str(retry)},
            )
    try:
        ctx = build_context(session, meeting, account, body.question, body.quick_prompt)
        messages = build_messages(ctx, body.question, [(t.question, t.answer) for t in body.history])
    except Exception:
        if factory is not None:
            gpu_lease.release(factory, gpu_lease.HOLDER_QA, lease_ref)
        raise
    return StreamingResponse(
        _event_stream(request_id=request_id, meeting_id=meeting.id, llm=llm, messages=messages, ctx=ctx, factory=factory, lease_ref=lease_ref, started=started, disconnected=request.is_disconnected),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no", "X-Request-Id": request_id},
    )
