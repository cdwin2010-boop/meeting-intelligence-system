"""유사/중복 업무 검색(온디맨드). 같은 프로젝트의 과거 회의록에서 열려 있는 업무를 업무명·담당자·기한 글자 유사도로 찾는다.
이 함수 한 곳이 검색 인터페이스다(이후 pgvector 의미 검색으로 바꿀 때 이 함수만 교체한다). 표준 라이브러리(difflib)만 쓴다.

점수(0~100) = 업무명 유사도(공백·구두점 정리 후 difflib) 70% + 담당자 동일 20% + 기한 7일 이내 근접 10%.
SIMILAR_MIN_SCORE 미만은 제외하고 점수순(같으면 최신 회의 먼저)으로 SIMILAR_MAX_RESULTS 건까지. 후보는 SIMILAR_SCAN_MAX 건까지 훑는다(최신 회의 순).
자동 병합은 없다: 이 검색은 읽기 전용이고 열람 기록도 남기지 않는다.
"""
import re
from dataclasses import dataclass
from difflib import SequenceMatcher

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.access import meeting_visibility
from app.config import settings
from app.models import Account, ActionItem, Meeting, MeetingClassification
from app.models.closure import active_meeting_condition
from app.models.item_conditions import open_item

NAME_WEIGHT = 70
ASSIGNEE_WEIGHT = 20
DUE_WEIGHT = 10
DUE_NEAR_DAYS = 7
NAME_SIMILAR_RATIO = 0.5  # 이 이상이면 "업무명 유사" 이유를 붙인다
_NON_WORD = re.compile(r"[\W_]+")


@dataclass
class SimilarCandidate:
    item: ActionItem
    meeting: Meeting
    score: int
    reasons: list[str]


def _normalize(text: str) -> str:
    return _NON_WORD.sub("", (text or "").lower())


def score_pair(base: ActionItem, other: ActionItem) -> tuple[int, list[str]]:
    """두 업무의 유사 점수(0~100)와 이유 목록."""
    reasons: list[str] = []
    a, b = _normalize(base.title), _normalize(other.title)
    ratio = SequenceMatcher(None, a, b).ratio() if a and b else 0.0
    total = ratio * NAME_WEIGHT
    if ratio >= NAME_SIMILAR_RATIO:
        reasons.append("업무명 유사")
    if base.assignee_id is not None and base.assignee_id == other.assignee_id:
        total += ASSIGNEE_WEIGHT
        reasons.append("담당자 동일")
    if base.due_date is not None and other.due_date is not None and abs((base.due_date - other.due_date).days) <= DUE_NEAR_DAYS:
        total += DUE_WEIGHT
        reasons.append("기한 근접")
    return round(total), reasons


def find_similar(session: Session, account: Account, item: ActionItem, meeting: Meeting, project_id: int) -> list[SimilarCandidate]:
    """같은 프로젝트의 다른 회의록 중 회의 일시가 이 업무 회의 이전이거나 같고, 내가 열람할 수 있고(진행중), 열려 있는 업무 후보를 점수순으로."""
    rows = session.execute(
        select(ActionItem, Meeting)
        .join(Meeting, Meeting.id == ActionItem.meeting_id)
        .join(MeetingClassification, MeetingClassification.meeting_id == Meeting.id)
        .where(
            ActionItem.tenant_id == account.tenant_id,
            MeetingClassification.project_id == project_id,
            Meeting.id != meeting.id,
            Meeting.held_at <= meeting.held_at,
            meeting_visibility(account),
            active_meeting_condition(),
            open_item(),
        )
        .order_by(Meeting.held_at.desc(), Meeting.id.desc(), ActionItem.id)
        .limit(settings.similar_scan_max)
    ).all()
    found = []
    for other, other_meeting in rows:
        score, reasons = score_pair(item, other)
        if score >= settings.similar_min_score:
            found.append(SimilarCandidate(other, other_meeting, score, reasons))
    found.sort(key=lambda c: (-c.score, -c.meeting.held_at.timestamp(), -c.item.id))
    return found[: settings.similar_max_results]
