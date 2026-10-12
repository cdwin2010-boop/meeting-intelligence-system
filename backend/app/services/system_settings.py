"""시스템 관리자 설정(작업 82-1 파트 C). 허용 키는 두 개뿐이다.
- external_llm_enabled: Gemini 등 외부 LLM 사용 허용
- local_llm_external_host_allowed: 로컬 LLM 주소가 사설망 밖이어도 허용
유효값 규칙은 하나: 시스템 관리자 설정 행이 있으면 그 값, 없으면 .env 의 현재 값(명시하지 않았으면 거짓).
.env 값이 더 엄격하다는 이유로 행을 무시하거나, 행이 .env 보다 느슨할 수 없게 막는 규칙은 두지 않는다.
변경은 즉시 반영한다(요청마다 조회하고 캐시하지 않는다). 변경은 공통 이력(대상 종류 system_setting)에 사유와 함께 남긴다."""
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.models import SystemSetting
from app.models.common import ALL_SETTING_KEYS, KNOWLEDGE_SETTING_KEYS, SYSTEM_SETTING_KEYS, utcnow
from app.pipeline.local_llm import LocalLlmError, check_base_url
from app.services.history import KIND_SETTING_CHANGED, record_change

EXTERNAL_LLM_ENABLED = "external_llm_enabled"
LOCAL_LLM_EXTERNAL_HOST_ALLOWED = "local_llm_external_host_allowed"
KNOWLEDGE_INTEGRATION_ENABLED = "knowledge_integration_enabled"
KNOWLEDGE_API_ENDPOINT = "knowledge_api_endpoint"
REASON_MAX = 500
ENDPOINT_MAX = 500

SOURCE_DEFAULT = "default"  # 행도 없고 .env 에도 명시하지 않음(기본값 거짓)
SOURCE_ENV = "env"  # 행이 없고 .env 에 명시된 값
SOURCE_ADMIN = "admin"  # 시스템 관리자가 정한 값

# 설정 키 → 행이 없을 때 쓰는 .env 설정 필드 이름
_ENV_FIELD = {
    EXTERNAL_LLM_ENABLED: "external_llm_enabled",
    LOCAL_LLM_EXTERNAL_HOST_ALLOWED: "local_llm_allow_external_host",
    KNOWLEDGE_INTEGRATION_ENABLED: "knowledge_integration_enabled",
    KNOWLEDGE_API_ENDPOINT: "knowledge_api_endpoint",
}


class SettingError(ValueError):
    """설정 변경 규칙 위반. status: 404(허용 목록 밖 키)·422(사유 등 입력 오류)."""

    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


@dataclass(frozen=True)
class EffectiveSetting:
    key: str
    value: bool
    source: str
    updated_at: datetime | None = None
    updated_by: int | None = None
    text: str | None = None  # 문자열 값을 갖는 키(knowledge_api_endpoint)의 값. 참·거짓 키는 None


def _env_text(key: str) -> tuple[str, str]:
    field = _ENV_FIELD[key]
    return str(getattr(settings, field) or ""), (SOURCE_ENV if field in settings.model_fields_set else SOURCE_DEFAULT)


def _env_value(key: str) -> tuple[bool, str]:
    field = _ENV_FIELD[key]
    return bool(getattr(settings, field)), (SOURCE_ENV if field in settings.model_fields_set else SOURCE_DEFAULT)


def effective(session: Session, tenant_id: int, key: str) -> EffectiveSetting:
    row = session.scalar(select(SystemSetting).where(SystemSetting.tenant_id == tenant_id, SystemSetting.key == key))
    if row is not None:
        text = (row.value_text or "") if key in _TEXT_KEYS else None
        return EffectiveSetting(key, bool(row.value_bool), SOURCE_ADMIN, row.updated_at, row.updated_by, text)
    if key in _TEXT_KEYS:
        text, source = _env_text(key)
        return EffectiveSetting(key, bool(text), source, text=text)
    value, source = _env_value(key)
    return EffectiveSetting(key, value, source)


_TEXT_KEYS = (KNOWLEDGE_API_ENDPOINT,)


def all_effective(session: Session, tenant_id: int) -> list[EffectiveSetting]:
    """화면 목록용: 기존 두 키만(기존 화면이 이 목록의 키를 모두 알고 있다). 지식 연동 키는 knowledge_effective 를 쓴다."""
    return [effective(session, tenant_id, key) for key in SYSTEM_SETTING_KEYS]


def knowledge_effective(session: Session, tenant_id: int) -> list[EffectiveSetting]:
    return [effective(session, tenant_id, key) for key in KNOWLEDGE_SETTING_KEYS]


def check_endpoint(value: str) -> str:
    """knowledge_api_endpoint 값 검증: 비어 있으면 허용(연동 주소 없음). 아니면 로컬 LLM 주소와 같은 검사(루프백·사설망만, 외부 주소 거부)."""
    text = (value or "").strip()
    if not text:
        return ""
    if len(text) > ENDPOINT_MAX:
        raise SettingError(422, f"주소는 {ENDPOINT_MAX}자 이하여야 합니다.")
    try:
        check_base_url(text, allow_external=False)  # 로컬 LLM 연결 보호와 같은 함수(DNS 조회 없이 문자열·IP 기준)
    except LocalLlmError as exc:
        if exc.reason == "host_blocked":
            raise SettingError(422, "사설망·루프백 주소만 허용합니다. 외부 주소는 설정할 수 없습니다.") from None
        raise SettingError(422, "주소 형식이 올바르지 않습니다(http:// 또는 https:// 로 시작하는 주소).") from None
    return text.rstrip("/")


def set_value(session: Session, *, tenant_id: int, key: str, value: "bool | str", reason: str, actor_id: int) -> EffectiveSetting:
    if key not in ALL_SETTING_KEYS:
        raise SettingError(404, "알 수 없는 설정 키입니다.")
    is_text = key in _TEXT_KEYS
    if is_text and not isinstance(value, str):
        raise SettingError(422, "이 설정은 주소(문자열)로 입력하세요.")
    if not is_text and not isinstance(value, bool):
        raise SettingError(422, "이 설정은 참·거짓으로 입력하세요.")
    text = (reason or "").strip()
    if not text or len(text) > REASON_MAX:
        raise SettingError(422, f"변경 사유는 1~{REASON_MAX}자로 입력하세요.")
    text_value = check_endpoint(value) if is_text else None  # 주소 검사 실패는 422(저장하지 않음)
    bool_value = bool(text_value) if is_text else bool(value)
    before = effective(session, tenant_id, key)
    row = session.scalar(select(SystemSetting).where(SystemSetting.tenant_id == tenant_id, SystemSetting.key == key))
    now = utcnow()
    if row is None:
        row = SystemSetting(
            tenant_id=tenant_id, key=key, value_bool=bool_value, value_text=text_value, updated_by=actor_id, updated_at=now
        )
        session.add(row)
    else:
        row.value_bool, row.value_text, row.updated_by, row.updated_at = bool_value, text_value, actor_id, now
    session.flush()
    # 이력에는 주소 전체를 남기지 않는다(주소가 있는지 여부만): 내부 주소가 이력·로그에 번지지 않게
    shown_before = bool(before.text) if is_text else before.value
    record_change(
        session, tenant_id=tenant_id, target_type="system_setting", target_id=row.id, kind=KIND_SETTING_CHANGED, actor_id=actor_id,
        before={"value": shown_before, "source": before.source}, after={"value": bool_value, "source": SOURCE_ADMIN},
        extra={"key": key, "reason": text},
    )
    return effective(session, tenant_id, key)
