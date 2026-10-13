"""워커 자동 재기동 감독(작업 A-1 c): 제한된 횟수만 재기동하고 한도 초과 시 중단·기록한다. 실제 프로세스를 띄우지 않는다."""
import json

import pytest

from app import worker_supervisor as sup
from app.config import settings
from app.services import system_health as health


class Script:
    """종료 코드를 차례로 돌려주는 가짜 워커 실행. 시계는 호출마다 elapsed 만큼 흐른다."""

    def __init__(self, codes, run_seconds=1.0):
        self.codes = list(codes)
        self.run_seconds = run_seconds
        self.calls = 0
        self.now = 1000.0
        self.sleeps: list[float] = []

    def spawn(self):
        self.calls += 1
        assert self.calls <= 50, "무한 재시도 의심"
        self.now += self.run_seconds
        return self.codes.pop(0)

    def clock(self):
        return self.now

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += seconds


def run(script, tmp_path, **kw):
    path = tmp_path / "state.json"
    result = sup.supervise(
        script.spawn, path=path, sleep=script.sleep, clock=script.clock,
        max_restarts=kw.get("max_restarts", 3), window_sec=kw.get("window_sec", 600), delay_sec=kw.get("delay_sec", 5),
    )
    return result, json.loads(path.read_text(encoding="utf-8"))


def test_normal_exit_is_not_restarted(tmp_path):
    script = Script([0])
    result, state = run(script, tmp_path)
    assert result == sup.STATUS_STOPPED and script.calls == 1 and script.sleeps == []
    assert state["status"] == "stopped" and state["restartCount"] == 0 and state["lastExitCode"] == 0


def test_abnormal_exit_is_restarted_then_stops_normally(tmp_path):
    script = Script([1, -9, 0])
    result, state = run(script, tmp_path)
    assert result == sup.STATUS_STOPPED and script.calls == 3
    assert script.sleeps == [5, 5]  # 설정한 대기만큼 쉬고 다시 띄운다
    assert state["restartCount"] == 2 and state["status"] == "stopped"


def test_restart_limit_stops_and_records(tmp_path, caplog):
    script = Script([1] * 10)
    result, state = run(script, tmp_path, max_restarts=3)
    assert result == sup.STATUS_LIMIT
    assert script.calls == 4  # 최초 1회 + 재기동 3회, 이후 다시 띄우지 않는다
    assert state["status"] == "limit_exceeded" and state["restartCount"] == 3 and state["limitExceededAt"]
    assert state["lastExitCode"] == 1 and state["maxRestarts"] == 3
    assert any("restart limit" in r.getMessage() for r in caplog.records)


def test_zero_max_restarts_never_restarts(tmp_path):
    script = Script([2, 2])
    result, state = run(script, tmp_path, max_restarts=0)
    assert result == sup.STATUS_LIMIT and script.calls == 1 and state["restartCount"] == 0


def test_crash_after_long_stable_run_counts_in_new_window(tmp_path):
    # 창(600초)보다 오래 안정적으로 돈 뒤의 종료는 횟수를 새로 센다 → 한도에 걸리지 않는다
    script = Script([1, 1, 1, 1, 1, 0], run_seconds=700.0)
    result, state = run(script, tmp_path, max_restarts=2)
    assert result == sup.STATUS_STOPPED and script.calls == 6


def test_state_has_no_secrets_and_is_readable_by_health(tmp_path, monkeypatch):
    script = Script([1, 0])
    path = tmp_path / "sup.json"
    sup.supervise(script.spawn, path=path, sleep=script.sleep, clock=script.clock, max_restarts=3, window_sec=600, delay_sec=1)
    monkeypatch.setattr(settings, "worker_supervisor_state_path", path)
    state = health.supervisor_state()
    assert state["restartCount"] == 1 and set(state) >= {"status", "lastExitCode", "lastRestartAt", "maxRestarts"}
    path.write_text("깨진 파일", encoding="utf-8")
    assert health.supervisor_state() is None  # 깨진 파일은 예외 없이 "기록 없음"


def test_settings_for_restart_limits_are_configurable(monkeypatch):
    from app.config import Settings

    monkeypatch.setenv("WORKER_RESTART_MAX", "7")
    monkeypatch.setenv("WORKER_RESTART_WINDOW_SEC", "90")
    loaded = Settings()
    assert loaded.worker_restart_max == 7 and loaded.worker_restart_window_sec == 90
    monkeypatch.setenv("WORKER_RESTART_MAX", "-1")
    with pytest.raises(ValueError):
        Settings()
