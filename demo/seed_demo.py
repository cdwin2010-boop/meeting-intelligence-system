"""데모 영상용 가상 데이터 시드(작업 D-1). 개발 DB 는 건드리지 않고, 인자로 받은 임시 DB 파일에만 쓴다.

사용: python demo/seed_demo.py <임시 db 경로> <출력 json 경로>   (backend 폴더를 작업 폴더로, backend 가상환경 파이썬으로 실행)
- 회사·인물·회의 내용은 모두 가상이다. 실제 AI 를 호출하지 않고, 미리 만든 추출 결과를 그대로 넣는다.
- 출력 json 에는 화면 이동에 쓸 회의 id 와 로그인 토큰이 들어간다(영상에는 나오지 않는다).
"""
import json
import sys
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent / "backend"
sys.path.insert(0, str(BACKEND))

from alembic import command  # noqa: E402
from alembic.config import Config  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import select  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

from app.auth.passwords import hash_password  # noqa: E402
from app.auth.tokens import create_access_token  # noqa: E402
from app.config import settings  # noqa: E402
from app.db import get_session, get_session_factory, make_engine  # noqa: E402
from app.main import app  # noqa: E402
from app.models import Account, ActionItem, Meeting, MeetingParticipant, SourceDocument, Tenant, Transcript, append_event  # noqa: E402
from app.models.common import utcnow  # noqa: E402


