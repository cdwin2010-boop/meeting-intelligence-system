"""허용 동작 목록(allowedActions): "이 사용자가 이 회의록·업무에서 지금 할 수 있는 동작"을 한 곳에서 계산한다.
화면은 이 목록으로 버튼을 보이거나 숨긴다(프론트에 직급·상태 조건을 하드코딩하지 않는다).

- 판정 규칙을 새로 만들지 않는다: 권한은 기존 함수(can_confirm_meeting·can_write_item·has_rank), 보류·종료·삭제 잠금은 reject_if_locked 를 그대로 부른다.
  이 모듈은 "동작 이름 → 기존 판정 + 상태 조건"의 대응표일 뿐이다.
- 목록은 화면 표시용이다. 실제 거부(403·409)는 각 API 가 그대로 판정하며 최종 안전장치다.
- 동작 이름은 아래 상수 한 곳에서만 정의한다(snake_case, 상태값 표기와 같은 방식).
"""
from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.auth.access import VIEW_ALL_RANKS, can_confirm_meeting, can_write_item
from app.auth.deps import has_rank
from app.auth.locks import HOLDABLE_STATUSES, reject_if_locked
from app.models import Account, ActionItem, Meeting, Project
from app.models.closure import PHASES
from app.services.projects import is_approver, is_project_lead
from app.services.reprocess import active_job_exists

# ---------------- 회의록 단위 동작 ----------------
CONFIRM_MEETING = "confirm_meeting"  # 회의록 확정(확정 대기일 때)
HOLD_MEETING = "hold_meeting"  # 보류
RESUME_MEETING = "resume_meeting"  # 재개(보류 중일 때)
END_MEETING = "end_meeting"  # 직권 종료
DELETE_MEETING = "delete_meeting"  # 회의록 삭제
EDIT_MINUTES = "edit_minutes"  # 5개 항목 직권 수정
UPLOAD_UPDATE = "upload_update"  # 수정 회의록 업로드
ADD_ITEM = "add_item"  # 업무 추가(수기 등록)
EDIT_SPEAKERS = "edit_speakers"  # 화자 지정
REPROCESS_MEETING = "reprocess_meeting"  # 실패한 회의록 재처리
RESOLVE_CHANGE_REQUEST = "resolve_change_request"  # 수정 요청 해결(수락·반려)
REQUEST_CHANGE = "request_change"  # 수정 요청 작성(회의록 전체·업무 모두 같은 이름)
DOWNLOAD_EXCEL = "download_excel"  # 엑셀 다운로드
VIEW_HISTORY = "view_history"  # 변경 이력 보기

# ---------------- 업무 단위 동작(REQUEST_CHANGE 는 위와 같은 이름) ----------------
CONFIRM_ITEM = "confirm_item"
CLOSE_ITEM = "close_item"
DELETE_ITEM = "delete_item"
SET_ASSIGNEE = "set_assignee"  # 담당자 지정·변경
SET_DUE = "set_due"  # 기한 입력·변경(날짜 또는 미확정)

# ---------------- 프로젝트 단위 동작(프로젝트 상세의 allowedActions) ----------------
CHANGE_LEAD = "change_lead"  # 총괄 변경
MANAGE_MEMBERS = "manage_members"  # 참여자 추가·역할 변경·제거
APPROVE_PROJECT = "approve_project"  # 프로젝트 승인(대기 중일 때)
REJECT_PROJECT = "reject_project"  # 프로젝트 반려(대기 중일 때)
PROJECT_ACTIONS = (CHANGE_LEAD, MANAGE_MEMBERS, APPROVE_PROJECT, REJECT_PROJECT)

MEETING_ACTIONS = (
    CONFIRM_MEETING, HOLD_MEETING, RESUME_MEETING, END_MEETING, DELETE_MEETING, EDIT_MINUTES, UPLOAD_UPDATE, ADD_ITEM,
    EDIT_SPEAKERS, REPROCESS_MEETING, RESOLVE_CHANGE_REQUEST, REQUEST_CHANGE, DOWNLOAD_EXCEL, VIEW_HISTORY,
)
ITEM_ACTIONS = (CONFIRM_ITEM, CLOSE_ITEM, DELETE_ITEM, SET_ASSIGNEE, SET_DUE, REQUEST_CHANGE)

