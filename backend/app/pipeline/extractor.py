"""
업무 추출기(stub app/pipeline/extractor.py 이식). 모든 추출기는 같은 모양을 따른다:
    extract(transcript, held_at) -> ExtractionResult   # 업무 후보 목록 + 출처(extract_model·prompt_version·extracted_at)

구조화 출력은 Pydantic 스키마 ActionItemList 를 쓰고, 미정 값은 None 이 아니라 "" 로 채운다 (프로젝트 지침).
담당자는 이름 문자열로만 돌려준다(계정 매핑은 4b단계).
held_at 은 회의 현지 시각(시간대 포함)으로 넘긴다. 날짜·요일·상대 날짜 계산의 기준이 되므로 UTC로 바꾼 값을 넘기면 날짜가 어긋날 수 있다.
"""
import copy
import hashlib
import json
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Protocol

from pydantic import BaseModel, Field, ValidationError, field_validator

from app.config import settings
from app.pipeline.dates import WEEKDAYS_KO, resolve_due_date
from app.models.minutes import MINUTES_FIELDS, NO_CONTENT
from app.pipeline.retry import generate_with_retry
from app.pipeline.fakes import fake_items_raw, fake_minutes_raw

log = logging.getLogger("app.extractor")
_TIMESTAMP_RE = re.compile(r"^(\d{1,2}):([0-5]\d):([0-5]\d)$")


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


class MeetingMinutesDraft(_EmptyStringModel):
    """회의록 5개 항목 출력 형식(엔진과 무관하게 고정). 정보가 없으면 빈 문자열(서버가 "내용없음"으로 저장)."""

    purpose: str = Field("", description="회의 목적")
    discussion: str = Field("", description="주요 논의사항")
    decisions: str = Field("", description="결정사항")
    risks: str = Field("", description="리스크")
    next_agenda: str = Field("", description="다음 안건")


class ExtractionError(RuntimeError):
    """LLM 응답이 스키마에 맞지 않음"""


@dataclass(frozen=True)
class ActionItemCandidate:
    """업무 후보 1건. 근거 인용(발화자·타임스탬프·원문)은 업무마다 1개(1:1)."""

    task: str
    assignee: str  # 이름 문자열(모르면 "")
    due_date: str  # YYYY-MM-DD 또는 ""
    quote_speaker: str
    quote_timestamp: str  # HH:MM:SS 또는 ""
    quote_text: str
    evidence_start_sec: float | None  # quote_timestamp 를 초로 바꾼 값(형식이 틀리면 None)


@dataclass(frozen=True)
class ExtractionResult:
    items: list[ActionItemCandidate] = field(default_factory=list)
    extract_model: str = ""
    prompt_version: str = ""
    extracted_at: datetime | None = None
    # 회의록 5개 항목(MINUTES_FIELDS 키 -> 글자). 응답이 없거나 형식이 틀려 만들지 못했으면 None(저장 때 모두 "내용없음")
    minutes: dict[str, str] | None = None
    minutes_prompt_version: str = ""


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


def timestamp_to_seconds(value: str) -> float | None:
    """'HH:MM:SS' → 초. 형식이 틀리면 None."""
    match = _TIMESTAMP_RE.match(value.strip())
    if not match:
        return None
    h, m, s = (int(g) for g in match.groups())
    return float(h * 3600 + m * 60 + s)


def postprocess(result: ActionItemList, held_at: datetime) -> list[ActionItemCandidate]:
    """서버 후처리: task 가 빈 항목은 버리고, 마감일은 YYYY-MM-DD(상대 표현은 회의일 기준 계산, 못 정하면 ""),
    값은 앞뒤 공백 제거. 같은 발화에서 나온 여러 업무는 합치지 않고 각각 남긴다(단일 발화 다중 업무)."""
    base = held_at.date()
    items: list[ActionItemCandidate] = []
    for item in result.items:
        task = item.task.strip()
        if not task:
            continue
        timestamp = item.quote.timestamp.strip()
        items.append(
            ActionItemCandidate(
                task=task,
                assignee=item.assignee.strip(),
                due_date=resolve_due_date(item.due_date, base),
                quote_speaker=item.quote.speaker.strip(),
                quote_timestamp=timestamp,
                quote_text=item.quote.text.strip(),
                evidence_start_sec=timestamp_to_seconds(timestamp),
            )
        )
    return items


