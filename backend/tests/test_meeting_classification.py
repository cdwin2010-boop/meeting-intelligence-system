"""작업 66-3: 회의 유형 5종 저장, 회의록의 프로젝트 연결, 프로젝트 참여자 열람 규칙, 프로젝트별 회의록 조회."""
import io

import pytest
from alembic import command
from sqlalchemy import select, text

from app.db import make_engine
from app.models import Account, Event, Meeting, MeetingClassification, MeetingClosure, MeetingHold, Project, ProjectMember, SourceDocument
from app.models.common import MEETING_TYPES, utcnow
from tests.test_meeting_queries import make_meeting
from tests.test_org_projects import call, create, team  # noqa: F401  (team 은 픽스처)
from tests.test_upload_processing import HELD_AT, M4A, _alembic, add_account, auth, env, saved_files  # noqa: F401  (env 는 픽스처)

_BEFORE_CLASSIFICATION = "c7e2a91d4b30"  # meeting_classifications 마이그레이션의 down_revision


def upload(env, account, **fields):
    data = {"title": "분류 회의", "heldAt": HELD_AT, **fields}
    return env["client"].post("/api/meetings/upload", headers=auth(account), data=data, files={"file": ("회의.m4a", M4A, "audio/mp4")})


def classifications(env) -> list[MeetingClassification]:
    with env["factory"]() as s:
        return list(s.scalars(select(MeetingClassification).order_by(MeetingClassification.id)))


def meeting_count(env) -> int:
    with env["factory"]() as s:
        return len(list(s.scalars(select(Meeting.id))))


def classify(env, meeting_id, project_id, *, tenant_id, meeting_type="project"):
    with env["factory"]() as s:
        s.add(MeetingClassification(tenant_id=tenant_id, meeting_id=meeting_id, meeting_type=meeting_type, project_id=project_id))
        s.commit()


@pytest.fixture
def project(env, team):
    """개발팀 부서장(head)이 만든 활성 프로젝트. 참여자: kim(담당자)."""
    return create(env, team, "head", members=[team["kim"]]).json()["id"]


def project_meeting(env, team, project_id, *, registrant="head", day=0, status="awaiting_confirmation", **kwargs) -> int:
    meeting_id = make_meeting(env["factory"], team[registrant], day=day, status=status, **kwargs)
    classify(env, meeting_id, project_id, tenant_id=team["tenant"])
    return meeting_id


# ================= 업로드 입력 =================
def test_upload_without_type_behaves_as_before_and_has_no_row(env, team):
    response = upload(env, team["head"])
    assert response.status_code == 202
    assert classifications(env) == []
    with env["factory"]() as s:
        created = s.scalar(select(Event).where(Event.event_type == "meeting.created"))
        assert created.payload["origin"] == "audio_minutes" and "job_id" in created.payload  # 기존 키 유지
        assert created.payload["meetingType"] is None and created.payload["projectId"] is None


@pytest.mark.parametrize("meeting_type", [t for t in MEETING_TYPES if t != "project"])
def test_each_non_project_type_is_saved(env, team, meeting_type):
    assert upload(env, team["kim"], meetingType=meeting_type).status_code == 202
    [row] = classifications(env)
    assert (row.meeting_type, row.project_id, row.created_by) == (meeting_type, None, team["kim"].id)
    detail = call(env, "GET", team["kim"], f"/api/meetings/{row.meeting_id}").json()
    assert detail["meetingType"] == meeting_type and detail["project"] is None


def test_project_type_is_saved_with_project_and_event_payload(env, team, project):
    response = upload(env, team["kim"], meetingType="project", projectId=str(project))
    assert response.status_code == 202, response.text
    [row] = classifications(env)
    assert (row.meeting_type, row.project_id, row.tenant_id) == ("project", project, team["tenant"])
    with env["factory"]() as s:
        created = s.scalar(select(Event).where(Event.event_type == "meeting.created"))
        assert created.payload["meetingType"] == "project" and created.payload["projectId"] == project
    detail = call(env, "GET", team["kim"], f"/api/meetings/{row.meeting_id}").json()
    assert detail["meetingType"] == "project" and detail["project"] == {"id": project, "name": "신제품 프로젝트"}