# 직권 종료할 수 있는 회의록 상태(app/api/meeting_lifecycle.py 의 ENDABLE_STATUSES 와 같다)
_ENDABLE_STATUSES = ("awaiting_confirmation", "confirmed")
# 수정 요청 해결 최소 직급(app/api/meeting_actions.py 의 RESOLVE_MIN_RANK 와 같다)
_RESOLVE_MIN_RANK = "manager"


def is_locked(meeting: Meeting, *, allow_on_hold: bool = False) -> bool:
    """보류·종료·삭제 잠금: 쓰기 API 가 409 로 거부하는 조건(reject_if_locked)과 같은 판정."""
    try:
        reject_if_locked(meeting, allow_on_hold=allow_on_hold)
    except HTTPException:
        return True
    return False


def meeting_allowed_actions(session: Session, account: Account, meeting: Meeting) -> list[str]:
    """회의록 단위 허용 동작(MEETING_ACTIONS 순서). 열람 판정을 통과한 회의록에만 부른다."""
    allowed = [REQUEST_CHANGE, DOWNLOAD_EXCEL, VIEW_HISTORY]
    if has_rank(account, _RESOLVE_MIN_RANK):
        allowed.append(RESOLVE_CHANGE_REQUEST)
    if can_confirm_meeting(session, account, meeting):
        locked = is_locked(meeting)
        holdable = meeting.status in HOLDABLE_STATUSES
        if not locked:
            allowed += [EDIT_MINUTES, UPLOAD_UPDATE, ADD_ITEM, EDIT_SPEAKERS]
            if meeting.status == "awaiting_confirmation":
                allowed.append(CONFIRM_MEETING)
            if meeting.status == "failed" and not active_job_exists(session, meeting.id):
                allowed.append(REPROCESS_MEETING)
            if holdable:
                allowed.append(HOLD_MEETING)
            if meeting.status in _ENDABLE_STATUSES:
                allowed.append(END_MEETING)
        if holdable and meeting.on_hold and not is_locked(meeting, allow_on_hold=True):
            allowed.append(RESUME_MEETING)
        if not meeting.deleted and meeting.status != "processing":
            allowed.append(DELETE_MEETING)
    return [name for name in MEETING_ACTIONS if name in allowed]


def item_allowed_actions(
    session: Session, account: Account, item: ActionItem, meeting: Meeting, *, lead: bool | None = None
) -> list[str]:
    """업무 단위 허용 동작(ITEM_ACTIONS 순서). lead 는 회의록 단위에서 이미 구한 can_confirm_meeting 결과(없으면 여기서 구한다)."""
    if item.status == "deleted":
        return []
    allowed = [REQUEST_CHANGE]
    if not is_locked(meeting):
        if can_write_item(session, account, item, meeting):
            if item.status == "pending":
                allowed.append(CONFIRM_ITEM)
            if item.status == "confirmed":
                allowed.append(CLOSE_ITEM)
            if item.status in ("pending", "confirmed"):
                allowed += [SET_ASSIGNEE, SET_DUE]
        if lead if lead is not None else can_confirm_meeting(session, account, meeting):
            allowed.append(DELETE_ITEM)
    return [name for name in ITEM_ACTIONS if name in allowed]


def project_allowed_actions(session: Session, account: Account, project: Project) -> list[str]:
    """프로젝트 단위 허용 동작(PROJECT_ACTIONS 순서). 판정은 services/projects.py 의 기존 함수와 같은 규칙을 호출한다."""
    allowed = []
    if project.status == "active" and (account.rank == "executive" or project.lead_account_id == account.id):
        allowed.append(CHANGE_LEAD)
    if project.status == "active" and is_project_lead(session, account, project):
        allowed.append(MANAGE_MEMBERS)
    if project.status == "pending_approval" and is_approver(session, account, project):
        allowed += [APPROVE_PROJECT, REJECT_PROJECT]
    return [name for name in PROJECT_ACTIONS if name in allowed]


def available_phases(account: Account) -> list[str]:
    """회의록 목록에서 조회할 수 있는 단계. 관리자 이상은 전체, 담당자는 진행중·종료만(보류·삭제 조회는 403)."""
    return list(PHASES) if account.rank in VIEW_ALL_RANKS else ["active", "ended"]


def phase_allowed(account: Account, phase: str) -> bool:
    """목록 API 가 담당자의 보류·삭제 조회를 막는 판정(availablePhases 와 같은 함수)."""
    return phase in available_phases(account)
