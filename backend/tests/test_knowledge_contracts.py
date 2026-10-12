"""지식 연동 계약 스키마(작업 83-1 파트 C): 정상·잘못된 샘플 검증, 스키마 파일과 원본 모델의 일치.
jsonschema 가 설치돼 있지 않아 pydantic 모델(app/integrations/contracts.py)을 원본으로 두고 스키마 파일이 모델과 같은지 검사한다."""
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.integrations.contracts import (
    KnowledgeCard, KnowledgeReferenceExchange, KnowledgeReferenceResponse, MeetingCard, MeetingSearchExchange,
    MeetingSearchResponse, SearchRequest, Subject,
)

DOCS = Path(__file__).resolve().parent.parent.parent / "docs" / "contracts"

CONTRACTS = {
    "meeting-search-v1": {"exchange": MeetingSearchExchange, "response": MeetingSearchResponse, "card": MeetingCard, "source": "aidots"},
    "knowledge-reference-v1": {"exchange": KnowledgeReferenceExchange, "response": KnowledgeReferenceResponse, "card": KnowledgeCard, "source": "kms"},
}


def load(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


@pytest.mark.parametrize("name", CONTRACTS)
def test_valid_sample_passes_and_invalid_sample_fails(name):
    model = CONTRACTS[name]["exchange"]
    valid = load(DOCS / "examples" / f"{name}.valid.json")
    parsed = model.model_validate(valid)
    assert parsed.request.schema_version == "1" and parsed.request.subject.external_sub
    assert all(card.source == CONTRACTS[name]["source"] for card in parsed.response.cards)
    with pytest.raises(ValidationError):
        model.model_validate(load(DOCS / "examples" / f"{name}.invalid.json"))


def test_each_invalid_sample_fails_for_the_documented_reasons():
    meeting = load(DOCS / "examples" / "meeting-search-v1.invalid.json")
    with pytest.raises(ValidationError) as info:
        MeetingSearchExchange.model_validate(meeting)
    text = str(info.value)
    assert "external_sub" in text and "date" in text and "source" in text  # 사용자 식별자 없음·날짜 형식·원본 시스템 값 틀림
    knowledge = load(DOCS / "examples" / "knowledge-reference-v1.invalid.json")
    with pytest.raises(ValidationError) as info:
        KnowledgeReferenceExchange.model_validate(knowledge)
    text = str(info.value)
    assert "schema_version" in text and "can_read" in text  # 버전 틀림·권한 정보 같은 알 수 없는 칸 금지


def test_limit_defaults_to_three_and_is_bounded():
    request = SearchRequest.model_validate({"schema_version": "1", "query": "q", "subject": {"external_sub": "u"}})
    assert request.limit == 3
    for bad in (0, 11):
        with pytest.raises(ValidationError):
            SearchRequest.model_validate({"schema_version": "1", "query": "q", "subject": {"external_sub": "u"}, "limit": bad})


def test_response_cannot_carry_permission_fields_or_user_identity():
    base = {"id": "1", "title": "t", "date": "2026-10-01", "summary": "s", "url": "u", "source": "aidots"}
    MeetingCard.model_validate(base)
    for extra in ("can_read", "permissions", "acl", "allowed_users"):
        with pytest.raises(ValidationError):
            MeetingCard.model_validate({**base, extra: True})
    with pytest.raises(ValidationError):
        Subject.model_validate({"external_sub": "u", "access_token": "x"})  # 사용자 토큰 전달 금지
    with pytest.raises(ValidationError):
        SearchRequest.model_validate({"schema_version": "1", "query": "q", "subject": {"external_sub": "u"}, "user_token": "x"})


def _compare(schema_part: dict, model, expect_const: dict | None = None):
    """스키마 정의의 속성·필수·제약이 pydantic 모델과 같은지."""
    generated = model.model_json_schema()
    assert set(schema_part["properties"]) == set(generated["properties"]), model.__name__
    assert set(schema_part["required"]) == set(generated.get("required", [])), model.__name__
    assert schema_part["additionalProperties"] is False
    for key, spec in schema_part["properties"].items():
        got = generated["properties"][key]
        for constraint in ("minLength", "maxLength", "minimum", "maximum", "pattern", "maxItems"):
            if constraint in spec or constraint in got:
                assert spec.get(constraint) == got.get(constraint), (model.__name__, key, constraint)
        if "const" in spec:
            assert got.get("const") == spec["const"] or got.get("enum") == [spec["const"]], (model.__name__, key)


@pytest.mark.parametrize("name", CONTRACTS)
def test_schema_files_match_the_models(name):
    schema = load(DOCS / f"{name}.json")
    spec = CONTRACTS[name]
    defs = schema["definitions"]
    assert schema["$id"] == name and set(schema["required"]) == {"request", "response"}
    _compare(defs["subject"], Subject)
    _compare(defs["request"], SearchRequest)
    _compare(defs["card"], spec["card"])
    _compare(defs["response"], spec["response"])
    assert defs["card"]["properties"]["source"] == {"const": spec["source"]}
    assert defs["request"]["properties"]["limit"]["default"] == 3
    assert defs["response"]["properties"]["cards"]["maxItems"] == 10


@pytest.mark.parametrize("name", CONTRACTS)
def test_schema_description_states_the_permission_principle(name):
    description = load(DOCS / f"{name}.json")["description"]
    assert "권한을 통과시킨 결과" in description and "external_sub" in description and "사용자 토큰" in description


def test_the_two_contracts_are_symmetric():
    a = load(DOCS / "meeting-search-v1.json")["definitions"]
    b = load(DOCS / "knowledge-reference-v1.json")["definitions"]
    assert a["request"] == b["request"] or a["request"]["properties"].keys() == b["request"]["properties"].keys()
    assert a["card"]["properties"].keys() == b["card"]["properties"].keys()
    assert a["response"]["properties"].keys() == b["response"]["properties"].keys()
    assert a["card"]["properties"]["source"] != b["card"]["properties"]["source"]  # 원본 시스템만 다르다


def test_example_files_contain_no_real_secrets_or_addresses():
    for path in (DOCS / "examples").glob("*.json"):
        text = path.read_text(encoding="utf-8")
        assert "Bearer" not in text and "token" not in text.lower().replace("schema_version", "")
        assert "example" in text  # 주소는 예시 도메인만
