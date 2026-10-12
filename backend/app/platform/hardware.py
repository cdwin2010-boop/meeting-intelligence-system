"""GPU 감지(작업 77-1). 설치 시 PC 서버의 GPU 를 확인해 권장 모델 프로파일을 고르는 데 쓴다.

감지 순서: nvidia-smi(인자 목록 호출, shell 미사용, 5초 제한) → 선택적 torch(cuda) 조회 → GPU 없음(CPU 프로파일).
예외를 올리지 않고 감지 방법·경고를 결과에 담는다. 다중 GPU 는 총 VRAM 이 가장 큰 GPU 를 대상으로 하고(단일 GPU 직렬 설계) 나머지는 정보로만 기록한다.
현행 처리 파이프라인에서는 쓰지 않는다(도구·테스트 전용).
"""
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

NVIDIA_SMI_QUERY = ("--query-gpu=name,memory.total,memory.free,driver_version", "--format=csv,noheader,nounits")
DEFAULT_TIMEOUT_SECONDS = 5.0

METHOD_NVIDIA_SMI = "nvidia-smi"
METHOD_TORCH = "torch"
METHOD_NONE = "none"
# PATH 에 nvidia-smi 가 없을 때 시도하는 Windows 기본 설치 위치
_WINDOWS_SMI_FALLBACK = "C:/Program Files/NVIDIA Corporation/NVSMI/nvidia-smi.exe"


@dataclass(frozen=True)
class GpuInfo:
    index: int
    name: str
    total_mb: int
    free_mb: int
    driver_version: str  # torch 로 감지하면 알 수 없어 ""


@dataclass(frozen=True)
class HardwareReport:
    gpus: tuple[GpuInfo, ...]  # 감지된 모든 GPU(입력 순서)
    target: GpuInfo | None  # 모델 프로파일 선택 대상(총 VRAM 이 가장 큰 GPU), 없으면 None
    method: str  # "nvidia-smi" | "torch" | "none"
    warnings: tuple[str, ...] = ()

    @property
    def others(self) -> tuple[GpuInfo, ...]:
        """대상이 아닌 나머지 GPU(정보용)."""
        return tuple(g for g in self.gpus if self.target is None or g.index != self.target.index)


Runner = Callable[[list[str], float], "subprocess.CompletedProcess[str]"]
TorchLoader = Callable[[], Any]


def _run(cmd: list[str], timeout: float) -> "subprocess.CompletedProcess[str]":
    return subprocess.run(  # noqa: S603 — 인자 목록, shell 미사용
        cmd, capture_output=True, text=True, timeout=timeout, check=False, stdin=subprocess.DEVNULL
    )


def _import_torch() -> Any:
    import torch  # 선택적 의존성

    return torch


def parse_nvidia_smi(output: str) -> tuple[list[GpuInfo], list[str]]:
    """`--query-gpu=name,memory.total,memory.free,driver_version --format=csv,noheader,nounits` 출력을 읽는다.
    이름에 쉼표가 있어도 뒤의 세 필드(총·여유 VRAM, 드라이버)를 기준으로 읽는다. 잘못된 줄은 경고만 남기고 건너뛴다."""
    gpus: list[GpuInfo] = []
    warnings: list[str] = []
    for number, raw in enumerate((output or "").splitlines(), start=1):
        line = raw.strip()
        if not line:
            continue
        parts = [p.strip() for p in line.split(",")]
        if len(parts) < 4:
            warnings.append(f"nvidia-smi {number}번째 줄을 읽지 못했습니다(필드 수 부족).")
            continue
        driver, free_text, total_text = parts[-1], parts[-2], parts[-3]
        name = ", ".join(parts[:-3]).strip()
        try:
            total, free = int(float(total_text)), int(float(free_text))
        except ValueError:
            warnings.append(f"nvidia-smi {number}번째 줄의 VRAM 값을 읽지 못했습니다.")
            continue
        if not name or total <= 0 or free < 0 or free > total:
            warnings.append(f"nvidia-smi {number}번째 줄의 값이 올바르지 않습니다.")
            continue
        gpus.append(GpuInfo(index=len(gpus), name=name, total_mb=total, free_mb=free, driver_version=driver))
    return gpus, warnings


