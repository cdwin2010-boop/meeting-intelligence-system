"""시스템 상태 점검(작업 A-1 a·b): 디스크 여유 공간 경고·업로드 거절, GPU 상태 점검(예외 없음, 확인 불가)."""
import subprocess
import sys
from collections import namedtuple

import pytest

from app.config import Settings, settings
from app.services import system_health as health
from tests.test_upload_processing import add_account, auth, env, upload  # noqa: F401  (env 는 픽스처)

Usage = namedtuple("Usage", "total used free")
GB = 1024 * 1024 * 1024


def fake_usage(free_mb: int, total_mb: int = 500_000):
    return lambda path: Usage(total_mb * 1024 * 1024, (total_mb - free_mb) * 1024 * 1024, free_mb * 1024 * 1024)


@pytest.fixture
def thresholds(monkeypatch):
    monkeypatch.setattr(settings, "disk_warn_free_mb", 5000)
    monkeypatch.setattr(settings, "disk_min_free_mb", 1000)


# ---------------- a) 디스크 ----------------
@pytest.mark.parametrize("free_mb, expected", [(100_000, "ok"), (4_999, "warning"), (5_000, "ok"), (999, "critical"), (0, "critical")])
def test_disk_status_levels_follow_settings(monkeypatch, thresholds, free_mb, expected):
    monkeypatch.setattr(health.shutil, "disk_usage", fake_usage(free_mb))
    items = health.disk_status()
    assert [i.name for i in items] == ["업로드 저장 폴더", "데이터베이스"]
    assert items[0].status == expected and items[0].free_mb == free_mb


def test_disk_status_unknown_when_unreadable_and_never_raises(monkeypatch, thresholds):
    def boom(path):
        raise OSError("읽을 수 없음")

    monkeypatch.setattr(health.shutil, "disk_usage", boom)
    items = health.disk_status()
    assert all(i.status == "unknown" and i.free_mb is None and i.message == "확인 불가" for i in items)
    assert health.upload_block_reason() is None  # 점검 불가는 업로드를 막지 않는다


def test_upload_is_rejected_with_clear_reason_when_space_is_short(env, thresholds, monkeypatch):
    monkeypatch.setattr(health.shutil, "disk_usage", fake_usage(10))
    staff = add_account(env["factory"], "고객사A", "stf", "staff", name="김담당")
    res = upload(env, staff)
    assert res.status_code == 507
    assert "저장 공간이 부족" in res.json()["detail"] and "관리자에게 문의" in res.json()["detail"]
    assert env["queued"] == []  # 회의록·작업이 만들어지지 않는다


def test_upload_passes_when_space_is_enough_or_only_warning(env, thresholds, monkeypatch):
    staff = add_account(env["factory"], "고객사A", "stf", "staff", name="김담당")
    monkeypatch.setattr(health.shutil, "disk_usage", fake_usage(2_000))  # 경고 수준: 거절하지 않는다
    assert upload(env, staff).status_code == 202


def test_disk_thresholds_come_from_settings_not_code(monkeypatch):
    monkeypatch.setenv("DISK_WARN_FREE_MB", "777")
    monkeypatch.setenv("DISK_MIN_FREE_MB", "55")
    loaded = Settings()
    assert loaded.disk_warn_free_mb == 777 and loaded.disk_min_free_mb == 55
    monkeypatch.setenv("DISK_MIN_FREE_MB", "999")
    with pytest.raises(ValueError):
        Settings()  # 최소 기준이 경고 기준보다 클 수 없다


# ---------------- b) GPU ----------------
def run_ok(cmd, timeout):
    return subprocess.CompletedProcess(cmd, 0, stdout="NVIDIA RTX 4070, 12282, 9000, 551.23\n", stderr="")


def test_gpu_status_reads_vram_usage():
    state = health.gpu_status(runner=run_ok)
    assert state.status == "ok" and state.label == "정상"
    [gpu] = state.gpus
    assert gpu.name == "NVIDIA RTX 4070" and gpu.total_mb == 12282 and gpu.free_mb == 9000
    assert gpu.used_mb == 3282 and gpu.used_percent == 27


@pytest.mark.parametrize(
    "runner",
    [
        lambda cmd, timeout: (_ for _ in ()).throw(FileNotFoundError()),
        lambda cmd, timeout: (_ for _ in ()).throw(subprocess.TimeoutExpired(cmd, timeout)),
        lambda cmd, timeout: (_ for _ in ()).throw(RuntimeError("이상한 오류")),
        lambda cmd, timeout: subprocess.CompletedProcess(cmd, 9, stdout="", stderr="오류"),
        lambda cmd, timeout: subprocess.CompletedProcess(cmd, 0, stdout="알 수 없는 출력", stderr=""),
    ],
)
def test_gpu_status_is_unknown_without_exception(runner):
    state = health.gpu_status(runner=runner)
    assert state.status == "unknown" and state.label == "확인 불가" and state.gpus == ()


def test_gpu_status_does_not_import_torch(monkeypatch):
    """모니터링은 torch 를 불러오지 않는다(GPU 메모리를 쓰지 않아 STT·로컬 LLM 동시 구동 금지 규칙에 영향이 없다)."""
    monkeypatch.delitem(sys.modules, "torch", raising=False)
    health.gpu_status(runner=lambda cmd, timeout: subprocess.CompletedProcess(cmd, 1, stdout="", stderr=""))
    assert "torch" not in sys.modules
