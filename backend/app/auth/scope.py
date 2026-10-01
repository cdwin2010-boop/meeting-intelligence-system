"""고객사 범위 규칙: 업무 데이터 조회는 항상 로그인 계정의 tenant_id 로 거른다."""
from typing import TypeVar

from sqlalchemy import Select

from app.models import Account

S = TypeVar("S", bound=Select)


def scoped(query: S, account: Account) -> S:
    """select(모델) 조회에 `모델.tenant_id == account.tenant_id` 조건을 붙인다.
    tenant_id 열이 없는 모델(tenants, meeting_participants 등)은 실수를 막기 위해 오류를 낸다."""
    descriptions = query.column_descriptions
    if not descriptions:
        raise ValueError("scoped() 는 select(모델) 형태의 조회에만 쓸 수 있습니다.")
    for desc in descriptions:
        entity = desc.get("entity")
        if entity is None or not hasattr(entity, "tenant_id"):
            raise ValueError(f"tenant_id 가 없는 대상은 scoped() 로 거를 수 없습니다: {desc.get('name')}")
        query = query.where(entity.tenant_id == account.tenant_id)
    return query