def _detect_with_smi(runner: Runner, smi_path: str, timeout: float) -> tuple[list[GpuInfo], list[str]]:
    candidates = [smi_path] + ([_WINDOWS_SMI_FALLBACK] if smi_path == "nvidia-smi" else [])
    done = None
    try:
        for candidate in candidates:
            try:
                done = runner([candidate, *NVIDIA_SMI_QUERY], timeout)
                break
            except FileNotFoundError:
                continue
        if done is None:
            return [], ["nvidia-smi 를 찾을 수 없습니다."]
    except subprocess.TimeoutExpired:
        return [], [f"nvidia-smi 응답이 {timeout:g}초 안에 오지 않았습니다."]
    except Exception as exc:  # noqa: BLE001 — 감지 실패가 설치·점검을 막으면 안 된다
        return [], [f"nvidia-smi 실행에 실패했습니다({type(exc).__name__})."]
    if done.returncode != 0:
        return [], [f"nvidia-smi 가 오류 코드 {done.returncode} 로 끝났습니다."]
    if not (done.stdout or "").strip():
        return [], ["nvidia-smi 출력이 비어 있습니다."]
    gpus, warnings = parse_nvidia_smi(done.stdout)
    if not gpus:
        warnings.append("nvidia-smi 출력에서 GPU 를 읽지 못했습니다.")
    return gpus, warnings


def _detect_with_torch(loader: TorchLoader) -> tuple[list[GpuInfo], list[str]]:
    try:
        torch = loader()
    except Exception as exc:  # noqa: BLE001 — torch 미설치(ImportError)·설치 불량
        return [], [f"torch 를 쓸 수 없습니다({type(exc).__name__})."]
    try:
        if not torch.cuda.is_available():
            return [], ["torch 가 CUDA GPU 를 찾지 못했습니다."]
        gpus: list[GpuInfo] = []
        for index in range(int(torch.cuda.device_count())):
            props = torch.cuda.get_device_properties(index)
            total_mb = int(props.total_memory // (1024 * 1024))
            try:
                free_bytes, _total = torch.cuda.mem_get_info(index)
                free_mb = int(free_bytes // (1024 * 1024))
            except Exception:  # noqa: BLE001 — 여유 VRAM 을 못 읽으면 총량으로 가정
                free_mb = total_mb
            gpus.append(GpuInfo(index=index, name=str(props.name), total_mb=total_mb, free_mb=free_mb, driver_version=""))
        return gpus, []
    except Exception as exc:  # noqa: BLE001
        return [], [f"torch GPU 조회에 실패했습니다({type(exc).__name__})."]


def _pick_target(gpus: list[GpuInfo]) -> GpuInfo | None:
    return max(gpus, key=lambda g: (g.total_mb, g.free_mb, -g.index)) if gpus else None


def detect_gpus(
    *,
    runner: Runner | None = None,
    torch_loader: TorchLoader | None = None,
    smi_path: str = "nvidia-smi",
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
) -> HardwareReport:
    """GPU 를 감지한다. 예외를 올리지 않는다(runner·torch_loader 는 테스트용 주입)."""
    warnings: list[str] = []
    gpus, smi_warnings = _detect_with_smi(runner or _run, smi_path, timeout)
    warnings.extend(smi_warnings)
    method = METHOD_NVIDIA_SMI
    if not gpus:
        gpus, torch_warnings = _detect_with_torch(torch_loader or _import_torch)
        warnings.extend(torch_warnings)
        method = METHOD_TORCH
    if not gpus:
        warnings.append("GPU 를 감지하지 못해 CPU 프로파일로 처리합니다.")
        return HardwareReport(gpus=(), target=None, method=METHOD_NONE, warnings=tuple(warnings))
    target = _pick_target(gpus)
    if len(gpus) > 1:
        warnings.append(f"GPU 가 {len(gpus)}개 감지되어 총 VRAM 이 가장 큰 GPU({target.index}번)만 대상으로 합니다(단일 GPU 직렬 처리).")
    return HardwareReport(gpus=tuple(gpus), target=target, method=method, warnings=tuple(warnings))
