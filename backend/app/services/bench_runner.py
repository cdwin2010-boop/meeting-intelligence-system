"""원격(시험용) 엔진 연결 점검·L-3 벤치마크 측정 러너(작업 L-3a).

- 측정 항목: A 연결·모델 상태 / B 단건 추출 / C 컨텍스트 경계(단일 호출 vs 분할 경로) / D 근거 검증 3분류(C 결과에서 집계) /
  E 실패 경로(잘못된 모델명) / F 모델 상태·자원(확인 가능한 경우만) / G 같은 입력 연속 실행(드리프트).
  빠른 점검 = A·B(1회)·E(1회)·F(1회). 전체 측정 = A~F 각 BENCH_REPEAT 회 + G BENCH_DRIFT_RUNS 회.
- 입력은 도구에 내장한 가상 전사본(bench_data)뿐이다. 실제 회의 데이터·DB 는 쓰지 않는다. DB 스키마 변경 없음.
- 체크포인트: 단위(항목 1회차)가 끝날 때마다 BENCH_DIR/state.json 에 원자적으로 저장한다. 연결이 끊기면 상태를 interrupted 로 두고,
  이어하기(resume)는 완료 단위를 건너뛰고 남은 단위만 실행한다. 이어하기 때 주소·키·모델은 그 시점의 설정을 다시 읽으므로 바뀌어도 된다.
- 측정은 백그라운드 스레드 1개만(같은 시점 1개). 정지 요청은 현재 요청이 끝난 뒤 반영된다.
- 보안: 상태·결과 파일과 오류 문구에는 주소·키·호스트명·전사 원문·인용문을 넣지 않는다(건수·시간·비율·상태·오류 분류 코드만).
"""
import json
import logging
import os
import re
import statistics
import tempfile
import threading
import time
import uuid
from dataclasses import dataclass, replace
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlsplit

import httpx

from app.config import settings
from app.pipeline.chunked_extract import (
    STATUS_FAILED, ChunkedExtraction, ChunkOptions, _norm, extract_chunked, profile_chunk_options,
)
from app.engine_lab import params as lab_params
from app.services import engine_diag
from app.pipeline.local_llm import NO_KEY, LocalLlmError, OpenAICompatClient, check_base_url, list_models
from app.pipeline.ollama_llm import OllamaClient, list_ollama_models
from app.services import bench_data
from app.services.bench_data import BenchTranscript, GoldItem

log = logging.getLogger("app.bench")

MODE_QUICK = "quick"
MODE_FULL = "full"
S_IDLE, S_RUNNING, S_COMPLETED = "idle", "running", "completed"
S_INTERRUPTED, S_STOPPED, S_FAILED = "interrupted", "stopped", "failed"
RESUMABLE = (S_INTERRUPTED, S_STOPPED)
DISCONNECT_MESSAGE = "연결이 끊겼습니다. 새 주소를 입력하고 이어하기를 눌러 주세요"
INVALID_MODEL = "__bench_invalid_model__"  # E 항목: 서버에 없는 모델명(호출 확인용, 실제 모델명이 아니다)
NOT_MEASURED = "미측정"
UNMEASURABLE = "측정 불가"
_TRANSIENT_REASONS = {"connect", "timeout", "http_5xx", "http_429"}
_GUIDANCE = {
    "connect": "서버에 연결할 수 없습니다. 주소·터널 상태를 확인하세요",
    "timeout": "응답 시간이 초과되었습니다",
    "http_5xx": "서버 오류입니다. 서버 상태를 확인하세요",
    "http_429": "요청 한도를 초과했다고 응답했습니다",
    "http_4xx": "서버가 요청을 거절했습니다(주소·키·모델명을 확인하세요)",
    "context_exceeded": "모델 맥락 길이를 넘는 요청입니다",
    "format": "응답 형식이 올바르지 않습니다",
    "host_blocked": "외부 주소 사용이 허용되지 않았습니다",
    "config": "서버 주소 형식이 올바르지 않습니다",
    "model_missing": "서버에 없는 모델명이라 거절했습니다",
}


class BenchBusy(RuntimeError):
    """이미 측정 작업이 실행 중."""


class BenchError(RuntimeError):
    """시작·이어하기 요청을 받을 수 없음(메시지는 화면에 그대로 보인다)."""


class _Interrupted(Exception):
    """연결 오류가 재시도를 소진했다 → 상태를 중단됨(연결 끊김)으로 두고 멈춘다."""


class _Stop(Exception):
    """사용자 정지 요청."""


@dataclass(frozen=True)
class BenchConfig:
    """한 번의 시작·이어하기에 쓰는 연결값(주소·키는 메모리에만 있고 파일에 쓰지 않는다)."""

    base_url: str
    model: str
    api_key: str
    allow_external: bool
    item_timeout: float
    retry_max: int
    retry_delay: float
    repeat: int
    drift_runs: int
    long_max_chars: int
    # 작업 L-5: 화면 변수와 연결 방식(기본값은 현행 동작과 같다)
    context_tokens: int = 8192
    output_reserve: int = 2048
    chunk_chars: int = 0  # 0 = 자동
    connection_type: str = "openai"


# ------------------------- 단위(체크포인트 단위) 계획 -------------------------
def plan_for(mode: str, repeat: int, drift_runs: int) -> list[str]:
    if mode == MODE_QUICK:
        return ["A#1", "B#1", "E#1", "F#1"]
    plan: list[str] = []
    for kind in ("A", "B", "C", "E", "F"):
        plan += [f"{kind}#{i}" for i in range(1, repeat + 1)]
    plan += [f"G#{i}" for i in range(1, drift_runs + 1)]
    return plan


_TITLES = {
    "A": "A 연결·모델 상태", "B": "B 단건 추출", "C": "C 컨텍스트 경계(단일·분할)", "E": "E 실패 경로(잘못된 모델명)",
    "F": "F 모델 상태·자원", "G": "G 연속 실행(드리프트)",
}


def label_of(key: str) -> str:
    kind, _, nth = key.partition("#")
    return f"{_TITLES.get(kind, kind)} · {nth}회차"


# ------------------------- 파일(체크포인트) -------------------------
def _dir() -> Path:
    path = settings.bench_dir
    return path if path.is_absolute() else Path(__file__).resolve().parents[2] / path


def _state_file() -> Path:
    return _dir() / "state.json"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


_file_lock = threading.RLock()  # 상태 파일 쓰기·읽기를 직렬화한다(Windows 는 읽는 중인 파일을 교체하면 PermissionError)


def _write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with _file_lock:
        fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".bench_", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as out:
                json.dump(data, out, ensure_ascii=False)
            for attempt in range(5):  # 백신·색인기가 잠시 잡고 있을 때를 대비한 짧은 재시도
                try:
                    os.replace(tmp, path)
                    break
                except PermissionError:
                    if attempt == 4:
                        raise
                    time.sleep(0.05)
        except OSError:
            Path(tmp).unlink(missing_ok=True)
            raise


def load_state() -> dict[str, Any] | None:
    try:
        with _file_lock:
            data = json.loads(_state_file().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) and "plan" in data else None


def _save(state: dict[str, Any]) -> None:
    state["updatedAt"] = _now()
    _write_json(_state_file(), state)


# ------------------------- 측정용 클라이언트(요청별 시간·토큰 기록) -------------------------
def _estimate_input_tokens(body: dict, tokens_per_char: float) -> int:
    """요청 본문의 메시지 글자 수로 입력 토큰을 보수적으로 추정한다(본문은 기록하지 않고 숫자만 남긴다)."""
    chars = sum(len(str(m.get("content", ""))) for m in body.get("messages", []) if isinstance(m, dict))
    return int(chars * tokens_per_char) + 1


