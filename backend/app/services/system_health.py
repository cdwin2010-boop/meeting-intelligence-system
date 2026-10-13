"""시스템 상태 점검(작업 A-1): 디스크 여유 공간·GPU 상태·워커 자동 재기동 기록. 읽기 전용이며 어떤 함수도 예외를 올리지 않는다.

- 디스크: 업로드 저장 폴더와 DB 파일이 있는 디스크의 여유 공간을 설정값(DISK_WARN_FREE_MB·DISK_MIN_FREE_MB)과 비교한다.
  여유 < MIN 이면 critical(업로드 거절 사유), < WARN 이면 warning. 읽지 못하면 unknown("확인 불가").
- GPU: nvidia-smi 만 읽는다(torch 를 불러오지 않아 GPU 메모리를 쓰지 않는다. STT·로컬 LLM 동시 구동 금지 규칙은 그대로).
  GPU 가 없거나 읽기에 실패하면 unknown("확인 불가").
- 워커 감독 기록: app/worker_supervisor.py 가 남긴 상태 파일을 읽는다(없으면 감독 미사용).
경로·주소·키는 응답에 담지 않는다(표시 이름만).
"""
import json
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sqlalchemy.engine import make_url

from app.config import settings

OK = "ok"
WARNING = "warning"
CRITICAL = "critical"
UNKNOWN = "unknown"
UNKNOWN_LABEL = "확인 불가"
_MB = 1024 * 1024
_SEVERITY = {OK: 0, UNKNOWN: 1, WARNING: 2, CRITICAL: 3}


@dataclass(frozen=True)
class DiskItem:
    name: str  # 표시 이름(경로 아님)
    status: str
    free_mb: int | None
    total_mb: int | None
    message: str


def _nearest_existing(path: Path) -> Path | None:
    current = path
    for _ in range(64):  # 상한: 무한 루프 방지
        if current.exists():
            return current
        if current.parent == current:
            return None
        current = current.parent
    return None


def _disk_item(name: str, path: Path | None) -> DiskItem:
    if path is None:
        return DiskItem(name, UNKNOWN, None, None, UNKNOWN_LABEL)
    try:
        target = _nearest_existing(path.resolve())
        if target is None:
            return DiskItem(name, UNKNOWN, None, None, UNKNOWN_LABEL)
        usage = shutil.disk_usage(target)
    except Exception:  # noqa: BLE001 — 점검 실패가 서비스를 막으면 안 된다
        return DiskItem(name, UNKNOWN, None, None, UNKNOWN_LABEL)
    free_mb, total_mb = usage.free // _MB, usage.total // _MB
    if free_mb < settings.disk_min_free_mb:
        return DiskItem(name, CRITICAL, free_mb, total_mb, f"여유 공간이 {free_mb}MB 로 최소 기준({settings.disk_min_free_mb}MB)보다 적습니다.")
    if free_mb < settings.disk_warn_free_mb:
        return DiskItem(name, WARNING, free_mb, total_mb, f"여유 공간이 {free_mb}MB 로 경고 기준({settings.disk_warn_free_mb}MB)보다 적습니다.")
    return DiskItem(name, OK, free_mb, total_mb, "정상")


def _db_path() -> Path | None:
    try:
        url = make_url(settings.database_url)
        if not url.drivername.startswith("sqlite") or not url.database or url.database == ":memory:":
            return None  # 파일 DB 가 아니면(다른 서버 DB) 이 서버의 디스크로 판단하지 않는다
        return Path(url.database)
    except Exception:  # noqa: BLE001
        return None


def disk_status() -> list[DiskItem]:
    return [_disk_item("업로드 저장 폴더", Path(settings.upload_dir)), _disk_item("데이터베이스", _db_path())]


def upload_block_reason() -> str | None:
    """업로드를 받으면 안 되는 디스크 상태면 사용자에게 보일 사유, 아니면 None. 점검 불가(unknown)는 막지 않는다."""
    for item in disk_status():
        if item.status == CRITICAL:
            return f"서버 저장 공간이 부족하여 업로드할 수 없습니다({item.name}). 관리자에게 문의해 주세요."
    return None


# ---- GPU ----
@dataclass(frozen=True)
class GpuItem:
    name: str
    total_mb: int
    used_mb: int
    free_mb: int
    used_percent: int


@dataclass(frozen=True)
class GpuState:
    status: str  # ok | unknown
    label: str  # "정상" | "확인 불가"
    gpus: tuple[GpuItem, ...]
    message: str


def gpu_status(runner=None) -> GpuState:
    """GPU 유무·VRAM 사용량. 없거나 읽기 실패면 unknown(확인 불가). runner 는 테스트용 주입."""
    try:
        from app.platform.hardware import detect_gpus

        def _no_torch():
            raise RuntimeError("torch 는 불러오지 않는다")

        report = detect_gpus(runner=runner, torch_loader=_no_torch, timeout=settings.gpu_status_timeout_seconds)
        if not report.gpus:
            return GpuState(UNKNOWN, UNKNOWN_LABEL, (), "GPU 를 확인하지 못했습니다.")
        items = tuple(
            GpuItem(g.name, g.total_mb, g.total_mb - g.free_mb, g.free_mb, int(round((g.total_mb - g.free_mb) * 100 / g.total_mb)))
            for g in report.gpus
        )
        return GpuState(OK, "정상", items, "")
    except Exception:  # noqa: BLE001 — 어떤 경우에도 예외를 올리지 않는다
        return GpuState(UNKNOWN, UNKNOWN_LABEL, (), "GPU 를 확인하지 못했습니다.")


# ---- 워커 감독 기록 ----
def _state_file() -> Path:
    path = settings.worker_supervisor_state_path
    return path if path.is_absolute() else Path(__file__).resolve().parents[2] / path


def supervisor_state() -> dict[str, Any] | None:
    """감독 프로세스가 남긴 상태(없으면 None = 감독 미사용). 파일이 깨져 있으면 None."""
    try:
        data = json.loads(_state_file().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def worse(a: str, b: str) -> str:
    return a if _SEVERITY.get(a, 0) >= _SEVERITY.get(b, 0) else b
