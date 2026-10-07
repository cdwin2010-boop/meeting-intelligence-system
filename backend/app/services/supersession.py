"""업무 대체(대체 연결) 서비스. 오늘 회의의 업무(new)가 같은 프로젝트의 과거 회의 업무(old)를 대체한다.

- 자동 병합 금지: 사람이 대체를 실행한다(프로젝트 총괄·지시자). 담당자는 대체 요청(수정 요청의 한 종류)만 올린다.
- 상태값을 늘리지 않는다. 대체는 item_supersessions 행이고, 대체된 업무의 제외·포함 규칙은 models/item_conditions.py 한 곳에 있다.
- 이 모듈이 item_supersessions 표를 읽고 쓰는 유일한 서비스다(다른 곳은 아래 함수나 item_conditions 를 쓴다).
- 커밋은 호출부가 한다(대체와 이력이 같은 트랜잭션).
"""
from dataclasses import dataclass

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.access import get_visible_meeting, meeting_visibility
from app.auth.locks import reject_if_locked
from app.auth.scope import scoped
from app.models import Account, ActionItem, ItemSupersession, Meeting, MeetingClassification, Project
from app.services.history import KIND_ITEM_SUPERSEDED, record_change
from app.services.projects import is_project_lead

OPEN_STATUSES = ("pending", "confirmed")


def _http(code: int, detail: str) -> HTTPException:
    return HTTPException(status_code=code, detail=detail)


def project_of_meeting(session: Session, meeting_id: int) -> Project | None:
    """회의록이 연결된 프로젝트(프로젝트 회의가 아니거나 미지정이면 None)."""
    return session.scalar(
        select(Project).join(MeetingClassification, MeetingClassification.project_id == Project.id).where(MeetingClassification.meeting_id == meeting_id)
    )


def has_supersede_authority(session: Session, account: Account, project: Project | None) -> bool:
    """대체 권한: 그 프로젝트의 총괄(활성 프로젝트, 직급 manager 이상) 또는 지시자."""
    if project is None or project.tenant_id != account.tenant_id:
        return False
    if account.rank == "executive":
        return True
    return account.rank == "manager" and project.status == "active" and is_project_lead(session, account, project)


def superseded_ids(session: Session, item_ids: list[int]) -> set[int]:
    """주어진 업무 중 대체된 업무의 id."""
    if not item_ids:
        return set()
    return set(session.scalars(select(ItemSupersession.old_item_id).where(ItemSupersession.old_item_id.in_(item_ids))))


def is_superseded(session: Session, item_id: int) -> bool:
    return item_id in superseded_ids(session, [item_id])


def reject_if_superseded(session: Session, item: ActionItem) -> None:
    """대체된 업무의 수정·확정·종결·삭제를 409 로 거부한다."""
    if is_superseded(session, item.id):
        raise _http(status.HTTP_409_CONFLICT, "대체된 업무입니다. 수정·확정·종결·삭제할 수 없습니다")


def is_open(session: Session, item: ActionItem) -> bool:
    """열려 있는 업무(확정 대기·확정, 대체되지 않음). 조건식은 item_conditions.open_item 과 같은 의미다."""
    return item.status in OPEN_STATUSES and not is_superseded(session, item.id)


# ---------------- 응답용 참조 ----------------
def supersession_refs(session: Session, account: Account, item_ids: list[int]) -> tuple[dict[int, dict], dict[int, list[dict]]]:
    """(대체된 업무 id → 대체한 업무 참조, 대체하는 업무 id → 대체된 업무 참조 목록). 참조 = itemId·meetingId·meetingTitle.
    상대 업무의 회의록을 열람할 수 없으면 meetingTitle 은 None(열람 정보 노출 방지)."""
    if not item_ids:
        return {}, {}
    links = session.scalars(
        select(ItemSupersession).where(ItemSupersession.old_item_id.in_(item_ids) | ItemSupersession.new_item_id.in_(item_ids))
    ).all()
    other_ids = {link.new_item_id for link in links if link.old_item_id in item_ids} | {link.old_item_id for link in links if link.new_item_id in item_ids}
    if not other_ids:
        return {}, {}
    rows = session.execute(
        select(ActionItem.id, ActionItem.meeting_id, Meeting.title).join(Meeting, Meeting.id == ActionItem.meeting_id).where(ActionItem.id.in_(other_ids))
    ).all()
    visible_meetings = set(session.scalars(select(Meeting.id).where(Meeting.id.in_({r.meeting_id for r in rows}), meeting_visibility(account))))
    ref = {r.id: {"itemId": r.id, "meetingId": r.meeting_id, "meetingTitle": r.title if r.meeting_id in visible_meetings else None} for r in rows}
    superseded_by: dict[int, dict] = {}
    supersedes: dict[int, list[dict]] = {}
    for link in links:
        if link.old_item_id in item_ids and link.new_item_id in ref:
            superseded_by[link.old_item_id] = ref[link.new_item_id]
        if link.new_item_id in item_ids and link.old_item_id in ref:
            supersedes.setdefault(link.new_item_id, []).append(ref[link.old_item_id])
    return superseded_by, supersedes


