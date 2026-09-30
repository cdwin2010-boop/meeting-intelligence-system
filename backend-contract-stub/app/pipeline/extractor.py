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

[1. 무엇을 잡는가]
전사문 전체를 처음부터 끝까지 훑어, 아래 기준에 맞는 것을 빠짐없이 모두 찾습니다. 앞부분에서 몇 개 찾았다고 멈추지 않습니다.
포함:
- 지시-수락: 누군가 일을 지시(요청)하고 다른 사람이 수락한 것.
- 확정 약속: 실무 보고 중 구체적인 기한 또는 대상이 있는 약속.
  예: "~하겠습니다", "~할 예정입니다", "~까지 하겠습니다", "오늘 다시 연락해 보겠습니다".
제외:
- 이미 끝난 일의 보고 ("보냈습니다", "처리했습니다").
- 기한도 대상도 없는 막연한 의견 ("생각해 보겠습니다", "검토해 봐야겠네요").
- 잡담 (점심 메뉴, 인사, 날씨 등).
- 일반론 설명이나 강의.

[2. 담당자(assignee) 표기]
화자 번호보다 실제 호칭을 우선합니다. 다음 순서를 따릅니다.
a) 먼저 전사문 전체를 훑어 "화자 표기 → 이름/직함" 대응을 정합니다. 근거로 인정하는 경우는 다음뿐입니다.
   - 누군가 이름·직함으로 부르고("권 부장님", "한 팀장") 바로 이어서 특정 화자가 답하는 경우
   - 화자 본인이 이름·직함을 밝히는 경우
   - 다른 사람이 "○○ 씨가 하고 있고"처럼 특정인을 지목하고, 그 사람이 이어서 말하는 경우
   근거가 한 번뿐이고 답한 사람이 여럿이라 누구인지 불분명하면 대응을 만들지 않습니다.
b) 대응이 있으면 assignee 에 그 호칭을 씁니다. 예: "권 부장", "한 팀장", "박다솜".
   존칭("님")은 빼고, 성과 직함이 함께 나오면 함께 씁니다.
   성 없이 직함만 알 수 있고 같은 직함이 여러 명일 수 있으면 호칭으로 쓰지 말고 c)를 따릅니다.
c) 이름을 알 수 없으면 전사문의 화자 표기(예: "화자5")를 그대로 씁니다. 이름을 지어내지 않습니다.
d) 누가 맡는지 자체가 불분명하면(여러 명이 "네"라고 답한 경우 등) assignee 를 빈 문자열("")로 둡니다.
e) 같은 화자는 회의 전체에서 항상 같은 표기를 씁니다.
- 지시-수락이면 수락한 사람, 확정 약속이면 약속한 사람이 담당자입니다.

[3. 작업(task) 표기]
- 해야 할 일을 한 문장으로 씁니다.
- 업체·고객명은 전사문 표기 그대로 씁니다. 비슷한 이름(예: "EZ"와 "이지트")을 임의로 합치거나 바꾸지 않습니다.

[4. 마감일(due_date)]
- "오늘", "내일", "이번 주 목요일", "다음 주 금요일" 같은 상대 표현은 회의 일시를 기준으로 계산해 YYYY-MM-DD 로 씁니다.
  "오늘"은 회의일, "이번 주"는 회의일이 속한 월요일~일요일, "다음 주"는 그다음 월요일~일요일입니다.
- 날짜 하나로 정할 근거가 없으면(언급 없음, 요일 없이 "다음 주"만 있음 등) null 이 아니라 빈 문자열("")로 둡니다. 날짜를 짐작해 넣지 않습니다.

[5. 근거 인용(quote)]
- 근거가 된 발화를 전사문에서 글자 그대로 인용합니다(speaker, timestamp, text). 고쳐 쓰거나 요약하지 않습니다.
  지시-수락이면 지시 발화, 확정 약속이면 약속한 발화를 인용합니다.
- speaker 와 timestamp 는 전사문 표기 그대로 씁니다(호칭으로 바꾸지 않음).

[6. 빈 결과]
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
