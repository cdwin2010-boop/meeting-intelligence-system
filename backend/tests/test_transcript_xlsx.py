"""자료 파일(엑셀 .xlsx) 등록(작업 84-1): 변환 규칙, 업로드 때 바로 거부, STT 건너뛰기·재처리, 응답 필드. 엑셀 파일은 테스트 코드에서 만든다."""
import datetime as dt
import io
import re
import zipfile

import pytest
from openpyxl import Workbook
from sqlalchemy import select

from app.config import settings
from app.models import ActionItem, Event, Meeting, SourceDocument, Transcript
from app.pipeline.extractor import FakeExtractor
from app.pipeline.fakes import FAKE_SCRIPT
from app.pipeline.transcript_text import TranscriptTextError, build_result, parse_transcript
from app.pipeline.transcript_xlsx import xlsx_to_text, xlsx_to_utterances
from app.services.processing import process_meeting
from app.services.source_kind import TRANSCRIPT_TXT_ENGINE
from tests.test_transcript_txt import NoStt, post_txt
from tests.test_upload_processing import (  # noqa: F401  (env 는 픽스처)
    HELD_AT, BrokenExtractor, add_account, auth, env, saved_files,
)

XLSX_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def workbook_bytes(rows, *, sheet="회의", extra=None, hidden_extra=None) -> bytes:
    wb = Workbook()
    ws = wb.active
    ws.title = sheet
    for row in rows:
        ws.append(row)
    for name, data in (extra or {}).items():
        other = wb.create_sheet(name)
        for row in data:
            other.append(row)
    for name, data in (hidden_extra or {}).items():
        other = wb.create_sheet(name)
        for row in data:
            other.append(row)
        other.sheet_state = "hidden"
    buffer = io.BytesIO()
    wb.save(buffer)
    return buffer.getvalue()


HEADER = ["시각", "화자", "발언"]
SCRIPT_ROWS = [[ts, speaker, text] for ts, speaker, text in FAKE_SCRIPT]


def post_xlsx(env, account, content: bytes, *, filename="회의 자료.xlsx", source_kind="transcript_xlsx", title="엑셀 회의"):
    data = {"title": title, "heldAt": HELD_AT, "sourceKind": source_kind}
    return env["client"].post("/api/meetings/upload", headers=auth(account), data=data, files={"file": (filename, content, XLSX_TYPE)})


@pytest.fixture
def mgr(env):
    return add_account(env["factory"], "고객사A", "mgr", "manager", name="박관리")


def run(env, job_id, extractor=FakeExtractor):
    NoStt.called = 0
    return process_meeting(job_id, session_factory=env["factory"], stt_factory=NoStt(), extractor_factory=extractor)


# ---------------- 변환 규칙 ----------------
def test_header_columns_become_timed_lines_and_match_txt_result():
    text = xlsx_to_text(workbook_bytes([HEADER, *SCRIPT_ROWS]))
    lines = text.splitlines()
    assert lines[0] == f"[{FAKE_SCRIPT[0][0]}] {FAKE_SCRIPT[0][1]}: {FAKE_SCRIPT[0][2]}"
    from app.pipeline.fakes import fake_transcript

    assert parse_transcript(text).text == fake_transcript()  # 같은 내용의 txt 와 같은 전사문


def test_header_names_columns_in_any_order_and_english_names():
    rows = [["Text", "Speaker", "Time"], ["안녕하세요", "김대리", "00:01:05"], ["네 알겠습니다", "박팀장", "1:10"]]
    assert xlsx_to_text(workbook_bytes(rows)).splitlines() == ["[00:01:05] 김대리: 안녕하세요", "[00:01:10] 박팀장: 네 알겠습니다"]


def test_title_rows_above_header_are_skipped_and_blank_rows_ignored():
    rows = [["회의 기록"], [], ["일시", "2026-10-01"], HEADER, ["00:00:03", "김대리", "시작합니다"], [None, None, None], ["00:00:09", "박팀장", "네"]]
    assert xlsx_to_text(workbook_bytes(rows)).splitlines() == ["[00:00:03] 김대리: 시작합니다", "[00:00:09] 박팀장: 네"]


