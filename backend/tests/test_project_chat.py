"""작업 89-2b: 프로젝트 질의응답 — 범위(그 프로젝트의 열람 가능한 정리된 회의록만), 권한(404/409), 허용 동작 ask_project, 근거에 회의록 정보,
회의 1건 질의와 같은 정책(422·429·503·저장 안 함). 가짜 LLM 만 쓴다(외부 호출 없음). 임시 DB 만 쓴다."""
import logging
import re
from datetime import date, datetime, timezone

import pytest

from app.api.meeting_chat import get_qa_llm_factory, reset_rate_limit
from app.config import settings
from app.main import app
from app.meeting_qa.access import project_askable_meetings
from app.meeting_qa.llm import FakeQaLlm, QaLlmError
from app.meeting_qa.project_context import build_project_context
from app.models import Meeting, MeetingClosure, MeetingHold, Project
from tests.test_meeting_chat import add_minutes, answer_text, events, ids_in
from tests.test_meeting_classification import classify
from tests.test_meeting_queries import make_meeting
from tests.test_org_projects import call, create, env, team  # noqa: F401  (env·team 은 픽스처)
from tests.test_upload_processing import auth

QUESTION = "견적서는 누가 언제까지 송부하나요 zq9-비밀질문"


@pytest.fixture(autouse=True)
def _reset(monkeypatch):
    reset_rate_limit()
    monkeypatch.setattr(settings, "qa_enabled", True)
    monkeypatch.setattr(settings, "qa_rate_limit_per_minute", 100)
    yield
    app.dependency_overrides.pop(get_qa_llm_factory, None)
    reset_rate_limit()


def use_llm(llm):
    app.dependency_overrides[get_qa_llm_factory] = lambda: (lambda: llm)
    return llm


@pytest.fixture
def world(env, team):
    """프로젝트 P(참여자 kim): 정리된 회의 2건 + 삭제 1건 + 정리 전(처리 중) 1건. 다른 프로젝트 Q 의 회의 1건."""
    f = env["factory"]
    pid = create(env, team, "head", members=[team["kim"]]).json()["id"]
    qid = create(env, team, "head", name="다른 프로젝트").json()["id"]
    m1 = make_meeting(
        f, team["head"], day=1, status="confirmed", title="1차 킥오프",
        items=[{"title": "견적서 송부", "assignee_id": team["kim"].id, "due_date": date(2026, 10, 9), "status": "confirmed", "evidence_start_sec": 75.0}],
        transcript="[00:01:15] 화자2: 견적서는 이번 주 금요일까지 송부하겠습니다\n",
    )
    add_minutes(env, m1, decisions="견적 범위는 설비 포함으로 확정")
    m2 = make_meeting(
        f, team["head"], day=2, status="awaiting_confirmation", title="2차 점검",
        items=[{"title": "설비 점검 일정 조율", "assignee_id": team["head"].id, "due_undetermined": True, "status": "pending"}],
    )
    add_minutes(env, m2, decisions="점검은 다음 달 첫째 주")
    m3 = make_meeting(f, team["head"], day=3, status="confirmed", title="삭제된 회의", items=[{"title": "삭제회의업무zzz", "status": "confirmed"}])
    add_minutes(env, m3, decisions="삭제회의결정zzz")
    m4 = make_meeting(f, team["head"], day=4, status="processing", title="처리 중 회의")
    m5 = make_meeting(f, team["head"], day=5, status="confirmed", title="다른 프로젝트 회의", items=[{"title": "타프로젝트업무yyy", "status": "confirmed"}])
    add_minutes(env, m5, decisions="타프로젝트결정yyy")
    with f() as s:
        s.add(MeetingClosure(meeting_id=m3, tenant_id=team["tenant"], deleted_at=datetime.now(timezone.utc)))
        s.commit()
    for mid in (m1, m2, m3, m4):
        classify(env, mid, pid, tenant_id=team["tenant"])
    classify(env, m5, qid, tenant_id=team["tenant"])
    return {"pid": pid, "qid": qid, "m1": m1, "m2": m2, "m3": m3, "m4": m4, "m5": m5}


def chat(env, account, pid, question=QUESTION, **extra):
    body = {"question": question, "history": [], "quickPrompt": None, **extra}
    return env["client"].post(f"/api/projects/{pid}/chat", headers=auth(account), json=body)


def askable(env, account, pid, limit=None):
    with env["factory"]() as s:
        return [m.id for m in project_askable_meetings(s, account, s.get(Project, pid), limit=limit)]


