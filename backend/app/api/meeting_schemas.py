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


class EventOut(CamelModel):
    event_type: str
    actor: AccountRef | None
    created_at: datetime


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


class TranscriptOut(CamelModel):
    full_text: str
    segments: list[dict[str, Any]] | None
    stt_provider: str