def test_upload_validation_errors_leave_nothing_behind(env, team, project):
    with env["factory"]() as s:
        pending = create(env, team, "kim", name="대기 중").json()["id"]
        rejected = create(env, team, "kim", name="반려됨").json()["id"]
    call(env, "POST", team["head"], f"/api/projects/{rejected}/reject", json={"reason": "x"})
    cases = [
        (team["kim"], {"meetingType": "weird"}, 422),
        (team["kim"], {"projectId": str(project)}, 422),  # 유형 없이 projectId
        (team["kim"], {"meetingType": "project"}, 422),  # 프로젝트 유형인데 projectId 없음
        (team["kim"], {"meetingType": "regular", "projectId": str(project)}, 422),
        (team["kim"], {"meetingType": "project", "projectId": "99999"}, 404),
        (team["outsider"], {"meetingType": "project", "projectId": str(project)}, 404),  # 다른 고객사 프로젝트(올리는 사람 기준 고객사에 없음)
        (team["kim"], {"meetingType": "project", "projectId": str(pending)}, 409),
        (team["kim"], {"meetingType": "project", "projectId": str(rejected)}, 409),
        (team["sales"], {"meetingType": "project", "projectId": str(project)}, 403),  # 참여자가 아님
    ]
    for account, fields, expected in cases:
        assert upload(env, account, **fields).status_code == expected, fields
    assert meeting_count(env) == 0 and classifications(env) == [] and saved_files(env) == []


