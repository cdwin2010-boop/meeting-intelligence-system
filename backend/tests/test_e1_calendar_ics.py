"""E-1: 캘린더 파일 내보내기(GET /api/me/calendar.ics). 읽기 전용, 열람 범위·scope 권한은 /api/me/calendar 와 같다."""
from datetime import date, datetime, timezone

from app.services.ics_export import build_ics, escape_text, fold_line
from tests.test_meeting_queries import make_meeting
from tests.test_notices_todos import call, team  # noqa: F401  (team 은 픽스처)
from tests.test_upload_processing import env  # noqa: F401  (env 는 픽스처)

URL = "/api/me/calendar.ics"


def seed(env, team):
    f = env["factory"]
    return make_meeting(f, team["mgr"], status="confirmed", participants=[team["staff"]], title="비밀 회의 제목", items=[
        {"title": "진행 중, 확정", "assignee_id": team["staff"].id, "due_date": date(2026, 10, 5), "status": "confirmed"},
        {"title": "승인 대기", "assignee_id": team["staff"].id, "due_date": date(2026, 10, 6), "status": "pending"},
        {"title": "기한 미확정", "assignee_id": team["staff"].id, "due_date": None, "due_undetermined": True, "status": "pending"},
        {"title": "종결됨", "assignee_id": team["staff"].id, "due_date": date(2026, 10, 7), "status": "closed"},
        {"title": "삭제됨", "assignee_id": team["staff"].id, "due_date": date(2026, 10, 8), "status": "deleted"},
        {"title": "남의 업무", "assignee_id": team["staff2"].id, "due_date": date(2026, 10, 9), "status": "confirmed"},
    ])


def test_requires_login(env):
    assert env["client"].get(URL).status_code == 401


def test_headers_and_exclusions(env, team):
    seed(env, team)
    res = call(env, "GET", team["staff"], URL)
    assert res.status_code == 200
    assert res.headers["content-type"].startswith("text/calendar") and "utf-8" in res.headers["content-type"]
    assert 'filename="aidots-tasks.ics"' in res.headers["content-disposition"]
    text = res.content.decode("utf-8")
    assert text.startswith("BEGIN:VCALENDAR\r\nVERSION:2.0\r\n") and "CALSCALE:GREGORIAN" in text and "PRODID:" in text
    assert text.endswith("END:VCALENDAR\r\n") and "\n" not in text.replace("\r\n", "")
    assert "SUMMARY:진행 중\\, 확정" in text and "SUMMARY:승인 대기" in text
    for excluded in ("기한 미확정", "종결됨", "삭제됨", "남의 업무"):
        assert excluded not in text
    assert text.count("BEGIN:VEVENT") == 2
    assert "DTSTART;VALUE=DATE:20261005\r\nDTEND;VALUE=DATE:20261006" in text  # 종일 일정, 날짜 그대로
    assert "DESCRIPTION:AI DOTS에서 확인" in text


def test_uid_is_stable_across_exports(env, team):
    seed(env, team)
    uids = lambda: sorted(l for l in call(env, "GET", team["staff"], URL).content.decode().split("\r\n") if l.startswith("UID:"))
    first = uids()
    assert first and first == uids() and all(u.endswith("@aidots") and u.startswith("UID:task-") for u in first)


def test_scope_all_permission(env, team):
    seed(env, team)
    assert call(env, "GET", team["staff"], URL, params={"scope": "all"}).status_code == 403
    res = call(env, "GET", team["mgr"], URL, params={"scope": "all"})
    assert res.status_code == 200 and "남의 업무" in res.content.decode()


def test_no_meeting_content_in_file(env, team):
    seed(env, team)
    text = call(env, "GET", team["mgr"], URL, params={"scope": "all"}).content.decode()
    assert "비밀 회의 제목" not in text and "http" not in text and "/v2/" not in text and "evidence" not in text.lower()


def test_other_tenant_isolated(env, team):
    seed(env, team)
    res = call(env, "GET", team["outsider"], URL, params={"scope": "all"})
    assert res.status_code == 200 and "BEGIN:VEVENT" not in res.content.decode()


def test_escape_text():
    assert escape_text("a,b;c\\d\ne") == "a\\,b\\;c\\\\d\\ne"


def test_fold_korean_never_splits_characters_and_within_75_octets():
    line = "SUMMARY:" + "한글업무명" * 30
    folded = fold_line(line)
    parts = folded.split("\r\n")
    assert len(parts) > 1 and all(p.startswith(" ") for p in parts[1:])
    assert all(len(p.encode("utf-8")) <= 75 for p in parts)
    assert "".join([parts[0]] + [p[1:] for p in parts[1:]]) == line  # 접힌 줄을 이으면 원문과 같다
    parts[0].encode("utf-8").decode("utf-8")


def test_build_ics_dtstamp_utc():
    text = build_ics([(7, "업무", date(2026, 12, 31))], datetime(2026, 10, 15, 3, 4, 5, tzinfo=timezone.utc))
    assert "DTSTAMP:20261015T030405Z" in text and "DTSTART;VALUE=DATE:20261231" in text and "DTEND;VALUE=DATE:20270101" in text
