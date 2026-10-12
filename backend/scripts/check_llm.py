"""로컬 LLM 서버 연결 확인 도구(작업 81-1). GET {LOCAL_LLM_BASE_URL}/models 만 호출한다.
프롬프트·회의 내용을 보내지 않고 추출 호출도 하지 않는다. 주소는 일부만 마스킹해 출력하고 키는 출력하지 않는다.
외부 호스트는 LOCAL_LLM_ALLOW_EXTERNAL_HOST 가 참일 때만 연결한다.

사용법 (backend 폴더에서): python scripts/check_llm.py
종료 코드: 0 = 연결·인증 정상, 1 = 오류(분류를 출력), 2 = 설정 없음
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import settings  # noqa: E402
from app.pipeline.local_llm import (  # noqa: E402
    TRANSIENT, LocalLlmError, classify_host, host_of, list_models, mask_host,
)


def main(out=None) -> int:
    stream = out or sys.stdout
    base_url = settings.local_llm_base_url
    if not base_url:
        print("LOCAL_LLM_BASE_URL 이 설정되어 있지 않습니다.", file=stream)
        return 2
    host = host_of(base_url)
    model = settings.local_llm_model.strip()
    print(f"서버: {mask_host(host)} ({classify_host(host)})", file=stream)
    print(f"설정 모델: {model or '(미설정)'}", file=stream)
    try:
        models = list_models(
            base_url, settings.local_llm_api_key.get_secret_value(), settings.extract_llm_timeout_seconds,
            settings.local_llm_allow_external_host,
        )
    except LocalLlmError as exc:
        kind = "일시 오류(transient)" if exc.kind == TRANSIENT else "영구 오류(permanent)"
        print(f"연결 확인 실패: {kind} / {exc.reason} — {exc}", file=stream)
        return 1
    print(f"연결·인증: 정상 (모델 {len(models)}개 확인)", file=stream)
    if model:
        print("설정 모델 존재: " + ("예" if model in models else "아니오(서버 모델 목록에 없음)"), file=stream)
    else:
        print("설정 모델 존재: 확인 불가(LOCAL_LLM_MODEL 미설정)", file=stream)
    return 0 if (not model or model in models) else 1


if __name__ == "__main__":
    raise SystemExit(main())
