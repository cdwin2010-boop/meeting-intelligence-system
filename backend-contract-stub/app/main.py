from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import settings
from app.pipeline.service import recover_interrupted_jobs
from app.routers.api import router


@asynccontextmanager
async def lifespan(_app: FastAPI):
    # (v1.9.10) 서버 시작 때 한 번: 이전 서버에서 처리 중·대기였던 업로드 작업을 failed("서버 재시작으로 중단됨")로 → Retry 가능
    recover_interrupted_jobs()
    yield


app = FastAPI(title="Meeting Decision Intelligence API (contract stub)", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,  # .env에서 읽은 허용 출처 목록
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],  # 필요한 것만 열어 둔다 (PUT: 화자 이름 저장, v1.9.9)
    allow_headers=["*"],
)

app.include_router(router)


@app.get("/health")
def health() -> dict[str, str]:
    # 서버가 살아 있는지 확인하는 가장 쉬운 주소
    return {"status": "ok"}
