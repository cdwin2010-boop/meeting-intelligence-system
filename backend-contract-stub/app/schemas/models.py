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
    # 전사 원문. 아직 전사 전이거나 시드 데이터면 None (프론트 화면의 모달/토글에서 열람)
    transcript_text: str | None = None
    # v1.9.9: 화자 표기("화자1") → 실제 이름/직함. 매핑이 없으면 {} (원본 assignee·전사는 그대로 두고 화면이 적용)
    speaker_names: dict[str, str] = {}


class SpeakerNames(CamelModel):
    """PUT /meetings/{id}/speakers 응답: 실제로 저장된 매핑 (공백 제거·빈 값 삭제 반영)"""
    speaker_names: dict[str, str]


class MeetingSummary(CamelModel):
    """회의 목록(GET /meetings)의 한 줄. 상세(Meeting)와 달리 참석자·전사 원문은 싣지 않는다."""
    id: str
    title: str
    started_at: str
    # 가장 최근 작업의 상태. 업로드 작업이 없는 시드 회의는 None
    job_status: Literal["queued", "processing", "completed", "failed"] | None = None


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


class UploadReceipt(CamelModel):
    """업로드 성공(202) 응답: '접수증'. 전사가 끝나기를 기다리지 않고 바로 돌려준다."""
    meeting_id: str
    job_id: str
    status: Literal["queued"]


class JobStatus(CamelModel):
    """일반 사용자가 자기 작업 하나의 진행 상태를 볼 때 쓴다 (관리자용 AdminJob과 별개)."""
    id: str
    meeting_id: str  # 시드 작업처럼 회의와 연결되지 않은 경우 "" (None 대신 빈 문자열)
    status: Literal["queued", "processing", "completed", "failed"]
    stage: Literal["STT", "LLM"] | None = None
    error_message: str = ""
