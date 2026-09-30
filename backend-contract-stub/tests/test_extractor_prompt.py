"""추출 프롬프트에 v1.9.5 정책 문구가 들어 있는지 확인한다 (LLM 호출 없음, 문자열 검사만)."""
from datetime import datetime, timedelta, timezone

from app.pipeline.extractor import build_prompt

STARTED_AT = datetime(2026, 9, 29, 14, 0, tzinfo=timezone(timedelta(hours=9)))


def test_prompt_states_inclusion_policy_and_full_scan():
    prompt = build_prompt("[00:00:03] 화자1: 테스트", STARTED_AT)
    assert "처음부터 끝까지" in prompt and "빠짐없이" in prompt
    assert "확정 약속" in prompt and "지시-수락" in prompt
    for excluded in ("이미 끝난 일", "생각해 보겠습니다", "잡담", "일반론"):
        assert excluded in prompt
    assert "2026-09-29T14:00:00+09:00 (화요일)" in prompt  # 회의 일시·요일은 그대로


def test_prompt_states_assignee_naming_rules_and_empty_strings():
    prompt = build_prompt("[00:00:03] 화자1: 테스트", STARTED_AT)
    assert "화자 표기 → 이름/직함" in prompt  # 대응표 먼저
    assert "지어내지 않습니다" in prompt  # 이름을 모르면 화자 표기 그대로
    assert 'assignee 를 빈 문자열("")' in prompt  # 담당 불분명 → ""
    assert "임의로 합치거나 바꾸지 않습니다" in prompt  # 업체명 원문 유지
    assert 'null 이 아니라 빈 문자열("")' in prompt  # 마감일 근거 없음 → ""
    assert prompt.rstrip().endswith("[00:00:03] 화자1: 테스트")  # 전사문은 맨 끝에 그대로


def test_prompt_states_v198_recall_rules():
    """v1.9.8: 한 발화 여러 항목, 제안·권유형 지시, 자발적 약속"""
    prompt = build_prompt("[00:00:03] 화자1: 테스트", STARTED_AT)
    assert "각각 별도 항목" in prompt and "업체·프로젝트·고객명이 다르면 다른 항목" in prompt
    assert "제안·권유형 요청도 지시로" in prompt and "~하시는 게 좋을 것 같아요" in prompt
    assert "자발적 약속도 포함" in prompt and "오늘 마무리될 것 같습니다" in prompt
    assert "정리 완료했고요" in prompt  # 끝난 일 보고는 계속 제외


def test_prompt_has_final_check_step_without_duplicates():
    """v1.9.8: 마지막 점검 단계 — 다시 훑어 빠진 것만 추가, 겹치면 추가 안 함"""
    prompt = build_prompt("[00:00:03] 화자1: 테스트", STARTED_AT)
    assert "[6. 마지막 점검]" in prompt
    assert "다시 처음부터 훑어" in prompt
    assert "빠진 것만 추가" in prompt and "겹치는 것은 추가하지 않습니다" in prompt
    assert prompt.index("[6. 마지막 점검]") < prompt.index("전사문:\n")  # 점검 지시는 전사문 앞
