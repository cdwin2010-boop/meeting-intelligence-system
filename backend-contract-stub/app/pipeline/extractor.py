"""
액션아이템 추출기. 모든 추출기는 같은 모양을 따른다:
    extract(transcript, started_at) -> list[dict]   # 저장 형식 {"task","assignee","dueDate","quote":{...}}

구조화 출력은 Pydantic 스키마 ActionItemList 를 쓰고, 미정 값은 None 이 아니라 "" 로 채운다 (프로젝트 지침).
"""
import copy
import re
from datetime import date, datetime
from typing import Any, Protocol

from pydantic import BaseModel, Field, ValidationError, field_validator

from app.config import settings
from app.uploads import fake_action_items

WEEKDAYS_KO = ["월", "화", "수", "목", "금", "토", "일"]
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


class _EmptyStringModel(BaseModel):
    # LLM 이 null 을 보내도 "" 로 바꾼다 (누락은 기본값 "" 로 채워진다)
    @field_validator("*", mode="before")
    @classmethod
    def _none_to_empty(cls, value: Any) -> Any:
        return "" if value is None else value


class ExtractedQuote(_EmptyStringModel):
    speaker: str = Field("", description="발화한 사람 (전사문 표기 그대로)")
    timestamp: str = Field("", description="HH:MM:SS (전사문 표기 그대로)")
    text: str = Field("", description="근거 발화 원문 (전사문에서 그대로 인용)")


class ExtractedItem(_EmptyStringModel):
    task: str = Field("", description="해야 할 일 (한 문장)")
    assignee: str = Field("", description="일을 수락한 사람. 모르면 빈 문자열")
    due_date: str = Field("", description="마감일 YYYY-MM-DD. 정해지지 않았으면 빈 문자열")
    quote: ExtractedQuote = Field(default_factory=ExtractedQuote)

    @field_validator("quote", mode="before")
    @classmethod
    def _none_quote(cls, value: Any) -> Any:
        return {} if value is None or value == "" else value


class ActionItemList(_EmptyStringModel):
    items: list[ExtractedItem] = Field(default_factory=list)

    @field_validator("items", mode="before")
    @classmethod
    def _none_items(cls, value: Any) -> Any:
        return [] if value is None or value == "" else value


class ExtractionError(RuntimeError):
    """LLM 응답이 스키마에 맞지 않음"""


def response_json_schema() -> dict:
    """Gemini 에 줄 JSON Schema: $ref 를 풀어 넣고, 모든 필드를 required 로 만든다(빈 값은 ""로 채우도록 유도)."""
    raw = ActionItemList.model_json_schema()
    defs = raw.pop("$defs", {})

    def resolve(node: Any) -> Any:
        if isinstance(node, dict):
            if "$ref" in node:
                return resolve(copy.deepcopy(defs[node["$ref"].split("/")[-1]]))
            out = {k: resolve(v) for k, v in node.items() if k not in ("title", "default")}
            if out.get("type") == "object" and "properties" in out:
                out["required"] = list(out["properties"])
            return out
        if isinstance(node, list):
            return [resolve(v) for v in node]
        return node

    return resolve(raw)


def _valid_date(value: str) -> str:
    value = value.strip()
    if not _DATE_RE.match(value):
        return ""
    try:
        date.fromisoformat(value)
    except ValueError:
        return ""
    return value


def postprocess(result: ActionItemList) -> list[dict]:
    """서버 후처리: 날짜가 YYYY-MM-DD 가 아니면 "", task 가 빈 항목은 버림 → 저장 형식(camelCase)으로 변환."""
    items: list[dict] = []
    for item in result.items:
        task = item.task.strip()
        if not task:
            continue
        items.append({
            "task": task,
            "assignee": item.assignee.strip(),
            "dueDate": _valid_date(item.due_date),
            "quote": {"speaker": item.quote.speaker.strip(), "timestamp": item.quote.timestamp.strip(),
                      "text": item.quote.text.strip()},
        })
    return items


def build_prompt(transcript: str, started_at: datetime) -> str:
    weekday = WEEKDAYS_KO[started_at.weekday()]
    return f"""다음은 한국어 회의 전사문입니다. 액션아이템을 추출하세요.

회의 일시: {started_at.isoformat()} ({weekday}요일)

규칙:
- "지시(요청)한 사람"과 "수락한 사람"이 있는 지시-수락 쌍만 액션아이템으로 잡습니다. 잡담, 단순 의견, 미정 논의는 제외합니다.
- assignee 는 일을 수락한 사람입니다.
- "이번 주 목요일", "다음 주 금요일", "내일" 같은 상대 표현은 회의 일시를 기준으로 YYYY-MM-DD 로 바꿔 due_date 에 넣습니다.
  "이번 주"는 회의일이 속한 월요일~일요일, "다음 주"는 그다음 월요일~일요일입니다.
- 마감일이 언급되지 않았거나 알 수 없는 값은 null 이 아니라 빈 문자열("")로 둡니다.
- quote 에는 근거가 된 지시 발화를 전사문에서 글자 그대로 인용합니다(speaker, timestamp, text). 고쳐 쓰거나 요약하지 않습니다.
- 액션아이템이 없으면 items 를 빈 배열로 둡니다.

전사문:
{transcript}"""


class Extractor(Protocol):
    def extract(self, transcript: str, started_at: datetime) -> list[dict]: ...


class FakeExtractor:
    """가짜 추출: 테스트 가이드 A파일 정답표 2건 (기존 fake_action_items 재사용)."""

    def extract(self, transcript: str, started_at: datetime) -> list[dict]:
        return fake_action_items(started_at)


class GeminiExtractor:
    def __init__(self, client: Any, model: str):
        self._client = client
        self._model = model

    def extract(self, transcript: str, started_at: datetime) -> list[dict]:
        from google.genai import types

        response = self._client.models.generate_content(
            model=self._model,
            contents=build_prompt(transcript, started_at),
            # 설치된 google-genai 에는 response_schema 와 response_json_schema 가 모두 있다.
            # Pydantic 이 만든 JSON Schema 를 그대로 줄 수 있는 response_json_schema 를 쓴다.
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                response_json_schema=response_json_schema(),
                temperature=0,
            ),
        )
        try:
            parsed = ActionItemList.model_validate_json(response.text or "")
        except ValidationError as exc:
            raise ExtractionError("LLM response did not match ActionItemList") from exc
        return postprocess(parsed)


def make_extractor() -> Extractor:
    if settings.llm_provider == "gemini":
        from app.pipeline.gemini_client import make_gemini_client

        return GeminiExtractor(make_gemini_client(), settings.gemini_llm_model)
    return FakeExtractor()
