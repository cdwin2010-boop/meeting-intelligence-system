"""원격(시험용) 엔진 연결 점검·L-3 벤치마크 측정 API(작업 L-3a, 시험 전용).

- 권한은 엔진 시험 설정과 같다: 시험 모드(ENGINE_TEST_MODE)가 켜져 있어야 하고(꺼져 있으면 404), 관리자 이상 또는 시스템 관리자만(그 밖은 403).
- 작업 L-5: 변수 입력(범위는 config/bench_params.json, 서버에서 다시 검사)·프리셋 A~D·맥락 스윕·측정 설정 선기록·이력 비교.
- 측정은 서버 백그라운드 스레드로 실행하고 요청은 즉시 돌려준다. 같은 시점에 1개만(이미 실행 중이면 409).
- 음성 인식·추출 작업이 진행 중이면 GPU 를 겹쳐 쓰지 않도록 시작·이어하기를 409 로 거부한다(로딩 테스트와 같은 규칙).
- 응답·결과 파일에는 주소·키·호스트명·전사 원문을 담지 않는다(건수·시간·비율·상태만).
"""
import json
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy.orm import Session

from app.api.engine_test import _require_admin
from app.api.schemas import CamelModel
from app.auth.deps import get_current_account
from app.config import settings
from app.db import get_session
from app.models import Account, append_event
from app.engine_lab import params as lab_params
from app.pipeline.local_llm import LocalLlmError, list_models
from app.pipeline.ollama_llm import list_ollama_models
from app.services import bench_runner as bench
from app.services import engine_diag
from app.services import engine_test as engine
from app.services.system_settings import LOCAL_LLM_EXTERNAL_HOST_ALLOWED, effective
from app.services.task_queue import queue_counts

router = APIRouter(prefix="/api/admin/engine-test/bench", tags=["engine-test"])


class BenchStartIn(CamelModel):
    mode: str  # quick | full (맥락 스윕이면 무시하고 full)
    preset: str = ""  # "" | A | B | C | D
    values: dict[str, Any] | None = None  # 화면 변수 값(생략한 항목은 프리셋 값, 없으면 기본값)
    model: str | None = None  # 서버 목록에서 고른 모델명(생략하면 저장된 설정의 모델)
    sweep: bool = False  # 맥락 스윕: 프리셋 B→C→D 연속 측정


class BenchStatusOut(CamelModel):
    status: str  # idle | running | completed | interrupted | stopped | failed
    mode: str
    run_id: str
    current_label: str
    done_units: int
    total_units: int
    elapsed_seconds: float
    message: str
    can_resume: bool
    can_download: bool
    retries: int
    preset: str = ""
    settings_summary: str = ""
    sweep_index: int = 0
    sweep_total: int = 0


def _status() -> BenchStatusOut:
    return BenchStatusOut(**bench.status())


def _allow_external(session: Session, account: Account) -> bool:
    allow_external = True  # 시험 모드가 외부 주소 허용 근거. EXTERNAL_POLICY_ENFORCED 면 시스템 관리자 설정도 따른다(로딩 테스트와 동일)
    if settings.external_policy_enforced:
        allow_external = bool(effective(session, account.tenant_id, LOCAL_LLM_EXTERNAL_HOST_ALLOWED).value)
    return allow_external


def _remote_or_422() -> engine.RemoteSettings:
    """시작·이어하기 때마다 현재 저장된 원격 설정을 다시 읽는다(주소가 바뀌어도 이어갈 수 있다)."""
    remote = engine.load_remote_settings()
    if not remote.configured:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="서버 주소와 모델명을 먼저 저장하세요")
    return remote


def _make_config(remote: engine.RemoteSettings, allow_external: bool, values: dict[str, Any], model: str) -> bench.BenchConfig:
    return bench.BenchConfig(
        base_url=remote.base_url, model=model, api_key=remote.api_key, allow_external=allow_external,
        item_timeout=float(values["itemTimeoutSeconds"]), retry_max=int(values["retryMax"]),
        retry_delay=float(values["retryDelaySeconds"]), repeat=int(values["repeat"]), drift_runs=int(values["driftRuns"]),
        long_max_chars=int(values["longMaxChars"]), context_tokens=int(values["contextTokens"]),
        output_reserve=int(values["outputReserveTokens"]), chunk_chars=int(values["chunkChars"]),
        connection_type=remote.connection_type,
    )


