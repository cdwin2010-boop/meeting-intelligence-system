"""(v1.9.10) 파일 SQLite 저장: 재시작(= Store 를 같은 파일로 다시 열기)해도 데이터·작업 번호가 이어지고,
시드는 처음 한 번만, 예전 스키마 파일은 열만 보강(데이터 유지), 초기화는 --yes 로만.
모든 테스트는 tmp_path 안의 파일만 쓴다(실제 data/stub.db 와 무관)."""
import json
import sqlite3

import pytest

from app.config import settings
from app.db import Store
from scripts import reset_db


def seed_counts(store: Store) -> tuple[int, int, int]:
    return (len(store.list_meetings()), len(store.list_action_items("mtg-2026-0925", None, "asc")), len(store.list_jobs(None, "asc")))


def test_new_file_gets_seed_once_and_data_survives_reopen(tmp_path):
    path = tmp_path / "stub.db"
    first = Store(path)
    assert path.is_file()
    assert seed_counts(first) == (1, 6, 9)
    meeting_id, job_id = first.create_upload("재시작 테스트", "2026-09-30T10:00:00+09:00", "gemini-api", 60)
    first.set_speaker_names(meeting_id, {"화자1": "권영우 부장"})
    first.delete_action_item("AI-001")

    again = Store(path)  # 서버 재시작과 같다
    assert seed_counts(again) == (2, 5, 10)  # 시드를 다시 넣지 않고, 지운 항목도 되살리지 않는다
    assert again.get_meeting(meeting_id)["speakerNames"] == {"화자1": "권영우 부장"}
    assert again.get_job_status(job_id)["meetingId"] == meeting_id


def test_job_numbers_continue_after_reopen(tmp_path):
    path = tmp_path / "stub.db"
    _, job1 = Store(path).create_upload("첫 회의", "2026-09-30T10:00:00+09:00", "gemini-api", 60)
    _, job2 = Store(path).create_upload("둘째 회의", "2026-09-30T11:00:00+09:00", "gemini-api", 60)
    assert job1 != job2
    assert int(job2.rsplit("-", 1)[1]) == int(job1.rsplit("-", 1)[1]) + 1  # 번호가 초기화·중복되지 않는다


def test_old_schema_file_is_upgraded_without_losing_rows(tmp_path):
    """speaker_names·engine 열이 없던 예전 파일을 열면 열만 추가되고 기존 행은 그대로"""
    path = tmp_path / "old.db"
    con = sqlite3.connect(path)
    con.executescript(
        """
        CREATE TABLE meetings (id TEXT PRIMARY KEY, body TEXT NOT NULL, transcript TEXT);
        CREATE TABLE action_items (seq INTEGER PRIMARY KEY AUTOINCREMENT, id TEXT UNIQUE, meeting_id TEXT, task TEXT,
            assignee TEXT, due_date TEXT, status TEXT, quote TEXT);
        CREATE TABLE jobs (seq INTEGER PRIMARY KEY AUTOINCREMENT, id TEXT UNIQUE, meeting_title TEXT, audio_seconds INTEGER,
            elapsed_seconds INTEGER, status TEXT, attempt INTEGER, started_at TEXT, worker TEXT, error_log TEXT, meeting_id TEXT);
        """
    )
    body = {"id": "mtg-old", "title": "예전 회의", "startedAt": "2026-09-01T10:00:00+09:00", "attendees": []}
    con.execute("INSERT INTO meetings (id, body, transcript) VALUES (?, ?, ?)",
                ("mtg-old", json.dumps(body, ensure_ascii=False), "[00:00:01] 화자1: 예전 원문"))
    con.commit()
    con.close()

    store = Store(path)
    meeting = store.get_meeting("mtg-old")
    assert meeting["transcriptText"] == "[00:00:01] 화자1: 예전 원문"
    assert meeting["speakerNames"] == {}
    assert [m["id"] for m in store.list_meetings()] == ["mtg-old"]  # 데이터가 있던 파일에는 시드를 넣지 않는다
    columns = {r[1] for r in sqlite3.connect(path).execute("PRAGMA table_info(jobs)")}
    assert "engine" in columns


def test_reset_script_needs_yes(tmp_path, monkeypatch, capsys):
    path = tmp_path / "stub.db"
    uploads = tmp_path / "uploads"
    uploads.mkdir()
    (uploads / "JOB-20260930-010.mp3").write_bytes(b"ID3")
    (uploads / "memo.txt").write_text("keep")
    monkeypatch.setattr(settings, "stub_db_path", path)
    monkeypatch.setattr(settings, "upload_dir", uploads)
    Store(path).create_upload("지우면 안 되는 회의", "2026-09-30T10:00:00+09:00", "gemini-api", 60)

    # --yes 없이: 아무것도 바꾸지 않는다 (잘못된 옵션도 마찬가지)
    assert reset_db.main([]) == 0
    assert reset_db.main(["--force"]) == 2
    assert seed_counts(Store(path))[0] == 2
    assert (uploads / "JOB-20260930-010.mp3").exists()

    # --yes: 시드로 되돌리고 업로드 음성만 지운다 (그 밖의 파일은 그대로)
    assert reset_db.main(["--yes"]) == 0
    assert seed_counts(Store(path)) == (1, 6, 9)
    assert not (uploads / "JOB-20260930-010.mp3").exists()
    assert (uploads / "memo.txt").exists()
    assert "화자" not in capsys.readouterr().out  # 전사 원문 같은 내용은 출력하지 않는다(건수·경로만)


def test_pytest_never_uses_the_real_db_file():
    from app.db import store

    from app import config

    real_default = config._DATA_DIR.resolve()
    assert "stub-pytest-db-" in str(store.path)  # conftest 가 만든 임시 파일
    assert real_default not in store.path.resolve().parents  # 저장소의 data 폴더가 아님


@pytest.mark.parametrize("name", ["stub.db", "stub.db-journal", "stub.db-wal", "uploads/JOB-1.mp3"])
def test_reload_does_not_watch_data_files(name):
    """uvicorn --reload 기본 감시 대상은 *.py 뿐 → data 폴더의 DB·음성 파일 변경은 재시작을 일으키지 않는다"""
    from pathlib import Path

    from uvicorn.config import Config
    from uvicorn.supervisors.watchfilesreload import FileFilter

    file_filter = FileFilter(Config("app.main:app", reload=True))
    assert file_filter(Path("data") / name) is False
