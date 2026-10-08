"""쓰기 API 입출력 스키마(camelCase)."""
from datetime import date, datetime
from typing import Literal

from pydantic import Field, field_validator, model_validator

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
    # 요청 종류: edit(기본, 수정 요청) / supersede(대체 요청: itemId 가 대체하는 업무, supersedesItemId 가 대체될 과거 업무)
    kind: Literal["edit", "supersede"] = "edit"
    supersedes_item_id: int | None = None

    @model_validator(mode="after")
    def _kind_fields(self) -> "ChangeRequestCreate":
        if self.kind == "supersede" and (self.item_id is None or self.supersedes_item_id is None):
            raise ValueError("대체 요청은 itemId 와 supersedesItemId 가 필요합니다")
        if self.kind == "edit" and self.supersedes_item_id is not None:
            raise ValueError("supersedesItemId 는 대체 요청(kind=supersede)에만 보낼 수 있습니다")
        return self

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


class CloseBody(ReasonBody):
    """업무 종결 입력: 사유(필수) + 종결 구분(선택). 구분은 completed(정상 완료)·forced(직권 종료)만, 아니면 422.
    구분을 보내지 않으면 구분 행을 만들지 않는다(구분 없음, 기존 호환)."""
    closure_kind: Literal["completed", "forced"] | None = None


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
    # 요청 종류(추가 필드): edit(수정 요청, 종류가 없던 예전 요청 포함) / supersede(대체 요청)와 대체될 과거 업무
    kind: str = "edit"
    supersedes_item_id: int | None = None


class ChangeRequestResolve(CamelModel):
    decision: Literal["accepted", "rejected"]
    reason: str | None = Field(default=None, max_length=2000)