@pytest.mark.parametrize(
    "value, expected",
    [
        (dt.time(0, 1, 5), "[00:01:05]"),
        (dt.timedelta(minutes=2, seconds=3), "[00:02:03]"),
        (dt.datetime(2026, 10, 1, 1, 2, 3), "[01:02:03]"),
        (0.5, "[12:00:00]"),  # 엑셀 시간 값(하루 비율)
        (83, "[00:01:23]"),  # 초 단위 숫자
        ("12:34", "[00:12:34]"),
        ("[01:02:03]", "[01:02:03]"),
    ],
)
def test_time_cell_formats(value, expected):
    line = xlsx_to_text(workbook_bytes([HEADER, [value, "김대리", "발언"]]))
    assert line == f"{expected} 김대리: 발언"


def test_unreadable_time_gives_untimed_line_and_blank_speaker_stays_unlabeled():
    rows = [HEADER, ["오전", "김대리", "첫 발언"], [None, None, "이어지는 발언"], ["00:00:20", "박팀장", "답변"]]
    # 빈 화자 칸은 직전 화자를 이어 쓰지 않고 라벨 없이 그대로 둔다(작업 85-1)
    assert xlsx_to_text(workbook_bytes(rows)).splitlines() == ["김대리: 첫 발언", "이어지는 발언", "[00:00:20] 박팀장: 답변"]
    result = build_result(xlsx_to_utterances(workbook_bytes(rows)))
    assert [s["speaker"] for s in result.segments] == ["김대리", "", "박팀장"]


def test_no_speaker_column_has_no_label_and_keeps_rows_separate():
    rows = [["시간", "내용"], ["00:00:01", "첫째"], ["00:00:02", "둘째"]]
    assert xlsx_to_text(workbook_bytes(rows)).splitlines() == ["[00:00:01] 첫째", "[00:00:02] 둘째"]
    only = build_result(xlsx_to_utterances(workbook_bytes([["발언"], ["하나"], ["둘: 셋"]])))
    assert only.text.splitlines() == ["하나", "둘: 셋"]  # 임의 "화자1" 없음, 발언 안의 콜론도 라벨로 읽지 않는다
    assert [s["speaker"] for s in only.segments] == ["", ""]
    assert all("화자1" not in line for line in only.text.splitlines())


def test_multiple_visible_sheets_in_order_and_hidden_sheet_ignored():
    raw = workbook_bytes(
        [HEADER, ["00:00:01", "가", "첫 시트"]],
        extra={"둘째": [HEADER, ["00:00:05", "나", "둘째 시트"]]},
        hidden_extra={"숨김": [HEADER, ["00:00:09", "다", "숨긴 시트"]]},
    )
    assert xlsx_to_text(raw).splitlines() == ["[00:00:01] 가: 첫 시트", "[00:00:05] 나: 둘째 시트"]


def test_multiline_cell_is_joined_and_speaker_is_shown_as_written():
    rows = [HEADER, ["00:00:01", " 김 대리(팀) ", "첫 줄\n둘째 줄:  끝"]]
    # 화자 표기의 공백·문장부호는 지우지 않는다(작업 85-1). 앞뒤 공백만 다듬는다
    assert xlsx_to_text(workbook_bytes(rows)) == "[00:00:01] 김 대리(팀): 첫 줄 둘째 줄: 끝"
    result = build_result(xlsx_to_utterances(workbook_bytes(rows)))
    assert result.text == "[00:00:01] 김 대리(팀): 첫 줄 둘째 줄: 끝"


def test_formula_cells_use_stored_values_only_and_never_the_formula_text():
    wb = Workbook()
    ws = wb.active
    ws.append(HEADER)
    ws.append(["00:00:01", "김대리", "=1+1"])  # 저장된 계산 값이 없는 수식: 읽지 않는다
    ws.append(["00:00:02", "김대리", "=HYPERLINK(\"http://example.com\",\"x\")"])
    ws.append(["00:00:03", "김대리", "진짜 발언"])
    buffer = io.BytesIO()
    wb.save(buffer)
    text = xlsx_to_text(buffer.getvalue())
    assert text == "[00:00:03] 김대리: 진짜 발언"
    assert "=" not in text and "HYPERLINK" not in text