# ---------------- 범위 ----------------
def test_context_covers_only_askable_meetings_of_the_project(env, team, world):
    with env["factory"]() as s:
        meetings = project_askable_meetings(s, team["head"], s.get(Project, world["pid"]))
        assert [m.id for m in meetings] == [world["m2"], world["m1"]]  # 최근 순, 삭제·정리 전·다른 프로젝트 제외
        ctx = build_project_context(s, meetings, team["head"], "견적서 송부 설비 점검", None)
    text = ctx.block()
    assert "[1차 킥오프]" in text and "[2차 점검]" in text
    assert "zzz" not in text and "yyy" not in text and "처리 중 회의" not in text
    assert {x.meeting_id for x in ctx.sources} <= {world["m1"], world["m2"]}
    assert ctx.pending_included is True  # 확정 전 업무·확정 대기 회의록 포함
    assert ctx.mode == "summary"


def test_meeting_limit_caps_the_sources(env, team, world, monkeypatch):
    monkeypatch.setattr(settings, "qa_project_max_meetings", 1)
    assert askable(env, team["head"], world["pid"], limit=settings.qa_project_max_meetings) == [world["m2"]]
    llm = use_llm(FakeQaLlm())
    assert chat(env, team["head"], world["pid"], question="견적서 송부").status_code == 200
    assert "1차 킥오프" not in llm.calls[0][-1]["content"]


# ---------------- 응답·근거 ----------------
def test_citations_carry_meeting_info_and_labels_name_the_meeting(env, team, world):
    llm = use_llm(FakeQaLlm(responder=lambda m: f"견적서는 금요일까지입니다 [{ids_in(m)[0]}]."))
    res = chat(env, team["head"], world["pid"], question="견적서 송부")
    assert res.status_code == 200 and res.headers["content-type"].startswith("text/event-stream")
    evs = events(res)
    assert [n for n, _ in evs][0] == "meta" and [n for n, _ in evs][-1] == "done"
    cites = next(d for n, d in evs if n == "citations")["items"]
    assert cites and cites[0]["meetingId"] in (world["m1"], world["m2"]) and cites[0]["meetingTitle"] in ("1차 킥오프", "2차 점검")
    assert cites[0]["label"].startswith(cites[0]["meetingTitle"] + "·")
    assert "[S" not in answer_text(evs)
    assert evs[-1][1]["grounded"] is True
    assert "여러 회의록" in llm.calls[0][0]["content"]  # 프로젝트용 시스템 지시


def test_item_with_evidence_cites_speaker_time_and_meeting(env, team, world):
    def responder(messages):
        for line in messages[-1]["content"].splitlines():
            m = re.match(r"\[(S\d+)\] \[1차 킥오프\] 업무: 견적서 송부", line)
            if m:
                return f"견적서 송부 담당이 있습니다 [{m.group(1)}]"
        return "없음"

    use_llm(FakeQaLlm(responder=responder))
    evs = events(chat(env, team["head"], world["pid"], question="견적서 송부 담당"))
    cite = next(d for n, d in evs if n == "citations")["items"][0]
    assert cite["meetingId"] == world["m1"] and cite["speaker"] == "화자2" and cite["startSec"] == 75.0
    assert "[1차 킥오프·화자2/01:15]" in answer_text(evs)


def test_no_evidence_gives_fixed_phrase_and_no_citations(env, team, world):
    use_llm(FakeQaLlm(responder=lambda m: "확실합니다 [S99]"))
    evs = events(chat(env, team["head"], world["pid"]))
    assert answer_text(evs) == "본 회의록에서 확인할 수 없는 내용입니다"
    assert evs[-1][1]["noAnswer"] is True and next(d for n, d in evs if n == "citations")["items"] == []


def test_meeting_chat_citations_are_unchanged(env, team, world):
    use_llm(FakeQaLlm())
    res = env["client"].post(f"/api/meetings/{world['m1']}/chat", headers=auth(team["head"]), json={"question": "견적서 송부", "history": []})
    cite = next(d for n, d in events(res) if n == "citations")["items"][0]
    assert "meetingId" not in cite and "meetingTitle" not in cite


# ---------------- 권한·상태 ----------------
def test_member_staff_can_ask_but_non_member_and_other_tenant_get_404(env, team, world):
    use_llm(FakeQaLlm())
    assert chat(env, team["kim"], world["pid"]).status_code == 200
    for who in ("loner", "outsider"):
        res = chat(env, team[who], world["pid"])
        assert res.status_code == 404 and "프로젝트" in res.json()["detail"]
    assert chat(env, team["head"], 99999).status_code == 404


