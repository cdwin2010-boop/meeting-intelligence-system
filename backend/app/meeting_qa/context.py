"""컨텍스트 조립(작업 89-1).

- 1차: 정리된 요약(회의록 5개 항목)·업무. 2차(필요할 때만): 같은 회의 발화에서 BM25 발췌.
- 컨텍스트 상한(QA_CONTEXT_TOKENS)과 발췌 개수(QA_MAX_EXCERPTS)는 설정값. 넘으면 질문과 관련 높은 순으로 자른다.
- 모든 자료에 [S1] 같은 ID 를 붙인다(모델은 ID 로만 인용하고, 서버가 실제 발화·시각으로 바꾼다).
- 회의 발화는 데이터로만 쓴다: ID 위조·구분자·제어 문자를 무력화하고 자료 블록 안에서만 전달한다(지시문 삽입 방지).
"""
import re
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.meeting_qa.bm25 import Bm25, coverage
from app.models import Account, ActionItem, Meeting, MeetingMinutes, Transcript
from app.models.item_conditions import live_item, not_deleted_item
from app.models.minutes import MINUTES_FIELDS, NO_CONTENT
from app.pipeline.transcript_text import parse_transcript

QUICK_PROMPTS = ("decisions", "my_tasks", "schedule")
MODE_SUMMARY, MODE_EXCERPTS = "summary", "summary+excerpts"
_CTRL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")
_FAKE_ID = re.compile(r"\[\s*S\s*\d+[^\]]{0,20}\]", re.IGNORECASE)
_DELIM = re.compile(r"</?\s*자료\s*>|<<+|>>+|```+|^#{1,6}\s", re.MULTILINE)
_EVIDENCE_TOLERANCE_SEC = 2.0
_MIN_PARTIAL_CHARS = 60


@dataclass
class Source:
    ref_id: str  # "S1"
    kind: str  # utterance | minutes | item
    text: str
    label: str = ""  # 화면 표시용(발화가 아닌 근거: "회의록·결정사항" 등)
    utterance_id: int | None = None
    speaker: str = ""
    start_sec: float | None = None


@dataclass
class QaContext:
    sources: list[Source] = field(default_factory=list)
    mode: str = MODE_SUMMARY
    pending_included: bool = False  # 확정 전 업무(또는 확정 대기 회의록)가 컨텍스트에 들어 있음
    budget_chars: int = 0

    def block(self) -> str:
        return "\n".join(f"[{s.ref_id}] {s.text}" for s in self.sources)

    def by_id(self) -> dict[str, Source]:
        return {s.ref_id: s for s in self.sources}


def sanitize(text: str) -> str:
    """회의 내용을 데이터로만 다루기 위한 정리: 제어 문자 제거, ID 위조·자료 구분자 무력화, 공백 정리."""
    value = _CTRL.sub(" ", text or "")
    value = _FAKE_ID.sub(lambda m: "(" + m.group(0)[1:-1] + ")", value)
    value = _DELIM.sub(" ", value)
    return re.sub(r"\s+", " ", value).strip()


def _utterances(session: Session, meeting: Meeting) -> list[dict]:
    row = session.scalar(select(Transcript).where(Transcript.meeting_id == meeting.id))
    if row is None:
        return []
    if row.segments:
        found = [
            {"speaker": str(s.get("speaker") or ""), "start_sec": s.get("start_sec"), "text": str(s.get("text") or "")}
            for s in row.segments
            if isinstance(s, dict)
        ]
        if found:
            return found
    parsed = parse_transcript(row.full_text or "")
    if parsed.segments:
        return [
            {"speaker": str(s.get("speaker") or ""), "start_sec": s.get("start_sec"), "text": str(s.get("text") or "")}
            for s in parsed.segments
        ]
    # 모든 줄에 시각·화자가 있어 구간이 비어 있으면 줄머리에서 다시 읽는다
    out = []
    for line in (parsed.text or "").splitlines():
        m = re.match(r"^\[(\d+):(\d{2})(?::(\d{2}))?\]\s*(?:([^:]{1,20}):\s*)?(.*)$", line)
        if m:
            a, b, c = int(m.group(1)), int(m.group(2)), m.group(3)
            sec = a * 3600 + b * 60 + int(c) if c is not None else a * 60 + b
            out.append({"speaker": m.group(4) or "", "start_sec": float(sec), "text": m.group(5)})
        elif line.strip():
            out.append({"speaker": "", "start_sec": None, "text": line.strip()})
    return out


def _nearest_utterance(utterances: list[dict], start_sec: float | None) -> int | None:
    if start_sec is None:
        return None
    best, gap = None, _EVIDENCE_TOLERANCE_SEC + 1
    for i, u in enumerate(utterances):
        s = u.get("start_sec")
        if isinstance(s, (int, float)) and abs(s - start_sec) < gap:
            best, gap = i, abs(s - start_sec)
    return best if gap <= _EVIDENCE_TOLERANCE_SEC else None


def _item_line(item: ActionItem, names: dict[int, str]) -> str:
    who = names.get(item.assignee_id or 0, "담당자 미정")
    if item.due_undetermined:
        due = "기한 미확정"
    else:
        due = f"기한 {item.due_date.isoformat()}" if item.due_date else "기한 미정"
    state = {"pending": "확정 전", "confirmed": "확정", "closed": "종결"}.get(item.status, item.status)
    return f"업무: {sanitize(item.title) or '(제목 없음)'} / 담당 {who} / {due} / 상태 {state}"


