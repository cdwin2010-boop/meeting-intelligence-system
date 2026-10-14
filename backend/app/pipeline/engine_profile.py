"""엔진 프로파일(작업 B-1d 보강): 엔진별 능력치를 값으로 둔다. 새 엔진(예: Ollama)을 더할 때 프로파일 값만 만들면 된다.
요청 전 사전 예산 계산(구간 글자 상한)과 응답 잘림 감지가 이 값을 쓴다. 값은 설정(config.py)에서 읽고 하드코딩하지 않는다.

- context_tokens: 맥락 길이(n_ctx). 프롬프트 + 구간 + 출력 예약이 이 값의 budget_ratio(90%) 안에 들어가게 구간을 자른다.
- max_output_tokens: 엔진이 낼 수 있는 최대 출력 토큰(설정값). 실제 예약은 output_reserve_tokens() 가 맥락의 1/4 이하로 줄인다.
- supports_json_format: response_format(json_object) 지원 여부. 거짓이면 처음부터 보내지 않는다.
- tokens_per_char: 토큰 수 계산 방식 = 보수적 글자 비율(한국어는 크게 잡는다). use_server_tokenizer 는 서버 토크나이저 사용 옵션(기본 꺼짐,
  이번 작업에서는 값만 두고 글자 비율을 쓴다).
- send_context_option: 요청에 맥락 길이를 명시해야 하는 엔진인지(향후 Ollama num_ctx). 이번에는 값만 두고 보내지 않는다.
- truncation_ratio: 응답의 사용 토큰이 맥락의 이 비율 이상이면 "맥락 한계 도달, 잘림 의심"(조용히 앞을 잘라내는 런타임 대비).
"""
from dataclasses import dataclass


@dataclass(frozen=True)
class EngineProfile:
    name: str = "openai-compat"
    context_tokens: int = 8192
    max_output_tokens: int = 4096
    supports_json_format: bool = True
    tokens_per_char: float = 1.3
    use_server_tokenizer: bool = False
    send_context_option: bool = False
    budget_ratio: float = 0.90
    truncation_ratio: float = 0.98

    @property
    def budget_tokens(self) -> int:
        """프롬프트 + 구간 + 출력 예약 의 허용 합계(맥락의 budget_ratio)."""
        return int(self.context_tokens * self.budget_ratio)

    def output_reserve_tokens(self) -> int:
        """출력 예약. 설정값이 맥락의 1/4 보다 크면 맥락의 1/4(최소 512)로 줄인다: 예약이 너무 크면 고정 지시만으로 맥락이 차서
        구간을 넣을 자리가 없어진다."""
        return max(1, min(self.max_output_tokens, max(512, self.context_tokens // 4)))

    def tokens_of_chars(self, chars: int) -> int:
        return int(chars * self.tokens_per_char) + 1
