"""추출 엔진 선택(작업 81-1). 서비스 파일(processing.py)을 키우지 않으려고 엔진·회의 유형 조회를 여기에 둔다.
LLM_PROVIDER 가 local 이면 로컬 LLM 추출기(안건 분할·3단계 파서)를, 아니면 기존 추출기 선택(STT_PROVIDER 따름)을 쓴다."""
from collections.abc import Callable

from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from app.config import settings
from app.models import MeetingClassification
from app.pipeline.extractor import Extractor
from app.pipeline.local_extractor import make_local_extractor
from app.services.external_policy import local_external_host_allowed


def meeting_type_of(factory: sessionmaker, meeting_id: int) -> str:
    """회의 유형 코드. 분류 기록이 없으면 미지정("")."""
    with factory() as session:
        found = session.scalar(select(MeetingClassification.meeting_type).where(MeetingClassification.meeting_id == meeting_id))
    return found or ""


def select_extractor(
    factory: sessionmaker, meeting_id: int, extractor_factory: Callable[[], Extractor] | None, default: Callable[[], Extractor]
) -> Extractor:
    """주입된 추출기가 있으면 그것을, 로컬 엔진이면 로컬 추출기를, 아니면 기본 추출기를 만든다."""
    if extractor_factory is not None:
        return extractor_factory()
    if settings.effective_llm_provider == "local":
        return make_local_extractor(
            meeting_type_of(factory, meeting_id), allow_external=local_external_host_allowed(factory, meeting_id)
        )
    return default()
