from fastapi import FastAPI

from app.api import action_items, auth, meeting_actions, meeting_queries, meetings, probe

app = FastAPI(title="Meeting Intelligence Backend (v2)")
app.include_router(auth.router)
app.include_router(meetings.router)
app.include_router(meeting_queries.router)
app.include_router(meeting_actions.router)
app.include_router(action_items.router)
# 3단계 검증용 보호 엔드포인트(업무 API가 생기면 제거)
app.include_router(probe.router)


@app.get("/api/health")
def health() -> dict[str, str]:
    # 서버 생존 확인용. DB는 건드리지 않는다
    return {"status": "ok"}