def parse_minutes(text: str) -> dict[str, str]:
    """5개 항목 응답 JSON 문자열 -> {칸: 글자}. 앞뒤 공백을 지우고 빈 항목은 "내용없음". 형식이 틀리면 ExtractionError."""
    try:
        draft = MeetingMinutesDraft.model_validate_json(text or "")
    except ValidationError as exc:
        raise ExtractionError("LLM response did not match MeetingMinutesDraft") from exc
    return {key: (getattr(draft, key).strip() or NO_CONTENT) for key in MINUTES_FIELDS}


def minutes_json_schema() -> dict:
    """Gemini 에 줄 5개 항목 JSON Schema(모든 칸 required, 문자열)."""
    raw = MeetingMinutesDraft.model_json_schema()
    properties = {k: {kk: vv for kk, vv in v.items() if kk not in ("title", "default")} for k, v in raw["properties"].items()}
    return {"type": "object", "properties": properties, "required": list(properties)}


def parse_response(text: str, held_at: datetime) -> list[ActionItemCandidate]:
    """LLM 응답 JSON 문자열 → 업무 후보 목록. 스키마에 맞지 않으면 ExtractionError."""
    try:
        parsed = ActionItemList.model_validate_json(text or "")
    except ValidationError as exc:
        raise ExtractionError("LLM response did not match ActionItemList") from exc
    return postprocess(parsed, held_at)


# 추출 프롬프트 템플릿. {started_at}·{weekday}·{transcript} 자리만 회의마다 바뀌고 나머지는 고정이다.
# 템플릿 안에 다른 중괄호를 쓰면 str.format 이 깨지므로 넣지 않는다.
PROMPT_TEMPLATE = """다음은 한국어 회의 전사문입니다. 액션아이템을 추출하세요.

회의 일시: {started_at} ({weekday}요일)

[1. 무엇을 잡는가]
전사문 전체를 처음부터 끝까지 훑어, 아래 기준에 맞는 것을 빠짐없이 모두 찾습니다. 앞부분에서 몇 개 찾았다고 멈추지 않습니다.
포함:
- 지시-수락: 누군가 일을 지시(요청)하고 다른 사람이 수락한 것.
  제안·권유형 요청도 지시로 봅니다. 예: "~하시는 게 좋을 것 같아요", "~볼 수 있으면 좋겠네요", "~해 주세요".
  상대가 "네", "알겠습니다", "네, 그렇게 하겠습니다"로 받으면 지시-수락으로 포함합니다.
- 확정 약속: 실무 보고 중 구체적인 기한 또는 대상이 있는 약속.
  예: "~하겠습니다", "~할 예정입니다", "~까지 하겠습니다", "오늘 다시 연락해 보겠습니다".
  시키지 않았어도 본인이 할 일과 기한(또는 대상)을 함께 말한 자발적 약속도 포함합니다.
  예: "오늘 마무리될 것 같습니다", "이번 주 중에 하겠습니다", "~까지 등록해야 됩니다".
- 한 발화 안에 약속·업무가 여러 개면 각각 별도 항목으로 뽑습니다. 긴 보고 발화 하나에서 항목 하나만 뽑고 끝내지 않습니다.
  업체·프로젝트·고객명이 다르면 다른 항목입니다.
제외:
- 이미 끝난 일의 보고 ("보냈습니다", "처리했습니다", "정리 완료했고요").
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

[6. 마지막 점검]
- 목록을 다 만든 뒤 전사문을 다시 처음부터 훑어 "~하겠습니다", "~할 예정", "~까지", "알겠습니다", "네, 그렇게" 같은 표현이 붙은 문장을 찾습니다.
- 그중 [1]의 포함 기준에 맞는데 목록에 빠진 것만 추가합니다. 이미 뽑은 항목과 겹치는 것은 추가하지 않습니다.
- 추가하는 항목도 [1]의 제외 기준과 [2]~[5]의 담당자·작업·마감일·인용 규칙을 그대로 따릅니다.

[7. 빈 결과]
- 액션아이템이 없으면 items 를 빈 배열로 둡니다.

전사문:
{transcript}"""

# 회의록 5개 항목 프롬프트. 업무 추출 프롬프트와는 따로 부른다(업무 추출 규칙·결과에 영향이 없도록).
MINUTES_PROMPT_TEMPLATE = """다음은 한국어 회의 전사문입니다. 회의록의 5개 항목을 작성하세요.

회의 일시: {started_at} ({weekday}요일)

작성 규칙:
- purpose: 이 회의의 목적을 한두 문장으로 씁니다.
- discussion: 주요 논의사항을 핵심만 줄 단위(항목마다 한 줄)로 씁니다.
- decisions: 회의에서 합의·결정된 사항을 줄 단위로 씁니다.
- risks: 언급된 위험·우려·지연 요인을 줄 단위로 씁니다.
- next_agenda: 다음 회의에서 다룰 안건이나 후속 논의를 줄 단위로 씁니다.
- 전사문에 근거가 없는 내용은 지어내지 않습니다. 정보가 없는 항목은 null 이 아니라 빈 문자열("")로 둡니다.
- 전사문에 나온 이름·업체명은 표기 그대로 씁니다.

전사문:
{transcript}"""
MINUTES_PROMPT_VERSION = hashlib.sha256(MINUTES_PROMPT_TEMPLATE.encode("utf-8")).hexdigest()[:12]

