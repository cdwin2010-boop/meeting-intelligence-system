"""API 응답 모양. 파이썬 내부는 snake_case, JSON으로 나갈 때는 camelCase (frontend/lib/types.ts와 동일)."""
from typing import Literal

from pydantic import BaseModel, ConfigDict
from pydantic.alias_generators import to_camel


class CamelModel(BaseModel):
    # alias_generator: 내보낼 때 이름표를 camelCase로 붙임 / populate_by_name: 파이썬에서는 snake_case로도 생성 가능
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)


class Attendee(CamelModel):
    id: str
    name: str
    role: str


class Meeting(CamelModel):
    id: str
    title: str
    started_at: str
    attendees: list[Attendee]


class Quote(CamelModel):
    speaker: str
    timestamp: str
    text: str


class ActionItem(CamelModel):
    id: str
    task: str
    assignee: str
    due_date: str
    status: Literal["open", "in_progress", "done", "overdue"]
    quote: Quote


class JobWorker(CamelModel):
    id: str
    host: str
    engine: Literal["gemini-api", "faster-whisper"]
    stage: Literal["STT", "LLM"]
    gpu: str | None = None


class AdminJob(CamelModel):
    id: str
    meeting_title: str
    audio_seconds: int
    elapsed_seconds: int | None = None
    status: Literal["queued", "processing", "completed", "failed"]
    attempt: int
    started_at: str | None = None
    worker: JobWorker | None = None
    error_log: list[str] = []