class _MeteredClient(OpenAICompatClient):
    """운영 원격 엔진과 같은 요청 경로(OpenAICompatClient)에 호출별 소요 시간·사용 토큰을 기록한다. 본문은 기록하지 않는다."""

    def __init__(self, *args: Any, **kwargs: Any):
        super().__init__(*args, **kwargs)
        self.calls: list[dict[str, Any]] = []
        self.tokens_per_char = 1.3

    def _post(self, body: dict, timeout: float) -> httpx.Response:
        started = time.monotonic()
        entry: dict[str, Any] = {"elapsedMs": 0, "promptTokens": None, "completionTokens": None, "status": None,
                                 "estInputTokens": _estimate_input_tokens(body, self.tokens_per_char)}
        try:
            response = super()._post(body, timeout)
        except LocalLlmError:
            entry["elapsedMs"] = int((time.monotonic() - started) * 1000)
            self.calls.append(entry)
            raise
        entry["elapsedMs"] = int((time.monotonic() - started) * 1000)
        entry["status"] = response.status_code
        try:
            usage = response.json().get("usage") or {}
            for key, src in (("promptTokens", "prompt_tokens"), ("completionTokens", "completion_tokens")):
                value = usage.get(src)
                entry[key] = value if isinstance(value, int) else None
        except (ValueError, AttributeError, TypeError):
            pass
        self.calls.append(entry)
        return response


class _MeteredOllamaClient(OllamaClient):
    """Ollama 네이티브(/api/chat) 경로에 같은 기록을 남긴다. 토큰 수는 응답의 prompt_eval_count·eval_count 에서 읽는다."""

    def __init__(self, *args: Any, **kwargs: Any):
        super().__init__(*args, **kwargs)
        self.calls: list[dict[str, Any]] = []
        self.tokens_per_char = 1.3

    def _post(self, path: str, body: dict, timeout: float) -> httpx.Response:
        if "messages" not in body:  # 모델 내리기(keep_alive=0) 같은 호출은 측정 호출이 아니다
            return super()._post(path, body, timeout)
        started = time.monotonic()
        entry: dict[str, Any] = {"elapsedMs": 0, "promptTokens": None, "completionTokens": None, "status": None,
                                 "estInputTokens": _estimate_input_tokens(body, self.tokens_per_char)}
        try:
            response = super()._post(path, body, timeout)
        except LocalLlmError:
            entry["elapsedMs"] = int((time.monotonic() - started) * 1000)
            self.calls.append(entry)
            raise
        entry["elapsedMs"] = int((time.monotonic() - started) * 1000)
        entry["status"] = response.status_code
        try:
            data = response.json()
            for key, src in (("promptTokens", "prompt_eval_count"), ("completionTokens", "eval_count")):
                value = data.get(src)
                entry[key] = value if isinstance(value, int) else None
        except (ValueError, AttributeError, TypeError):
            pass
        self.calls.append(entry)
        return response


def _profile(cfg: BenchConfig):
    """측정에 쓰는 엔진 프로파일: 화면 변수(맥락·출력 예약)와 연결 방식을 반영한다."""
    from app.services import engine_test as engine

    base = engine.remote_profile()
    return replace(base, context_tokens=cfg.context_tokens, max_output_tokens=cfg.output_reserve,
                   send_context_option=cfg.connection_type == "ollama")


def _client(cfg: BenchConfig, model: str | None = None) -> Any:
    from app.services import engine_test as engine  # 순환 import 방지(서비스 간 지연 import)

    profile = _profile(cfg)
    if cfg.connection_type == "ollama":
        client: Any = _MeteredOllamaClient(
            engine.ollama_base(cfg.base_url), model or cfg.model, num_ctx=profile.context_tokens,
            num_predict=profile.output_reserve_tokens(), allow_external=cfg.allow_external,
            json_mode_enabled=profile.supports_json_format, truncation_ratio=profile.truncation_ratio,
        )
    else:
        client = _MeteredClient(
            cfg.base_url, model or cfg.model, cfg.api_key or NO_KEY, max_output_tokens=profile.output_reserve_tokens(),
            allow_external=cfg.allow_external, json_mode_enabled=profile.supports_json_format, context_tokens=profile.context_tokens,
            truncation_ratio=profile.truncation_ratio,
        )
    client.tokens_per_char = profile.tokens_per_char
    return client


def _options(cfg: BenchConfig) -> ChunkOptions:
    from app.services import engine_test as engine

    remote = engine.load_remote_settings()
    return profile_chunk_options(
        _profile(cfg), "", configured_chars=cfg.chunk_chars or remote.chunk_max_chars, timeout=cfg.item_timeout,
        retry_max=engine.REMOTE_RETRY_CEILING, retry_ceiling=engine.REMOTE_RETRY_CEILING,
    )


# ------------------------- 계산 도우미 -------------------------
def _stats(values: list[float | int | None]) -> dict[str, Any]:
    nums = [v for v in values if isinstance(v, (int, float))]
    if not nums:
        return {"n": 0, "mean": None, "min": None, "max": None}
    return {"n": len(nums), "mean": round(statistics.fmean(nums), 2), "min": min(nums), "max": max(nums)}


def _tps(tokens: int | None, elapsed_ms: int) -> float | None:
    if tokens is None or elapsed_ms <= 0:
        return None
    return round(tokens / (elapsed_ms / 1000), 2)


def _sum_known(values: list[int | None]) -> int | None:
    known = [v for v in values if isinstance(v, int)]
    return sum(known) if known else None


_ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def due_class(candidate_due: str, gold_due: str) -> tuple[str, int | None]:
    """기한 분류: blank(빈 값) / formatError(형식 오류) / different(유효한 날짜지만 정답과 다름, 일수 차이) / match. 값 자체는 남기지 않는다."""
    value = (candidate_due or "").strip()
    if not value:
        return "blank", None
    try:
        got = date.fromisoformat(value) if _ISO_DATE.match(value) else None
        want = date.fromisoformat(gold_due)
    except ValueError:
        got, want = None, None
    if got is None or want is None:
        return "formatError", None
    return ("match", 0) if got == want else ("different", (got - want).days)


def _match(gold: tuple[GoldItem, ...], extraction: ChunkedExtraction) -> dict[str, Any]:
    """정답지와 추출 결과 비교: 누락률, 담당자·기한 일치율(찾은 업무 기준). 인용문·업무명 원문은 결과에 남기지 않는다."""
    used: set[int] = set()
    found = assignee_ok = due_ok = 0
    due_counts = {"blank": 0, "different": 0, "formatError": 0, "match": 0}
    day_diffs: list[int] = []
    for g in gold:
        for idx, ci in enumerate(extraction.items):
            if idx in used:
                continue
            c = ci.candidate
            text = _norm(f"{c.task} {c.quote_text}")
            if _norm(g.ref) in text or (_norm(g.team) in text and _norm(g.noun) in text):
                used.add(idx)
                found += 1
                assignee_ok += int(g.assignee in c.assignee)
                due_ok += int(c.due_date == g.due)
                kind, diff = due_class(c.due_date, g.due)
                due_counts[kind] += 1
                if diff is not None and kind == "different":
                    day_diffs.append(diff)
                break
    total = len(gold)
    return {
        "goldCount": total, "extractedCount": len(extraction.items), "foundCount": found,
        "missRate": round((total - found) / total, 4) if total else None,
        "assigneeMatchRate": round(assignee_ok / found, 4) if found else None,
        "dueMatchRate": round(due_ok / found, 4) if found else None,
        "dueClass": due_counts, "dueDayDiffs": day_diffs,
    }


