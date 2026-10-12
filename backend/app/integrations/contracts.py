"""지식 연동 계약 스키마의 원본 모델(작업 83-1). docs/contracts/*.json 은 이 모델과 일치해야 한다(테스트로 확인).
검증 라이브러리(jsonschema)가 설치돼 있지 않아 pydantic 모델을 원본으로 두고, 스키마 파일의 속성·필수·상수가 모델과 같은지 검사한다.

원칙: 응답에는 권한 정보가 아니라 "원본 시스템이 이미 권한을 통과시킨 결과"만 담는다.
"""
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

SCHEMA_VERSION = "1"
DATE_PATTERN = r"^\d{4}-\d{2}-\d{2}$"


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Subject(_Strict):
    # 사용자는 external_sub 로만 알린다(사용자 토큰·이메일·이름은 보내지 않는다)
    external_sub: str = Field(min_length=1, max_length=200)


class SearchRequest(_Strict):
    schema_version: Literal["1"]
    query: str = Field(min_length=1, max_length=200)
    subject: Subject
    limit: int = Field(default=3, ge=1, le=10)


class ReferenceItem(_Strict):
    id: str = Field(min_length=1, max_length=200)
    title: str = Field(min_length=1, max_length=300)
    date: str = Field(pattern=DATE_PATTERN)
    summary: str = Field(max_length=300)  # 한 줄 요약
    url: str = Field(min_length=1, max_length=1000)


class MeetingCard(ReferenceItem):
    source: Literal["aidots"]


class KnowledgeCard(ReferenceItem):
    source: Literal["kms"]


class MeetingSearchResponse(_Strict):
    schema_version: Literal["1"]
    cards: list[MeetingCard] = Field(max_length=10)


class KnowledgeReferenceResponse(_Strict):
    schema_version: Literal["1"]
    cards: list[KnowledgeCard] = Field(max_length=10)


class MeetingSearchExchange(_Strict):
    """meeting-search-v1: KMS → AI DOTS 회의록 검색의 요청·응답 쌍(예시 파일 단위)."""

    request: SearchRequest
    response: MeetingSearchResponse


class KnowledgeReferenceExchange(_Strict):
    """knowledge-reference-v1: AI DOTS → KMS 관련 규정 조회의 요청·응답 쌍(요청·응답 형식은 위와 대칭)."""

    request: SearchRequest
    response: KnowledgeReferenceResponse
