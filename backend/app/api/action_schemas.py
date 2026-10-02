"""쓰기 API 입출력 스키마(camelCase)."""
from datetime import date, datetime
from typing import Literal

from pydantic import Field, field_validator

from app.api.meeting_schemas import AccountRef
from app.api.schemas import CamelModel


class ActionItemPatch(CamelModel):
    """보낸 필드만 바꾼다(model_fields_set 로 '안 보냄'과 'null'을 구분)."""

    title: str | None = None
    assignee_id: int | None = None
    due_date: date | None = None
    due_undetermined: bool | None = None


class MeetingConfirmResult(CamelModel):
    id: int
    status: str
    confirm_kind: str | None
    confirmed_by: AccountRef | None
    confirmed_at: datetime | None
    # withItems=true 일 때만 채운다
    confirmed_item_ids: list[int] = Field(default_factory=list)
    skipped_item_ids: list[int] = Field(default_factory=list)


class ChangeRequestCreate(CamelModel):
    comment: str = Field(min_length=1, max_length=2000)
    item_id: int | None = None

    @field_validator("comment")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("comment 는 비워 둘 수 없습니다")
        return stripped


# 처리 사유 최대 글자 수(수정 요청 코멘트·해결 사유와 같음)
REASON_MAX = 2000


class ReasonBody(CamelModel):
    """처리 사유(필수): 업무 종결·삭제, 회의록 보류·삭제·직권 종료. 내용은 판단하지 않고 비어 있지 않은지만 본다(앞뒤 공백 제거).
    없거나 공백뿐이면 422(FastAPI 입력 검증, 수정 요청 코멘트와 같은 방식)."""
    reason: str = Field(min_length=1, max_length=REASON_MAX)

    @field_validator("reason")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("reason 는 비워 둘 수 없습니다")
        return stripped


class ChangeRequestCreated(CamelModel):
    request_id: int


class Resolution(CamelModel):
    decision: Literal["accepted", "rejected"]
    reason: str | None
    resolved_by: AccountRef | None
    resolved_at: datetime


class ChangeRequestOut(CamelModel):
    request_id: int
    requester: AccountRef | None
    comment: str
    item_id: int | None
    created_at: datetime
    resolution: Resolution | None


class ChangeRequestResolve(CamelModel):
    decision: Literal["accepted", "rejected"]
    reason: str | None = Field(default=None, max_length=2000)