def _invalid(errors: list[lab_params.ParamError]) -> HTTPException:
    """범위 밖·조합 오류: 문제 입력란(key)과 "무엇이/왜/얼마로" 문장을 함께 돌려준다."""
    return HTTPException(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        detail={"message": errors[0].message, "errors": [{"key": e.key, "message": e.message} for e in errors]},
    )


def _resolve_values(preset: str, raw: dict[str, Any] | None, remote: engine.RemoteSettings) -> dict[str, Any]:
    """프리셋 → 화면 값 순으로 덮어 범위·조합을 검사한 값. 오류가 있으면 422(입력값은 화면에 그대로 남는다)."""
    if preset and lab_params.preset_of(preset) is None:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="프리셋은 A~D 중 하나여야 합니다")
    merged: dict[str, Any] = dict(lab_params.base_values(preset))
    merged.update(raw or {})
    values, errors = lab_params.validate(merged)
    if not errors:
        errors = lab_params.check_combination(values, engine.remote_profile())
    if errors:
        raise _invalid(errors)
    return values


def _resolve_model(requested: str | None, remote: engine.RemoteSettings) -> str:
    model = (requested or "").strip() or remote.model
    if len(model) > 200:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="모델명은 200자 이내여야 합니다")
    return model


def _config(session: Session, account: Account) -> bench.BenchConfig:
    """이어하기용: 값은 기록된 측정 설정(없으면 기본값), 주소·키·모델은 현재 저장값."""
    remote = _remote_or_422()
    state = bench.load_state() or {}
    saved = ((state.get("settings") or {}).get("values")) or {}
    values, _errors = lab_params.validate(saved)
    return _make_config(remote, _allow_external(session, account), values, remote.model)


def _guard_not_busy(session: Session) -> None:
    pending, running = queue_counts(session)
    if engine.processing_in_progress(session) or running > 0:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="음성 인식·업무 추출 작업이 진행 중이라 측정을 시작할 수 없습니다. 작업이 끝난 뒤 다시 시도하세요",
        )


def _preflight(cfg: bench.BenchConfig) -> dict:
    """시작·이어하기 직전 연결 진단. 정상이 아니면 시작하지 않고 분류를 409 로 알린다(기존 중단 기록의 머리에도 남긴다)."""
    diag = engine_diag.diagnose(cfg.base_url, cfg.api_key, cfg.model, cfg.allow_external, cfg.connection_type)
    result = diag.as_dict()
    if not diag.ok:
        bench.record_diagnosis(result)
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"message": f"측정을 시작하지 않았습니다. 연결 진단 결과: {diag.hint}", **result},
        )
    return result


def _record(session: Session, account: Account, event_type: str, payload: dict) -> None:
    append_event(
        session, tenant_id=account.tenant_id, entity_type="engine_test", entity_id=account.tenant_id,
        event_type=event_type, actor_account_id=account.id, payload=payload,
    )
    session.commit()


def _sweep_builder(remote: engine.RemoteSettings, allow_external: bool, base: dict[str, Any], model: str) -> bench.SweepBuilder:
    """스윕의 프리셋별 설정: 화면 값에 그 프리셋의 값(맥락·구간·반복·드리프트)을 덮어 맥락마다 settings 를 만든다."""

    def build(preset_id: str) -> tuple[bench.BenchConfig, dict[str, Any]]:
        values = _resolve_values(preset_id, {k: v for k, v in base.items() if k not in lab_params.preset_of(preset_id)["values"]}, remote)
        cfg = _make_config(remote, allow_external, values, model)
        return cfg, bench.make_settings(cfg, preset_id)

    return build


