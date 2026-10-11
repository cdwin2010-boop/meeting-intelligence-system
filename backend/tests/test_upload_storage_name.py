"""업로드 저장 파일명 규칙(작업 76-1): {회의록id}_{YYYYMMDDHH}_{순번3자리}{확장자}. 임시 DB·임시 폴더·fake 엔진만 쓴다."""
import re
import threading
from datetime import datetime, timezone

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Meeting, SourceDocument
from app.services import upload_storage
from app.services.processing import process_meeting
from app.services.upload_storage import claim_stored_file, stored_filename
from tests.test_transcript_txt import TIMED, post_txt  # noqa: F401
from tests.test_upload_processing import add_account, auth, env, saved_files, upload  # noqa: F401  (env 는 픽스처)

NAME = re.compile(r"(\d+)_(\d{10})_(\d{3,})\.[a-z0-9]+")


@pytest.fixture
def mgr(env):
    return add_account(env["factory"], "고객사A", "mgr", "manager", name="박관리")


def _doc_path(env, meeting_id) -> str:
    with env["factory"]() as s:
        return s.get(SourceDocument, s.get(Meeting, meeting_id).source_document_id).file_path


# ---------------- 파일명 생성 함수 ----------------
def test_filename_format_and_lowercase_ext():
    at = datetime(2026, 10, 14, 0, 30, tzinfo=timezone.utc)  # 한국 09:30
    assert stored_filename(41, at, 1, "M4A") == "41_2026101409_001.m4a"
    assert stored_filename(41, at, 1, ".wav") == "41_2026101409_001.wav"


def test_filename_uses_korea_time_boundary():
    # UTC 15시는 한국 다음 날 0시
    assert stored_filename(7, datetime(2026, 10, 13, 15, 0, tzinfo=timezone.utc), 2, "mp3") == "7_2026101400_002.mp3"
    assert stored_filename(7, datetime(2026, 10, 13, 14, 59, tzinfo=timezone.utc), 2, "mp3") == "7_2026101323_002.mp3"
    assert stored_filename(7, datetime(2026, 10, 13, 15, 0), 2, "mp3") == "7_2026101400_002.mp3"  # 시간대 없는 값은 UTC 로 본다


def test_filename_sequence_grows_past_999():
    at = datetime(2026, 10, 14, tzinfo=timezone.utc)
    assert stored_filename(1, at, 999, "m4a").endswith("_999.m4a")
    assert stored_filename(1, at, 1000, "m4a").endswith("_1000.m4a")


@pytest.mark.parametrize("ext", ["../x", "m4a/", "한글", "a b", ""])
def test_filename_rejects_unsafe_ext(ext):
    with pytest.raises(ValueError):
        stored_filename(1, datetime(2026, 10, 14, tzinfo=timezone.utc), 1, ext)


# ---------------- 순번·덮어쓰기 방지 ----------------
def test_claim_never_overwrites_and_takes_next_sequence(tmp_path):
    at = datetime(2026, 10, 14, tzinfo=timezone.utc)
    existing = tmp_path / stored_filename(5, at, 1, "m4a")
    existing.write_bytes(b"old")
    staged = tmp_path / "x.part"
    staged.write_bytes(b"new")
    target = claim_stored_file(staged, tmp_path, 5, at, "m4a")
    assert target.name.endswith("_002.m4a") and target.read_bytes() == b"new"
    assert existing.read_bytes() == b"old" and not staged.exists()


def test_claim_fails_safely_when_no_slot(tmp_path, monkeypatch):
    monkeypatch.setattr(upload_storage, "_MAX_ATTEMPTS", 2)
    at = datetime(2026, 10, 14, tzinfo=timezone.utc)
    for n in (1, 2):
        (tmp_path / stored_filename(5, at, n, "m4a")).write_bytes(b"old")
    staged = tmp_path / "x.part"
    staged.write_bytes(b"new")
    with pytest.raises(FileExistsError):
        claim_stored_file(staged, tmp_path, 5, at, "m4a")
    assert (tmp_path / stored_filename(5, at, 1, "m4a")).read_bytes() == b"old"


def test_concurrent_claims_get_distinct_sequences(tmp_path):
    at = datetime(2026, 10, 14, tzinfo=timezone.utc)
    results: list[str] = []
    errors: list[BaseException] = []

    def worker(i: int) -> None:
        try:
            staged = tmp_path / f"{i}.part"
            staged.write_bytes(str(i).encode())
            results.append(claim_stored_file(staged, tmp_path, 9, at, "m4a").name)
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(12)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors and len(set(results)) == 12
    assert sorted(int(NAME.fullmatch(n).group(3)) for n in results) == list(range(1, 13))
    # 각 파일에는 서로 다른 내용이 남아 있다(덮어쓰기 없음)
    assert sorted(p.read_bytes() for p in tmp_path.glob("9_*")) == sorted(str(i).encode() for i in range(12))


