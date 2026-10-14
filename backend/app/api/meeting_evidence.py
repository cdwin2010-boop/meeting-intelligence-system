"""근거 위치 재계산: POST /api/meetings/{id}/recompute-evidence (작업 B-1c-1).
- 권한: 관리자(manager)·지시자(executive)만(담당자 403). 볼 수 없는 회의록은 404. 적용(dryRun=false)은 보류·종료·삭제 회의록 409(기존 잠금 규칙).
- dryRun=true 는 저장 없이 건수만 돌려준다(확인 창의 대상 건수). 적용에는 reason(사유) 필수(공백뿐이면 422).
- AI·외부 호출 없음, 전사문 원문 불변. 규칙은 app/services/evidence_recompute.py.
- 응답: targets(재계산 대상=진행 중 업무) · changed(변경) · approx(근사) · unchanged(미변경) · excluded(확정 제외) · applied."""
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.api.schemas import CamelModel
from app.auth.access import get_visible_meeting
from app.auth.deps import get_current_account, has_rank
from app.auth.locks import reject_if_locked
from app.db import get_session
from app.models import Account
from app.services import evidence_recompute

router = APIRouter(prefix="/api/meetings", tags=["meeting-evidence"])

REASON_MAX = 500


class RecomputeBody(CamelModel):
    reason: str = ""
    dry_run: bool = False


class RecomputeOut(CamelModel):
    targets: int
    changed: int
    approx: int
    unchanged: int
    excluded: int
    applied: bool


@router.post("/{meeting_id}/recompute-evidence", response_model=RecomputeOut, response_model_by_alias=True)
def recompute_evidence(
    meeting_id: int,
    body: RecomputeBody,
    account: Account = Depends(get_current_account),
    session: Session = Depends(get_session),
) -> RecomputeOut:
    meeting = get_visible_meeting(session, account, meeting_id)
    if meeting is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="회의록을 찾을 수 없습니다")
    if not has_rank(account, "manager"):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="관리자 또는 지시자만 근거 위치를 다시 계산할 수 있습니다")
    results, excluded = evidence_recompute.compute(session, meeting.id)
    summary = evidence_recompute.summarize(results, excluded)
    if body.dry_run:
        return RecomputeOut(**summary.__dict__, applied=False)
    reason = body.reason.strip()
    if not reason or len(reason) > REASON_MAX:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="사유를 입력해 주세요(최대 500자)")
    reject_if_locked(meeting)
    evidence_recompute.apply(session, meeting, account.id, reason, results, summary)
    session.commit()
    return RecomputeOut(**summary.__dict__, applied=True)
