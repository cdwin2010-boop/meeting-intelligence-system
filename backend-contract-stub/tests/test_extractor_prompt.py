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
