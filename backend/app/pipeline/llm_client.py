"""LLM 클라이언트 인터페이스(작업 75-2). 실제 HTTP 구현은 만들지 않는다(로컬 엔진 적용 지시에서 추가).
테스트용 FakeLlmClient 만 둔다: 스크립트된 응답 목록을 차례로 돌려주고 호출을 기록한다."""
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Protocol

Message = dict[str, str]  # {"role": "system"|"user"|"assistant", "content": "..."}


class LlmClientError(RuntimeError):
    """클라이언트 호출 실패(연결·시간 초과·서버 오류). 메시지에는 전사 본문을 넣지 않는다."""


class LlmClient(Protocol):
    def complete(self, messages: list[Message], json_mode: bool, timeout: float) -> str: ...


@dataclass
class LlmCall:
    messages: list[Message]
    json_mode: bool
    timeout: float


@dataclass
class FakeLlmClient:
    """responses 의 문자열은 그대로 돌려주고, 예외 객체는 던진다. responder 가 있으면 그것이 우선한다."""

    responses: list[str | BaseException] = field(default_factory=list)
    responder: Callable[[list[Message]], str] | None = None
    calls: list[LlmCall] = field(default_factory=list)

    def complete(self, messages: list[Message], json_mode: bool, timeout: float) -> str:
        self.calls.append(LlmCall(messages=[dict(m) for m in messages], json_mode=json_mode, timeout=timeout))
        if self.responder is not None:
            return self.responder(messages)
        if not self.responses:
            raise LlmClientError("스크립트된 응답이 없습니다.")
        value = self.responses.pop(0)
        if isinstance(value, BaseException):
            raise value
        return value