# 프롬프트 버전: 고정 템플릿(전사문·회의 일시 제외)의 SHA-256 앞 12자. 템플릿 글자가 바뀌면 자동으로 바뀐다(수동 관리 없음).
PROMPT_VERSION = hashlib.sha256(PROMPT_TEMPLATE.encode("utf-8")).hexdigest()[:12]

# 가짜 추출기는 실제 모델·프롬프트를 쓰지 않으므로 출처를 이 값으로 기록한다
FAKE_PROVENANCE = "fake"


def build_prompt(transcript: str, held_at: datetime) -> str:
    weekday = WEEKDAYS_KO[held_at.weekday()]
    return PROMPT_TEMPLATE.format(started_at=held_at.isoformat(), weekday=weekday, transcript=transcript)


def build_minutes_prompt(transcript: str, held_at: datetime) -> str:
    weekday = WEEKDAYS_KO[held_at.weekday()]
    return MINUTES_PROMPT_TEMPLATE.format(started_at=held_at.isoformat(), weekday=weekday, transcript=transcript)


class Extractor(Protocol):
    def extract(self, transcript: str, held_at: datetime) -> ExtractionResult: ...


class FakeExtractor:
    """가짜 추출: 테스트 가이드 A파일 정답표 2건."""

    def extract(self, transcript: str, held_at: datetime) -> ExtractionResult:
        parsed = ActionItemList.model_validate({"items": fake_items_raw(held_at)})
        return ExtractionResult(
            items=postprocess(parsed, held_at),
            extract_model=FAKE_PROVENANCE,
            prompt_version=FAKE_PROVENANCE,
            extracted_at=datetime.now(timezone.utc),
            minutes=parse_minutes(json.dumps(fake_minutes_raw())),
            minutes_prompt_version=FAKE_PROVENANCE,
        )


class GeminiExtractor:
    def __init__(self, client: Any, model: str):
        self._client = client
        self._model = model

    def extract(self, transcript: str, held_at: datetime) -> ExtractionResult:
        from google.genai import types

        response = generate_with_retry(
            self._client,
            model=self._model,
            contents=build_prompt(transcript, held_at),
            # Pydantic 이 만든 JSON Schema 를 그대로 줄 수 있는 response_json_schema 를 쓴다
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                response_json_schema=response_json_schema(),
                temperature=0,
            ),
        )
        items = parse_response(response.text or "", held_at)
        minutes = self._minutes(transcript, held_at)
        return ExtractionResult(
            items=items,
            extract_model=self._model,
            prompt_version=PROMPT_VERSION,
            extracted_at=datetime.now(timezone.utc),
            minutes=minutes,
            minutes_prompt_version=MINUTES_PROMPT_VERSION if minutes is not None else "",
        )

    def _minutes(self, transcript: str, held_at: datetime) -> dict[str, str] | None:
        """5개 항목은 업무 추출과 따로 부른다. 호출·응답 형식이 어떻게 틀려도 업무 추출 결과에는 영향이 없도록 None 으로 돌려준다
        (저장 때 5개 항목만 "내용없음"). 로그에는 예외 종류만 남긴다."""
        from google.genai import types

        try:
            response = generate_with_retry(
                self._client,
                model=self._model,
                contents=build_minutes_prompt(transcript, held_at),
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                    response_json_schema=minutes_json_schema(),
                    temperature=0,
                ),
            )
            return parse_minutes(response.text or "")
        except Exception as exc:  # noqa: BLE001
            log.warning("minutes generation failed: %s", type(exc).__name__)
            return None


def make_gemini_extractor(client: Any | None = None) -> GeminiExtractor:
    """설정(GEMINI_MODEL·GEMINI_KEY_MODE)으로 Gemini 추출기를 만든다. 어느 추출기를 쓸지는 호출부(4b)가 정한다."""
    if client is None:
        from app.pipeline.gemini_client import make_gemini_client

        client = make_gemini_client()
    return GeminiExtractor(client, settings.gemini_model)
