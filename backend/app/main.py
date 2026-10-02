from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import accounts, action_items, auth, me, meeting_actions, meeting_queries, meeting_speakers, meetings, probe
from app.config import settings

app = FastAPI(title="Meeting Intelligence Backend (v2)")
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
app.include_router(meeting_queries.router)
app.include_router(meeting_actions.router)
app.include_router(action_items.router)
app.include_router(me.router)
app.include_router(accounts.router)
app.include_router(meeting_speakers.router)
# 3단계 검증용 보호 엔드포인트(업무 API가 생기면 제거)
app.include_router(probe.router)


@app.get("/api/health")
def health() -> dict[str, str]:
    # 서버 생존 확인용. DB는 건드리지 않는다
    return {"status": "ok"}