# ---------------- 거부 규칙 ----------------
def test_rejects_unreadable_structures():
    cases = {
        "빈 시트": (workbook_bytes([]), "빈 파일"),
        "머리글만": (workbook_bytes([HEADER]), "발언이 없습니다"),
        "열 이름 없음": (workbook_bytes([["a", "b"], ["1", "2"]]), "양식을 사용해 주세요"),
        "zip 아님": (b"plain text, not a workbook", "엑셀(.xlsx) 파일이 아닙니다"),
        "빈 바이트": (b"", "빈 파일"),
    }
    for label, (raw, message) in cases.items():
        with pytest.raises(TranscriptTextError) as info:
            xlsx_to_text(raw)
        assert message in str(info.value), label


def test_rejects_password_protected_and_macro_files():
    encrypted = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\x00" * 600  # 암호가 걸린 엑셀은 OLE 컨테이너
    with pytest.raises(TranscriptTextError, match="비밀번호"):
        xlsx_to_text(encrypted)
    base = workbook_bytes([HEADER, ["00:00:01", "김대리", "발언"]])
    macro = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(base)) as src, zipfile.ZipFile(macro, "w") as dst:
        for item in src.infolist():
            dst.writestr(item, src.read(item.filename))
        dst.writestr("xl/vbaProject.bin", b"fake")
    with pytest.raises(TranscriptTextError, match="매크로"):
        xlsx_to_text(macro.getvalue())
    macro_type = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(base)) as src, zipfile.ZipFile(macro_type, "w") as dst:
        for item in src.infolist():
            data = src.read(item.filename)
            if item.filename == "[Content_Types].xml":
                data = data.replace(b"spreadsheetml.sheet.main+xml", b"ms-excel.sheet.macroEnabled.main+xml")
            dst.writestr(item, data)
    with pytest.raises(TranscriptTextError, match="매크로"):
        xlsx_to_text(macro_type.getvalue())