def test_classification_is_saved_in_the_same_transaction(env, team, project, monkeypatch):
    """연결 표 저장이 실패하면 회의록·원천 문서·업로드 파일이 모두 남지 않는다."""
    import app.api.meetings as meetings_api

    def boom(*args, **kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(meetings_api, "save_classification", boom)
    with pytest.raises(RuntimeError):
        upload(env, team["kim"], meetingType="project", projectId=str(project))
    with env["factory"]() as s:
        assert s.scalar(select(Meeting.id)) is None and s.scalar(select(SourceDocument.id)) is None
    assert saved_files(env) == []


# ================= 열람 규칙 =================
def test_project_member_staff_sees_project_meeting_without_attending(env, team, project):
    meeting_id = project_meeting(env, team, project, transcript="화자1: 안녕", items=[{"title": "업무", "assignee_id": team["mgr"].id}])
    base = f"/api/meetings/{meeting_id}"
    # 참여자(kim): 참석자도 담당자도 등록자도 아니지만 본다
    assert call(env, "GET", team["kim"], base).status_code == 200
    assert meeting_id in [m["id"] for m in call(env, "GET", team["kim"], "/api/meetings").json()["items"]]
    assert call(env, "GET", team["kim"], f"{base}/export").status_code == 200
    assert call(env, "GET", team["kim"], f"{base}/history").status_code == 200
    assert call(env, "GET", team["kim"], f"{base}/change-requests").status_code == 200
    assert call(env, "POST", team["kim"], f"{base}/change-requests", json={"comment": "확인 부탁"}).status_code == 201
    assert call(env, "GET", team["kim"], f"{base}/audio-url").json().get("detail") != "회의록을 찾을 수 없습니다"
    # 비참여자(sales)·다른 고객사는 같은 404
    for outsider in ("sales", "loner", "outsider"):
        assert call(env, "GET", team[outsider], base).status_code == 404
        assert call(env, "GET", team[outsider], f"{base}/export").status_code == 404
        assert call(env, "GET", team[outsider], f"{base}/history").status_code == 404
        assert call(env, "GET", team[outsider], f"{base}/change-requests").status_code == 404
        assert call(env, "GET", team[outsider], f"{base}/audio-url").status_code == 404
        assert meeting_id not in [m["id"] for m in call(env, "GET", team[outsider], "/api/meetings").json()["items"]]
    # 관리자 이상은 지금도 회사 전체
    assert call(env, "GET", team["head2"], base).status_code == 200 and call(env, "GET", team["boss"], base).status_code == 200


def test_project_member_sees_auto_confirmed_meeting_in_todos(env, team, project):
    meeting_id = project_meeting(env, team, project, status="confirmed")
    with env["factory"]() as s:
        meeting = s.get(Meeting, meeting_id)
        meeting.confirm_kind, meeting.confirmed_at = "period_elapsed", utcnow()
        s.commit()

    def unread(who):
        body = call(env, "GET", team[who], "/api/me/todos").json()
        return [e["meetingId"] for e in body["unreadAutoConfirmed"]["items"]]

    assert meeting_id in unread("kim") and meeting_id not in unread("sales")


def test_hold_and_deleted_phases_stay_hidden_from_project_member_staff(env, team, project):
    held = project_meeting(env, team, project, day=1)
    gone = project_meeting(env, team, project, day=2)
    visible = project_meeting(env, team, project, day=3)
    with env["factory"]() as s:
        s.add(MeetingHold(meeting_id=held, tenant_id=team["tenant"], on_hold=True))
        s.add(MeetingClosure(meeting_id=gone, tenant_id=team["tenant"], deleted_at=utcnow(), deleted_by=team["head"].id))
        s.commit()
    assert call(env, "GET", team["kim"], f"/api/meetings/{visible}").status_code == 200
    assert call(env, "GET", team["kim"], f"/api/meetings/{held}").status_code == 404
    assert call(env, "GET", team["kim"], f"/api/meetings/{gone}").status_code == 404
    assert call(env, "GET", team["mgr"], f"/api/meetings/{held}").status_code == 200  # 관리자 이상은 그대로


def test_inactive_project_membership_is_not_a_reason_to_view(env, team, project):
    meeting_id = project_meeting(env, team, project)
    assert call(env, "GET", team["kim"], f"/api/meetings/{meeting_id}").status_code == 200
    with env["factory"]() as s:
        s.get(Project, project).status = "rejected"
        s.commit()
    assert call(env, "GET", team["kim"], f"/api/meetings/{meeting_id}").status_code == 404
    # 대기 중인 프로젝트에 연결된 회의록도 같다
    pending = create(env, team, "kim", name="대기").json()["id"]
    other = project_meeting(env, team, pending)
    assert call(env, "GET", team["kim"], f"/api/meetings/{other}").status_code == 404


# ================= 프로젝트별 회의록 조회 =================
def test_project_meetings_listing(env, team, project):
    older = project_meeting(env, team, project, day=1)
    newer = project_meeting(env, team, project, day=5)
    held = project_meeting(env, team, project, day=3)
    other_project = create(env, team, "head2", dept="sales", name="영업 프로젝트").json()["id"]
    elsewhere = project_meeting(env, team, other_project, registrant="head2", day=9)
    unlinked = make_meeting(env["factory"], team["head"], day=8)
    with env["factory"]() as s:
        s.add(MeetingHold(meeting_id=held, tenant_id=team["tenant"], on_hold=True))
        s.commit()
    path = f"/api/projects/{project}/meetings"
    body = call(env, "GET", team["kim"], path).json()
    ids = [m["id"] for m in body["items"]]
    assert ids == [newer, older]  # 최신 회의 일시순, 다른 프로젝트·미연결 회의록·보류 단계는 섞이지 않음
    assert elsewhere not in ids and unlinked not in ids and held not in ids
    assert set(body["items"][0]) == {"id", "title", "heldAt", "registeredBy", "origin", "status", "confirmKind", "itemCount",
                                    "needsCompletionCount", "autoConfirmAt", "phase"}
    assert body["total"] == 2 and body["availablePhases"] == ["active", "ended"]
    # 쪽 나눔
    page1 = call(env, "GET", team["kim"], path, params={"size": 1, "page": 1}).json()
    page2 = call(env, "GET", team["kim"], path, params={"size": 1, "page": 2}).json()
    assert [m["id"] for m in page1["items"]] == [newer] and [m["id"] for m in page2["items"]] == [older] and page1["total"] == 2
    # 단계 인자: 담당자의 보류 조회는 403, 관리자 이상은 보류 단계를 볼 수 있다
    assert call(env, "GET", team["kim"], path, params={"phase": "on_hold"}).status_code == 403
    assert [m["id"] for m in call(env, "GET", team["mgr"], path, params={"phase": "on_hold"}).json()["items"]] == [held]
    # 프로젝트를 볼 수 없으면 404, 다른 고객사도 404
    assert call(env, "GET", team["sales"], path).status_code == 404
    assert call(env, "GET", team["outsider"], path).status_code == 404
    assert call(env, "GET", team["kim"], "/api/projects/99999/meetings").status_code == 404


# ================= 승인 판정 불변 =================
def test_project_lead_still_cannot_confirm_meetings_they_did_not_upload(env, team, project):
    """정책 미결, 이후 변경될 수 있음: 지금은 프로젝트 총괄이어도 기존 규칙(등록한 관리자 또는 담당자 등록 회의록의 참석 관리자)대로
    자기가 올리지 않은 프로젝트 회의록을 확정할 수 없다. 이 테스트는 현재 동작을 고정할 뿐 확정된 정책이 아니다."""
    meeting_id = project_meeting(env, team, project, registrant="kim", status="awaiting_confirmation")
    assert call(env, "POST", team["head"], f"/api/meetings/{meeting_id}/confirm").status_code == 403
    detail = call(env, "GET", team["head"], f"/api/meetings/{meeting_id}").json()
    assert "confirm_meeting" not in detail["allowedActions"]
    assert call(env, "POST", team["boss"], f"/api/meetings/{meeting_id}/confirm").status_code == 200  # 지시자는 기존대로


# ================= 마이그레이션 =================
def test_migration_roundtrip_and_downgrade_guard(tmp_path):
    url = f"sqlite:///{(tmp_path / 'm.db').as_posix()}"
    cfg = _alembic(url)
    command.upgrade(cfg, "head")
    command.downgrade(cfg, _BEFORE_CLASSIFICATION)
    command.upgrade(cfg, "head")
    command.check(cfg)
    engine = make_engine(url)
    try:
        with engine.begin() as conn:
            conn.execute(text("INSERT INTO tenants (id, name, auto_confirm_days, created_at) VALUES (1, 't', 5, '2026-10-01')"))
            conn.execute(text(
                "INSERT INTO accounts (id, tenant_id, login_id, password_hash, name, email, rank, is_active, created_at) "
                "VALUES (1, 1, 'a', 'x', 'a', 'a@x', 'manager', 1, '2026-10-01')"))
            conn.execute(text(
                "INSERT INTO source_documents (id, tenant_id, origin, doc_type, title, registered_by, created_at) "
                "VALUES (1, 1, 'audio_minutes', 'm', 't', 1, '2026-10-01')"))
            conn.execute(text(
                "INSERT INTO meetings (id, tenant_id, source_document_id, title, held_at, summary, decisions, status, "
                "first_created_at, created_at) VALUES (1, 1, 1, 't', '2026-10-01', '', '[]', 'confirmed', '2026-10-01', '2026-10-01')"))
        # 회의록이 있어도 분류 기록이 없으면 되돌리기·다시 올리기 가능(meetings 표는 다시 만들지 않음)
        command.downgrade(cfg, _BEFORE_CLASSIFICATION)
        command.upgrade(cfg, "head")
        with engine.begin() as conn:
            conn.execute(text(
                "INSERT INTO meeting_classifications (tenant_id, meeting_id, meeting_type, created_at) VALUES (1, 1, 'regular', '2026-10-02')"))
        with pytest.raises(RuntimeError, match="downgrade 거부"):
            command.downgrade(cfg, _BEFORE_CLASSIFICATION)
        with engine.connect() as conn:
            assert conn.execute(text("SELECT COUNT(*) FROM meeting_classifications")).scalar_one() == 1
            assert conn.execute(text("SELECT COUNT(*) FROM meetings")).scalar_one() == 1
    finally:
        engine.dispose()


def test_meeting_type_check_constraint_rejects_unknown_type(tmp_path):
    from sqlalchemy.exc import IntegrityError

    url = f"sqlite:///{(tmp_path / 'c.db').as_posix()}"
    command.upgrade(_alembic(url), "head")
    engine = make_engine(url)
    try:
        with pytest.raises(IntegrityError, match="CHECK"):
            with engine.begin() as conn:
                conn.execute(text(
                    "INSERT INTO meeting_classifications (tenant_id, meeting_id, meeting_type, created_at) VALUES (1, 1, 'weird', '2026-10-02')"))
    finally:
        engine.dispose()


def test_alembic_offline_postgresql_ddl_has_classification_table():
    cfg = _alembic("postgresql+psycopg://u:p@localhost/db")
    buffer = io.StringIO()
    cfg.output_buffer = buffer
    command.upgrade(cfg, "head", sql=True)
    sql = buffer.getvalue()
    assert "CREATE TABLE meeting_classifications" in sql
    assert "ck_meeting_classifications_meeting_type_valid" in sql and "uq_meeting_classifications_meeting_id" in sql