@router.post("/start", response_model=BenchStatusOut, response_model_by_alias=True, status_code=status.HTTP_202_ACCEPTED)
def start(body: BenchStartIn, account: Account = Depends(get_current_account), session: Session = Depends(get_session)) -> BenchStatusOut:
    _require_admin(session, account)
    if not body.sweep and body.mode not in (bench.MODE_QUICK, bench.MODE_FULL):
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="측정 종류는 quick 또는 full 이어야 합니다")
    remote_for_check = engine.load_remote_settings()
    order: list[str] = list(lab_params.spec()["sweep"]["order"]) if body.sweep else []
    if body.sweep and remote_for_check.connection_type != engine.CONN_OLLAMA:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="OpenAI 호환 방식은 맥락을 서버에 보낼 수 없어 맥락 스윕을 쓸 수 없습니다. 연결 방식을 Ollama 로 바꾸세요",
        )
    # 변수 검증은 서버 상태 확인보다 먼저(화면에서 고칠 수 있는 오류를 먼저 알린다)
    first = order[0] if order else body.preset
    base_for_sweep = dict(body.values or {})
    values = _resolve_values(
        first, {k: v for k, v in base_for_sweep.items() if not order or k not in lab_params.preset_of(first)["values"]}
        if order else body.values, remote_for_check,
    )
    _guard_not_busy(session)
    remote = _remote_or_422()
    allow_external = _allow_external(session, account)
    model = _resolve_model(body.model, remote)
    cfg = _make_config(remote, allow_external, values, model)
    diagnosis = _preflight(cfg)
    block = bench.make_settings(cfg, first)  # 측정 시작 직전의 설정 기록(결과보다 먼저 저장된다)
    builder = _sweep_builder(remote, allow_external, base_for_sweep, model) if order else None
    try:
        bench.start(bench.MODE_FULL if order else body.mode, cfg, diagnosis=diagnosis, settings_block=block,
                    sweep_presets=order or None, sweep_builder=builder)
    except bench.BenchBusy as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from None
    _record(session, account, "engine_test.bench_started", {"mode": body.mode, "preset": first, "sweep": bool(order)})
    return _status()


@router.post("/resume", response_model=BenchStatusOut, response_model_by_alias=True, status_code=status.HTTP_202_ACCEPTED)
def resume(account: Account = Depends(get_current_account), session: Session = Depends(get_session)) -> BenchStatusOut:
    _require_admin(session, account)
    _guard_not_busy(session)
    cfg = _config(session, account)
    diagnosis = _preflight(cfg)
    state = bench.load_state() or {}
    builder = None
    if state.get("sweep"):
        remote = engine.load_remote_settings()
        base = ((state.get("settings") or {}).get("values")) or {}
        builder = _sweep_builder(remote, cfg.allow_external, base, remote.model)
    try:
        bench.resume(cfg, diagnosis=diagnosis, sweep_builder=builder)
    except bench.BenchBusy as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from None
    except bench.BenchError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from None
    _record(session, account, "engine_test.bench_resumed", {})
    return _status()


class ParamsOut(CamelModel):
    params: list[dict[str, Any]]
    presets: list[dict[str, Any]]
    defaults: dict[str, Any]
    connection_type: str
    context_sendable: bool
    context_disabled_reason: str
    sweep_order: list[str]
    sweep_enabled: bool
    sweep_disabled_reason: str


@router.get("/params", response_model=ParamsOut, response_model_by_alias=True)
def get_params(account: Account = Depends(get_current_account), session: Session = Depends(get_session)) -> ParamsOut:
    """화면 변수의 기본값·허용 범위·가이드와 프리셋 정의(config/bench_params.json 한 곳). 연결 방식에 따른 비활성 사유 포함."""
    _require_admin(session, account)
    conn = engine.load_remote_settings().connection_type
    sendable = conn == engine.CONN_OLLAMA
    params = []
    for p in lab_params.spec()["params"]:
        item = {"key": p["key"], "label": p["label"], "unit": p["unit"], "type": p["type"], "default": lab_params.default_of(p),
                "min": p["min"], "max": p["max"], "guide": p["guide"], "autoValue": p.get("autoValue"), "disabledReason": ""}
        if p.get("requires") == "ollama" and not sendable:
            item["disabledReason"] = "이 방식은 맥락을 서버에 보낼 수 없음"
        params.append(item)
    return ParamsOut(
        params=params, presets=lab_params.presets(), defaults=lab_params.default_values(), connection_type=conn,
        context_sendable=sendable, context_disabled_reason="" if sendable else "이 방식은 맥락을 서버에 보낼 수 없음",
        sweep_order=list(lab_params.spec()["sweep"]["order"]), sweep_enabled=sendable,
        sweep_disabled_reason="" if sendable else "OpenAI 호환 방식은 맥락을 서버에 보낼 수 없어 맥락 스윕을 쓸 수 없음",
    )


