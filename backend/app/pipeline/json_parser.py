"""3단계 견고한 JSON 파서(작업 75-2). 순수 함수 중심, 외부 라이브러리 없음.

1단계(호출부): 엔진 네이티브 JSON 모드를 요청에 담고 프롬프트에도 ```json 블록을 유도한다(llm_client.complete 의 json_mode).
2단계: 응답에서 JSON 문자열을 뽑고(```json 펜스 우선, 없으면 첫 '{' 부터 괄호 균형 스캐너) Pydantic 스키마로 검증한다.
       BOM·앞뒤 설명문·마크다운만 걷어낸다. 끝 쉼표 제거·스마트 따옴표 정규화 같은 내용 변형은 하지 않는다.
       응답이 끝에서 잘린 경우에 한해 열린 문자열·괄호를 닫는 보수적 복구를 시도하고 복구 여부를 표시한다.
3단계: 실패하면 교정 프롬프트로 정확히 1회만 재시도한다(청크당 호출 최대 2회). 재시도도 실패하면 실패로 표시하고 예외를 올리지 않는다.
전사 본문은 로그·오류 요약에 넣지 않는다(필드 경로와 사유만).
"""
import json
import re
from dataclasses import dataclass, field
from typing import Any, Generic, TypeVar

from pydantic import BaseModel, ValidationError

from app.pipeline.llm_client import LlmClient, LlmClientError, Message

T = TypeVar("T", bound=BaseModel)

MAX_RETRIES = 1  # 코드로 강제하는 상한: 설정값이 더 커도 1회만
_FENCE = re.compile(r"```[ \t]*(?:json|JSON)?[ \t]*\r?\n?(.*?)```", re.DOTALL)
_FENCE_OPEN = re.compile(r"```[ \t]*(?:json|JSON)?[ \t]*\r?\n?", re.DOTALL)
_ERROR_MAX = 5
_REPAIR_TRIES = 60


@dataclass(frozen=True)
class ExtractedJson:
    text: str | None  # JSON 객체 문자열(없으면 None)
    repaired: bool = False  # 잘린 응답을 닫아서 복구했는가
    source: str = "none"  # "fence" | "scan" | "none"


def _scan_object(text: str, start: int) -> tuple[int | None, list[str], bool, list[tuple[int, list[str]]]]:
    """text[start] == '{' 부터 괄호 균형을 스캔한다(문자열·이스케이프 인식).
    (끝 위치(포함) 또는 None, 열린 괄호 스택, 문자열 안에서 끝났는가, 쉼표 위치별 스택 스냅샷)."""
    stack: list[str] = []
    in_string = False
    escaped = False
    commas: list[tuple[int, list[str]]] = []
    for i in range(start, len(text)):
        ch = text[i]
        if in_string:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch in "{[":
            stack.append("}" if ch == "{" else "]")
        elif ch in "}]":
            if not stack or stack[-1] != ch:
                return None, stack, False, commas  # 균형이 깨졌다
            stack.pop()
            if not stack:
                return i, stack, False, commas
        elif ch == ",":
            commas.append((i, list(stack)))
    return None, stack, in_string, commas


def _close_truncated(text: str, start: int) -> str | None:
    """끝에서 잘린 객체를 보수적으로 닫아 json.loads 가 통과하는 문자열을 만든다. 못 만들면 None."""
    end, stack, in_string, commas = _scan_object(text, start)
    if end is not None or not stack:
        return None
    body = text[start:]
    candidate = body + ('"' if in_string else "")
    candidate = candidate.rstrip()
    if candidate.endswith(","):
        candidate = candidate[:-1]
    as_is = candidate + "".join(reversed(stack))
    # 마지막 요소가 덜 쓰인 경우: 가장 최근 쉼표 앞까지 되돌려 닫는다(최대 _REPAIR_TRIES 번)
    rolled = [text[start:pos] + "".join(reversed(snapshot)) for pos, snapshot in reversed(commas[-_REPAIR_TRIES:])]
    # 문자열 한가운데서 잘렸다면 반쯤 쓰인 값을 남기지 않도록 덜 쓰인 요소를 버리는 쪽을 먼저 시도한다(내용 오염 방지)
    attempts = rolled + [as_is] if in_string else [as_is] + rolled
    for option in attempts:
        try:
            if isinstance(json.loads(option), dict):
                return option
        except ValueError:
            continue
    return None


_MAX_OBJECTS = 10


def extract_json_objects(response: str) -> list[ExtractedJson]:
    """응답에서 최상위 JSON 객체 후보를 앞에서부터 뽑는다(펜스 안 객체가 먼저, 최대 _MAX_OBJECTS 개)."""
    text = (response or "").lstrip("﻿")
    regions: list[tuple[str, str]] = []
    for match in _FENCE.finditer(text):
        if "{" in match.group(1):
            regions.append((match.group(1), "fence"))
            break
    if not regions:
        opened = _FENCE_OPEN.search(text)
        if opened and "{" in text[opened.end():]:  # 닫는 펜스 없이 잘린 응답
            regions.append((text[opened.end():], "fence"))
    regions.append((text, "scan"))

    found: list[ExtractedJson] = []
    for chunk, source in regions:
        pos = 0
        while len(found) < _MAX_OBJECTS:
            start = chunk.find("{", pos)
            if start < 0:
                break
            end, _stack, _in_string, _commas = _scan_object(chunk, start)
            if end is not None:
                found.append(ExtractedJson(chunk[start:end + 1], False, source))
                pos = end + 1
                continue
            closed = _close_truncated(chunk, start)
            if closed is not None:
                found.append(ExtractedJson(closed, True, source))
            break  # 균형이 안 맞는 끝: 더 찾지 않는다
    return found