# ---------------- 대체 실행 ----------------
@dataclass
class Prepared:
    old_item: ActionItem
    project: Project
    already: bool  # 이미 같은 쌍으로 대체돼 있음


def _creates_cycle(session: Session, new_item_id: int, old_item_id: int) -> bool:
    """old → new 연결을 더하면 순환이 되는가(new 에서 시작해 대체 연결을 따라가 old 에 닿는지)."""
    seen: set[int] = set()
    current = new_item_id
    while current not in seen:
        seen.add(current)
        link = session.scalar(select(ItemSupersession).where(ItemSupersession.old_item_id == current))
        if link is None:
            return False
        if link.new_item_id == old_item_id:
            return True
        current = link.new_item_id
    return True


def prepare_supersede(session: Session, account: Account, new_item: ActionItem, old_item_id: int) -> Prepared:
    """권한을 뺀 나머지 대체 조건을 모두 검증한다(대체 요청 생성과 대체 실행이 함께 쓴다). 위반하면 HTTPException."""
    new_meeting = session.get(Meeting, new_item.meeting_id)
    project = project_of_meeting(session, new_meeting.id)
    if project is None:
        raise _http(status.HTTP_409_CONFLICT, "프로젝트 회의록이 아닙니다")
    old_item = session.scalar(scoped(select(ActionItem), account).where(ActionItem.id == old_item_id))
    old_meeting = get_visible_meeting(session, account, old_item.meeting_id) if old_item is not None else None
    if old_item is None or old_meeting is None:
        raise _http(status.HTTP_404_NOT_FOUND, "업무를 찾을 수 없습니다")
    old_project = project_of_meeting(session, old_meeting.id)
    if old_project is None or old_project.id != project.id:
        raise _http(status.HTTP_409_CONFLICT, "같은 프로젝트의 회의록 업무만 대체할 수 있습니다")
    if old_meeting.id == new_meeting.id:
        raise _http(status.HTTP_409_CONFLICT, "같은 회의록의 업무는 대체할 수 없습니다")
    existing = session.scalar(select(ItemSupersession).where(ItemSupersession.old_item_id == old_item.id))
    if existing is not None:
        if existing.new_item_id == new_item.id:
            return Prepared(old_item, project, already=True)
        raise _http(status.HTTP_409_CONFLICT, "이미 다른 업무로 대체된 업무입니다")
    if project.status != "active":
        raise _http(status.HTTP_409_CONFLICT, "진행 중인 프로젝트의 업무만 대체할 수 있습니다")
    if not is_open(session, new_item) or not is_open(session, old_item):
        raise _http(status.HTTP_409_CONFLICT, "열려 있는 업무(확정 대기·확정)만 대체할 수 있습니다")
    reject_if_locked(new_meeting)
    reject_if_locked(old_meeting)
    if old_meeting.held_at > new_meeting.held_at:
        raise _http(status.HTTP_409_CONFLICT, "더 늦은 회의의 업무는 대체될 수 없습니다")
    if _creates_cycle(session, new_item.id, old_item.id):
        raise _http(status.HTTP_409_CONFLICT, "대체 관계가 순환합니다")
    return Prepared(old_item, project, already=False)


def supersede_item(
    session: Session, account: Account, new_item: ActionItem, old_item_id: int, reason: str, *, source_request_id: int | None = None
) -> bool:
    """대체를 실행한다(권한 검사 포함). 이미 같은 쌍이면 변경 없이 False. 이력은 대체되는 업무와 대체하는 업무 양쪽에 남긴다."""
    project = project_of_meeting(session, new_item.meeting_id)
    if project is None:
        raise _http(status.HTTP_409_CONFLICT, "프로젝트 회의록이 아닙니다")
    if not has_supersede_authority(session, account, project):
        raise _http(status.HTTP_403_FORBIDDEN, "프로젝트 총괄 또는 지시자만 업무를 대체할 수 있습니다")
    prepared = prepare_supersede(session, account, new_item, old_item_id)
    if prepared.already:
        return False
    old_item = prepared.old_item
    session.add(ItemSupersession(
        tenant_id=account.tenant_id, old_item_id=old_item.id, new_item_id=new_item.id, project_id=project.id,
        superseded_by=account.id, reason=reason, source_request_id=source_request_id,
    ))
    session.flush()
    extra = {"oldItemId": old_item.id, "newItemId": new_item.id, "projectId": project.id, "reason": reason, "sourceRequestId": source_request_id}
    for target_id in (old_item.id, new_item.id):
        record_change(
            session, tenant_id=account.tenant_id, target_type="action_item", target_id=target_id, kind=KIND_ITEM_SUPERSEDED,
            actor_id=account.id, before={"supersededByItemId": None}, after={"supersededByItemId": new_item.id, "supersededItemId": old_item.id},
            extra=extra,
        )
    return True
