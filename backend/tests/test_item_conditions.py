"""업무 상태 조건 함수(app/models/item_conditions.py) 단위 테스트와, 새 모듈 밖에서 업무 상태를 쿼리로 직접 비교하지 않는지 검사하는 가드."""
import re
from datetime import datetime, timezone
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.orm import aliased, sessionmaker

from app.db import Base, make_engine
from app.models import Account, ActionItem, Meeting, SourceDocument, Tenant
from app.models.item_conditions import (
    confirmed_item, deleted_item, inactive_item, not_deleted_item, open_item, pending_item,
)


@pytest.fixture()
def session(tmp_path):
    engine = make_engine(f"sqlite:///{(tmp_path / 'c.db').as_posix()}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    with factory() as s:
        tenant = Tenant(name="t")
        s.add(tenant)
        s.flush()
        account = Account(tenant_id=tenant.id, login_id="a", password_hash="x", name="가", email="a@example.com", rank="manager")
        s.add(account)
        s.flush()
        doc = SourceDocument(tenant_id=tenant.id, origin="audio_minutes", doc_type="회의록", title="m", registered_by=account.id)
        s.add(doc)
        s.flush()
        meeting = Meeting(tenant_id=tenant.id, source_document_id=doc.id, title="m", held_at=datetime(2026, 10, 1, tzinfo=timezone.utc))
        s.add(meeting)
        s.flush()
        for status in ("pending", "confirmed", "closed", "deleted"):
            s.add(ActionItem(tenant_id=tenant.id, meeting_id=meeting.id, title=status, status=status))
        s.commit()
        yield s
    engine.dispose()


def _titles(session, condition, entity=ActionItem):
    return sorted(session.scalars(select(entity.title).where(condition)))


@pytest.mark.parametrize(
    ("condition", "expected"),
    [
        (not_deleted_item, ["closed", "confirmed", "pending"]),
        (deleted_item, ["deleted"]),
        (open_item, ["confirmed", "pending"]),
        (inactive_item, ["closed", "deleted"]),
        (pending_item, ["pending"]),
        (confirmed_item, ["confirmed"]),
    ],
)
def test_condition_selects_expected_rows(session, condition, expected):
    assert _titles(session, condition()) == expected


def test_conditions_work_with_aliased_item(session):
    alias = aliased(ActionItem)
    assert _titles(session, open_item(alias), alias) == ["confirmed", "pending"]


# ---------------- 가드 ----------------
# 업무 상태를 쿼리 조건으로 직접 비교하는 코드(ActionItem.status == / != / in_ / not_in)는 item_conditions.py 밖에 둘 수 없다.
# 허용 목록: 없음(인스턴스의 item.status 비교는 파이썬 판정이라 해당하지 않는다. 상태 대입도 해당하지 않는다).
_DIRECT = re.compile(r"ActionItem\.status\s*(==|!=|\.in_|\.not_in|\.notin_|\.is_)")
_ALLOWED_FILES: set[str] = {"models/item_conditions.py"}


def test_no_direct_item_status_comparison_outside_conditions_module():
    root = Path(__file__).resolve().parent.parent / "app"
    offenders = []
    for path in root.rglob("*.py"):
        rel = path.relative_to(root).as_posix()
        if rel in _ALLOWED_FILES:
            continue
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            if _DIRECT.search(line):
                offenders.append(f"{rel}:{number}: {line.strip()}")
    assert offenders == [], "업무 상태 직접 비교는 app/models/item_conditions.py 함수를 쓰세요:\n" + "\n".join(offenders)
