"""업무 처리 현황 전일 집계를 수동으로 실행한다. 기본은 dry-run(저장하지 않고 건수 요약만 출력), --apply 를 붙여야 저장한다.
같은 기준일을 다시 저장하면 그 기준일 행을 지우고 다시 쓴다(멱등).

사용법 (backend 폴더에서, 먼저 `alembic upgrade head`):
    python scripts/run_workload_snapshot.py --tenant "고객사A"                      # 전일 기준 dry-run
    python scripts/run_workload_snapshot.py --tenant "고객사A" --date 2026-10-06 --apply
"""
import argparse
import sys
from datetime import date
from pathlib import Path

# backend 폴더를 import 경로에 추가(scripts/ 에서 실행해도 app 패키지를 찾도록)
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from app.db import SessionLocal  # noqa: E402
from app.models import Tenant  # noqa: E402
from app.services.workload import build_snapshot, default_snapshot_date, save_snapshot  # noqa: E402


def run(session: Session, tenant_name: str, snapshot_date: date, apply: bool) -> list[str]:
    tenant = session.scalar(select(Tenant).where(Tenant.name == tenant_name))
    if tenant is None:
        raise SystemExit(f"없는 고객사입니다: {tenant_name}")
    rows = build_snapshot(session, tenant.id, snapshot_date)
    session.rollback()  # 계산만 한 행은 저장하지 않는다
    lines = [
        f"기준일 {snapshot_date} · 행 {len(rows)}건 · 계정 {len({r.account_id for r in rows})}명",
        f"완료 {sum(r.completed_count for r in rows)} · 진행 중 {sum(r.in_progress_count for r in rows)} · "
        f"지연 {sum(r.overdue_count for r in rows)} · D-3 임박 {sum(r.due_soon_count for r in rows)} (부서 행 합계, 겸직 중복 포함)",
    ]
    if apply:
        saved = save_snapshot(session, tenant.id, snapshot_date)
        lines.append(f"저장했습니다: {saved}건")
    else:
        lines.append("dry-run: 저장하지 않았습니다. 저장하려면 --apply 를 붙이세요.")
    return lines


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--tenant", required=True, help="고객사 이름")
    parser.add_argument("--date", type=date.fromisoformat, default=None, help="집계 기준일(YYYY-MM-DD, 기본 한국 시간 전일)")
    parser.add_argument("--apply", action="store_true", help="저장(없으면 dry-run)")
    args = parser.parse_args(argv)
    with SessionLocal() as session:
        for line in run(session, args.tenant, args.date or default_snapshot_date(), args.apply):
            print(line)
    return 0


if __name__ == "__main__":
    sys.exit(main())
