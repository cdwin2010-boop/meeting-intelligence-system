"""Ollama 서버 연결·모델 확인 도구(작업 L-2). GET {OLLAMA_BASE_URL}/api/tags 만 호출한다(조회 전용).
프롬프트·회의 내용을 보내지 않고 모델 로드·추출 호출도 하지 않는다. 주소는 일부만 마스킹해 출력한다.
외부 호스트는 LOCAL_LLM_ALLOW_EXTERNAL_HOST 가 참일 때만 연결한다.

사용법 (backend 폴더에서): python scripts/check_ollama.py
종료 코드: 0 = 서버·모델 정상, 1 = 오류(ERR-LLM-* 코드 출력), 2 = 설정 없음
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import settings  # noqa: E402
from app.pipeline.local_llm import classify_host, host_of, mask_host  # noqa: E402
from app.pipeline.ollama_llm import list_ollama_models, model_installed  # noqa: E402
from app.pipeline.local_llm import LocalLlmError  # noqa: E402
from app.pipeline.ollama_llm import _as_ollama  # noqa: E402


def main(out=None) -> int:
    stream = out or sys.stdout
    base_url = settings.ollama_base_url.strip()
    model = settings.ollama_model.strip()
    if not base_url:
        print("OLLAMA_BASE_URL 이 설정되어 있지 않습니다.", file=stream)
        return 2
    host = host_of(base_url)
    print(f"서버: {mask_host(host)} ({classify_host(host)})", file=stream)
    print(f"설정 모델: {model or '(미설정)'}  num_ctx={settings.ollama_num_ctx}", file=stream)
    try:
        installed = list_ollama_models(base_url, min(settings.ollama_timeout_sec, 15.0), settings.local_llm_allow_external_host)
    except LocalLlmError as exc:
        err = _as_ollama(exc)
        print(f"연결 확인 실패: {err.job_error_code} / {err.reason} — {err}", file=stream)
        return 1
    print(f"서버 연결: 정상 (설치된 모델 {len(installed)}개)", file=stream)
    if not model:
        print("OLLAMA_MODEL 이 비어 있어 모델 존재 여부는 확인하지 않았습니다.", file=stream)
        return 2
    if model_installed(model, installed):
        print("설정 모델 존재: 예", file=stream)
        return 0
    print("설정 모델 존재: 아니오 — ERR-LLM-NO-MODEL (ollama pull 로 받아 두세요)", file=stream)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
