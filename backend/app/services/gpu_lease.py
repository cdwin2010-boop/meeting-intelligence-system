"""GPU 사용권(작업 89-1): 워커(STT·추출)와 회의록 질의가 GPU 를 겹쳐 쓰지 않게 하는 만료형 점유 표식.

- 자원은 "gpu" 1개, 행 1개(gpu_leases). 점유 중 = 행이 있고 만료 시각이 지나지 않음. 프로세스가 죽어도 만료 시각이 지나면 스스로 풀린다.
- acquire 는 한 문장의 조건부 UPDATE/INSERT 로 원자적으로 잡는다(두 프로세스가 동시에 잡을 수 없다). 같은 점유자·같은 ref 의 재획득은 연장이다.
- 워커는 질의가 점유 중이면 최대 GPU_LEASE_WAIT_SECONDS 기다린 뒤 진행한다(질의는 짧고 만료가 있어 STT 를 막지 않는다).
  질의는 워커가 점유 중이면 기다리지 않고 503 + Retry-After 로 돌려보낸다.
- 질문·답변 같은 내용은 이 표에 쓰지 않는다.
"""
import logging
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import insert, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from app.config import settings
from app.models.gpu_lease import GpuLease

log = logging.getLogger("app.gpu_lease")

RESOURCE = "gpu"
HOLDER_WORKER = "worker"
HOLDER_QA = "qa"


@dataclass(frozen=True)
class LeaseInfo:
    holder: str
    ref_id: int | None
    expires_at: datetime

    def retry_after_seconds(self, now: datetime | None = None) -> int:
        remaining = (self.expires_at - (now or datetime.now(timezone.utc))).total_seconds()
        return max(1, int(remaining) + 1)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def current(factory: sessionmaker) -> LeaseInfo | None:
    """지금 유효한(만료되지 않은) 점유. 없으면 None."""
    with factory() as session:
        row = session.get(GpuLease, RESOURCE)
        if row is None or row.expires_at <= _now():
            return None
        return LeaseInfo(row.holder, row.ref_id, row.expires_at)


def acquire(factory: sessionmaker, holder: str, ref_id: int | None, ttl_seconds: float) -> tuple[bool, LeaseInfo | None]:
    """잡으면 (True, None), 다른 점유자가 유효하게 쥐고 있으면 (False, 그 점유 정보)."""
    now = _now()
    expires = now + timedelta(seconds=ttl_seconds)
    with factory() as session:
        # 만료됐거나 같은 점유자(+같은 ref)의 행만 갱신한다: 조건과 갱신이 한 문장이라 경쟁이 없다
        result = session.execute(
            update(GpuLease)
            .where(
                GpuLease.resource == RESOURCE,
                (GpuLease.expires_at <= now) | ((GpuLease.holder == holder) & (GpuLease.ref_id == ref_id if ref_id is not None else GpuLease.ref_id.is_(None))),
            )
            .values(holder=holder, ref_id=ref_id, acquired_at=now, expires_at=expires)
        )
        if result.rowcount == 1:
            session.commit()
            return True, None
        row = session.get(GpuLease, RESOURCE)
        if row is None:
            try:
                session.execute(insert(GpuLease).values(resource=RESOURCE, holder=holder, ref_id=ref_id, acquired_at=now, expires_at=expires))
                session.commit()
                return True, None
            except IntegrityError:  # 동시에 다른 프로세스가 먼저 만들었다
                session.rollback()
                row = session.get(GpuLease, RESOURCE)
        if row is None:
            return False, None
        session.rollback()
        return False, LeaseInfo(row.holder, row.ref_id, row.expires_at)


def release(factory: sessionmaker, holder: str, ref_id: int | None) -> None:
    """내가 쥔 점유만 푼다(만료 시각을 지금으로 당긴다. 다른 점유자의 행은 건드리지 않는다)."""
    with factory() as session:
        stmt = update(GpuLease).where(GpuLease.resource == RESOURCE, GpuLease.holder == holder)
        stmt = stmt.where(GpuLease.ref_id == ref_id) if ref_id is not None else stmt.where(GpuLease.ref_id.is_(None))
        session.execute(stmt.values(expires_at=_now() - timedelta(seconds=1)))
        session.commit()


def acquire_waiting(factory: sessionmaker, holder: str, ref_id: int | None, ttl_seconds: float, wait_seconds: float,
                    sleep=time.sleep) -> bool:
    """워커용: 잡을 때까지 최대 wait_seconds 기다린다(1초 간격). 끝내 못 잡으면 False(호출부가 로그만 남기고 진행한다)."""
    deadline = time.monotonic() + max(wait_seconds, 0)
    while True:
        ok, _info = acquire(factory, holder, ref_id, ttl_seconds)
        if ok:
            return True
        if time.monotonic() >= deadline:
            return False
        sleep(1.0)


def worker_ttl() -> float:
    return float(settings.gpu_lease_worker_ttl_seconds)
