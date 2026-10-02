"""화자 매핑(화자 표기 → 같은 고객사 계정 / 미등록 이름 글자)의 읽기·표시·담당자 자동 채움.

- 화자 표기는 전사문에서 읽는다: 구간(segments)이 있으면 그 speaker, 없으면 "[타임스탬프] 화자: 말" 줄의 화자 부분.
- 표시 이름: 계정이면 계정 이름, 미등록이면 "이름(미등록)". 매핑이 없으면 표기 그대로.
- 담당자 자동 채움: 확정 전(pending)·담당자 빈 업무만, 추출 때 받은 담당자 글자(item.created 사건의 assignee_name)가
  계정으로 매핑된 화자 표기 또는 그 계정 이름과 정확히 같고, 맞는 계정이 하나뿐일 때만 채운다. 이미 담당자가 있으면 덮지 않는다.
  미등록 이름은 담당자로 쓰지 않는다(담당자는 계정만).
전사문 내용은 로그·사건 payload 에 넣지 않는다(건수·id 만)."""
import re
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Account, ActionItem, Event, MeetingSpeaker, Transcript, append_event

UNREGISTERED_SUFFIX = "(미등록)"
# "[00:12:40] 화자1: 말" — 대괄호 타임스탬프 뒤 첫 콜론 앞까지를 화자 표기로 본다
_LINE = re.compile(r"^(\s*\[[^\]\n]*\]\s*)([^:\n]+?)(\s*:)", re.MULTILINE)


@dataclass(frozen=True)
class SpeakerView:
    label: str
    account_id: int | None
    account_name: str | None
    display_name: str  # 미등록 이름 글자(계정이면 "")

    @property
    def unregistered(self) -> bool:
        return self.account_id is None

    @property
    def display(self) -> str:
        return self.account_name if self.account_id is not None else f"{self.display_name}{UNREGISTERED_SUFFIX}"


def transcript_labels(transcript: Transcript | None) -> list[str]:
    """전사문에 나오는 화자 표기(처음 나온 순서, 중복 제거)."""
    if transcript is None:
        return []
    labels: list[str] = []
    if transcript.segments:
        labels = [str(s.get("speaker") or "").strip() for s in transcript.segments if isinstance(s, dict)]
    else:
        labels = [m.group(2).strip() for m in _LINE.finditer(transcript.full_text or "")]
    return [label for label in dict.fromkeys(labels) if label]


def load_speakers(session: Session, meeting_id: int) -> list[SpeakerView]:
    rows = session.execute(
        select(MeetingSpeaker, Account.name)
        .outerjoin(Account, Account.id == MeetingSpeaker.account_id)
        .where(MeetingSpeaker.meeting_id == meeting_id)
        .order_by(MeetingSpeaker.id)
    ).all()
    return [
        SpeakerView(label=row.MeetingSpeaker.label, account_id=row.MeetingSpeaker.account_id,
                    account_name=row.name, display_name=row.MeetingSpeaker.display_name)
        for row in rows
    ]


def display_text(full_text: str, speakers: list[SpeakerView]) -> str:
    """원문 줄머리의 화자 표기만 표시 이름으로 바꾼다(한 번에 치환, 말 내용은 그대로)."""
    names = {s.label: s.display for s in speakers}
    if not names:
        return full_text

    def replace(match: re.Match) -> str:
        label = match.group(2).strip()
        return f"{match.group(1)}{names[label]}{match.group(3)}" if label in names else match.group(0)

    return _LINE.sub(replace, full_text or "")


def _extracted_assignee_names(session: Session, item_ids: list[int]) -> dict[int, str]:
    """업무별로 추출 때 받은 담당자 글자(처리 단계가 item.created 사건에 남긴 값)."""
    if not item_ids:
        return {}
    rows = session.execute(
        select(Event.entity_id, Event.payload).where(
            Event.entity_type == "action_item", Event.event_type == "item.created", Event.entity_id.in_(item_ids)
        )
    ).all()
    names: dict[int, str] = {}
    for entity_id, payload in rows:
        value = (payload or {}).get("assignee_name")
        if isinstance(value, str) and value.strip():
            names[entity_id] = value.strip()
    return names


def auto_assign_from_speakers(session: Session, *, meeting_id: int, tenant_id: int, actor: Account) -> list[int]:
    """계정으로 매핑된 화자와 추출 담당자 글자가 맞는 빈 담당자 업무를 채우고, 채운 업무 id 를 돌려준다(커밋은 호출부)."""
    speakers = [s for s in load_speakers(session, meeting_id) if s.account_id is not None]
    if not speakers:
        return []
    # 글자 → 계정 id 후보(화자 표기와 계정 이름 모두 키). 같은 글자에 계정이 둘 이상이면 채우지 않는다
    candidates: dict[str, set[int]] = {}
    for s in speakers:
        for key in (s.label, s.account_name or ""):
            if key:
                candidates.setdefault(key, set()).add(s.account_id)  # type: ignore[arg-type]
    active_ids = set(
        session.scalars(
            select(Account.id).where(
                Account.id.in_({s.account_id for s in speakers}), Account.tenant_id == tenant_id, Account.is_active.is_(True)
            )
        )
    )
    items = session.scalars(
        select(ActionItem).where(
            ActionItem.meeting_id == meeting_id, ActionItem.status == "pending", ActionItem.assignee_id.is_(None)
        ).order_by(ActionItem.id)
    ).all()
    names = _extracted_assignee_names(session, [i.id for i in items])
    filled: list[int] = []
    for item in items:
        matched = candidates.get(names.get(item.id, ""), set())
        if len(matched) != 1:
            continue
        account_id = next(iter(matched))
        if account_id not in active_ids:
            continue
        item.assignee_id = account_id
        append_event(
            session, tenant_id=item.tenant_id, entity_type="action_item", entity_id=item.id,
            event_type="item.updated", actor_account_id=actor.id,
            payload={"before": {"assigneeId": None}, "after": {"assigneeId": account_id}, "via": "speaker_mapping"},
        )
        filled.append(item.id)
    return filled