class ModelsOut(CamelModel):
    models: list[str]
    error: str


@router.get("/models", response_model=ModelsOut, response_model_by_alias=True)
def get_models(account: Account = Depends(get_current_account), session: Session = Depends(get_session)) -> ModelsOut:
    """저장된 서버의 모델 목록(측정에 쓸 모델을 고르는 용도). 실패해도 200 으로 사유만 알린다(주소·키 없음)."""
    _require_admin(session, account)
    remote = engine.load_remote_settings()
    if not remote.configured:
        return ModelsOut(models=[], error="서버 주소와 모델명을 먼저 저장하세요")
    allow_external = _allow_external(session, account)
    try:
        if remote.connection_type == engine.CONN_OLLAMA:
            names = list_ollama_models(engine.ollama_base(remote.base_url), 5.0, allow_external)
        else:
            names = list_models(remote.base_url, remote.api_key, 5.0, allow_external)
    except LocalLlmError:
        return ModelsOut(models=[], error="모델 목록을 불러오지 못했습니다. 연결 점검을 먼저 실행하세요")
    return ModelsOut(models=names[:100], error="")


@router.get("/history")
def get_history(account: Account = Depends(get_current_account), session: Session = Depends(get_session)) -> dict[str, Any]:
    """data/bench 의 이전 측정을 설정 요약과 핵심 결과로 나란히 비교한다."""
    _require_admin(session, account)
    return {"items": bench.list_history()}


@router.post("/stop", response_model=BenchStatusOut, response_model_by_alias=True)
def stop(account: Account = Depends(get_current_account), session: Session = Depends(get_session)) -> BenchStatusOut:
    _require_admin(session, account)
    bench.stop()  # 실행 중이 아니면 아무것도 하지 않는다(현재 상태를 그대로 돌려준다)
    return _status()


@router.get("/status", response_model=BenchStatusOut, response_model_by_alias=True)
def get_status(account: Account = Depends(get_current_account), session: Session = Depends(get_session)) -> BenchStatusOut:
    _require_admin(session, account)
    return _status()


@router.get("/result")
def download_result(
    run_id: str = Query("", alias="runId"), account: Account = Depends(get_current_account), session: Session = Depends(get_session)
) -> Response:
    """결과 JSON(요약 Markdown 포함) 내려받기. 완료되지 않았어도 측정된 만큼은 받을 수 있다. runId 를 주면 이력의 그 측정 파일."""
    _require_admin(session, account)
    if run_id:
        saved = bench.load_result_file(run_id)
        if saved is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="내려받을 측정 결과가 없습니다")
        return Response(
            content=json.dumps(saved, ensure_ascii=False, indent=2), media_type="application/json",
            headers={"Content-Disposition": f'attachment; filename="l3-bench-{run_id}.json"', "Cache-Control": "no-store"},
        )
    state = bench.load_state()
    if state is None or not any(u.get("state") == "done" for u in state["units"].values()):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="내려받을 측정 결과가 없습니다")
    result = bench.write_result_file(state)
    name = f"l3-bench-{result['runId']}.json"
    return Response(
        content=json.dumps(result, ensure_ascii=False, indent=2), media_type="application/json",
        headers={"Content-Disposition": f'attachment; filename="{name}"', "Cache-Control": "no-store"},
    )
