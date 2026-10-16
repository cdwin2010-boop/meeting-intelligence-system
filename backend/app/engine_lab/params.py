"""벤치마크 변수 정의·검증·프리셋(작업 L-5). 기본값·허용 범위·프리셋은 config/bench_params.json 한 곳에서만 읽는다(코드에 박지 않는다).

- validate(): 값 형식·범위 검사(서버 재검사). 오류는 "무엇이/왜/얼마로 바꿀지" 문장.
- check_combination(): 구간 크기 ≤ (맥락 × 0.9 − 고정 지시문 − 출력 예약) ÷ 글자당 토큰 상한 검사.
- describe(): 프리셋 이름, 수정 여부·항목, 값의 출처(기본값/프리셋/화면 입력). 키·주소·원문은 다루지 않는다.
"""
import json
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from app.config import settings
from app.pipeline.chunked_extract import MIN_AUTO_CHUNK_CHARS, fixed_prompt_chars, safe_chunk_chars
from app.pipeline.engine_profile import EngineProfile

PARAMS_PATH = Path(__file__).resolve().parent.parent.parent / "config" / "bench_params.json"
SRC_DEFAULT, SRC_PRESET, SRC_INPUT = "기본값", "프리셋", "화면 입력"


@dataclass(frozen=True)
class ParamError:
    key: str
    message: str


def spec() -> dict[str, Any]:
    return json.loads(PARAMS_PATH.read_text(encoding="utf-8"))


def _param_defs() -> list[dict[str, Any]]:
    return spec()["params"]


def default_of(p: dict[str, Any]) -> float | int:
    key = p.get("settingsKey")
    if key and hasattr(settings, key):
        value = getattr(settings, key)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return value
    return p["default"]


def default_values() -> dict[str, float | int]:
    return {p["key"]: default_of(p) for p in _param_defs()}


def presets() -> list[dict[str, Any]]:
    return spec()["presets"]


def preset_of(preset_id: str) -> dict[str, Any] | None:
    return next((p for p in presets() if p["id"] == preset_id), None)


def base_values(preset_id: str = "") -> dict[str, float | int]:
    """수정 여부를 가르는 기준값: 기본값 위에 프리셋 값을 덮은 것."""
    base = default_values()
    preset = preset_of(preset_id) if preset_id else None
    if preset:
        base.update(preset["values"])
    return base


def _fmt(value: float | int) -> str:
    return str(int(value)) if float(value).is_integer() else str(value)


def validate(values: dict[str, Any] | None) -> tuple[dict[str, float | int], list[ParamError]]:
    """빠진 항목은 기본값. 알 수 없는 항목·숫자가 아닌 값·범위 밖 값은 오류. 반환값은 (정리된 값, 오류 목록)."""
    out = default_values()
    errors: list[ParamError] = []
    defs = {p["key"]: p for p in _param_defs()}
    for key, raw in (values or {}).items():
        p = defs.get(key)
        if p is None:
            errors.append(ParamError(key, f"'{key}' 는 알 수 없는 변수입니다. 화면에 있는 변수만 보낼 수 있습니다."))
            continue
        lo, hi, dflt = p["min"], p["max"], default_of(p)
        label = p["label"]
        auto = p.get("autoValue")
        if isinstance(raw, bool) or not isinstance(raw, (int, float)):
            errors.append(ParamError(key, f"{label}에는 숫자만 입력할 수 있습니다. {_fmt(dflt)} 같은 숫자로 바꾸세요."))
            continue
        number = float(raw)
        if number != number or number in (float("inf"), float("-inf")):
            errors.append(ParamError(key, f"{label} 값이 올바른 숫자가 아닙니다. {_fmt(dflt)} 로 바꾸세요."))
            continue
        if auto is not None and number == auto:
            out[key] = int(auto)
            continue
        if p["type"] == "int" and not number.is_integer():
            errors.append(ParamError(key, f"{label}은(는) 정수여야 합니다(입력 {_fmt(number)}). {int(round(number))} 처럼 소수점 없이 바꾸세요."))
            continue
        if not lo <= number <= hi:
            extra = " (0 은 자동)" if auto is not None else ""
            errors.append(ParamError(
                key, f"{label}은(는) {_fmt(lo)}~{_fmt(hi)} 범위{extra}여야 합니다. 입력한 {_fmt(number)} 은(는) 서버가 감당할 수 있는 범위를 벗어납니다. "
                     f"{_fmt(min(max(number, lo), hi))} 으로 바꾸거나 기본값 {_fmt(dflt)} 을(를) 쓰세요."))
            continue
        out[key] = int(number) if p["type"] == "int" else number
    return out, errors


