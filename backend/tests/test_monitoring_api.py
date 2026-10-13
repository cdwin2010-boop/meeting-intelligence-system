"""모니터링 API(작업 A-1 d): 읽기 전용, 관리자 이상만, 점검 실패는 "확인 불가"."""
import json
import subprocess
from collections import namedtuple

import pytest
from sqlalchemy import select

from app.config import settings
from app.models import Event, Job
from app.services import system_health as health
from tests.test_upload_processing import add_account, auth, env, upload  # noqa: F401  (env 는 픽스처)

Usage = namedtuple("Usage", "total used free")
URL = "/api/admin/monitoring"
REAL_GPU_STATUS = health.gpu_status


def usage(free_mb, total_mb=400_000):
    return lambda path: Usage(total_mb * 1024 * 1024, (total_mb - free_mb) * 1024 * 1024, free_mb * 1024 * 1024)


@pytest.fixture
def mgr(env):
    return add_account(env["factory"], "고객사A", "mgr", "manager", name="박관리")


@pytest.fixture(autouse=True)
def fixed_environment(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "disk_warn_free_mb", 5000)
    monkeypatch.setattr(settings, "disk_min_free_mb", 1000)
    monkeypatch.setattr(settings, "worker_supervisor_state_path", tmp_path / "no-supervisor.json")
    monkeypatch.setattr(health.shutil, "disk_usage", usage(100_000))
    monkeypatch.setattr(health, "gpu_status", lambda runner=None: health.GpuState("unknown", "확인 불가", (), "GPU 를 확인하지 못했습니다."))


def test_requires_login_and_manager_rank(env, mgr):
    staff = add_account(env["factory"], "고객사A", "stf", "staff", name="김담당")
    assert env["client"].get(URL).status_code == 401
    assert env["client"].get(URL, headers=auth(staff)).status_code == 403
    assert env["client"].get(URL, headers=auth(mgr)).status_code == 200


def test_shape_and_values(env, mgr, monkeypatch):
    monkeypatch.setattr(settings, "processing_mode", "inline")
    body = env["client"].get(URL, headers=auth(mgr)).json()
    assert set(body) == {"generatedAt", "overall", "disk", "gpu", "worker", "queue"}
    assert body["overall"] == "ok"
    assert body["disk"]["warnFreeMb"] == 5000 and body["disk"]["minFreeMb"] == 1000
    assert [d["name"] for d in body["disk"]["items"]] == ["업로드 저장 폴더", "데이터베이스"]
    assert body["disk"]["items"][0]["status"] == "ok" and body["disk"]["items"][0]["freeMb"] == 100_000
    assert body["gpu"] == {"status": "unknown", "label": "확인 불가", "gpus": [], "message": "GPU 를 확인하지 못했습니다."}
    assert body["worker"]["processingMode"] == "inline" and body["worker"]["alive"] is None
    assert body["worker"]["supervisor"]["used"] is False
    assert body["queue"]["pending"] == 0 and body["queue"]["processing"] == 0 and body["queue"]["oldestPendingAt"] is None


def test_disk_warning_and_critical_reflect_in_overall(env, mgr, monkeypatch):
    monkeypatch.setattr(health.shutil, "disk_usage", usage(3000))
    assert env["client"].get(URL, headers=auth(mgr)).json()["overall"] == "warning"
    monkeypatch.setattr(health.shutil, "disk_usage", usage(10))
    assert env["client"].get(URL, headers=auth(mgr)).json()["overall"] == "critical"


def test_gpu_values_are_reported_when_readable(env, mgr, monkeypatch):
    def ok(cmd, timeout):
        return subprocess.CompletedProcess(cmd, 0, stdout="NVIDIA RTX 4070, 12282, 9000, 551.23\n", stderr="")

    monkeypatch.setattr(health, "gpu_status", lambda runner=None: REAL_GPU_STATUS(runner=ok))
    gpu = env["client"].get(URL, headers=auth(mgr)).json()["gpu"]
    assert gpu["status"] == "ok" and gpu["gpus"][0]["usedMb"] == 3282 and gpu["gpus"][0]["usedPercent"] == 27


def test_queue_and_worker_status_in_queue_mode(env, mgr, monkeypatch):
    monkeypatch.setattr(settings, "processing_mode", "queue")
    upload(env, mgr)
    body = env["client"].get(URL, headers=auth(mgr)).json()
    assert body["queue"]["pending"] == 1 and body["queue"]["processing"] == 0 and body["queue"]["oldestPendingAt"]
    assert body["worker"]["alive"] is False  # 생존 신호가 없는데 대기 작업이 있다
    assert body["overall"] == "warning"


def test_supervisor_limit_exceeded_is_critical_and_has_record(env, mgr, tmp_path, monkeypatch):
    path = tmp_path / "sup.json"
    path.write_text(
        json.dumps({
            "status": "limit_exceeded", "restartCount": 3, "recentRestarts": 3, "maxRestarts": 3, "lastExitCode": 1,
            "lastExitAt": "2026-10-14T01:00:00+00:00", "lastRestartAt": "2026-10-14T00:59:00+00:00", "limitExceededAt": "2026-10-14T01:00:00+00:00",
        }),
        encoding="utf-8",
    )
    monkeypatch.setattr(settings, "worker_supervisor_state_path", path)
    body = env["client"].get(URL, headers=auth(mgr)).json()
    sup = body["worker"]["supervisor"]
    assert sup["used"] is True and sup["status"] == "limit_exceeded" and sup["restartCount"] == 3 and sup["lastExitCode"] == 1
    assert body["overall"] == "critical"


def test_read_only_no_side_effects_and_no_paths_or_secrets(env, mgr):
    with env["factory"]() as s:
        before = (s.scalar(select(Event.id).order_by(Event.id.desc())), s.scalar(select(Job.id)))
    text = env["client"].get(URL, headers=auth(mgr)).text
    with env["factory"]() as s:
        after = (s.scalar(select(Event.id).order_by(Event.id.desc())), s.scalar(select(Job.id)))
    assert before == after
    assert str(settings.upload_dir) not in text and "sqlite" not in text.lower()
    for method in ("post", "put", "patch", "delete"):  # 읽기 전용: 다른 방식의 요청은 받지 않는다
        assert getattr(env["client"], method)(URL, headers=auth(mgr)).status_code == 405
