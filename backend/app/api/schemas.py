"""API 입출력 스키마. JSON 필드는 camelCase, 파이썬 내부는 snake_case(alias 변환)."""
from pydantic import BaseModel, ConfigDict
from pydantic.alias_generators import to_camel


class CamelModel(BaseModel):
    # populate_by_name: 입력은 camelCase(loginId)와 snake_case(login_id) 모두 받는다
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)


class LoginRequest(CamelModel):
    login_id: str
    password: str


class TokenResponse(CamelModel):
    access_token: str
    token_type: str = "bearer"


class MeResponse(CamelModel):
    id: int
    name: str
    rank: str
    tenant_id: int
