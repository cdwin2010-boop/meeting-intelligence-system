"""내부 전용 상태 확인(작업 B-1c-1): GET /internal/queue-status -> {"active": n, "queued": n} (숫자만).
- 요청의 실제 연결 주소(request.client.host, 소켓 peer)가 루프백(127.0.0.1·::1)일 때만 응답한다. 그 밖은 403.
  X-Forwarded-For 같은 전달 헤더는 읽지 않는다(신뢰하지 않음). 인증 없음: 회의 제목·내용·사용자 정보는 응답에 넣지 않는다.
- 설정 INTERNAL_QUEUE_STATUS_ENABLED(기본 켬)가 꺼져 있으면 404.
- 이 경로는 /api 아래가 아니라서 프론트(웹 화면)가 중계하지 않는다. 앞단 리버스 프록시를 둘 때는 이 경로를 막아야 한다
  (프록시가 같은 PC 에서 접속하면 peer 가 루프백으로 보이므로)."""
from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import settings
from app.db import get_session
from app.models import Job

router = APIRouter(prefix="/internal", tags=["internal"])

LOOPBACK_HOSTS = frozenset({"127.0.0.1", "::1"})


@router.get("/queue-status")
def queue_status(request: Request, session: Session = Depends(get_session)) -> dict[str, int]:
    if not settings.internal_queue_status_enabled:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="not found")
    peer = request.client.host if request.client else ""
    if peer not in LOOPBACK_HOSTS:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="forbidden")
    rows = dict(session.execute(select(Job.status, func.count()).where(Job.status.in_(("running", "queued"))).group_by(Job.status)).all())
    return {"active": int(rows.get("running", 0)), "queued": int(rows.get("queued", 0))}