def extract_json_object(response: str) -> ExtractedJson:
    """응답 글자에서 첫 번째 최상위 JSON 객체 문자열을 뽑는다(없으면 text=None)."""
    found = extract_json_objects(response)
    return found[0] if found else ExtractedJson(None, False, "none")


def validate_json(json_text: str, model: type[T]) -> tuple[T | None, list[str]]:
    """JSON 문자열을 스키마로 검증한다. 알 수 없는 추가 필드는 무시한다. (값, 오류 요약 목록)."""
    try:
        data: Any = json.loads(json_text)
    except ValueError as exc:
        return None, [f"JSON 구문 오류: {str(exc)[:120]}"]
    if not isinstance(data, dict):
        return None, ["최상위 값이 JSON 객체가 아닙니다."]
    try:
        return model.model_validate(data), []
    except ValidationError as exc:
        errors = [f"{'.'.join(str(p) for p in e['loc']) or '(root)'}: {e['msg']}"[:160] for e in exc.errors()[:_ERROR_MAX]]
        return None, errors


def parse_response(response: str, model: type[T]) -> tuple[T | None, list[str], bool]:
    """2단계 전체: (검증된 값, 오류 요약, 잘린 응답을 복구했는가). 후보 객체가 여럿이면 처음으로 검증을 통과하는 것."""
    candidates = extract_json_objects(response)
    if not candidates:
        return None, ["응답에서 JSON 객체를 찾지 못했습니다."], False
    first_errors: list[str] = []
    for index, candidate in enumerate(candidates):
        value, errors = validate_json(candidate.text or "", model)
        if value is not None:
            return value, [], candidate.repaired
        if index == 0:
            first_errors = errors
    return None, first_errors, candidates[0].repaired


def build_repair_prompt(response: str, errors: list[str], max_chars: int) -> str:
    """교정 프롬프트: 원본 응답 앞부분 + 오류 요약 + "JSON 만 다시 출력" 지시."""
    head = (response or "")[:max_chars]
    reasons = "\n".join(f"- {e}" for e in errors[:_ERROR_MAX]) or "- 형식 오류"
    return (
        "직전 응답을 스키마에 맞는 JSON 으로 읽을 수 없었습니다.\n\n"
        f"[직전 응답(앞부분)]\n{head}\n\n"
        f"[오류]\n{reasons}\n\n"
        "스키마에 맞게 고쳐서 ```json 코드 블록 안에 JSON 만 다시 출력하세요. 설명은 쓰지 않습니다."
    )


@dataclass
class ParseOutcome(Generic[T]):
    status: str  # "ok" | "repaired"(교정 재시도로 성공) | "failed"
    value: T | None
    calls: int
    errors: list[str] = field(default_factory=list)
    truncated_recovered: bool = False  # 잘린 응답을 닫아 복구함(어느 시도에서든)


def run_with_repair(
    client: LlmClient,
    messages: list[Message],
    model: type[T],
    *,
    timeout: float,
    repair_max_chars: int,
    retry_max: int = MAX_RETRIES,
) -> ParseOutcome[T]:
    """호출 → 2단계 파싱 → 실패 시 교정 프롬프트로 정확히 1회 재시도. 총 호출은 최대 2회이고 예외를 올리지 않는다."""
    retries = max(0, min(retry_max, MAX_RETRIES))
    calls = 0
    errors: list[str] = []
    truncated = False
    current = list(messages)
    last_response = ""
    for attempt in range(retries + 1):
        calls += 1
        try:
            last_response = client.complete(current, json_mode=True, timeout=timeout)
        except (LlmClientError, OSError, TimeoutError) as exc:
            errors = [f"LLM 호출 실패({type(exc).__name__})"]
            if attempt < retries:
                continue  # 호출 실패도 같은 메시지로 한 번 더(총 2회 상한은 그대로)
            break
        except Exception as exc:  # noqa: BLE001 — 청크 하나의 실패가 전체를 멈추지 않는다
            errors = [f"LLM 호출 실패({type(exc).__name__})"]
            break
        value, errors, repaired = parse_response(last_response, model)
        truncated = truncated or repaired
        if value is not None:
            return ParseOutcome("ok" if attempt == 0 else "repaired", value, calls, [], truncated)
        if attempt < retries:
            current = list(messages) + [{"role": "user", "content": build_repair_prompt(last_response, errors, repair_max_chars)}]
    return ParseOutcome("failed", None, calls, errors, truncated)
