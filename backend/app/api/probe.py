"""3단계 검증용: 직급 권한 의존성(require_rank)이 실제 라우트에서 동작하는지 확인하는 보호 엔드포인트.
업무 API가 생기면 지운다."""
from fastapi import APIRouter, Depends

from app.auth.deps import require_rank
from app.models import Account

router = APIRouter(prefix="/api/_probe", tags=["probe"])


@router.get("/manager")
def manager_probe(account: Account = Depends(require_rank("manager"))) -> dict[str, str]:
    # 3단계 검증용: manager 이상만 200
    return {"status": "ok", "rank": account.rank}
