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
from app.models.common import SYSTEM_SETTING_KEYS, utcnow
from app.services.history import KIND_SETTING_CHANGED, record_change

EXTERNAL_LLM_ENABLED = "external_llm_enabled"
LOCAL_LLM_EXTERNAL_HOST_ALLOWED = "local_llm_external_host_allowed"
REASON_MAX = 500

SOURCE_DEFAULT = "default"  # 행도 없고 .env 에도 명시하지 않음(기본값 거짓)
SOURCE_ENV = "env"  # 행이 없고 .env 에 명시된 값
SOURCE_ADMIN = "admin"  # 시스템 관리자가 정한 값

# 설정 키 → 행이 없을 때 쓰는 .env 설정 필드 이름
_ENV_FIELD = {
    EXTERNAL_LLM_ENABLED: "external_llm_enabled",
    LOCAL_LLM_EXTERNAL_HOST_ALLOWED: "local_llm_allow_external_host",
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


def _env_value(key: str) -> tuple[bool, str]:
    field = _ENV_FIELD[key]
    return bool(getattr(settings, field)), (SOURCE_ENV if field in settings.model_fields_set else SOURCE_DEFAULT)


def effective(session: Session, tenant_id: int, key: str) -> EffectiveSetting:
    row = session.scalar(select(SystemSetting).where(SystemSetting.tenant_id == tenant_id, SystemSetting.key == key))
    if row is not None:
        return EffectiveSetting(key, bool(row.value_bool), SOURCE_ADMIN, row.updated_at, row.updated_by)
    value, source = _env_value(key)
    return EffectiveSetting(key, value, source)


def all_effective(session: Session, tenant_id: int) -> list[EffectiveSetting]:
    return [effective(session, tenant_id, key) for key in SYSTEM_SETTING_KEYS]


def set_value(session: Session, *, tenant_id: int, key: str, value: bool, reason: str, actor_id: int) -> EffectiveSetting:
    if key not in SYSTEM_SETTING_KEYS:
        raise SettingError(404, "알 수 없는 설정 키입니다.")
    text = (reason or "").strip()
    if not text or len(text) > REASON_MAX:
        raise SettingError(422, f"변경 사유는 1~{REASON_MAX}자로 입력하세요.")
    before = effective(session, tenant_id, key)
    row = session.scalar(select(SystemSetting).where(SystemSetting.tenant_id == tenant_id, SystemSetting.key == key))
    now = utcnow()
    if row is None:
        row = SystemSetting(tenant_id=tenant_id, key=key, value_bool=bool(value), updated_by=actor_id, updated_at=now)
        session.add(row)
    else:
        row.value_bool, row.updated_by, row.updated_at = bool(value), actor_id, now
    session.flush()
    record_change(
        session, tenant_id=tenant_id, target_type="system_setting", target_id=row.id, kind=KIND_SETTING_CHANGED, actor_id=actor_id,
        before={"value": before.value, "source": before.source}, after={"value": bool(value), "source": SOURCE_ADMIN},
        extra={"key": key, "reason": text},
    )
    return effective(session, tenant_id, key)