def test_scope_cannot_be_chosen_by_request_body(env, team, world):
    llm = use_llm(FakeQaLlm())
    res = chat(env, team["head"], world["pid"], question="타프로젝트 알려줘", meetingId=world["m5"], projectId=world["qid"])
    assert res.status_code == 200
    material = llm.calls[0][-1]["content"].split("질문:")[0]
    assert "yyy" not in material and "다른 프로젝트 회의" not in material


def test_project_without_askable_meetings_is_409(env, team, world):
    empty = create(env, team, "head", name="빈 프로젝트").json()["id"]
    use_llm(FakeQaLlm())
    assert chat(env, team["head"], empty).status_code == 409
    only_processing = create(env, team, "head", name="처리 중만").json()["id"]
    classify(env, make_meeting(env["factory"], team["head"], status="processing"), only_processing, tenant_id=team["tenant"])
    assert chat(env, team["head"], only_processing).status_code == 409


def test_staff_do_not_get_on_hold_meetings_of_the_project(env, team, world):
    with env["factory"]() as s:
        s.add(MeetingHold(meeting_id=world["m2"], tenant_id=team["tenant"], on_hold=True))
        s.commit()
    llm = use_llm(FakeQaLlm())
    assert chat(env, team["kim"], world["pid"], question="설비 점검").status_code == 200
    assert "2차 점검" not in llm.calls[0][-1]["content"]  # 담당자는 보류 회의록을 볼 수 없으므로 자료에서도 빠진다
    llm2 = use_llm(FakeQaLlm())
    chat(env, team["head"], world["pid"], question="설비 점검")
    assert "2차 점검" in llm2.calls[0][-1]["content"]


def test_allowed_action_ask_project_is_server_decided(env, team, world):
    path = f"/api/projects/{world['pid']}"
    assert "ask_project" in call(env, "GET", team["head"], path).json()["allowedActions"]
    assert "ask_project" in call(env, "GET", team["kim"], path).json()["allowedActions"]
    empty = create(env, team, "head", name="빈 프로젝트").json()["id"]
    assert "ask_project" not in call(env, "GET", team["head"], f"/api/projects/{empty}").json()["allowedActions"]


def test_qa_disabled_hides_action_and_endpoint(env, team, world, monkeypatch):
    monkeypatch.setattr(settings, "qa_enabled", False)
    assert "ask_project" not in call(env, "GET", team["head"], f"/api/projects/{world['pid']}").json()["allowedActions"]
    assert chat(env, team["head"], world["pid"]).status_code == 404


# ---------------- 입력 검사·제한·엔진 ----------------
def test_validation_rate_limit_and_engine_errors(env, team, world, monkeypatch):
    use_llm(FakeQaLlm())
    assert chat(env, team["head"], world["pid"], question="가" * 501).status_code == 422
    assert chat(env, team["head"], world["pid"], question="  ").status_code == 422
    assert chat(env, team["head"], world["pid"], history=[{"question": "q", "answer": "a"}] * 5).status_code == 422
    assert chat(env, team["head"], world["pid"], quickPrompt="nope").status_code == 422
    monkeypatch.setattr(settings, "qa_rate_limit_per_minute", 1)
    reset_rate_limit()
    assert chat(env, team["head"], world["pid"]).status_code == 200
    limited = chat(env, team["head"], world["pid"])
    assert limited.status_code == 429 and int(limited.headers["Retry-After"]) >= 1

    def broken():
        raise QaLlmError("llm_unavailable", "엔진 없음")

    reset_rate_limit()
    app.dependency_overrides[get_qa_llm_factory] = lambda: broken
    res = chat(env, team["head"], world["pid"])
    assert res.status_code == 503 and res.json()["detail"]["code"] == "llm_unavailable"


def test_quick_prompt_my_tasks_puts_my_items_first(env, team, world):
    with env["factory"]() as s:
        meetings = project_askable_meetings(s, team["kim"], s.get(Project, world["pid"]))
        ctx = build_project_context(s, meetings, team["kim"], "내 업무", "my_tasks")
    assert "견적서 송부" in ctx.sources[0].text and ctx.sources[0].meeting_id == world["m1"]


# ---------------- 저장 안 함 ----------------
def test_question_is_not_logged_and_log_has_only_request_scope_status(env, team, world, caplog):
    caplog.set_level(logging.DEBUG)
    use_llm(FakeQaLlm())
    assert chat(env, team["head"], world["pid"]).status_code == 200
    joined = "\n".join(r.getMessage() for r in caplog.records)
    assert "zq9-비밀질문" not in joined
    assert re.search(r"qa request=\w+ project=%d status=" % world["pid"], joined)