# ---------------- 업로드 흐름 ----------------
def test_upload_saves_with_new_name_and_keeps_original_name_as_title(env, mgr):
    res = upload(env, mgr, filename="../../회의 녹음 최종.M4A", title="")
    assert res.status_code == 202
    [saved] = saved_files(env)
    m = NAME.fullmatch(saved.name)
    assert m and int(m.group(1)) == res.json()["meetingId"] and m.group(3) == "001"
    assert saved.suffix == ".m4a" and saved.parent.name == str(mgr.tenant_id)  # 폴더 구조 불변
    assert "회의" not in saved.name and ".." not in saved.name and " " not in saved.name
    assert _doc_path(env, res.json()["meetingId"]) == f"{mgr.tenant_id}/{saved.name}"
    detail = env["client"].get(f"/api/meetings/{res.json()['meetingId']}", headers=auth(mgr)).json()
    assert detail["title"] == "회의 녹음 최종.M4A"  # 원본 파일명이 제목으로 보존된다


def test_two_uploads_have_distinct_names_matching_their_meeting_ids(env, mgr):
    a = upload(env, mgr).json()["meetingId"]
    b = upload(env, mgr).json()["meetingId"]
    names = {p.name for p in saved_files(env)}
    assert {int(NAME.fullmatch(n).group(1)) for n in names} == {a, b}


def test_txt_upload_uses_new_name_and_pipeline_reads_it(env, mgr):
    res = post_txt(env, mgr, TIMED.encode())
    assert res.status_code == 202
    [saved] = saved_files(env)
    assert NAME.fullmatch(saved.name) and saved.suffix == ".txt"
    assert process_meeting(res.json()["jobId"], session_factory=env["factory"]) == "completed"


def test_audio_pipeline_and_playback_with_new_name(env, mgr):
    res = upload(env, mgr)
    meeting_id = res.json()["meetingId"]
    assert process_meeting(res.json()["jobId"], session_factory=env["factory"]) == "completed"
    assert env["client"].get(f"/api/meetings/{meeting_id}/audio-url", headers=auth(mgr)).status_code == 200
    detail = env["client"].get(f"/api/meetings/{meeting_id}", headers=auth(mgr)).json()
    assert detail["hasAudio"] is True and detail["sourceKind"] == "audio"


def test_legacy_named_file_still_works(env, mgr):
    res = upload(env, mgr)
    meeting_id = res.json()["meetingId"]
    [saved] = saved_files(env)
    legacy = saved.parent / ("a" * 32 + ".m4a")
    saved.rename(legacy)
    with env["factory"]() as s:
        s.get(SourceDocument, s.get(Meeting, meeting_id).source_document_id).file_path = f"{mgr.tenant_id}/{legacy.name}"
        s.commit()
    assert process_meeting(res.json()["jobId"], session_factory=env["factory"]) == "completed"
    assert env["client"].get(f"/api/meetings/{meeting_id}/audio-url", headers=auth(mgr)).status_code == 200


# ---------------- 실패·검증 ----------------
@pytest.mark.parametrize(
    "kwargs, status",
    [
        ({"filename": "a.exe"}, 415),
        ({"content": b""}, 400),
        ({"held_at": "2026-09-29T14:00:00"}, 400),
    ],
)
def test_validation_errors_leave_no_file(env, mgr, kwargs, status):
    res = upload(env, mgr, **kwargs)
    assert res.status_code == status
    assert saved_files(env) == []


def test_txt_validation_error_leaves_no_file(env, mgr):
    assert post_txt(env, mgr, b"\x00\x01binary").status_code == 422
    assert saved_files(env) == []


def test_error_while_storing_file_rolls_back_and_cleans_up(env, mgr, monkeypatch):
    def boom(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr("app.api.meetings.claim_stored_file", boom)
    with pytest.raises(OSError):
        upload(env, mgr)
    assert saved_files(env) == []
    with env["factory"]() as s:
        assert s.scalars(select(Meeting)).all() == [] and s.scalars(select(SourceDocument)).all() == []


def test_commit_error_rolls_back_and_deletes_saved_file(env, mgr, monkeypatch):
    def boom(self):
        raise RuntimeError("commit failed")

    monkeypatch.setattr(Session, "commit", boom)
    with pytest.raises(RuntimeError):
        upload(env, mgr)
    monkeypatch.undo()
    assert saved_files(env) == []  # 최종 이름으로 저장된 파일도 지워진다
    with env["factory"]() as s:
        assert s.scalars(select(Meeting)).all() == []