def _classify_evidence(transcript: BenchTranscript, extraction: ChunkedExtraction) -> dict[str, Any]:
    """근거 3분류: 검증 통과 / Line ID 불일치(인용문은 전사본 안에 있음) / 인용문 미일치."""
    whole = _norm(transcript.text)
    passed = id_mismatch = not_found = 0
    for item in extraction.items:
        if item.evidence_verified:
            passed += 1
        elif _norm(item.candidate.quote_text) and _norm(item.candidate.quote_text) in whole:
            id_mismatch += 1
        else:
            not_found += 1
    total = len(extraction.items)
    rate = (lambda n: round(n / total, 4) if total else None)
    return {
        "total": total, "verified": passed, "lineIdMismatch": id_mismatch, "quoteNotFound": not_found,
        "verifiedRate": rate(passed), "lineIdMismatchRate": rate(id_mismatch), "quoteNotFoundRate": rate(not_found),
    }


def input_token_summary(calls: list[dict[str, Any]], context_tokens: int) -> dict[str, Any]:
    """호출별 입력 토큰의 최소/중앙/최대와 입력 잘림 의심 여부(값만 남긴다).
    의심 조건: 입력 토큰 ≥ 맥락×0.98, 또는 추정 입력이 현저히 큰데 값이 서버 기본 맥락(예: 2048)과 같음."""
    sp = lab_params.spec()
    ratio, margin, defaults = float(sp["truncationSuspectRatio"]), float(sp["estimateMarginRatio"]), set(sp["serverDefaultContexts"])
    known = [(c["promptTokens"], c.get("estInputTokens")) for c in calls if isinstance(c.get("promptTokens"), int)]
    reasons: list[str] = []
    if any(p >= context_tokens * ratio for p, _ in known):
        reasons.append("near_context_limit")
    if any(p in defaults and isinstance(est, int) and est >= p * margin for p, est in known):
        reasons.append("equals_server_default")
    tokens = [p for p, _ in known]
    return {"n": len(tokens), "min": min(tokens) if tokens else None, "median": statistics.median(tokens) if tokens else None,
            "max": max(tokens) if tokens else None, "truncationSuspected": bool(reasons), "truncationReasons": reasons}


