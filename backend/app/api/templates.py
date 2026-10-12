"""내려받기용 양식 파일(작업 85-1, 경로 이동 85-2). 회의 번호 경로(/api/meetings/{meeting_id})와 겹치지 않도록 /api/templates 아래에 둔다."""
from urllib.parse import quote

from fastapi import APIRouter, Depends, Response
from sqlalchemy.orm import Session

from app.auth.deps import forbid_system_admin, get_current_account
from app.db import get_session
from app.models import Account
from app.services.transcript_template import TEMPLATE_FILENAME, build_transcript_template

router = APIRouter(prefix="/api/templates", tags=["templates"])


@router.get("/transcript-xlsx")
def download_transcript_template(account: Account = Depends(get_current_account), session: Session = Depends(get_session)) -> Response:
    """엑셀 자료 파일 양식(.xlsx)을 즉시 만들어 내려준다(저장소에 파일 없음). 올리기 권한과 같은 규칙(시스템 관리자 제외)."""
    forbid_system_admin(session, account)
    return Response(
        content=build_transcript_template(),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(TEMPLATE_FILENAME)}", "Cache-Control": "no-store"},
    )
