"""지식(KMS) 연동 호출 인터페이스(작업 83-1 선반영). 실제 HTTP 구현은 만들지 않는다.

방침(설계 문서 docs/design-kms-integration.md):
- 자료는 복제하지 않고 연합 조회 우선. 권한 판단은 자료의 원본 시스템 한 곳에서만 한다(응답은 원본 시스템이 이미 권한을 통과시킨 결과).
- 시스템 간 호출은 서비스 토큰 + 사용자는 external_sub 로만 알린다(사용자 토큰은 전달하지 않는다).
- 사용자 버튼 클릭 때의 검색어만 받는다. 안건·업무·회의록 내용이 자동으로 이 인터페이스에 전달되는 코드는 두지 않는다.
- 스위치(knowledge_integration_enabled)가 꺼져 있으면 어떤 경로로도 외부 호출이 일어나지 않는다(NullKnowledgeClient).
"""
from abc import ABC, abstractmethod
from dataclasses import dataclass, field

from app.config import settings

DEFAULT_LIMIT = 3
MAX_LIMIT = 10
QUERY_MAX = 200

STATUS_DISABLED = "disabled"  # 연동 꺼짐: 호출하지 않았다
STATUS_NOT_READY = "not_ready"  # 켜져 있으나 호출 구현이 아직 없다(선반영 단계): 호출하지 않았다
STATUS_OK = "ok"

MESSAGE_DISABLED = "지식 연동이 꺼져 있습니다."
MESSAGE_NOT_READY = "지식 연동 호출 기능이 아직 구현되지 않았습니다."


class KnowledgeQueryError(ValueError):
    """검색어·사용자 식별자·건수가 올바르지 않음(호출 전에 거절한다)."""


@dataclass(frozen=True)
class ReferenceCard:
    """관련 자료 카드 한 건. summary 는 한 줄 요약."""

    title: str
    date: str  # YYYY-MM-DD
    summary: str
    url: str
    source: str  # 자료의 원본 시스템 식별(예: "kms", "aidots")


@dataclass(frozen=True)
class ReferenceSearchResult:
    """검색 결과. cards 는 원본 시스템이 이미 권한을 통과시킨 것만 담는다(권한 정보 자체는 담지 않는다)."""

    status: str
    cards: tuple[ReferenceCard, ...] = field(default_factory=tuple)
    message: str = ""

    @property
    def disabled(self) -> bool:
        return self.status != STATUS_OK


def validate_request(query: str, external_sub: str, limit: int) -> tuple[str, str, int]:
    """호출 전 검증. 공백만 있는 검색어·빈 사용자 식별자·범위 밖 건수는 거절한다."""
    q = (query or "").strip()
    if not q or len(q) > QUERY_MAX:
        raise KnowledgeQueryError(f"검색어는 1~{QUERY_MAX}자여야 합니다.")
    sub = (external_sub or "").strip()
    if not sub:
        raise KnowledgeQueryError("사용자 식별자(external_sub)가 필요합니다.")
    if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= MAX_LIMIT:
        raise KnowledgeQueryError(f"건수는 1~{MAX_LIMIT} 사이여야 합니다.")
    return q, sub, limit


class KnowledgeClient(ABC):
    """지식 연동 클라이언트. 구현체는 서비스 토큰으로 호출하고 사용자는 external_sub 로만 알린다."""

    @abstractmethod
    def search_references(self, query: str, external_sub: str, limit: int = DEFAULT_LIMIT) -> ReferenceSearchResult:
        ...


class NullKnowledgeClient(KnowledgeClient):
    """기본 구현: 어떤 호출도 하지 않고 "연동 꺼짐"(또는 "구현 전") 결과만 돌려준다."""

    def __init__(self, status: str = STATUS_DISABLED):
        self._status = status

    def search_references(self, query: str, external_sub: str, limit: int = DEFAULT_LIMIT) -> ReferenceSearchResult:
        validate_request(query, external_sub, limit)  # 잘못된 입력은 꺼져 있어도 같은 방식으로 거절
        message = MESSAGE_DISABLED if self._status == STATUS_DISABLED else MESSAGE_NOT_READY
        return ReferenceSearchResult(status=self._status, cards=(), message=message)


class FakeKnowledgeClient(KnowledgeClient):
    """테스트용 고정 응답. 호출 기록을 남긴다(네트워크 없음)."""

    def __init__(self, cards: tuple[ReferenceCard, ...] = ()):
        self._cards = tuple(cards)
        self.calls: list[tuple[str, str, int]] = []

    def search_references(self, query: str, external_sub: str, limit: int = DEFAULT_LIMIT) -> ReferenceSearchResult:
        q, sub, count = validate_request(query, external_sub, limit)
        self.calls.append((q, sub, count))
        return ReferenceSearchResult(status=STATUS_OK, cards=self._cards[:count])


def make_knowledge_client(*, enabled: bool, endpoint: str) -> KnowledgeClient:
    """스위치와 주소로 클라이언트를 고른다. 꺼져 있거나 주소가 없으면 Null(호출 없음).
    켜져 있어도 실제 HTTP 구현이 없으므로 지금은 "구현 전" Null 을 돌려준다(어떤 경우에도 외부 호출 없음)."""
    if not enabled or not (endpoint or "").strip():
        return NullKnowledgeClient(STATUS_DISABLED)
    return NullKnowledgeClient(STATUS_NOT_READY)


def client_from_settings() -> KnowledgeClient:
    """.env 초기값 기준 클라이언트(고객사 설정은 services/system_settings.py 의 유효값을 make_knowledge_client 에 넘긴다)."""
    return make_knowledge_client(enabled=settings.knowledge_integration_enabled, endpoint=settings.knowledge_api_endpoint)
