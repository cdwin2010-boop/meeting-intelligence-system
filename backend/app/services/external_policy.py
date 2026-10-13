"""외부 연동 허용 판단(작업 82-1 파트 C). 판단 지점은 이 모듈 한 곳이다(규칙 복제 금지).

- EXTERNAL_POLICY_ENFORCED(기본 false)가 거짓이면 현행 동작 그대로: Gemini 는 제한 없이 실행되고 로컬 LLM 의 외부 주소는
  LOCAL_LLM_ALLOW_EXTERNAL_HOST(.env)만 따른다(개발 호환).
- 참이면 시스템 관리자 설정(system_settings, 행이 없으면 .env 값)을 따른다: Gemini 는 external_llm_enabled 가 참일 때만 실행하고
  아니면 처리를 실패(external_disabled, 재처리 가능)로 마친다. 로컬 외부 주소는 local_llm_external_host_allowed 가 참일 때만 허용한다.
처리 실패 표시는 81-1 의 오류 분류(error_code 의 job_error_code)를 따른다."""
from sqlalchemy.orm import sessionmaker

from app.config import settings
from app.models import Meeting
from app.services.system_settings import EXTERNAL_LLM_ENABLED, LOCAL_LLM_EXTERNAL_HOST_ALLOWED, effective

ERROR_CODE = "external_disabled"


class ExternalDisabledError(RuntimeError):
    """외부 LLM 사용이 시스템 설정으로 허용되지 않음. 메시지에 주소·키는 없다."""

    job_error_code = ERROR_CODE
    kind = "permanent"  # 설정을 바꾼 뒤 사람이 재처리한다(자동 재시도 대상 아님)

    def __init__(self) -> None:
        super().__init__("외부 LLM 사용이 허용되지 않아 처리하지 않았습니다. 시스템 관리자의 외부 연동 설정을 확인하세요.")


def uses_external_llm() -> bool:
    """이번 처리가 외부 LLM(Gemini)을 쓰는가: 추출 엔진 또는 STT 엔진이 gemini."""
    return settings.effective_llm_provider == "gemini" or settings.stt_provider == "gemini"


def enforce_external_policy(factory: sessionmaker, meeting_id: int) -> None:
    """처리 시작 때(외부 호출 전) 호출. 스위치가 꺼져 있거나 외부 LLM 을 쓰지 않으면 아무것도 하지 않는다. 막히면 ExternalDisabledError."""
    if not settings.external_policy_enforced or not uses_external_llm():
        return
    with factory() as session:
        meeting = session.get(Meeting, meeting_id)
        if meeting is None or not effective(session, meeting.tenant_id, EXTERNAL_LLM_ENABLED).value:
            raise ExternalDisabledError()


def local_external_host_allowed(factory: sessionmaker, meeting_id: int, remote: bool = False) -> bool:
    """로컬 LLM 클라이언트에 넘길 allow_external 값. remote=True(원격 시험 엔진, 작업 91-1)이면 시험 모드가 켜져 있는 것을 허용 근거로 보되
    EXTERNAL_POLICY_ENFORCED 가 참이면 시스템 관리자의 외부 주소 허용 설정도 따른다."""
    if not settings.external_policy_enforced:
        return True if remote else settings.local_llm_allow_external_host
    with factory() as session:
        meeting = session.get(Meeting, meeting_id)
        if meeting is None:
            return False
        return effective(session, meeting.tenant_id, LOCAL_LLM_EXTERNAL_HOST_ALLOWED).value