def test_rejects_corrupt_zip_oversize_and_too_many_chars(monkeypatch):
    good = workbook_bytes([HEADER, ["00:00:01", "김대리", "발언"]])
    with pytest.raises(TranscriptTextError, match="읽을 수 없습니다"):
        xlsx_to_text(good[: len(good) // 2])
    monkeypatch.setattr(settings, "transcript_upload_max_bytes", 1024)
    with pytest.raises(TranscriptTextError, match="너무 큽니다"):
        xlsx_to_text(good)
    monkeypatch.undo()
    monkeypatch.setattr(settings, "transcript_max_chars", 10)
    with pytest.raises(TranscriptTextError, match="글자 수"):
        xlsx_to_text(workbook_bytes([HEADER, ["00:00:01", "김대리", "열 글자를 넘기는 아주 긴 발언입니다"]]))


# ---------------- 업로드·처리 ----------------
def test_upload_xlsx_is_stored_and_processed_without_stt(env, mgr):
    res = post_xlsx(env, mgr, workbook_bytes([HEADER, *SCRIPT_ROWS]))
    assert res.status_code == 202
    [saved] = saved_files(env)
    assert saved.suffix == ".xlsx" and re.fullmatch(r"\d+_\d{10}_001", saved.stem)
    with env["factory"]() as s:
        meeting = s.get(Meeting, res.json()["meetingId"])
        assert s.get(SourceDocument, meeting.source_document_id).file_path.endswith(".xlsx")
        created = s.scalars(select(Event).where(Event.event_type == "meeting.created")).one()
        assert created.payload["sourceKind"] == "transcript_xlsx"
    assert run(env, res.json()["jobId"]) == "completed"
    assert NoStt.called == 0
    with env["factory"]() as s:
        transcript = s.scalar(select(Transcript))
        assert transcript.stt_provider == TRANSCRIPT_TXT_ENGINE
        assert transcript.full_text.splitlines()[0].startswith("[00:00:03] 김도현:")
        assert len(list(s.scalars(select(ActionItem)))) == 2  # 이후 추출은 음성·txt 와 같다


def test_detail_fields_and_audio_requests(env, mgr):
    res = post_xlsx(env, mgr, workbook_bytes([HEADER, *SCRIPT_ROWS]))
    meeting_id = res.json()["meetingId"]
    run(env, res.json()["jobId"])
    detail = env["client"].get(f"/api/meetings/{meeting_id}", headers=auth(mgr)).json()
    assert detail["sourceKind"] == "transcript_xlsx" and detail["hasAudio"] is False
    assert env["client"].get(f"/api/meetings/{meeting_id}/audio-url", headers=auth(mgr)).status_code == 404
    # 근거 시각은 엑셀의 시각 열에서 온다
    assert any(item["evidenceStartSec"] is not None for item in detail["actionItems"])


def test_reprocess_uses_stored_transcript_without_stt(env, mgr):
    res = post_xlsx(env, mgr, workbook_bytes([HEADER, *SCRIPT_ROWS]))
    meeting_id = res.json()["meetingId"]
    assert run(env, res.json()["jobId"], extractor=BrokenExtractor) == "failed"
    again = env["client"].post(f"/api/meetings/{meeting_id}/reprocess", headers=auth(mgr))
    assert again.status_code == 202
    assert run(env, again.json()["jobId"]) == "completed"
    assert NoStt.called == 0


def test_timeless_xlsx_keeps_evidence_time_empty(env, mgr):
    rows = [["화자", "발언"], ["이서연", "네, 제가 다음 주 금요일까지 정리해서 공유하겠습니다."]]
    res = post_xlsx(env, mgr, workbook_bytes(rows))
    assert run(env, res.json()["jobId"]) == "completed"
    with env["factory"]() as s:
        assert all(i.evidence_start_sec is None for i in s.scalars(select(ActionItem)))


@pytest.mark.parametrize(
    "filename, content, source_kind, message",
    [
        ("자료.txt", b"x", "transcript_xlsx", ".xlsx 파일만"),
        ("자료.xlsm", b"x", "transcript_xlsx", "매크로"),
        ("자료.xls", b"x", "transcript_xlsx", ".xlsx 파일만"),
        ("자료.xlsx", b"not a workbook", "transcript_xlsx", "엑셀(.xlsx) 파일이 아닙니다"),
        ("자료.xlsx", b"x", "transcript_txt", ".txt 파일만"),
        ("자료.xlsx", b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\x00" * 100, "transcript_xlsx", "비밀번호"),
    ],
)
def test_upload_rejections_are_422_and_nothing_is_saved(env, mgr, filename, content, source_kind, message):
    res = post_xlsx(env, mgr, content, filename=filename, source_kind=source_kind)
    assert res.status_code == 422 and message in res.json()["detail"]
    assert saved_files(env) == []
    with env["factory"]() as s:
        assert s.scalars(select(Meeting)).all() == []


def test_upload_size_limit_and_empty_sheet(env, mgr, monkeypatch):
    monkeypatch.setattr(settings, "transcript_upload_max_bytes", 2000)
    big = workbook_bytes([HEADER, *[["00:00:01", "김대리", "발언 " * 50] for _ in range(100)]])
    assert len(big) > 2000
    res = post_xlsx(env, mgr, big)
    assert res.status_code == 422 and "너무 큽니다" in res.json()["detail"]
    monkeypatch.undo()
    res = post_xlsx(env, mgr, workbook_bytes([]))
    assert res.status_code == 422 and "빈 파일" in res.json()["detail"]
    assert saved_files(env) == []


def test_audio_upload_and_txt_upload_are_unchanged(env, mgr):
    res = post_txt(env, mgr, "화자1: 안녕하세요 오늘 회의 시작합니다".encode("utf-8"))
    assert res.status_code == 202
    bad = env["client"].post(
        "/api/meetings/upload", headers=auth(mgr), data={"title": "x", "heldAt": HELD_AT},
        files={"file": ("자료.xlsx", workbook_bytes([HEADER, ["00:00:01", "a", "b"]]), XLSX_TYPE)},
    )
    assert bad.status_code == 422  # 85-2: 음성으로 올린 xlsx 는 자료 파일 안내
    assert bad.json()["detail"] == "자료 파일(엑셀)입니다. 자료 종류를 확인해 주세요."


# ---------------- 양식 내려받기·round-trip(작업 85-1) ----------------
def test_template_download_requires_login_and_blocks_system_admin(env, mgr):
    client = env["client"]
    assert client.get("/api/templates/transcript-xlsx").status_code == 401
    ok = client.get("/api/templates/transcript-xlsx", headers=auth(mgr))
    assert ok.status_code == 200
    assert ok.headers["content-type"].startswith("application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    assert "attachment" in ok.headers["content-disposition"]
    admin = add_account(env["factory"], "고객사A", "sysadm", "staff", name="시스템")
    from app.models import Account
    from app.services.system_admin import designate

    with env["factory"]() as session:
        designate(session, session.get(Account, admin.id), actor_id=None)
        session.commit()
    assert client.get("/api/templates/transcript-xlsx", headers=auth(admin)).status_code == 403


def test_template_has_header_only_data_sheet_and_guide_and_empty_form_is_rejected(env, mgr):
    from openpyxl import load_workbook

    content = env["client"].get("/api/templates/transcript-xlsx", headers=auth(mgr)).content
    wb = load_workbook(io.BytesIO(content))
    assert wb.sheetnames == ["회의 발언", "작성 안내"]
    assert [list(r) for r in wb["회의 발언"].iter_rows(values_only=True)] == [["시각", "화자", "발언"]]  # 예시 행 없음
    guide = " ".join(str(c) for r in wb["작성 안내"].iter_rows(values_only=True) for c in r if c)
    for word in ("mm:ss", "비워도", "필수", "매크로", "암호"):
        assert word in guide
    # 비어 있는 양식을 그대로 올리면 등록되지 않는다
    response = post_xlsx(env, mgr, content, filename="양식.xlsx")
    assert response.status_code == 422
    assert "발언 열이 비어 있습니다" in response.json()["detail"]


def test_template_round_trip_filled_rows_register(env, mgr):
    from openpyxl import load_workbook

    content = env["client"].get("/api/templates/transcript-xlsx", headers=auth(mgr)).content
    wb = load_workbook(io.BytesIO(content))
    sheet = wb["회의 발언"]
    sheet.append(["00:00:05", "김대리", "예산을 검토하겠습니다"])
    sheet.append([None, None, "시각과 화자가 없는 발언"])
    buffer = io.BytesIO()
    wb.save(buffer)
    response = post_xlsx(env, mgr, buffer.getvalue(), filename="작성한 양식.xlsx")
    assert response.status_code == 202
    utterances = xlsx_to_utterances(buffer.getvalue())
    assert [(u["speaker"], u["text"]) for u in utterances] == [("김대리", "예산을 검토하겠습니다"), ("", "시각과 화자가 없는 발언")]


def test_missing_text_column_message_points_to_template(env, mgr):
    response = post_xlsx(env, mgr, workbook_bytes([["시각", "화자"], ["00:00:01", "김대리"]]))
    assert response.status_code == 422
    assert "양식" in response.json()["detail"]


def test_unlabeled_rows_are_shown_as_written_in_transcript_api_and_stay_unchanged_on_reprocess(env, mgr):
    rows = [["시각", "발언"], ["00:00:03", "김 대리(팀): 예산 검토는 제가 하겠습니다"], [None, "화자 열이 없는 줄"]]
    res = post_xlsx(env, mgr, workbook_bytes(rows))
    meeting_id = res.json()["meetingId"]
    assert run(env, res.json()["jobId"]) == "completed"
    body = env["client"].get(f"/api/meetings/{meeting_id}/transcript", headers=auth(mgr)).json()
    assert "화자1" not in body["fullText"] and "화자1" not in body["displayText"]
    assert body["fullText"].splitlines() == ["[00:00:03] 김 대리(팀): 예산 검토는 제가 하겠습니다", "화자 열이 없는 줄"]
    assert [s["speaker"] for s in body["segments"]] == ["", ""]
    assert [s["text"] for s in body["segments"]][1] == "화자 열이 없는 줄"


def test_old_template_path_is_gone_and_new_path_is_not_shadowed_by_meeting_id_route(env, mgr):
    client = env["client"]
    # 옛 경로는 회의 번호(정수) 경로로 해석돼 정수 오류가 난다 = 남아 있지 않다
    old = client.get("/api/meetings/transcript-template", headers=auth(mgr))
    assert old.status_code == 422 and "integer" in str(old.json())
    new = client.get("/api/templates/transcript-xlsx", headers=auth(mgr))
    assert new.status_code == 200 and new.content[:2] == b"PK"


def test_audio_kind_with_xlsx_upload_gives_data_file_hint(env, mgr):
    res = post_xlsx(env, mgr, workbook_bytes([HEADER, ["00:00:01", "김대리", "발언"]]), source_kind="audio")
    assert res.status_code == 422
    assert res.json()["detail"] == "자료 파일(엑셀)입니다. 자료 종류를 확인해 주세요."
    assert saved_files(env) == []
