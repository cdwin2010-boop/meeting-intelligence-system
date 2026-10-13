"""엔진 시험 모드 관리 API(작업 91-1, 시험 전용): 원격(OpenAI 호환 HTTP) 엔진 설정·로딩 테스트.

- 모든 요청은 ENGINE_TEST_MODE 가 켜져 있을 때만 받는다(꺼져 있으면 403, 서버가 거부한다. 화면 숨김만으로 막지 않는다).
- 권한: 관리자 이상(중간관리자·지시자) 또는 시스템 관리자. 담당자는 403.
- API 키는 응답·로그에 평문으로 내보내지 않는다(마스킹 표시와 설정 여부만). 서버 주소는 설정 화면에서 바꿀 수 있어야 하므로 관리자에게는 보인다.
- 로딩 테스트: 모델 목록 조회 → 현재 모델명 확인 → 짧은 추론 1회 → 응답 시간·JSON 파싱 성공 여부·실패 사유.
  음성 인식·추출 작업이 진행 중이면 409(GPU 를 겹쳐 쓰지 않는다).
"""
import json
import time

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.api.schemas import CamelModel
from app.auth.deps import get_current_account, has_rank
from app.config import settings
from app.db import get_session
from app.models import Account, append_event
from app.pipeline.local_llm import LocalLlmError, OpenAICompatClient, list_models
from app.services import engine_test as engine
from app.services.system_admin import is_system_admin
from app.services.system_settings import LOCAL_LLM_EXTERNAL_HOST_ALLOWED, effective
from app.services.task_queue import queue_counts

router = APIRouter(prefix="/api/admin/engine-test", tags=["engine-test"])

_MODELS_SHOWN = 20
_LOAD_TEST_TIMEOUT_MAX = 60.0  # 로딩 테스트 각 단계의 대기 상한(초)
_REASON_TEXT = {
    "connect": engine.UNAVAILABLE_MESSAGE,
    "timeout": "원격 엔진 응답 시간이 초과되었습니다",
    "http_5xx": engine.UNAVAILABLE_MESSAGE,
    "http_429": "원격 엔진이 요청 한도를 초과했다고 응답했습니다",
    "http_4xx": "원격 엔진이 요청을 거절했습니다(주소·키·모델명을 확인하세요)",
    "format": "원격 엔진 응답 형식이 올바르지 않습니다",
    "host_blocked": "외부 주소 사용이 허용되지 않았습니다(시스템 관리자의 외부 연동 설정을 확인하세요)",
    "config": "서버 주소 형식이 올바르지 않습니다",
}


class RemoteSettingsOut(CamelModel):
    base_url: str
    model: str
    api_key_set: bool
    api_key_masked: str  # 값이 있으면 고정 마스킹 문자열(원문 아님), 없으면 ""
    timeout_seconds: float
    chunk_max_chars: int  # 0 = 기본 청크 한도 사용
    configured: bool
    default_engine: str  # default | remote (ENGINE_TEST_DEFAULT)
    notice: str


class RemoteSettingsIn(CamelModel):
    base_url: str
    model: str
    api_key: str | None = None  # 비우거나 생략하면 기존 키 유지
    timeout_seconds: float
    chunk_max_chars: int = 0


class LoadTestOut(CamelModel):
    ok: bool
    model_listed: bool  # 모델 목록 조회 성공
    model_found: bool  # 설정한 모델이 목록에 있음
    current_model: str
    available_models: list[str]
    response_ms: int | None  # 짧은 추론 1회의 응답 시간
    json_parsed: bool
    failure_stage: str  # "" | models | inference
    failure_reason: str


NOTICE = "외부 터널을 거치므로 시험용 데이터만 사용하세요"


def _require_admin(session: Session, account: Account) -> None:
    engine.require_test_mode()
    if not (has_rank(account, "manager") or is_system_admin(session, account.id)):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="권한이 없습니다")


def _out() -> RemoteSettingsOut:
    remote = engine.load_remote_settings()
    return RemoteSettingsOut(
        base_url=remote.base_url, model=remote.model, api_key_set=bool(remote.api_key), api_key_masked=engine.mask_key(remote.api_key),
        timeout_seconds=remote.timeout_seconds, chunk_max_chars=remote.chunk_max_chars, configured=remote.configured,
        default_engine=settings.engine_test_default, notice=NOTICE,
    )


@router.get("/remote", response_model=RemoteSettingsOut, response_model_by_alias=True)
def get_remote(account: Account = Depends(get_current_account), session: Session = Depends(get_session)) -> RemoteSettingsOut:
    _require_admin(session, account)
    return _out()


def _unprocessable(message: str) -> HTTPException:
    return HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=message)


