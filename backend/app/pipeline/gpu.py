"""GPU 메모리 직렬 해제(작업 75-1): STT 가 끝난 직후, 추출 단계가 시작되기 전에 부른다(STT 와 LLM 을 동시에 올리지 않기 위해).
torch 가 없거나 CUDA 를 못 쓰거나 오류가 나도 예외를 올리지 않는다(로그만 남긴다)."""
import gc
import logging

log = logging.getLogger("app.gpu")


def release_gpu_memory() -> bool:
    """gc.collect() 후 CUDA 캐시를 비운다. 실제로 비웠으면 True."""
    gc.collect()
    try:
        import torch  # 선택적 의존성: 설치돼 있지 않아도 동작한다
    except Exception as exc:  # noqa: BLE001 — ImportError 외에 설치 불량도 무시
        log.debug("torch 를 쓸 수 없어 GPU 메모리 해제를 건너뜁니다: %s", type(exc).__name__)
        return False
    try:
        if not torch.cuda.is_available():
            return False
        torch.cuda.empty_cache()
        return True
    except Exception as exc:  # noqa: BLE001 — 해제 실패가 처리 전체를 막으면 안 된다
        log.warning("GPU 메모리 해제 실패(무시): %s", type(exc).__name__)
        return False