def _chunk_rows(extraction: ChunkedExtraction, calls: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """구간별 소요: ChunkReport.calls 개수만큼 기록된 호출을 순서대로 나눠 합산한다(구간은 순차 처리)."""
    rows: list[dict[str, Any]] = []
    pos = 0
    for report in extraction.chunks:
        part = calls[pos: pos + report.calls]
        pos += report.calls
        rows.append(
            {"index": report.index + 1, "status": report.status, "calls": report.calls, "itemCount": report.item_count,
             "elapsedMs": sum(c["elapsedMs"] for c in part)}
        )
    return rows


# ------------------------- 단위 실행 -------------------------
def _is_transient(exc: BaseException) -> bool:
    return isinstance(exc, LocalLlmError) and exc.reason in _TRANSIENT_REASONS


class _Ctx:
    def __init__(self, cfg: BenchConfig, stop: threading.Event, sleep: Callable[[float], None] = time.sleep):
        self.cfg = cfg
        self.stop = stop
        self.sleep = sleep
        self._options: ChunkOptions | None = None
        self.unit_retries = 0  # 현재 단위에서 실제로 다시 시도한 횟수(소진 후 중단되어도 남는다)
        self._short = bench_data.short_transcript()
        self._long: BenchTranscript | None | bool = False  # False = 아직 안 만듦

    @property
    def options(self) -> ChunkOptions:
        if self._options is None:
            self._options = _options(self.cfg)
        return self._options

    @property
    def short(self) -> BenchTranscript:
        return self._short

    @property
    def long(self) -> BenchTranscript | None:
        if self._long is False:
            # 구간 글자 수 한도의 약 2.3배(3구간 이상)를 목표로 한다. 상한 안에서 만들 수 없으면 None
            self._long = bench_data.long_transcript(int(self.options.max_chars * 2.3) + 1, self.cfg.long_max_chars)
        return self._long  # type: ignore[return-value]

    def retrying(self, fn: Callable[[], dict[str, Any]]) -> tuple[dict[str, Any], int]:
        """연결 오류(끊김·타임아웃·5xx·429)면 최대 retry_max 회 다시 시도한다. 소진하면 _Interrupted. 반환: (결과, 재시도 횟수)."""
        attempts = 0
        while True:
            if self.stop.is_set():
                raise _Stop()
            try:
                return fn(), attempts
            except LocalLlmError as exc:
                if not _is_transient(exc) or attempts >= self.cfg.retry_max:
                    if _is_transient(exc):
                        raise _Interrupted(exc.reason) from None
                    raise
                attempts += 1
                self.unit_retries = attempts
                self.sleep(self.cfg.retry_delay)


def _extract(ctx: _Ctx, transcript: BenchTranscript) -> dict[str, Any]:
    client = _client(ctx.cfg)
    started = time.monotonic()
    result = extract_chunked(transcript.text, bench_data.HELD_AT, client, ctx.options)
    wall_ms = int((time.monotonic() - started) * 1000)
    transient = result.client_error if isinstance(result.client_error, LocalLlmError) and _is_transient(result.client_error) else None
    if result.all_failed and transient is not None:
        raise transient  # 재시도 래퍼가 처리
    calls = client.calls
    llm_ms = sum(c["elapsedMs"] for c in calls)
    completion = _sum_known([c["completionTokens"] for c in calls])
    prompt = _sum_known([c["promptTokens"] for c in calls])
    failed_chunks = sum(1 for c in result.chunks if c.status == STATUS_FAILED)
    parsed_ok = bool(result.chunks) and failed_chunks == 0
    return {
        "elapsedMs": wall_ms, "llmMs": llm_ms, "httpCalls": len(calls), "chunkCount": len(result.chunks),
        "promptTokens": prompt, "completionTokens": completion,
        "tokensPerSecond": _tps(completion, llm_ms),
        "jsonParsed": parsed_ok, "repaired": any(c.status == "repaired" for c in result.chunks), "failedChunks": failed_chunks,
        "truncatedRecovered": any(c.truncated_recovered for c in result.chunks),
        # 교정 재시도·잘림 복구 없이 처음 응답이 그대로 읽힌 경우(JSON 안정성 지표)
        "cleanParsed": parsed_ok and not any(c.status == "repaired" or c.truncated_recovered for c in result.chunks),
        "itemsBeforeDedupe": sum(c.item_count for c in result.chunks), "itemsAfterDedupe": len(result.items),
        "chunks": _chunk_rows(result, calls),
        "accuracy": _match(transcript.gold, result),
        "evidence": _classify_evidence(transcript, result),
        "inputTokens": input_token_summary(calls, ctx.cfg.context_tokens),
        "_extraction": result,  # 호출부가 쓰고 버린다(저장하지 않는다)
    }


def _strip(result: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in result.items() if not k.startswith("_")}


def unit_a(ctx: _Ctx) -> dict[str, Any]:
    started = time.monotonic()
    if ctx.cfg.connection_type == "ollama":
        from app.services import engine_test as engine

        models = list_ollama_models(engine.ollama_base(ctx.cfg.base_url), ctx.cfg.item_timeout, ctx.cfg.allow_external)
    else:
        models = list_models(ctx.cfg.base_url, ctx.cfg.api_key, ctx.cfg.item_timeout, ctx.cfg.allow_external)
    elapsed = int((time.monotonic() - started) * 1000)
    found = engine_diag.model_in_list(ctx.cfg.model, models)  # 앞뒤 공백·':latest' 차이는 같은 이름으로 본다(L-4a)
    return {
        "ok": True, "elapsedMs": elapsed, "modelCount": len(models), "modelFound": found, "model": ctx.cfg.model,
        "note": "" if found else "설정한 모델이 서버 목록에 없습니다",
    }


def unit_b(ctx: _Ctx) -> dict[str, Any]:
    result = _extract(ctx, ctx.short)
    out = _strip(result)
    out["ok"] = bool(out["jsonParsed"])
    return out


def unit_c(ctx: _Ctx) -> dict[str, Any]:
    single = _strip(_extract(ctx, ctx.short))
    long_t = ctx.long
    if long_t is None:
        split: dict[str, Any] = {"measured": False, "reason": f"{NOT_MEASURED}: 긴 가상 전사본 상한 안에서 분할 경로를 일으킬 수 없음"}
    else:
        split = _strip(_extract(ctx, long_t))
        split.update(measured=True, transcriptChars=long_t.chars, splitTriggered=split["chunkCount"] > 1)
    return {
        "ok": single["jsonParsed"] and (split.get("jsonParsed", True) if split.get("measured") else True),
        "budgetChars": ctx.options.max_chars, "single": single, "split": split,
        "evidenceTotal": _merge_evidence([single["evidence"]] + ([split["evidence"]] if split.get("measured") else [])),
    }


def _merge_evidence(rows: list[dict[str, Any]]) -> dict[str, Any]:
    total = sum(r["total"] for r in rows)
    parts = {k: sum(r[k] for r in rows) for k in ("verified", "lineIdMismatch", "quoteNotFound")}
    rate = (lambda n: round(n / total, 4) if total else None)
    return {"total": total, **parts, "verifiedRate": rate(parts["verified"]), "lineIdMismatchRate": rate(parts["lineIdMismatch"]),
            "quoteNotFoundRate": rate(parts["quoteNotFound"])}


def unit_e(ctx: _Ctx) -> dict[str, Any]:
    client = _client(ctx.cfg, model=INVALID_MODEL)
    messages = [{"role": "user", "content": 'Reply with exactly this JSON object: {"ok": true}'}]
    started = time.monotonic()
    try:
        client.complete(messages, json_mode=False, timeout=ctx.cfg.item_timeout)
    except LocalLlmError as exc:
        if _is_transient(exc):
            raise
        elapsed = int((time.monotonic() - started) * 1000)
        return {
            "ok": True, "rejected": True, "elapsedMs": elapsed, "errorCode": exc.reason, "jobErrorCode": exc.job_error_code,
            "guidance": _GUIDANCE.get(exc.reason, "서버가 요청을 처리하지 못했습니다"),
        }
    elapsed = int((time.monotonic() - started) * 1000)
    return {"ok": False, "rejected": False, "elapsedMs": elapsed, "errorCode": "", "jobErrorCode": "",
            "guidance": "서버가 잘못된 모델명을 거절하지 않고 응답했습니다(모델명 검증이 약한 서버)"}


def _origin(base_url: str) -> str:
    parts = urlsplit(base_url)
    return f"{parts.scheme}://{parts.netloc}"


def unit_f(ctx: _Ctx) -> dict[str, Any]:
    """모델 상태·자원: Ollama 의 /api/ps 가 있을 때만 확인한다(없으면 측정 불가). 모델을 내리거나 바꾸지 않는다(읽기 전용)."""
    cfg = ctx.cfg
    origin = _origin(cfg.base_url)
    check_base_url(origin, cfg.allow_external)
    try:
        with httpx.Client(timeout=min(cfg.item_timeout, 30), follow_redirects=False, trust_env=False) as http:
            response = http.get(f"{origin}/api/ps")
    except httpx.HTTPError as exc:
        # 연결 자체가 안 되면 이어하기 대상, 그 밖의 오류는 측정 불가로 기록한다
        if isinstance(exc, (httpx.ConnectError, httpx.TimeoutException)):
            raise LocalLlmError("연결 실패", kind="transient", reason="connect") from None
        return {"ok": True, "measurable": False, "reason": f"{UNMEASURABLE}: 상태 API 요청 실패"}
    if response.status_code != 200:
        return {"ok": True, "measurable": False, "reason": f"{UNMEASURABLE}: 서버가 Ollama 상태 API(/api/ps)를 제공하지 않음"}
    try:
        models = response.json().get("models") or []
        entries = [m for m in models if isinstance(m, dict)]
    except (ValueError, AttributeError):
        return {"ok": True, "measurable": False, "reason": f"{UNMEASURABLE}: 상태 응답 형식을 읽을 수 없음"}
    target = next((m for m in entries if any(engine_diag.model_matches(cfg.model, str(m.get(k) or "")) for k in ("name", "model"))), None)
    out: dict[str, Any] = {"ok": True, "measurable": True, "source": "ollama:/api/ps", "loadedModelCount": len(entries),
                           "targetLoaded": target is not None}
    if target is not None:
        size, vram = target.get("size"), target.get("size_vram")
        out["sizeBytes"] = size if isinstance(size, int) else None
        out["vramBytes"] = vram if isinstance(vram, int) else None
        out["gpuOffloadRatio"] = round(vram / size, 4) if isinstance(size, int) and size and isinstance(vram, int) else None
        out["expiresAtPresent"] = bool(target.get("expires_at"))
        length = target.get("context_length")  # 서버가 실제로 적재한 맥락 길이(있는 서버만)
        out["contextLength"] = length if isinstance(length, int) else None
    else:
        out["note"] = "측정 직후 대상 모델이 메모리에 올라 있지 않음(이미 언로드되었거나 다른 이름으로 로드됨)"
    return out


def unit_g(ctx: _Ctx) -> dict[str, Any]:
    result = _strip(_extract(ctx, ctx.short))
    result["ok"] = bool(result["jsonParsed"])
    return result


_UNITS: dict[str, Callable[[_Ctx], dict[str, Any]]] = {
    "A": unit_a, "B": unit_b, "C": unit_c, "E": unit_e, "F": unit_f, "G": unit_g,
}


# ------------------------- 실행·상태 -------------------------
_lock = threading.Lock()
_thread: threading.Thread | None = None
_stop = threading.Event()


def is_running() -> bool:
    return _thread is not None and _thread.is_alive()


def values_of(cfg: BenchConfig) -> dict[str, Any]:
    """측정에 쓰는 변수 값(설정 기록용). 키·주소는 없다."""
    return {"contextTokens": cfg.context_tokens, "chunkChars": cfg.chunk_chars, "outputReserveTokens": cfg.output_reserve,
            "repeat": cfg.repeat, "driftRuns": cfg.drift_runs, "retryMax": cfg.retry_max, "retryDelaySeconds": cfg.retry_delay,
            "itemTimeoutSeconds": cfg.item_timeout, "longMaxChars": cfg.long_max_chars}


def make_settings(cfg: BenchConfig, preset_id: str = "") -> dict[str, Any]:
    """측정 설정 기록(settings 블록): 프리셋·수정 여부와 항목·모든 변수값·값의 출처·연결 방식·모델명·시각. 키·주소·원문은 넣지 않는다."""
    values = values_of(cfg)
    info = lab_params.describe(values, preset_id)
    effective: dict[str, Any] = {"outputReserveEffective": _profile(cfg).output_reserve_tokens()}
    try:
        effective["chunkCharsEffective"] = _options(cfg).max_chars
    except LocalLlmError:
        effective["chunkCharsEffective"] = None
    try:
        effective["chunkCapChars"] = lab_params.chunk_cap(values, _profile(cfg))
    except Exception:  # noqa: BLE001 — 기록용 계산이 측정을 막지 않는다
        effective["chunkCapChars"] = None
    return {
        "preset": info["preset"], "presetName": info["presetName"], "modified": info["modified"], "modifiedItems": info["modifiedItems"],
        "summary": info["summary"], "values": values, "sources": info["sources"], "connectionType": cfg.connection_type,
        "model": cfg.model, "effective": effective, "recordedAt": _now(),
    }


def _new_state(mode: str, cfg: BenchConfig, settings_block: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        # settings 는 결과보다 먼저(맨 앞) 기록한다: 중간에 끊겨도 어떤 설정으로 측정했는지 남는다
        "settings": settings_block if settings_block is not None else make_settings(cfg),
        "runId": uuid.uuid4().hex[:12], "mode": mode, "status": S_RUNNING, "startedAt": _now(), "updatedAt": _now(),
        "plan": plan_for(mode, cfg.repeat, cfg.drift_runs), "units": {}, "current": "", "elapsedSeconds": 0.0,
        "message": "", "resumeCount": 0, "retries": 0,
        "config": {"model": cfg.model, "itemTimeoutSeconds": cfg.item_timeout, "retryMax": cfg.retry_max,
                   "repeat": cfg.repeat, "driftRuns": cfg.drift_runs},
    }


def _persist(state: dict[str, Any]) -> None:
    """상태(체크포인트)와 결과 파일을 함께 저장한다. 결과 파일 쓰기 실패는 측정을 멈추지 않는다."""
    _save(state)
    try:
        write_result_file(state)
    except Exception as exc:  # noqa: BLE001
        log.warning("bench result file write failed: %s", type(exc).__name__)


SweepBuilder = Callable[[str], "tuple[BenchConfig, dict[str, Any]]"]


def _run(state: dict[str, Any], cfg: BenchConfig, sleep: Callable[[float], None]) -> None:
    ctx = _Ctx(cfg, _stop, sleep)
    base = float(state.get("elapsedSeconds", 0.0))
    t0 = time.monotonic()
    key = ""

    def tick() -> None:
        state["elapsedSeconds"] = round(base + time.monotonic() - t0, 1)

    try:
        for key in state["plan"]:
            if state["units"].get(key, {}).get("state") == "done":
                continue
            if _stop.is_set():
                raise _Stop()
            state["current"] = key
            tick()
            _persist(state)
            kind = key.partition("#")[0]
            started = time.monotonic()
            ctx.unit_retries = 0
            try:
                result, retries = ctx.retrying(lambda: _UNITS[kind](ctx))
            except LocalLlmError as exc:  # 영구 오류(재시도 무의미): 이 단위만 실패로 기록하고 계속한다
                result = {"ok": False, "failedReason": exc.reason, "guidance": _GUIDANCE.get(exc.reason, "요청을 처리하지 못했습니다")}
                retries = ctx.unit_retries  # 재시도 뒤 영구 오류가 된 경우도 시도한 횟수를 센다
            state["retries"] = int(state.get("retries", 0)) + retries
            state["units"][key] = {"state": "done", "label": label_of(key), "seconds": round(time.monotonic() - started, 2),
                                   "retries": retries, **result}
            tick()
            _persist(state)
        state["status"], state["current"], state["message"] = S_COMPLETED, "", ""
    except _Interrupted as exc:
        # 재시도를 소진한 중단도 retries 에 집계한다
        state["retries"] = int(state.get("retries", 0)) + ctx.unit_retries
        # 끊김 형태(연결 거부·시간 초과·HTTP 오류 등 분류 코드)를 남긴다. 주소·본문은 남기지 않는다
        state["interruptReason"] = str(exc.args[0]) if exc.args else ""
        state["interruptedAt"] = key
        state["status"], state["message"] = S_INTERRUPTED, DISCONNECT_MESSAGE
    except _Stop:
        state["retries"] = int(state.get("retries", 0)) + ctx.unit_retries
        state["status"], state["message"] = S_STOPPED, "사용자가 중단했습니다. 이어하기로 남은 항목을 실행할 수 있습니다"
    except Exception as exc:  # noqa: BLE001 — 예기치 못한 오류도 상태로 남긴다(주소·본문 없이 분류만)
        log.warning("bench run failed: %s", type(exc).__name__)
        state["status"], state["message"] = S_FAILED, f"측정 중 오류가 발생했습니다({type(exc).__name__})"
    tick()
    _persist(state)


def _fetch_ps(cfg: BenchConfig) -> list[dict[str, Any]] | None:
    """Ollama /api/ps 의 적재 모델 목록. 읽을 수 없으면 None."""
    origin = _origin(cfg.base_url)
    try:
        check_base_url(origin, cfg.allow_external)
        with httpx.Client(timeout=min(cfg.item_timeout, 10), follow_redirects=False, trust_env=False) as http:
            response = http.get(f"{origin}/api/ps")
        if response.status_code != 200:
            return None
        return [m for m in (response.json().get("models") or []) if isinstance(m, dict)]
    except (httpx.HTTPError, LocalLlmError, ValueError, AttributeError):
        return None


def _unload_between(cfg: BenchConfig, sleep: Callable[[float], None]) -> dict[str, Any]:
    """스윕에서 맥락이 바뀌기 전에 모델을 내린다(keep_alive=0). 내려갔는지 /api/ps 로 확인한다(최대 5회, 1초 간격)."""
    from app.services import engine_test as engine

    if cfg.connection_type != "ollama":
        return {"done": False, "confirmed": None, "reason": "OpenAI 호환 방식은 모델을 내릴 수 없음"}
    client = OllamaClient(engine.ollama_base(cfg.base_url), cfg.model, num_ctx=cfg.context_tokens, num_predict=1,
                          allow_external=cfg.allow_external)
    done = client.unload(timeout=min(cfg.item_timeout, 30))
    confirmed: bool | None = None
    for _ in range(5):
        entries = _fetch_ps(cfg)
        if entries is None:
            break
        confirmed = not any(
            engine_diag.model_matches(cfg.model, str(m.get(k) or "")) for m in entries for k in ("name", "model"))
        if confirmed:
            break
        sleep(1.0)
    return {"done": done, "confirmed": confirmed}


def _run_series(state: dict[str, Any], cfg: BenchConfig, sleep: Callable[[float], None], builder: SweepBuilder | None,
                skip_current: bool = False) -> None:
    """한 번 측정하고, 맥락 스윕이면 남은 프리셋을 순서대로 이어서 측정한다(맥락마다 settings·결과를 한 묶음으로 저장)."""
    if not skip_current:
        _run(state, cfg, sleep)
    while True:
        sweep = state.get("sweep")
        if not sweep or builder is None or state.get("status") != S_COMPLETED:
            return
        nxt = int(sweep["index"]) + 1
        if nxt >= len(sweep["presets"]) or _stop.is_set():
            return
        try:
            next_cfg, block = builder(sweep["presets"][nxt])
        except Exception as exc:  # noqa: BLE001
            log.warning("bench sweep build failed: %s", type(exc).__name__)
            state["status"], state["message"] = S_FAILED, f"스윕 준비 중 오류가 발생했습니다({type(exc).__name__})"
            _persist(state)
            return
        unload = _unload_between(cfg, sleep)  # 다음 맥락으로 다시 올리기 전에 VRAM 을 비운다
        new = _new_state(MODE_FULL, next_cfg, block)
        new["sweep"] = {**sweep, "index": nxt, "runIds": [*sweep.get("runIds", []), state["runId"]], "unloadBefore": unload}
        if state.get("diagnosis"):
            new["diagnosis"] = state["diagnosis"]
        _persist(new)
        state, cfg = new, next_cfg
        _run(state, cfg, sleep)


def _sweep_info(builder_presets: list[str] | None) -> dict[str, Any] | None:
    if not builder_presets:
        return None
    return {"id": uuid.uuid4().hex[:8], "presets": list(builder_presets), "index": 0, "runIds": []}


def start(mode: str, cfg: BenchConfig, *, diagnosis: dict[str, Any] | None = None, sleep: Callable[[float], None] = time.sleep,
          wait: bool = False, settings_block: dict[str, Any] | None = None, sweep_presets: list[str] | None = None,
          sweep_builder: SweepBuilder | None = None) -> dict[str, Any]:
    global _thread
    if mode not in (MODE_QUICK, MODE_FULL):
        raise BenchError("측정 종류는 quick 또는 full 이어야 합니다")
    with _lock:
        if is_running():
            raise BenchBusy("이미 측정이 진행 중입니다")
        state = _new_state(mode, cfg, settings_block)
        if diagnosis is not None:
            state["diagnosis"] = diagnosis  # 시작 직전 연결 진단 결과(결과 파일 머리에 기록, 이후 막힌 시도 값으로 덮지 않는다)
        info = _sweep_info(sweep_presets)
        if info:
            state["sweep"] = info
        _stop.clear()
        _persist(state)  # 측정 시작 직전: settings 를 결과보다 먼저 저장한다
        _thread = threading.Thread(target=_run_series, args=(state, cfg, sleep, sweep_builder), name="bench-run", daemon=True)
        _thread.start()
    if wait:
        _thread.join()
    return state


def _sweep_pending(state: dict[str, Any]) -> bool:
    sweep = state.get("sweep")
    return bool(sweep) and int(sweep.get("index", 0)) + 1 < len(sweep.get("presets", []))


def resume(cfg: BenchConfig, *, diagnosis: dict[str, Any] | None = None, sleep: Callable[[float], None] = time.sleep, wait: bool = False,
           sweep_builder: SweepBuilder | None = None) -> dict[str, Any]:
    global _thread
    with _lock:
        if is_running():
            raise BenchBusy("이미 측정이 진행 중입니다")
        state = load_state()
        if state is None:
            raise BenchError("이어할 측정이 없습니다")
        # 서버가 재시작되어 running 으로 남은 기록도 이어할 수 있다(스레드가 없으므로 사실상 중단됨)
        completed_sweep = state.get("status") == S_COMPLETED and _sweep_pending(state)
        if state.get("status") not in (*RESUMABLE, S_RUNNING, S_FAILED) and not completed_sweep:
            raise BenchError("이어할 수 있는 측정이 없습니다(이미 완료되었습니다)")
        if not completed_sweep:
            state.update(status=S_RUNNING, message="", resumeCount=int(state.get("resumeCount", 0)) + 1)
        if diagnosis is not None:
            state["diagnosis"] = diagnosis
        _stop.clear()
        _persist(state)
        _thread = threading.Thread(target=_run_series, args=(state, cfg, sleep, sweep_builder, completed_sweep), name="bench-run", daemon=True)
        _thread.start()
    if wait:
        _thread.join()
    return state


def record_diagnosis(diagnosis: dict[str, Any]) -> None:
    """측정을 시작하지 못했을 때 기존 기록의 blockedAttempt 필드에만 남긴다. 측정 직전 진단(diagnosis)은 덮어쓰지 않는다.
    실행 중이거나 기록이 없으면 아무것도 하지 않는다."""
    with _lock:
        if is_running():
            return
        state = load_state()
        if state is None:
            return
        state["blockedAttempt"] = {**diagnosis, "recordedAt": _now()}
        _persist(state)


def stop() -> bool:
    if not is_running():
        return False
    _stop.set()
    return True


def status() -> dict[str, Any]:
    """화면용 상태. 실행 스레드가 없는데 파일이 running 이면 중단됨으로 본다(서버 재시작 등)."""
    state = load_state()
    if state is None:
        return {"status": S_IDLE, "mode": "", "runId": "", "currentLabel": "", "doneUnits": 0, "totalUnits": 0, "elapsedSeconds": 0,
                "message": "", "canResume": False, "canDownload": False, "retries": 0, "preset": "", "settingsSummary": "",
                "sweepIndex": 0, "sweepTotal": 0}
    st = state.get("status", S_IDLE)
    message = state.get("message", "")
    if st == S_RUNNING and not is_running():
        st, message = S_INTERRUPTED, DISCONNECT_MESSAGE
    done = sum(1 for u in state["units"].values() if u.get("state") == "done")
    current = state.get("current", "")
    settings_block = state.get("settings") or {}
    sweep = state.get("sweep") or {}
    return {
        "status": st, "mode": state.get("mode", ""), "runId": state.get("runId", ""),
        "currentLabel": label_of(current) if current and st == S_RUNNING else "",
        "doneUnits": done, "totalUnits": len(state["plan"]), "elapsedSeconds": state.get("elapsedSeconds", 0),
        "message": message, "canResume": st in RESUMABLE or st == S_FAILED or (st == S_COMPLETED and _sweep_pending(state) and not is_running()),
        "canDownload": done > 0, "retries": state.get("retries", 0), "preset": settings_block.get("preset", ""),
        "settingsSummary": settings_block.get("summary", ""),
        "sweepIndex": int(sweep.get("index", 0)) + 1 if sweep else 0, "sweepTotal": len(sweep.get("presets", [])) if sweep else 0,
    }


def list_history(limit: int = 50) -> list[dict[str, Any]]:
    """data/bench 의 이전 측정을 설정 요약과 핵심 결과로 나란히 비교할 수 있게 돌려준다(값만, 주소·키·원문 없음)."""
    rows: list[dict[str, Any]] = []
    for path in _dir().glob("result-*.json"):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(data, dict):
            continue
        cfg = data.get("settings") or {}
        sec = data.get("sections") or {}
        rate = (sec.get("B") or {}).get("tokensPerSecond") or {}
        rows.append({
            "runId": data.get("runId", ""), "startedAt": data.get("startedAt", ""), "status": data.get("status", ""),
            "preset": cfg.get("preset", ""), "presetSummary": cfg.get("summary", ""), "connectionType": cfg.get("connectionType", ""),
            "model": cfg.get("model", ""), "contextTokens": (cfg.get("values") or {}).get("contextTokens"),
            "completedUnits": data.get("completedUnits", 0), "totalUnits": data.get("totalUnits", 0),
            "tokensPerSecond": rate.get("mean"), "vramBytes": (sec.get("F") or {}).get("vramBytes"),
            "verifiedRate": (sec.get("D") or {}).get("verifiedRate"), "warningCount": len(data.get("warnings") or []),
            "sweepId": (data.get("sweep") or {}).get("id", ""),
        })
    rows.sort(key=lambda r: r["startedAt"], reverse=True)
    return rows[:limit]


_RUN_ID = re.compile(r"^[0-9a-f]{12}$")


def load_result_file(run_id: str) -> dict[str, Any] | None:
    if not _RUN_ID.match(run_id or ""):
        return None
    try:
        return json.loads((_dir() / f"result-{run_id}.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


# ------------------------- 결과 파일 -------------------------
def _units_of(state: dict[str, Any], kind: str) -> list[dict[str, Any]]:
    return [u for k, u in state["units"].items() if k.startswith(f"{kind}#") and u.get("state") == "done"]


def _merge_input(rows: list[dict[str, Any]]) -> dict[str, Any]:
    live = [r for r in rows if r and r.get("n")]
    if not live:
        return {"n": 0, "min": None, "median": None, "max": None}
    return {"n": sum(r["n"] for r in live), "min": min(r["min"] for r in live),
            "median": statistics.median([r["median"] for r in live]), "max": max(r["max"] for r in live)}


def _merge_due(accuracies: list[dict[str, Any]]) -> dict[str, Any]:
    """기한 분류 건수와 정답 대비 일수 차이 분포(값·원문 없음)."""
    counts = {"blank": 0, "different": 0, "formatError": 0, "match": 0}
    dist: dict[str, int] = {}
    for acc in accuracies:
        for k in counts:
            counts[k] += int((acc.get("dueClass") or {}).get(k, 0))
        for d in acc.get("dueDayDiffs") or []:
            dist[str(d)] = dist.get(str(d), 0) + 1
    return {"dueClass": counts, "dayDiffDistribution": dict(sorted(dist.items(), key=lambda kv: int(kv[0])))}


def _suspect_runs(rows: list[dict[str, Any]]) -> int:
    return sum(1 for r in rows if (r.get("inputTokens") or {}).get("truncationSuspected"))


def _warnings(state: dict[str, Any], sections: dict[str, Any]) -> list[str]:
    out: list[str] = []
    wanted = ((state.get("settings") or {}).get("values") or {}).get("contextTokens")
    loaded = (sections.get("F") or {}).get("contextLength")
    if isinstance(loaded, int) and isinstance(wanted, int) and loaded != wanted:
        out.append(f"서버가 적재한 맥락 길이({loaded})가 설정한 맥락({wanted})과 다릅니다. 측정이 설정과 다른 조건으로 이뤄졌을 수 있습니다")
    for kind, name in (("B", "B 단건 추출"), ("C", "C 컨텍스트 경계"), ("G", "G 연속 실행")):
        n = (sections.get(kind) or {}).get("truncationSuspectedRuns")
        if n:
            out.append(f"입력 잘림 의심: {name} {n}회(입력 토큰이 맥락 한계에 가깝거나 서버 기본 맥락 값과 같음)")
    return out


def build_result(state: dict[str, Any]) -> dict[str, Any]:
    """다운로드용 결과(JSON). 건수·시간·비율·상태만 담는다. 미측정 항목은 사유와 함께 "미측정"으로 적는다(추정으로 채우지 않는다)."""
    done = {k for k, u in state["units"].items() if u.get("state") == "done"}
    sections: dict[str, Any] = {}

    def section(kind: str, build: Callable[[list[dict[str, Any]]], dict[str, Any]]) -> None:
        all_rows = _units_of(state, kind)
        rows = [r for r in all_rows if not r.get("failedReason")]
        failed = [r["failedReason"] for r in all_rows if r.get("failedReason")]
        planned = [k for k in state["plan"] if k.startswith(f"{kind}#")]
        if not planned:
            sections[kind] = {"status": NOT_MEASURED, "reason": "이 측정 종류(빠른 점검)에서는 실행하지 않는 항목"}
        elif not all_rows:
            sections[kind] = {"status": NOT_MEASURED, "reason": "측정이 이 항목까지 진행되지 않음"}
        elif not rows:
            sections[kind] = {"status": "측정 실패", "reason": "모든 회차가 요청 오류로 끝남", "failedRuns": len(failed),
                              "failedReasons": sorted(set(failed)), "runs": 0, "plannedRuns": len(planned)}
        else:
            body = build(rows)
            body["runs"], body["plannedRuns"] = len(rows), len(planned)
            body["failedRuns"], body["failedReasons"] = len(failed), sorted(set(failed))
            body["status"] = "완료" if len(rows) == len(planned) else "일부 측정"
            sections[kind] = body

    section("A", lambda r: {"elapsedMs": _stats([x["elapsedMs"] for x in r]), "modelFoundAll": all(x.get("modelFound") for x in r),
                            "modelCount": r[-1].get("modelCount")})

    def single_like(r: list[dict[str, Any]]) -> dict[str, Any]:
        return {
            "elapsedMs": _stats([x["elapsedMs"] for x in r]), "promptTokens": _stats([x.get("promptTokens") for x in r]),
            "completionTokens": _stats([x.get("completionTokens") for x in r]),
            "tokensPerSecond": _stats([x.get("tokensPerSecond") for x in r]),
            "jsonParsedRuns": sum(1 for x in r if x.get("jsonParsed")), "repairedRuns": sum(1 for x in r if x.get("repaired")),
            "cleanParsedRuns": sum(1 for x in r if x.get("cleanParsed")),
            "truncatedRecoveredRuns": sum(1 for x in r if x.get("truncatedRecovered")),
            "missRate": _stats([x["accuracy"]["missRate"] for x in r]),
            "assigneeMatchRate": _stats([x["accuracy"]["assigneeMatchRate"] for x in r]),
            "dueMatchRate": _stats([x["accuracy"]["dueMatchRate"] for x in r]),
            "dueClassification": _merge_due([x["accuracy"] for x in r]),
            "inputTokens": _merge_input([x.get("inputTokens") or {} for x in r]),
            "truncationSuspectedRuns": _suspect_runs(r),
        }

    section("B", single_like)

    def c_build(r: list[dict[str, Any]]) -> dict[str, Any]:
        splits = [x["split"] for x in r if x["split"].get("measured")]
        body: dict[str, Any] = {
            "budgetChars": r[-1].get("budgetChars"),
            "single": {"elapsedMs": _stats([x["single"]["elapsedMs"] for x in r]), "chunkCount": _stats([x["single"]["chunkCount"] for x in r]),
                       "jsonParsedRuns": sum(1 for x in r if x["single"].get("jsonParsed"))},
        }
        if splits:
            body["split"] = {
                "measured": True, "transcriptChars": splits[-1]["transcriptChars"],
                "splitTriggeredRuns": sum(1 for s in splits if s.get("splitTriggered")),
                "elapsedMs": _stats([s["elapsedMs"] for s in splits]), "chunkCount": _stats([s["chunkCount"] for s in splits]),
                "itemsBeforeDedupe": _stats([s["itemsBeforeDedupe"] for s in splits]),
                "itemsAfterDedupe": _stats([s["itemsAfterDedupe"] for s in splits]),
                "failedChunks": _stats([s["failedChunks"] for s in splits]),
                "perChunkMs": _stats([c["elapsedMs"] for s in splits for c in s["chunks"]]),
                "jsonParsedRuns": sum(1 for s in splits if s.get("jsonParsed")),
                "missRate": _stats([s["accuracy"]["missRate"] for s in splits]),
                "assigneeMatchRate": _stats([s["accuracy"]["assigneeMatchRate"] for s in splits]),
                "dueMatchRate": _stats([s["accuracy"]["dueMatchRate"] for s in splits]),
            }
        else:
            body["split"] = r[-1]["split"]
        parts = [x["single"] for x in r] + splits
        body["dueClassification"] = _merge_due([p["accuracy"] for p in parts])
        body["inputTokens"] = _merge_input([p.get("inputTokens") or {} for p in parts])
        body["truncationSuspectedRuns"] = _suspect_runs(parts)
        return body

    section("C", c_build)

    c_rows = [r for r in _units_of(state, "C") if not r.get("failedReason")]
    if c_rows:
        total = _merge_evidence([x["evidenceTotal"] for x in c_rows])
        sections["D"] = {"status": "완료" if len(c_rows) == len([k for k in state["plan"] if k.startswith("C#")]) else "일부 측정",
                         "source": "C 항목의 단일·분할 추출 결과를 합산", **total}
    else:
        sections["D"] = {"status": NOT_MEASURED, "reason": "C 항목이 실행되지 않아 근거 검증을 집계할 수 없음"}

    def e_build(r: list[dict[str, Any]]) -> dict[str, Any]:
        return {"rejectedRuns": sum(1 for x in r if x.get("rejected")), "errorCodes": sorted({x.get("errorCode", "") for x in r}),
                "jobErrorCodes": sorted({x.get("jobErrorCode", "") for x in r}), "guidance": r[-1].get("guidance", ""),
                "elapsedMs": _stats([x["elapsedMs"] for x in r])}

    section("E", e_build)

    def f_build(r: list[dict[str, Any]]) -> dict[str, Any]:
        measurable = [x for x in r if x.get("measurable")]
        if not measurable:
            return {"measurable": False, "reason": r[-1].get("reason", UNMEASURABLE)}
        last = measurable[-1]
        return {"measurable": True, "source": last.get("source"), "targetLoadedRuns": sum(1 for x in measurable if x.get("targetLoaded")),
                "vramBytes": last.get("vramBytes"), "sizeBytes": last.get("sizeBytes"), "gpuOffloadRatio": last.get("gpuOffloadRatio"),
                "loadedModelCount": last.get("loadedModelCount"), "expiresAtPresent": last.get("expiresAtPresent"),
                "contextLength": last.get("contextLength")}

    section("F", f_build)

    def g_build(r: list[dict[str, Any]]) -> dict[str, Any]:
        ordered = [x for k in state["plan"] if k.startswith("G#") and (x := state["units"].get(k)) and x.get("state") == "done"]
        times = [x["elapsedMs"] for x in ordered if not x.get("failedReason")]
        drift = None
        if len(times) >= 2 and times[0] > 0:
            drift = round((times[-1] - times[0]) / times[0], 4)
        return {"elapsedMsSeries": times, "elapsedMs": _stats(times), "driftLastVsFirst": drift,
                "failedRuns": sum(1 for x in ordered if not x.get("ok") or x.get("failedReason")), "jsonParsedRuns": sum(1 for x in ordered if x.get("jsonParsed")),
                "dueClassification": _merge_due([x["accuracy"] for x in ordered if x.get("accuracy")]),
                "inputTokens": _merge_input([x.get("inputTokens") or {} for x in ordered]),
                "truncationSuspectedRuns": _suspect_runs(ordered)}

    section("G", g_build)
    result = {
        # 설정 기록이 맨 앞: 결과보다 먼저 저장되고, 중간에 끊겨도 남는다
        "settings": state.get("settings") or {},
        "schema": "dots-bench-v1", "warnings": _warnings(state, sections),
        "diagnosis": state.get("diagnosis") or {}, "blockedAttempt": state.get("blockedAttempt") or {},
        "sweep": state.get("sweep") or {},
        "runId": state.get("runId"), "mode": state.get("mode"),
        "status": status_of(state),
        "interruption": {"reason": state.get("interruptReason", ""), "atUnit": state.get("interruptedAt", "")}
        if state.get("interruptReason") else {},
        "startedAt": state.get("startedAt"), "updatedAt": state.get("updatedAt"), "elapsedSeconds": state.get("elapsedSeconds"),
        "completedUnits": len(done), "totalUnits": len(state["plan"]), "resumeCount": state.get("resumeCount", 0),
        "retries": state.get("retries", 0), "config": state.get("config", {}), "sections": sections,
        "notes": ["입력은 도구에 내장한 가상 전사본이다(실제 회의 데이터 아님).",
                  "주소·키·호스트명·전사 원문·인용문은 이 파일에 포함하지 않는다.",
                  f"측정하지 못한 항목은 \"{NOT_MEASURED}\" 또는 \"{UNMEASURABLE}\"로 표시하며 추정값을 넣지 않는다."],
    }
    result["summaryMarkdown"] = to_markdown(result)
    return result


def status_of(state: dict[str, Any]) -> str:
    st = state.get("status", S_IDLE)
    return S_INTERRUPTED if st == S_RUNNING and not is_running() else st


def _ms(stat: dict[str, Any]) -> str:
    return "—" if not stat or stat.get("mean") is None else f"{stat['mean']:.0f} (최소 {stat['min']:.0f} · 최대 {stat['max']:.0f})"


def _num(stat: dict[str, Any], digits: int = 2) -> str:
    return "—" if not stat or stat.get("mean") is None else f"{stat['mean']:.{digits}f}"


def _pct(stat: dict[str, Any]) -> str:
    return "—" if not stat or stat.get("mean") is None else f"{stat['mean'] * 100:.1f}%"


def to_markdown(result: dict[str, Any]) -> str:
    s = result["sections"]
    lines = [
        f"# L-3 벤치마크 측정 요약 ({result['mode']}, 상태: {result['status']})", "",
        f"- 완료 단위 {result['completedUnits']}/{result['totalUnits']} · 경과 {result['elapsedSeconds']}초 · 재시도 {result['retries']}회 · 이어하기 {result['resumeCount']}회",
        "- 입력: 도구 내장 가상 전사본. 주소·키·호스트명·원문은 포함하지 않음",
        f"- 설정: {(result.get('settings') or {}).get('summary', '—')} · 연결 방식 {(result.get('settings') or {}).get('connectionType', '—')}"
        f" · 모델 {(result.get('settings') or {}).get('model', '—')}",
        *[f"- 경고: {w}" for w in result.get("warnings", [])], "",
        "| 항목 | 상태 | 핵심 수치 |", "|---|---|---|",
    ]

    def row(name: str, body: dict[str, Any], summary: str) -> None:
        lines.append(f"| {name} | {body.get('status', '')} | {summary} |")

    for sec in s.values():
        sec.setdefault("status", "")
    a = s["A"]
    row("A 연결·모델", a, "—" if "elapsedMs" not in a else f"응답 {_ms(a['elapsedMs'])} ms · 모델 목록에 있음 {a.get('modelFoundAll')}")
    b = s["B"]
    row("B 단건 추출", b, "—" if "elapsedMs" not in b else
        f"소요 {_ms(b['elapsedMs'])} ms · 초당 생성 토큰 {_num(b['tokensPerSecond'])} · JSON 성공 {b['jsonParsedRuns']}/{b['runs']}(교정·복구 없이 {b['cleanParsedRuns']}) · 누락률 {_pct(b['missRate'])}")
    c = s["C"]
    if "single" in c and isinstance(c.get("split"), dict) and c["split"].get("measured"):
        sp = c["split"]
        row("C 컨텍스트 경계", c, f"단일 {_ms(c['single']['elapsedMs'])} ms · 분할 {_ms(sp['elapsedMs'])} ms · 구간 수 {_num(sp['chunkCount'], 1)} · "
                               f"중복 제거 {_num(sp['itemsBeforeDedupe'], 1)}→{_num(sp['itemsAfterDedupe'], 1)}건")
    elif "single" in c:
        row("C 컨텍스트 경계", c, f"단일 {_ms(c['single']['elapsedMs'])} ms · 분할 {c['split'].get('reason', NOT_MEASURED)}")
    else:
        row("C 컨텍스트 경계", c, c.get("reason", "—"))
    d = s["D"]
    row("D 근거 검증", d, "—" if "total" not in d else
        f"총 {d['total']}건 · 통과 {d['verified']}({_fmt_rate(d['verifiedRate'])}) · Line ID 불일치 {d['lineIdMismatch']}({_fmt_rate(d['lineIdMismatchRate'])}) · "
        f"인용문 미일치 {d['quoteNotFound']}({_fmt_rate(d['quoteNotFoundRate'])})")
    e = s["E"]
    row("E 실패 경로", e, "—" if "rejectedRuns" not in e else f"거절 {e['rejectedRuns']}/{e['runs']} · 오류 분류 {', '.join(e['errorCodes']) or '—'}")
    f = s["F"]
    if f.get("measurable") is False:
        row("F 자원 반환", f, f.get("reason", UNMEASURABLE))
    elif "measurable" in f:
        row("F 자원 반환", f, f"VRAM {f.get('vramBytes')} B · GPU 적재 비율 {f.get('gpuOffloadRatio')} · 대상 모델 적재 {f['targetLoadedRuns']}/{f['runs']}")
    else:
        row("F 자원 반환", f, f.get("reason", "—"))
    g = s["G"]
    row("G 연속 실행", g, "—" if "elapsedMsSeries" not in g else
        f"{len(g['elapsedMsSeries'])}회 시계열 {g['elapsedMsSeries']} ms · 드리프트(마지막/처음) {_fmt_rate(g['driftLastVsFirst'])} · 실패 {g['failedRuns']}")
    return "\n".join(lines) + "\n"


def _fmt_rate(value: float | None) -> str:
    return "—" if value is None else f"{value * 100:.1f}%"


def write_result_file(state: dict[str, Any]) -> dict[str, Any]:
    result = build_result(state)
    _write_json(_dir() / f"result-{state.get('runId', 'run')}.json", result)
    return result