@router.put("/remote", response_model=RemoteSettingsOut, response_model_by_alias=True)
def put_remote(
    body: RemoteSettingsIn, account: Account = Depends(get_current_account), session: Session = Depends(get_session)
) -> RemoteSettingsOut:
    _require_admin(session, account)
    try:
        base_url = engine.validate_base_url(body.base_url)
    except ValueError as exc:
        raise _unprocessable(str(exc)) from None
    model = body.model.strip()
    if not model or len(model) > 200:
        raise _unprocessable("모델명을 입력하세요(200자 이내)")
    if not 1 <= body.timeout_seconds <= 3600:
        raise _unprocessable("요청 타임아웃은 1~3600초여야 합니다")
    if body.chunk_max_chars != 0 and not 500 <= body.chunk_max_chars <= 100000:
        raise _unprocessable("청크 한도는 0(기본) 또는 500~100000 글자여야 합니다")
    key = (body.api_key or "").strip()
    if len(key) > 500:
        raise _unprocessable("API 키가 너무 깁니다")
    changed = engine.save_remote_settings(
        base_url=base_url, model=model, api_key=key or None, timeout_seconds=body.timeout_seconds, chunk_max_chars=body.chunk_max_chars
    )
    # 이력에는 바뀐 항목 이름만 남긴다(주소·키 값은 남기지 않는다)
    append_event(
        session, tenant_id=account.tenant_id, entity_type="engine_test", entity_id=account.tenant_id,
        event_type="engine_test.settings_changed", actor_account_id=account.id, payload={"changed": changed},
    )
    session.commit()
    return _out()


def _failure(stage: str, reason: str, **fields) -> LoadTestOut:
    base = dict(
        ok=False, model_listed=False, model_found=False, current_model="", available_models=[], response_ms=None,
        json_parsed=False, failure_stage=stage, failure_reason=reason,
    )
    base.update(fields)
    return LoadTestOut(**base)


@router.post("/remote/load-test", response_model=LoadTestOut, response_model_by_alias=True)
def load_test(account: Account = Depends(get_current_account), session: Session = Depends(get_session)) -> LoadTestOut:
    _require_admin(session, account)
    pending, running = queue_counts(session)
    if engine.processing_in_progress(session) or running > 0:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="음성 인식·업무 추출 작업이 진행 중이라 로딩 테스트를 할 수 없습니다. 작업이 끝난 뒤 다시 시도하세요",
        )
    remote = engine.load_remote_settings()
    if not remote.configured:
        raise _unprocessable("서버 주소와 모델명을 먼저 저장하세요")
    # 외부 주소 허용: 시험 모드가 허용 근거. 단 EXTERNAL_POLICY_ENFORCED 면 시스템 관리자의 외부 주소 허용 설정도 따른다
    allow_external = True
    if settings.external_policy_enforced:
        allow_external = bool(effective(session, account.tenant_id, LOCAL_LLM_EXTERNAL_HOST_ALLOWED).value)
    step_timeout = min(remote.timeout_seconds, _LOAD_TEST_TIMEOUT_MAX)

    models: list[str] = []
    try:
        models = list_models(remote.base_url, remote.api_key, step_timeout, allow_external)
    except LocalLlmError as exc:
        return _failure("models", _REASON_TEXT.get(exc.reason, engine.UNAVAILABLE_MESSAGE), current_model=remote.model)
    listed = dict(model_listed=True, model_found=remote.model in models, current_model=remote.model, available_models=models[:_MODELS_SHOWN])

    client = OpenAICompatClient(
        remote.base_url, remote.model, remote.api_key or "not-needed", max_output_tokens=64, allow_external=allow_external, json_mode_enabled=True
    )
    messages = [
        {"role": "system", "content": "You answer with a single JSON object and nothing else."},
        {"role": "user", "content": 'Reply with exactly this JSON object: {"ok": true}'},
    ]
    started = time.monotonic()
    try:
        content = client.complete(messages, json_mode=True, timeout=step_timeout)
    except LocalLlmError as exc:
        return _failure("inference", _REASON_TEXT.get(exc.reason, engine.UNAVAILABLE_MESSAGE), **listed)
    elapsed_ms = int((time.monotonic() - started) * 1000)
    try:
        parsed = json.loads(content.strip())
        json_ok = isinstance(parsed, dict)
    except ValueError:
        json_ok = False
    if not json_ok:
        return _failure("inference", "응답을 JSON 으로 읽지 못했습니다", response_ms=elapsed_ms, **listed)
    # 설정한 모델이 목록에 없어도 추론이 성공했으면 성공으로 보되 model_found=false 로 알린다
    return LoadTestOut(ok=True, response_ms=elapsed_ms, json_parsed=True, failure_stage="", failure_reason="", **listed)
