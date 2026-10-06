"""회의록 조회 API 응답 스키마(camelCase). 시각은 UTC(오프셋 포함 ISO), 날짜는 YYYY-MM-DD."""
from datetime import date, datetime
from typing import Any

from app.api.schemas import CamelModel


class AccountRef(CamelModel):
    id: int
    name: str


class MeetingListItem(CamelModel):
    id: int
    title: str
    held_at: datetime
    registered_by: AccountRef
    origin: str
    status: str
    confirm_kind: str | None
    item_count: int
    needs_completion_count: int
    auto_confirm_at: datetime | None
    # 회의록 단계(추가 필드): active(진행중) | ended(종료) | on_hold(보류) | deleted(삭제)
    phase: str = "active"


class MeetingListPage(CamelModel):
    items: list[MeetingListItem]
    total: int
    page: int
    size: int


class ActionItemOut(CamelModel):
    id: int
    title: str
    assignee: AccountRef | None
    due_date: date | None
    due_undetermined: bool
    status: str
    confirm_kind: str | None
    evidence_start_sec: float | None
    evidence_quote: str | None
    needs_completion: bool
    missing_fields: list[str]

    @classmethod
    def from_item(cls, item: Any, assignee: "AccountRef | None") -> "ActionItemOut":
        """ActionItem 모델 → 응답(보완 필요 판정은 모델의 계산 속성 재사용)."""
        return cls(
            id=item.id,
            title=item.title,
            assignee=assignee,
            due_date=item.due_date,
            due_undetermined=item.due_undetermined,
            status=item.status,
            confirm_kind=item.confirm_kind,
            evidence_start_sec=item.evidence_start_sec,
            evidence_quote=item.evidence_quote,
            needs_completion=item.needs_supplement,
            missing_fields=item.missing_fields,
        )


class EventOut(CamelModel):
    event_type: str
    actor: AccountRef | None
    created_at: datetime
    # 처리 사유(추가 필드): 사유를 남기는 사건(업무 종결·삭제, 회의록 보류·직권 종료·삭제)만, 그 밖에는 None
    reason: str | None = None


class MinutesOut(CamelModel):
    """회의록 5개 항목(상세 응답의 minutes). 정보가 없으면 "내용없음"."""

    purpose: str = "내용없음"
    discussion: str = "내용없음"
    decisions: str = "내용없음"
    risks: str = "내용없음"
    next_agenda: str = "내용없음"
    # 이 회의록을 만든 처리 엔진(전사 엔진 이름). 5개 항목이 아직 만들어지지 않았으면 None
    engine: str | None = None
    # 마지막 직권 수정(없으면 None)
    updated_by: AccountRef | None = None
    updated_at: datetime | None = None


class MeetingDetail(CamelModel):
    id: int
    title: str
    held_at: datetime
    summary: str
    decisions: list[Any]
    status: str
    confirm_kind: str | None
    confirmed_by: AccountRef | None
    confirmed_at: datetime | None
    first_created_at: datetime
    auto_confirm_at: datetime | None
    registered_by: AccountRef
    origin: str
    participants: list[AccountRef]
    action_items: list[ActionItemOut]
    recent_events: list[EventOut]
    # 보류(추가 필드): 보류 중이면 딸린 업무도 모두 보류. 마지막 보류·재개 기록
    on_hold: bool = False
    on_hold_by: AccountRef | None = None
    on_hold_at: datetime | None = None
    resumed_by: AccountRef | None = None
    resumed_at: datetime | None = None
    # 회의록 단계와 종료(구분 auto|manager·처리자·시각)·삭제(처리자·시각) 기록(추가 필드)
    phase: str = "active"
    end_kind: str | None = None
    ended_by: AccountRef | None = None
    ended_at: datetime | None = None
    deleted_by: AccountRef | None = None
    deleted_at: datetime | None = None
    # 처리 사유(추가 필드): 마지막 보류 사유, 직권 종료 사유(자동 종료는 None), 삭제 사유. 사유 도입 전 기록은 None
    on_hold_reason: str | None = None
    end_reason: str | None = None
    delete_reason: str | None = None
    # 회의록 5개 항목(추가 필드). summary·decisions 는 예전부터 있던 칸 그대로(이번에 바꾸지 않음)
    minutes: MinutesOut = MinutesOut()


class SpeakerOut(CamelModel):
    """화자 매핑 1건. 계정이면 accountId·accountName, 미등록이면 name(글자)만. displayName 은 화면 표시용("이름(미등록)")."""
    label: str
    account_id: int | None
    account_name: str | None
    name: str | None
    display_name: str
    unregistered: bool


class TranscriptOut(CamelModel):
    full_text: str
    segments: list[dict[str, Any]] | None
    stt_provider: str
    # 화자 매핑 반영(추가 필드): 줄머리 화자 표기를 표시 이름으로 바꾼 원문과 매핑 목록. fullText·segments 는 원본 그대로
    display_text: str = ""
    speakers: list[SpeakerOut] = []