def profile_for(values: dict[str, Any], base: EngineProfile | None = None) -> EngineProfile:
    """측정에 쓰는 엔진 프로파일(맥락·출력 예약만 변수 값으로 바꾼다)."""
    ref = base or EngineProfile(
        name="remote-bench", tokens_per_char=settings.remote_llm_tokens_per_char,
        supports_json_format=settings.remote_llm_json_format, use_server_tokenizer=settings.remote_llm_use_server_tokenizer,
    )
    return replace(ref, context_tokens=int(values["contextTokens"]), max_output_tokens=int(values["outputReserveTokens"]))


def chunk_cap(values: dict[str, Any], base: EngineProfile | None = None) -> int:
    """맥락·출력 예약으로 정해지는 구간 글자 수 상한(사전 예산 90%)."""
    return safe_chunk_chars(profile_for(values, base), fixed_prompt_chars(""))


def check_combination(values: dict[str, Any], base: EngineProfile | None = None) -> list[ParamError]:
    """변수 조합 검사. 구간 크기가 상한을 넘거나 맥락이 구간을 넣기에 너무 작으면 오류."""
    profile = profile_for(values, base)
    fixed = fixed_prompt_chars("")
    available = profile.budget_tokens - profile.output_reserve_tokens() - profile.tokens_of_chars(fixed)
    cap = int(available / profile.tokens_per_char)
    if cap < MIN_AUTO_CHUNK_CHARS:
        return [ParamError(
            "contextTokens",
            f"맥락 길이 {int(values['contextTokens'])} 토큰에서 고정 지시문과 출력 예약을 빼면 구간을 넣을 자리가 {MIN_AUTO_CHUNK_CHARS}자 미만입니다. "
            f"맥락 길이를 늘리거나 출력 예약 토큰을 줄이세요.")]
    chunk = int(values["chunkChars"])
    if chunk and chunk > cap:
        return [ParamError(
            "chunkChars",
            f"구간 크기 {chunk}자는 맥락 {int(values['contextTokens'])} 토큰·출력 예약 {profile.output_reserve_tokens()} 토큰 기준 상한 {cap}자를 넘습니다. "
            f"{cap}자 이하로 바꾸거나 0(자동)으로 두세요.")]
    return []


def describe(values: dict[str, Any], preset_id: str = "") -> dict[str, Any]:
    """프리셋 이름, 수정 여부와 수정 항목, 값의 출처. 프리셋이 정의한 항목은 프리셋과, 나머지는 기본값과 비교한다."""
    preset = preset_of(preset_id) if preset_id else None
    base = base_values(preset["id"] if preset else "")
    defaults = default_values()
    labels = {p["key"]: p["label"] for p in _param_defs()}
    sources: dict[str, str] = {}
    modified: list[dict[str, Any]] = []
    for key in labels:
        value = values[key]
        if value != base[key]:
            sources[key] = SRC_INPUT
            modified.append({"key": key, "label": labels[key], "value": value, "base": base[key]})
        elif preset and key in preset["values"]:
            sources[key] = SRC_PRESET
        else:
            sources[key] = SRC_DEFAULT if value == defaults[key] else SRC_INPUT
    if preset is None:
        summary = "프리셋 없음(화면 입력)" if modified else "프리셋 없음(기본값)"
    elif modified:
        items = ", ".join(f"{m['label']}: {_fmt(m['value'])}" for m in modified)
        summary = f"프리셋 {preset['id']} 기반, 수정됨({items})"
    else:
        summary = f"프리셋 {preset['id']} 적용됨"
    return {"preset": preset["id"] if preset else "", "presetName": preset["name"] if preset else "", "modified": bool(modified and preset),
            "modifiedItems": modified if preset else [], "sources": sources, "summary": summary}
