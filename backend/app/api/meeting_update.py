"""수정 회의록 업로드 갱신(미리보기·적용)과 수기 업무 등록.

- 권한은 모두 같다: 열람(get_visible_meeting, 못 보면 404) → 회의록 확정 권한(can_confirm_meeting: 지시자·총괄 관리자, 아니면 403)
  → 보류·종료·삭제 회의록 409(reject_if_locked). 새 규칙을 만들지 않는다.
- POST /{id}/update-upload/preview : multipart(file, choices). 파일을 검증하고 변경 예정 목록만 돌려준다. 아무것도 저장하지 않는다.
- POST /{id}/update-upload/apply   : 같은 file·choices. 오류·동명이인 선택 누락이 있으면 아무것도 적용하지 않고 400(목록만).
  choices 는 JSON 글자 {"row:3": 계정ID, "participant:김철수": 계정ID} (미리보기의 ambiguities[].key 가 그 키).
- POST /{id}/action-items : 수기 업무 1건 등록(업무명 필수, 담당자 계정·기한 날짜 또는 미확정은 선택 — 비면 AI 업무처럼 보완 필요).
  근거는 "등록자 직권 지정", 등록자·시각은 변경 이력에 남는다. 전사·추출이 실패한 회의록에도 쓸 수 있다."""
import json
from datetime import date

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from pydantic import Field, field_validator
from sqlalchemy.orm import Session

from app.api.action_items import is_blank, item_out
from app.auth.locks import reject_if_locked
from app.api.meeting_schemas import ActionItemOut
from app.api.schemas import CamelModel
from app.auth.access import can_confirm_meeting, get_visible_meeting
from app.auth.deps import get_current_account
from app.config import settings
from app.db import get_session
from app.models import Account, Meeting
from app.services.upload_update import TITLE_MAX, UploadFileError, apply_plan, build_plan, create_manual_item

router = APIRouter(prefix="/api/meetings", tags=["meeting-update"])


class ManualItemIn(CamelModel):
    title: str = Field(max_length=TITLE_MAX)
    assignee_id: int | None = None
    due_date: date | None = None
    due_undetermined: bool = False

    @field_validator("title")
    @classmethod
    def _title_not_blank(cls, value: str) -> str:
        if is_blank(value):
            raise ValueError("title 은 비워 둘 수 없습니다")
        return value.strip()


def _http(code: int, detail) -> HTTPException:
    return HTTPException(status_code=code, detail=detail)


def _editable_meeting(session: Session, account: Account, meeting_id: int) -> Meeting:
    meeting = get_visible_meeting(session, account, meeting_id)
    if meeting is None:
        raise _http(status.HTTP_404_NOT_FOUND, "회의록을 찾을 수 없습니다")
    if not can_confirm_meeting(session, account, meeting):
        raise _http(status.HTTP_403_FORBIDDEN, "회의록 수정·업무 등록은 이 회의록을 확정할 수 있는 사람만 할 수 있습니다")
    reject_if_locked(meeting)
    return meeting


def _parse_choices(raw: str) -> dict[str, int]:
    if not raw.strip():
        return {}
    try:
        value = json.loads(raw)
        if not isinstance(value, dict) or not all(isinstance(k, str) and isinstance(v, int) and not isinstance(v, bool) for k, v in value.items()):
            raise ValueError
        return value
    except ValueError:
        raise _http(status.HTTP_400_BAD_REQUEST, "choices 는 {\"키\": 계정ID} 모양의 JSON 이어야 합니다") from None


async def _read_upload(file: UploadFile) -> bytes:
    limit = settings.update_max_file_kb * 1024
    data = await file.read(limit + 1)  # 한도를 넘는 만큼은 읽지 않는다
    return data


def _plan(session: Session, meeting: Meeting, data: bytes, choices: dict[str, int]):
    try:
        return build_plan(session, meeting, data, choices)
    except UploadFileError as exc:
        raise _http(exc.status, exc.message) from None


@router.post("/{meeting_id}/update-upload/preview")
async def preview_update(
    meeting_id: int,
    file: UploadFile = File(...),
    choices: str = Form(""),
    account: Account = Depends(get_current_account),
    session: Session = Depends(get_session),
) -> dict:
    meeting = _editable_meeting(session, account, meeting_id)
    plan = _plan(session, meeting, await _read_upload(file), _parse_choices(choices))
    session.rollback()  # 미리보기는 아무것도 저장하지 않는다
    return plan.preview()


@router.post("/{meeting_id}/update-upload/apply")
async def apply_update(
    meeting_id: int,
    file: UploadFile = File(...),
    choices: str = Form(""),
    account: Account = Depends(get_current_account),
    session: Session = Depends(get_session),
) -> dict:
    meeting = _editable_meeting(session, account, meeting_id)
    plan = _plan(session, meeting, await _read_upload(file), _parse_choices(choices))
    if plan.errors:
        # 전부 적용하거나 아무것도 적용하지 않는다: 오류 목록만 돌려준다
        raise _http(status.HTTP_400_BAD_REQUEST, {"message": "파일에 오류가 있어 적용하지 않았습니다", "errors": [e.out() for e in plan.errors]})
    if plan.ambiguities:
        raise _http(status.HTTP_400_BAD_REQUEST, {"message": "동명이인을 선택해야 합니다", "ambiguities": plan.ambiguities})
    try:
        result = apply_plan(session, meeting, account, plan)
        session.commit()
    except Exception:
        session.rollback()
        raise
    return result


@router.post("/{meeting_id}/action-items", response_model=ActionItemOut, response_model_by_alias=True, status_code=status.HTTP_201_CREATED)
def add_manual_item(
    meeting_id: int,
    body: ManualItemIn,
    account: Account = Depends(get_current_account),
    session: Session = Depends(get_session),
) -> ActionItemOut:
    meeting = _editable_meeting(session, account, meeting_id)
    if body.due_date is not None and body.due_undetermined:
        raise _http(status.HTTP_400_BAD_REQUEST, "기한 날짜와 '미확정'을 함께 지정할 수 없습니다")
    if body.assignee_id is not None:
        assignee = session.get(Account, body.assignee_id)
        if assignee is None or assignee.tenant_id != meeting.tenant_id or not assignee.is_active:
            # 다른 고객사·없는 계정·비활성 계정을 구분하지 않는다(업무 수정 API 와 같은 문구)
            raise _http(status.HTTP_400_BAD_REQUEST, "같은 고객사의 활성 계정만 담당자로 지정할 수 있습니다")
    item = create_manual_item(
        session, meeting, account, title=body.title, assignee_id=body.assignee_id,
        due_date=body.due_date, due_undetermined=body.due_undetermined,
    )
    session.commit()
    return item_out(session, item)