def build_context(session: Session, meeting: Meeting, account: Account, question: str, quick_prompt: str | None) -> QaContext:
    budget = int(settings.qa_context_tokens / settings.qa_tokens_per_char)
    ctx = QaContext(budget_chars=budget)
    utterances = _utterances(session, meeting)
    minutes = session.get(MeetingMinutes, meeting.id)

    # ---- 1차 후보 ----
    primary: list[dict] = []
    if minutes is not None:
        for field_name, title in MINUTES_FIELDS.items():
            value = (getattr(minutes, field_name) or "").strip()
            if value and value != NO_CONTENT:
                primary.append({"kind": "minutes", "text": f"{title}: {sanitize(value)}", "label": f"회의록·{title}", "field": field_name})
    # 삭제·대체된 업무는 뺀다(업무 상태 판정은 item_conditions 한 곳)
    items = session.scalars(select(ActionItem).where(ActionItem.meeting_id == meeting.id, live_item(), not_deleted_item()).order_by(ActionItem.id)).all()
    names: dict[int, str] = {}
    ids = {i.assignee_id for i in items if i.assignee_id}
    if ids:
        names = {a.id: a.name for a in session.scalars(select(Account).where(Account.id.in_(ids)))}
    for item in items:
        uid = _nearest_utterance(utterances, item.evidence_start_sec)
        primary.append(
            {"kind": "item", "text": _item_line(item, names), "label": f"업무·{sanitize(item.title)[:20]}", "item": item, "utterance": uid}
        )

    # ---- 순서: 빠른 질문은 해당 종류를 앞에, 그 밖은 BM25 관련도 순(같으면 원래 순서) ----
    if quick_prompt == "decisions":
        key = [(0 if p.get("field") == "decisions" else 1 if p["kind"] == "minutes" else 2, 0.0) for p in primary]
    elif quick_prompt == "my_tasks":
        key = [(0 if p["kind"] == "item" and p["item"].assignee_id == account.id else 2 if p["kind"] == "minutes" else 1, 0.0) for p in primary]
    elif quick_prompt == "schedule":
        key = []
        for p in primary:
            if p["kind"] == "item" and (p["item"].due_date or p["item"].due_undetermined):
                key.append((0, float(p["item"].due_date.toordinal()) if p["item"].due_date else 9_999_999.0))
            else:
                key.append((1, 0.0))
    else:
        scores = Bm25([p["text"] for p in primary]).scores(question) if primary else []
        key = [(-s, 0.0) for s in scores]
    order = sorted(range(len(primary)), key=lambda i: key[i]) if primary else []

    used = 0
    chosen: list[dict] = []
    for i in order:
        p = primary[i]
        room = budget - used
        if room <= 0:
            break
        text = p["text"]
        if len(text) > room:
            if room < _MIN_PARTIAL_CHARS:
                continue
            text = text[: room - 1] + "…"
        chosen.append({**p, "text": text})
        used += len(text)

    # ---- 2차: 필요할 때만(빠른 질문이 아니고, 질문이 1차 자료에 충분히 들어 있지 않을 때) ----
    excerpts: list[dict] = []
    if quick_prompt is None and settings.qa_max_excerpts > 0 and utterances:
        corpus = " ".join(p["text"] for p in chosen)
        if coverage(question, corpus) < settings.qa_primary_coverage_min:
            scores = Bm25([u["text"] for u in utterances]).scores(question)
            ranked = sorted((i for i, s in enumerate(scores) if s > 0), key=lambda i: -scores[i])
            for i in ranked:
                if len(excerpts) >= settings.qa_max_excerpts:
                    break
                text = sanitize(utterances[i]["text"])
                room = budget - used
                if not text or room <= 0:
                    continue
                if len(text) > room:
                    if room < _MIN_PARTIAL_CHARS:
                        continue
                    text = text[: room - 1] + "…"
                excerpts.append({"utterance": i, "text": text})
                used += len(text)
            excerpts.sort(key=lambda e: e["utterance"])  # 발췌는 시간 순으로 보여 준다
    if excerpts:
        ctx.mode = MODE_EXCERPTS

    # ---- 출처 ID 부여(출력 순서대로) ----
    seq = 0

    def next_id() -> str:
        nonlocal seq
        seq += 1
        return f"S{seq}"

    for p in chosen:
        src = Source(next_id(), p["kind"], p["text"], label=p["label"])
        if p["kind"] == "item":
            if p["item"].status == "pending":
                ctx.pending_included = True
            uid = p.get("utterance")
            if uid is not None:
                src.utterance_id, src.speaker, src.start_sec = uid, str(utterances[uid]["speaker"]), utterances[uid]["start_sec"]
        ctx.sources.append(src)
    for e in excerpts:
        u = utterances[e["utterance"]]
        speaker = sanitize(str(u["speaker"]))
        ctx.sources.append(
            Source(next_id(), "utterance", f"{speaker + ': ' if speaker else ''}{e['text']}", utterance_id=e["utterance"],
                   speaker=speaker, start_sec=u["start_sec"])
        )
    if meeting.status == "awaiting_confirmation":
        ctx.pending_included = True  # 회의록 자체가 확정 전
    return ctx
