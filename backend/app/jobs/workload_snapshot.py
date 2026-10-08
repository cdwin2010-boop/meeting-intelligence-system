"""업무 처리 현황 전일 집계의 자동 실행(작업 69-3). 새 패키지 없이 FastAPI lifespan 의 백그라운드 asyncio 작업으로 돈다.
- 설정한 시각(WORKLOAD_SNAPSHOT_TIME, APP_TIMEZONE 기준) 이후에 전일 기준일 집계가 없는 고객사를 집계한다.
- 서버가 켜질 때도 첫 확인을 바로 하므로, 꺼져 있던 동안 놓친 전일 집계를 설정 시각이 지났으면 따라잡는다.
- 집계는 별도 스레드에서 동기 실행해 요청 처리를 막지 않는다. 실패는 로그만 남기고 서버를 멈추지 않는다.
- WORKLOAD_SNAPSHOT_ENABLED=false 이거나 APP_ENV=test 이면 시작하지 않는다.
"""
import asyncio
import logging
from datetime import datetime, time, timezone
from zoneinfo import ZoneInfo

from sqlalchemy.orm import sessionmaker

from app.config import settings
from app.services.workload import default_snapshot_date, save_snapshot, tenants_missing_snapshot

log = logging.getLogger("app.jobs.workload_snapshot")

# 다음 확인까지 기다리는 시간(초)
CHECK_INTERVAL_SEC = 600


def scheduled_time() -> time:
    hour, minute = settings.workload_snapshot_time.split(":")
    return time(int(hour), int(minute))


def is_due(now: datetime) -> bool:
    """설정 시각(현지) 이후인가."""
    local = now.astimezone(ZoneInfo(settings.app_timezone)) if now.tzinfo else now.replace(tzinfo=timezone.utc).astimezone(ZoneInfo(settings.app_timezone))
    return local.time() >= scheduled_time()


def run_if_due(factory: sessionmaker, now: datetime | None = None, attempted: set | None = None) -> list[int]:
    """설정 시각이 지났고 전일 기준일 집계가 없는 고객사만 집계한다. 집계한 고객사 id 를 돌려준다.
    attempted: 이 프로세스에서 이미 시도한 (고객사, 기준일). 구성원이 없는 고객사를 10분마다 다시 집계하지 않게 한다."""
    now = now or datetime.now(timezone.utc)
    if not is_due(now):
        return []
    base = default_snapshot_date(now)
    done: list[int] = []
    with factory() as session:
        for tenant_id in tenants_missing_snapshot(session, base):
            if attempted is not None:
                if (tenant_id, base) in attempted:
                    continue
                attempted.add((tenant_id, base))
            try:
                save_snapshot(session, tenant_id, base)
                done.append(tenant_id)
            except Exception as exc:  # noqa: BLE001 — 한 고객사 실패가 다른 고객사·서버를 막지 않게
                session.rollback()
                log.warning("workload snapshot failed (tenant %s): %s", tenant_id, type(exc).__name__)
    return done


async def workload_loop(factory: sessionmaker) -> None:
    attempted: set = set()
    while True:
        try:
            done = await asyncio.to_thread(run_if_due, factory, None, attempted)
            if done:
                log.info("업무 처리 현황 집계 완료: 고객사 %s", done)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            log.warning("workload loop error: %s", type(exc).__name__)
        await asyncio.sleep(CHECK_INTERVAL_SEC)


def should_start() -> bool:
    return settings.workload_snapshot_enabled and settings.app_env != "test"
