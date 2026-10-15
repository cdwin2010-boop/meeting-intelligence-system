"""원격(시험용) 엔진 연결 점검·L-3 벤치마크 측정 API(작업 L-3a, 시험 전용).

- 권한은 엔진 시험 설정과 같다: 시험 모드(ENGINE_TEST_MODE)가 켜져 있어야 하고, 관리자 이상 또는 시스템 관리자만(그 밖은 403).
- 측정은 서버 백그라운드 스레드로 실행하고 요청은 즉시 돌려준다. 같은 시점에 1개만(이미 실행 중이면 409).
- 음성 인식·추출 작업이 진행 중이면 GPU 를 겹쳐 쓰지 않도록 시작·이어하기를 409 로 거부한다(로딩 테스트와 같은 규칙).
- 응답·결과 파일에는 주소·키·호스트명·전사 원문을 담지 않는다(건수·시간·비율·상태만).
"""
import json

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy.orm import Session

from app.api.engine_test import _require_admin
from app.api.schemas import CamelModel
from app.auth.deps import get_current_account
from app.config import settings
from app.db import get_session
from app.models import Account, append_event
from app.services import bench_runner as bench
from app.services import engine_diag
from app.services import engine_test as engine
from app.services.system_settings import LOCAL_LLM_EXTERNAL_HOST_ALLOWED, effective
from app.services.task_queue import queue_counts

router = APIRouter(prefix="/api/admin/engine-test/bench", tags=["engine-test"])


class BenchStartIn(CamelModel):
    mode: str  # quick | full


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


def _status() -> BenchStatusOut:
    return BenchStatusOut(**bench.status())


def _config(session: Session, account: Account) -> bench.BenchConfig:
    """시작·이어하기 때마다 현재 저장된 원격 설정을 다시 읽는다(주소가 바뀌어도 이어갈 수 있다)."""
    remote = engine.load_remote_settings()
    if not remote.configured:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="서버 주소와 모델명을 먼저 저장하세요")
    allow_external = True  # 시험 모드가 외부 주소 허용 근거. EXTERNAL_POLICY_ENFORCED 면 시스템 관리자 설정도 따른다(로딩 테스트와 동일)
    if settings.external_policy_enforced:
        allow_external = bool(effective(session, account.tenant_id, LOCAL_LLM_EXTERNAL_HOST_ALLOWED).value)
    return bench.BenchConfig(
        base_url=remote.base_url, model=remote.model, api_key=remote.api_key, allow_external=allow_external,
        item_timeout=settings.bench_item_timeout_seconds, retry_max=settings.bench_retry_max,
        retry_delay=settings.bench_retry_delay_seconds, repeat=settings.bench_repeat, drift_runs=settings.bench_drift_runs,
        long_max_chars=settings.bench_long_max_chars,
    )


def _guard_not_busy(session: Session) -> None:
    pending, running = queue_counts(session)
    if engine.processing_in_progress(session) or running > 0:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="음성 인식·업무 추출 작업이 진행 중이라 측정을 시작할 수 없습니다. 작업이 끝난 뒤 다시 시도하세요",
        )


def _preflight(cfg: bench.BenchConfig) -> dict:
    """시작·이어하기 직전 연결 진단. 정상이 아니면 시작하지 않고 분류를 409 로 알린다(기존 중단 기록의 머리에도 남긴다)."""
    diag = engine_diag.diagnose(cfg.base_url, cfg.api_key, cfg.model, cfg.allow_external)
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


@router.post("/start", response_model=BenchStatusOut, response_model_by_alias=True, status_code=status.HTTP_202_ACCEPTED)
def start(body: BenchStartIn, account: Account = Depends(get_current_account), session: Session = Depends(get_session)) -> BenchStatusOut:
    _require_admin(session, account)
    if body.mode not in (bench.MODE_QUICK, bench.MODE_FULL):
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="측정 종류는 quick 또는 full 이어야 합니다")
    _guard_not_busy(session)
    cfg = _config(session, account)
    diagnosis = _preflight(cfg)
    try:
        bench.start(body.mode, cfg, diagnosis=diagnosis)
    except bench.BenchBusy as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from None
    _record(session, account, "engine_test.bench_started", {"mode": body.mode})
    return _status()


@router.post("/resume", response_model=BenchStatusOut, response_model_by_alias=True, status_code=status.HTTP_202_ACCEPTED)
def resume(account: Account = Depends(get_current_account), session: Session = Depends(get_session)) -> BenchStatusOut:
    _require_admin(session, account)
    _guard_not_busy(session)
    cfg = _config(session, account)
    diagnosis = _preflight(cfg)
    try:
        bench.resume(cfg, diagnosis=diagnosis)
    except bench.BenchBusy as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from None
    except bench.BenchError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from None
    _record(session, account, "engine_test.bench_resumed", {})
    return _status()


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
def download_result(account: Account = Depends(get_current_account), session: Session = Depends(get_session)) -> Response:
    """결과 JSON(요약 Markdown 포함) 내려받기. 완료되지 않았어도 측정된 만큼은 받을 수 있다."""
    _require_admin(session, account)
    state = bench.load_state()
    if state is None or not any(u.get("state") == "done" for u in state["units"].values()):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="내려받을 측정 결과가 없습니다")
    result = bench.write_result_file(state)
    name = f"l3-bench-{result['runId']}.json"
    return Response(
        content=json.dumps(result, ensure_ascii=False, indent=2), media_type="application/json",
        headers={"Content-Disposition": f'attachment; filename="{name}"', "Cache-Control": "no-store"},
    )
