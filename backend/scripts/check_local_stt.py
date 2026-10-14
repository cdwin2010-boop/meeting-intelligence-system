"""로컬 STT 실모델 확인(작업 L-1). 짧은 샘플 1개를 1회만 전사하고 사용 모델·소요 시간·해제 전후 VRAM 을 출력한다.
형님 승인 후에만 실행한다(모델 다운로드가 일어날 수 있음). 사용: python scripts/check_local_stt.py <짧은_음성_파일>
설정은 backend/.env 의 STT_LOCAL_* 를 따른다. 전사문 내용·키·경로는 출력하지 않는다."""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import settings  # noqa: E402
from app.pipeline.local_stt import LocalStt, LocalSttError, config_from_settings  # noqa: E402


def _vram_mb() -> str:
    try:
        import torch

        if torch.cuda.is_available():
            free, total = torch.cuda.mem_get_info()
            return f"{(total - free) // (1024 * 1024)} MB 사용 / {total // (1024 * 1024)} MB"
    except Exception:  # noqa: BLE001
        pass
    try:
        import subprocess

        out = subprocess.run(
            ["nvidia-smi", "--query-gpu=memory.used,memory.total", "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=10,
        ).stdout.strip().splitlines()
        if out:
            used, total = [x.strip() for x in out[0].split(",")]
            return f"{used} MB 사용 / {total} MB"
    except Exception:  # noqa: BLE001
        pass
    return "측정 불가(torch·nvidia-smi 없음)"


def main() -> int:
    if len(sys.argv) != 2 or not Path(sys.argv[1]).is_file():
        print("사용법: python scripts/check_local_stt.py <짧은_음성_파일>")
        return 2
    cfg = config_from_settings(settings)
    print(f"모델={cfg.model_dir and '(로컬 폴더)' or cfg.model_size} device={cfg.device} compute={cfg.compute_type}")
    print(f"전사 전 VRAM: {_vram_mb()}")
    started = time.monotonic()
    try:
        result = LocalStt(cfg).transcribe(Path(sys.argv[1]), "application/octet-stream")
    except LocalSttError as exc:
        print(f"실패: {exc.job_error_code} - {exc}")
        print(f"해제 후 VRAM: {_vram_mb()}")
        return 1
    elapsed = time.monotonic() - started
    lines = result.text.count("\n") + 1
    print(f"성공: {lines}줄, 소요 {elapsed:.1f}초")
    print(f"전사·해제 후 VRAM: {_vram_mb()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
