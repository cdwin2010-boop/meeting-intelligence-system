"""워커 자동 재기동 감독(작업 A-1). `python worker_supervisor.py` 로 워커(worker.py)를 자식 프로세스로 띄우고 지켜본다.

- 정상 종료(종료 코드 0, 종료 신호로 멈춘 경우 포함)면 재기동하지 않고 끝낸다.
- 비정상 종료면 WORKER_RESTART_DELAY_SEC 뒤 다시 띄우되, 창(WORKER_RESTART_WINDOW_SEC) 안의 재기동이 WORKER_RESTART_MAX 번에
  이르면 더 이상 띄우지 않고 중단한다(무한 재시도 금지). 중단과 재기동 기록은 상태 파일(WORKER_SUPERVISOR_STATE_PATH)과 로그에 남는다.
- 기록에는 종료 코드·시각·횟수만 남긴다(전사 본문·경로·주소·키 없음). 상태 파일은 모니터링 API(읽기 전용)가 읽는다.
- 멈춘 작업의 복구(재대기·실패 처리)는 워커가 시작할 때 스스로 한다(worker_loop.Worker.startup).
"""
import json
import logging
import os
import subprocess
import sys
import tempfile
import time
from collections import deque
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path

from app.config import settings

log = logging.getLogger("app.worker_supervisor")

STATUS_RUNNING = "running"
STATUS_STOPPED = "stopped"  # 정상 종료로 감독을 마침
STATUS_LIMIT = "limit_exceeded"  # 재기동 한도를 넘겨 중단
EXIT_LIMIT_EXCEEDED = 3


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def state_file() -> Path:
    path = settings.worker_supervisor_state_path
    return path if path.is_absolute() else Path(__file__).resolve().parents[1] / path


def _write_state(path: Path, state: dict) -> None:
    """원자적으로 교체한다. 기록 실패가 감독을 멈추면 안 되므로 예외는 로그만 남긴다."""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".worker_supervisor_", suffix=".tmp")
        with os.fdopen(fd, "w", encoding="utf-8") as out:
            json.dump(state, out, ensure_ascii=False)
        os.replace(tmp, path)
    except OSError as exc:
        log.warning("worker supervisor state write failed: %s", type(exc).__name__)


def _spawn_worker() -> int:
    """worker.py 를 자식 프로세스로 실행하고 종료 코드를 돌려준다(블로킹)."""
    backend_dir = Path(__file__).resolve().parents[1]
    return subprocess.run([sys.executable, "worker.py"], cwd=str(backend_dir), check=False, stdin=subprocess.DEVNULL).returncode  # noqa: S603


def supervise(
    spawn: Callable[[], int] = _spawn_worker,
    *,
    max_restarts: int | None = None,
    window_sec: float | None = None,
    delay_sec: float | None = None,
    path: Path | None = None,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
) -> str:
    """감독 루프. 돌려주는 값: STATUS_STOPPED | STATUS_LIMIT. spawn 은 워커 1회 실행의 종료 코드를 돌려주는 호출(테스트용 주입)."""
    max_restarts = settings.worker_restart_max if max_restarts is None else max_restarts
    window_sec = settings.worker_restart_window_sec if window_sec is None else window_sec
    delay_sec = settings.worker_restart_delay_sec if delay_sec is None else delay_sec
    target = path or state_file()
    restarts: deque[float] = deque()  # 창 안의 재기동 시각(monotonic)
    state = {
        "status": STATUS_RUNNING, "restartCount": 0, "recentRestarts": 0, "maxRestarts": max_restarts, "windowSec": window_sec,
        "lastExitCode": None, "lastExitAt": None, "lastRestartAt": None, "limitExceededAt": None, "startedAt": _now_iso(), "updatedAt": _now_iso(),
    }
    _write_state(target, state)
    while True:
        started = clock()
        code = spawn()
        ended = clock()
        state.update(lastExitCode=code, lastExitAt=_now_iso(), updatedAt=_now_iso())
        if code == 0:
            state["status"] = STATUS_STOPPED
            _write_state(target, state)
            log.info("worker exited normally; supervisor stops")
            return STATUS_STOPPED
        # 오래 안정적으로 돈 뒤의 종료는 새 창에서 센다
        while restarts and ended - restarts[0] > window_sec:
            restarts.popleft()
        if started and ended - started > window_sec:
            restarts.clear()
        if len(restarts) >= max_restarts:
            state.update(status=STATUS_LIMIT, limitExceededAt=_now_iso(), recentRestarts=len(restarts))
            _write_state(target, state)
            log.error("worker exited abnormally (code %s); restart limit %d reached, supervisor stops", code, max_restarts)
            return STATUS_LIMIT
        restarts.append(ended)
        state.update(restartCount=state["restartCount"] + 1, recentRestarts=len(restarts), lastRestartAt=_now_iso(), updatedAt=_now_iso())
        _write_state(target, state)
        log.warning("worker exited abnormally (code %s); restart %d/%d in %gs", code, len(restarts), max_restarts, delay_sec)
        sleep(delay_sec)


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    try:
        result = supervise()
    except KeyboardInterrupt:
        log.info("supervisor interrupted")
        return 0
    return 0 if result == STATUS_STOPPED else EXIT_LIMIT_EXCEEDED
