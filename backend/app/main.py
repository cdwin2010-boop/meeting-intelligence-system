import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import (
    accounts, action_items, admin, auth, departments, item_supersession, projects, me, meeting_actions, meeting_hold, meeting_lifecycle, meeting_queries, meeting_speakers,
    meeting_audio, meeting_minutes, me_processing, meeting_process_now, meeting_reprocess, meeting_update, meetings, probe, system, templates, workload,
)
from app.config import settings
from app.db import SessionLocal
from app.jobs import workload_snapshot
from app.pipeline.local_llm import log_startup_warning
from app.services.reprocess import recover_interrupted_jobs

log = logging.getLogger("app.main")


@asynccontextmanager
async def lifespan(_app: FastAPI):
    # 시작 때: 가짜 처리 모드면 경고, queued·running 으로 남은 작업은 failed(server_restarted)로 정리(자동 재실행 없음)
    if settings.stt_provider == "fake":
        log.warning("가짜(fake) 처리 모드로 실행 중입니다. 업로드한 음성은 실제로 처리되지 않습니다(STT_PROVIDER=fake).")
    log_startup_warning(settings.effective_llm_provider, settings.local_llm_base_url, settings.local_llm_allow_external_host)
    try:
        # queue 모드: 대기·처리 중 작업은 단독 워커가 이어받으므로 서버 시작 때 실패로 바꾸지 않는다(워커가 recover_stale 로 복구)
        recovered = 0 if settings.processing_mode == "queue" else recover_interrupted_jobs(SessionLocal)
        if recovered:
            log.warning("서버 재시작으로 중단된 처리 작업 %d건을 실패(server_restarted)로 정리했습니다.", recovered)
    except Exception as exc:  # noqa: BLE001 — 복구 실패로 서버가 못 뜨면 안 된다
        log.warning("interrupted job recovery failed: %s", type(exc).__name__)
    # 업무 처리 현황 전일 집계: 백그라운드 작업(끄거나 test 환경이면 시작하지 않음). 켜질 때 바로 한 번 확인해 놓친 집계를 따라잡는다
    task = asyncio.create_task(workload_snapshot.workload_loop(SessionLocal)) if workload_snapshot.should_start() else None
    try:
        yield
    finally:
        if task is not None:
            task.cancel()


app = FastAPI(title="Meeting Intelligence Backend (v2)", lifespan=lifespan)
# 허용 출처만 브라우저 호출 허용. 인증은 Authorization 헤더(쿠키 아님)라 credentials 는 끈다
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_methods=["GET", "POST", "PATCH", "PUT", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type"],
    allow_credentials=False,
)
app.include_router(auth.router)
app.include_router(meetings.router)
app.include_router(templates.router)
app.include_router(meeting_queries.router)
app.include_router(meeting_audio.router)
app.include_router(meeting_minutes.router)
app.include_router(meeting_update.router)
app.include_router(meeting_reprocess.router)
app.include_router(meeting_process_now.router)
app.include_router(system.router)
app.include_router(admin.router)
app.include_router(me_processing.router)
app.include_router(meeting_actions.router)
app.include_router(meeting_hold.router)
app.include_router(meeting_lifecycle.router)
app.include_router(action_items.router)
app.include_router(item_supersession.router)
app.include_router(me.router)
app.include_router(workload.router)
app.include_router(accounts.router)
app.include_router(departments.router)
app.include_router(departments.me_router)
app.include_router(projects.router)
app.include_router(meeting_speakers.router)
# 3단계 검증용 보호 엔드포인트(업무 API가 생기면 제거)
app.include_router(probe.router)


@app.get("/api/health")
def health() -> dict[str, str]:
    # 서버 생존 확인용. DB는 건드리지 않는다
    return {"status": "ok"}
