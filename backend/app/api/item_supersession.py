"""유사 업무 검색(GET /api/action-items/{id}/similar)과 업무 대체(POST /api/action-items/{newItemId}/supersede).
- 검색은 읽기 전용 온디맨드이고 열람 기록을 남기지 않는다. 대체는 사람이(프로젝트 총괄·지시자) 실행한다(자동 병합 없음).
- 직권 종료·직권 취소는 기존 업무 종결·삭제 API 를 그대로 쓴다(새 API 없음)."""
from datetime import date, datetime

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy.orm import Session

from app.api.action_items import get_visible_item
from app.api.action_schemas import ReasonBody
from app.api.meeting_schemas import AccountRef
from app.api.schemas import CamelModel
from app.auth.deps import get_current_account
from app.auth.locks import reject_if_locked
from app.db import get_session
from app.models import Account, Meeting
from app.services import supersession as svc
from app.services.similar_items import find_similar

router = APIRouter(prefix="/api/action-items", tags=["action-items"])


class SimilarMeeting(CamelModel):
    id: int
    title: str
    held_at: datetime


class SimilarItemOut(CamelModel):
    item_id: int
    title: str
    assignee: AccountRef | None
    due_date: date | None
    status: str
    score: int
    reasons: list[str]
    meeting: SimilarMeeting


class SupersedeBody(ReasonBody):
    supersedes_item_id: int


class SupersedeOut(CamelModel):
    old_item_id: int
    new_item_id: int
    project_id: int
    changed: bool


def _conflict(message: str) -> HTTPException:
    return HTTPException(status_code=status.HTTP_409_CONFLICT, detail=message)


@router.get("/{item_id}/similar", response_model=list[SimilarItemOut], response_model_by_alias=True)
def similar_items(item_id: int, account: Account = Depends(get_current_account), session: Session = Depends(get_session)) -> list[SimilarItemOut]:
    """이 업무와 비슷한, 같은 프로젝트 과거 회의록의 열려 있는 업무(점수순). 그 회의록을 열람할 수 있는 사람 전원이 실행할 수 있다."""
    item = get_visible_item(session, account, item_id)
    meeting = session.get(Meeting, item.meeting_id)
    project = svc.project_of_meeting(session, meeting.id)
    if project is None:
        raise _conflict("프로젝트 회의록이 아닙니다")
    reject_if_locked(meeting)  # 보류·종료·삭제 단계는 409
    if not svc.is_open(session, item):
        raise _conflict("열려 있는 업무만 검색할 수 있습니다")
    result = []
    for candidate in find_similar(session, account, item, meeting, project.id):
        assignee = session.get(Account, candidate.item.assignee_id) if candidate.item.assignee_id is not None else None
        result.append(SimilarItemOut(
            item_id=candidate.item.id, title=candidate.item.title,
            assignee=AccountRef(id=assignee.id, name=assignee.name) if assignee else None,
            due_date=candidate.item.due_date, status=candidate.item.status, score=candidate.score, reasons=candidate.reasons,
            meeting=SimilarMeeting(id=candidate.meeting.id, title=candidate.meeting.title, held_at=candidate.meeting.held_at),
        ))
    return result


@router.post("/{new_item_id}/supersede", response_model=SupersedeOut, response_model_by_alias=True)
def supersede(
    new_item_id: int,
    body: SupersedeBody,
    response: Response,
    account: Account = Depends(get_current_account),
    session: Session = Depends(get_session),
) -> SupersedeOut:
    """과거 업무를 이 업무가 대체한다(프로젝트 총괄·지시자). 이미 같은 쌍이면 200(변경 없음), 새로 대체하면 201."""
    new_item = get_visible_item(session, account, new_item_id)
    changed = svc.supersede_item(session, account, new_item, body.supersedes_item_id, body.reason)
    project = svc.project_of_meeting(session, new_item.meeting_id)
    session.commit()
    response.status_code = status.HTTP_201_CREATED if changed else status.HTTP_200_OK
    return SupersedeOut(old_item_id=body.supersedes_item_id, new_item_id=new_item.id, project_id=project.id, changed=changed)