def main(db_path: str, out_path: str) -> None:
    url = f"sqlite:///{Path(db_path).as_posix()}"
    cfg = Config(str(BACKEND / "alembic.ini"))
    cfg.set_main_option("sqlalchemy.url", url)
    command.upgrade(cfg, "head")
    engine = make_engine(url)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)

    def _session():
        with factory() as s:
            yield s

    app.dependency_overrides[get_session] = _session
    app.dependency_overrides[get_session_factory] = lambda: factory
    today = date.today()

    def day(offset: int) -> date:
        return today + timedelta(days=offset)

    with factory() as s:
        tenant = Tenant(name="샘플전자(가상)")
        s.add(tenant)
        s.flush()
        people = {}
        for key, login, rank, name in (
            ("exec", "demo-exec", "executive", "정하윤"),
            ("mgr", "demo-mgr", "manager", "박서준"),
            ("kim", "demo-kim", "staff", "김도연"),
            ("lee", "demo-lee", "staff", "이지후"),
            ("choi", "demo-choi", "staff", "최민아"),
        ):
            account = Account(
                tenant_id=tenant.id, login_id=login, password_hash=hash_password("demo-not-shown"), name=name,
                email=f"{login}@sample.invalid", rank=rank,
            )
            s.add(account)
            s.flush()
            people[key] = account
        s.commit()
        tenant_id = tenant.id
        ids = {k: a.id for k, a in people.items()}

    def make_meeting(title, held_offset, status, items, lines, participants):
        with factory() as s:
            doc = SourceDocument(tenant_id=tenant_id, origin="audio_minutes", doc_type="회의록", title=title, registered_by=ids["mgr"])
            s.add(doc)
            s.flush()
            held = datetime.combine(day(held_offset), time(10, 0), tzinfo=timezone(timedelta(hours=9)))
            meeting = Meeting(
                tenant_id=tenant_id, source_document_id=doc.id, title=title, held_at=held, status=status,
                first_created_at=utcnow(), auto_confirm_at=utcnow() + timedelta(days=5),
            )
            s.add(meeting)
            s.flush()
            for key in participants:
                s.add(MeetingParticipant(meeting_id=meeting.id, account_id=ids[key]))
            created = []
            for fields in items:
                item = ActionItem(tenant_id=tenant_id, meeting_id=meeting.id, **fields)
                s.add(item)
                s.flush()
                created.append(item.id)
            s.add(Transcript(tenant_id=tenant_id, meeting_id=meeting.id, full_text="\n".join(lines), stt_provider="fake"))
            append_event(s, tenant_id=tenant_id, entity_type="meeting", entity_id=meeting.id, event_type="meeting.created",
                         actor_account_id=ids["mgr"], payload={})
            s.commit()
            return meeting.id, created

    m1_lines = [
        "[00:00:20] 박서준: 신제품 A 출시 일정 점검을 시작하겠습니다.",
        "[00:01:13] 박서준: 김도연 님, 시제품 검수 결과 보고서를 금요일까지 작성해 주세요.",
        "[00:01:25] 김도연: 네, 금요일까지 작성하겠습니다.",
        "[00:02:40] 박서준: 이지후 님은 협력사 납기 일정을 다시 확인해 주세요.",
        "[00:02:52] 이지후: 알겠습니다, 이번 주 안에 확인하겠습니다.",
        "[00:04:05] 정하윤: 최민아 님, 출시 홍보 문구 초안을 공유해 주세요.",
        "[00:04:18] 최민아: 내일까지 공유하겠습니다.",
        "[00:05:30] 박서준: 품질 기준표 개정안은 담당과 기한을 다음 회의에서 정하겠습니다.",
    ]
    m1, m1_items = make_meeting(
        "신제품 A 출시 준비 점검 회의", -1, "awaiting_confirmation",
        [
            {"title": "시제품 검수 결과 보고서 작성", "assignee_id": ids["kim"], "due_date": day(3), "status": "pending",
             "evidence_start_sec": 73.0, "evidence_quote": "시제품 검수 결과 보고서를 금요일까지 작성해 주세요."},
            {"title": "협력사 납기 일정 재확인", "assignee_id": ids["lee"], "due_date": day(5), "status": "pending",
             "evidence_start_sec": None, "evidence_quote": "협력사 납기 일정을 한 번 더 확인하겠습니다."},
            {"title": "출시 홍보 문구 초안 공유", "assignee_id": ids["choi"], "due_date": day(2), "status": "pending",
             "evidence_start_sec": 245.0, "evidence_quote": "출시 홍보 문구 초안을 공유해 주세요."},
            {"title": "품질 기준표 개정안 검토", "assignee_id": ids["mgr"], "due_date": None, "due_undetermined": True, "status": "pending",
             "evidence_start_sec": 330.0, "evidence_quote": "품질 기준표 개정안은 담당과 기한을 다음 회의에서 정하겠습니다."},
            {"title": "중복 등록된 업무(예시)", "assignee_id": ids["kim"], "due_date": day(4), "status": "pending"},
        ],
        m1_lines, ["exec", "mgr", "kim", "lee", "choi"],
    )
    m2_lines = [
        "[00:00:30] 박서준: 월간 영업 현황 회의를 진행하겠습니다.",
        "[00:01:50] 박서준: 김도연 님, 고객사 견적서를 내일 발송해 주세요.",
        "[00:02:02] 김도연: 네, 내일 발송하겠습니다.",
        "[00:03:10] 박서준: 이지후 님은 영업 보고 양식을 정리해 주세요.",
        "[00:03:20] 이지후: 다음 주까지 정리하겠습니다.",
    ]
    m2, m2_items = make_meeting(
        "월간 영업 현황 회의", -3, "confirmed",
        [
            {"title": "고객사 견적서 발송", "assignee_id": ids["kim"], "due_date": day(1), "status": "confirmed",
             "evidence_start_sec": 110.0, "evidence_quote": "고객사 견적서를 내일 발송해 주세요."},
            {"title": "영업 보고 양식 정리", "assignee_id": ids["lee"], "due_date": day(9), "status": "confirmed",
             "evidence_start_sec": 190.0, "evidence_quote": "영업 보고 양식을 정리해 주세요."},
            {"title": "분기 목표 수정안 제출", "assignee_id": ids["choi"], "due_date": None, "due_undetermined": True, "status": "confirmed"},
        ],
        m2_lines, ["exec", "mgr", "kim", "lee", "choi"],
    )
    m3_lines = ["[00:00:15] 박서준: 협력사 일정 협의 회의입니다.", "[00:01:00] 이지후: 일정은 협력사 회신 후 다시 정하겠습니다."]
    m3, _ = make_meeting(
        "협력사 일정 협의 회의", -5, "awaiting_confirmation",
        [{"title": "협력사 회신 확인", "assignee_id": ids["lee"], "due_date": day(7), "status": "pending",
          "evidence_start_sec": 60.0, "evidence_quote": "일정은 협력사 회신 후 다시 정하겠습니다."}],
        m3_lines, ["mgr", "lee"],
    )

    def auth(key: str) -> dict[str, str]:
        return {"Authorization": f"Bearer {create_access_token(ids[key])}"}

    client = TestClient(app)
    # 처리자와 사유가 남는 이력: 업무 삭제(표시만 숨김)와 회의록 보류
    deleted = client.post(f"/api/action-items/{m1_items[4]}/delete", headers=auth("mgr"), json={"reason": "같은 내용이 중복 등록되어 정리"})
    held = client.post(f"/api/meetings/{m3}/hold", headers=auth("mgr"), json={"reason": "협력사 회신 대기로 일정 재협의"})
    if deleted.status_code != 200 or held.status_code != 200:
        raise SystemExit(f"시드 실패: delete={deleted.status_code} hold={held.status_code}")

    Path(out_path).write_text(
        json.dumps(
            {"meetings": {"awaiting": m1, "confirmed": m2, "held": m3}, "items": {"first": m1_items[0], "unverified": m1_items[1]},
             "tokens": {key: create_access_token(ids[key]) for key in ids}, "today": today.isoformat()},
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    engine.dispose()
    print("seed ok")


if __name__ == "__main__":
    if len(sys.argv) != 3:
        raise SystemExit("사용: python demo/seed_demo.py <임시 db 경로> <출력 json 경로>")
    main(sys.argv[1], sys.argv[2])
