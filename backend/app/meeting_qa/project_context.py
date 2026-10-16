"""프로젝트 범위 컨텍스트 조립(작업 89-2b).

- 자료는 그 프로젝트에 연결된 회의록(요청자가 열람 가능·삭제되지 않음·정리된 회의록 있음, 최근 QA_PROJECT_MAX_MEETINGS 건)의 요약 5개 항목과 업무뿐이다.
  범위는 서버가 프로젝트 ID 로 정한다. 발화 발췌(2차)는 쓰지 않는다(여러 회의의 발화를 섞지 않는다).
- 모든 자료 앞에 회의록 제목을 붙여 어느 회의의 내용인지 구분하고, 근거(Source)에도 회의록 ID·제목을 담아 화면이 그 회의록으로 이동할 수 있게 한다.
- 정렬·예산·빠른 질문 규칙은 회의 1건(context.rank_and_choose)과 같다.
"""
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.meeting_qa.context import QaContext, Source, _item_line, _nearest_utterance, _utterances, rank_and_choose, sanitize
from app.models import Account, ActionItem, Meeting, MeetingMinutes
from app.models.item_conditions import live_item, not_deleted_item
from app.models.minutes import MINUTES_FIELDS, NO_CONTENT

TITLE_MAX = 30


def _title(meeting: Meeting) -> str:
    return sanitize(meeting.title or "")[:TITLE_MAX] or f"회의록 {meeting.id}"


def build_project_context(session: Session, meetings: list[Meeting], account: Account, question: str, quick_prompt: str | None) -> QaContext:
    budget = int(settings.qa_context_tokens / settings.qa_tokens_per_char)
    ctx = QaContext(budget_chars=budget)
    primary: list[dict] = []
    for meeting in meetings:
        title = _title(meeting)
        minutes = session.get(MeetingMinutes, meeting.id)
        if minutes is not None:
            for field_name, field_title in MINUTES_FIELDS.items():
                value = (getattr(minutes, field_name) or "").strip()
                if value and value != NO_CONTENT:
                    primary.append({
                        "kind": "minutes", "text": f"[{title}] {field_title}: {sanitize(value)}", "label": f"회의록·{field_title}",
                        "field": field_name, "meeting": meeting,
                    })
        items = session.scalars(
            select(ActionItem).where(ActionItem.meeting_id == meeting.id, live_item(), not_deleted_item()).order_by(ActionItem.id)
        ).all()
        names: dict[int, str] = {}
        ids = {i.assignee_id for i in items if i.assignee_id}
        if ids:
            names = {a.id: a.name for a in session.scalars(select(Account).where(Account.id.in_(ids)))}
        utterances = None
        for item in items:
            uid = None
            if item.evidence_start_sec is not None:
                if utterances is None:
                    utterances = _utterances(session, meeting)
                uid = _nearest_utterance(utterances, item.evidence_start_sec)
            primary.append({
                "kind": "item", "text": f"[{title}] {_item_line(item, names)}", "label": f"업무·{sanitize(item.title)[:20]}",
                "item": item, "utterance": uid, "meeting": meeting, "utterances": utterances,
            })

    chosen, _used = rank_and_choose(primary, question, quick_prompt, account, budget)
    for seq, p in enumerate(chosen, start=1):
        meeting: Meeting = p["meeting"]
        src = Source(f"S{seq}", p["kind"], p["text"], label=p["label"], meeting_id=meeting.id, meeting_title=_title(meeting))
        if p["kind"] == "item":
            if p["item"].status == "pending":
                ctx.pending_included = True
            uid = p.get("utterance")
            if uid is not None:
                u = p["utterances"][uid]
                src.utterance_id, src.speaker, src.start_sec = uid, str(u["speaker"]), u["start_sec"]
        if meeting.status == "awaiting_confirmation":
            ctx.pending_included = True  # 확정 대기 회의록의 내용이 들어 있음
        ctx.sources.append(src)
    return ctx
