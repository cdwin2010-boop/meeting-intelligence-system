from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import settings
from app.routers.api import router

app = FastAPI(title="Meeting Decision Intelligence API (contract stub)")

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
