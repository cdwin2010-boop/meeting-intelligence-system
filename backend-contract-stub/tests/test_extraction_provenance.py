"""(v1.10.1) 추출 결과 출처 기록: action_items 의 extract_model·prompt_version·extracted_at.
내부 DB 기록만 검사한다(API 응답에는 싣지 않음). 네트워크 없이 돈다: Gemini 클라이언트는 가짜 객체로 주입한다.
구버전 파일 검사는 tmp_path 안에서 만든 파일의 '복사본'만 연다(실제 data/stub.db 와 무관)."""
import hashlib
import json
import shutil
import sqlite3
from datetime import datetime
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.config import settings
from app.db import Store, store
from app.main import app
from app.pipeline import gemini_client, service
from app.pipeline.extractor import FAKE_PROVENANCE, PROMPT_TEMPLATE, PROMPT_VERSION, FakeExtractor, make_extractor
from app.uploads import fake_transcript

client = TestClient(app)

M4A = b"\x00\x00\x00\x18ftypM4A \x00\x00\x00\x00" + b"\x00" * 1000
FORM = {"title": "출처 기록 회의", "startedAt": "2026-09-29T14:00:00+09:00"}
PROVENANCE_COLUMNS = ("extract_model", "prompt_version", "extracted_at")


@pytest.fixture(autouse=True)
def fresh(monkeypatch):
    store.reset()
    monkeypatch.setattr(settings, "fake_worker_step_seconds", 0)
    monkeypatch.setattr(settings, "fake_worker_enabled", False)  # 처리는 테스트가 직접 부른다


def upload() -> dict:
    r = client.post("/api/meetings", data=FORM, files={"file": ("A.m4a", M4A, "audio/mp4")})
    assert r.status_code == 202, r.text
    return r.json()


def provenance_rows(path, meeting_id: str) -> list[tuple]:
    """DB 파일에서 직접 읽은 (extract_model, prompt_version, extracted_at) 목록"""
    with sqlite3.connect(path) as con:
        return con.execute(
            "SELECT extract_model, prompt_version, extracted_at FROM action_items WHERE meeting_id = ? ORDER BY seq",
            (meeting_id,),
        ).fetchall()


class StubStt:
    def transcribe(self, audio_path, mime_type):
        return fake_transcript()

    def release(self):
        pass


class FakeModels:
    """Gemini models.generate_content 흉내: 정해 둔 JSON 을 돌려준다"""

    def __init__(self, text: str):
        self.text = text
        self.model = None

    def generate_content(self, *, model, contents, config=None):
        self.model = model
        return SimpleNamespace(text=self.text)


ITEMS_JSON = json.dumps({"items": [{"task": "시안 확정", "assignee": "정민수", "due_date": "2026-10-01",
                                    "quote": {"speaker": "화자1", "timestamp": "00:00:20", "text": "확정해 주세요."}}]})


def assert_iso_with_timezone(value: str) -> None:
    parsed = datetime.fromisoformat(value)
    assert parsed.tzinfo is not None


# ---------------- 새 열이 채워지는지 ----------------
def test_gemini_extraction_records_model_from_settings_and_prompt_version(monkeypatch):
    receipt = upload()  # 접수는 fake 설정으로(키 검사 없음). 처리 단계에서만 Gemini 추출기를 쓴다
    models = FakeModels(ITEMS_JSON)
    monkeypatch.setattr(settings, "llm_provider", "gemini")
    monkeypatch.setattr(settings, "gemini_llm_model", "test-llm-model-from-settings")
    monkeypatch.setattr(gemini_client, "make_gemini_client", lambda: SimpleNamespace(models=models))

    service.process_job(receipt["jobId"], stt_factory=StubStt, extractor_factory=make_extractor)

    assert models.model == "test-llm-model-from-settings"  # 실제로 그 모델로 요청했고
    [(model, version, extracted_at)] = provenance_rows(store.path, receipt["meetingId"])
    assert model == "test-llm-model-from-settings"  # 같은 이름이 기록된다(설정값에서 읽음)
    assert version == PROMPT_VERSION
    assert_iso_with_timezone(extracted_at)


def test_fake_extractor_records_fake():
    receipt = upload()
    service.process_job(receipt["jobId"], stt_factory=StubStt, extractor_factory=FakeExtractor)
    rows = provenance_rows(store.path, receipt["meetingId"])
    assert len(rows) == 2
    for model, version, extracted_at in rows:
        assert (model, version) == (FAKE_PROVENANCE, FAKE_PROVENANCE) == ("fake", "fake")
        assert_iso_with_timezone(extracted_at)


def test_fake_worker_path_records_fake_too(monkeypatch):
    monkeypatch.setattr(settings, "fake_worker_enabled", True)  # STT·LLM 둘 다 fake → 가짜 처리기(run_fake_worker)
    receipt = upload()
    assert client.get(f"/api/jobs/{receipt['jobId']}").json()["status"] == "completed"
    rows = provenance_rows(store.path, receipt["meetingId"])
    assert len(rows) == 2
    assert all(r[:2] == ("fake", "fake") and r[2] for r in rows)


def test_extractor_without_provenance_attributes_records_null():
    class BareExtractor:  # 출처 속성이 없는 추출기 → 알 수 없음(NULL)
        def extract(self, transcript, started_at):
            return [{"task": "x", "assignee": "", "dueDate": "", "quote": {"speaker": "", "timestamp": "", "text": ""}}]

    receipt = upload()
    service.process_job(receipt["jobId"], stt_factory=StubStt, extractor_factory=BareExtractor)
    [(model, version, extracted_at)] = provenance_rows(store.path, receipt["meetingId"])
    assert (model, version) == (None, None)
    assert_iso_with_timezone(extracted_at)


def test_seed_items_have_null_provenance():
    assert provenance_rows(store.path, "mtg-2026-0925") == [(None, None, None)] * 6


def test_prompt_version_is_sha256_prefix_of_fixed_template():
    assert PROMPT_VERSION == hashlib.sha256(PROMPT_TEMPLATE.encode("utf-8")).hexdigest()[:12]
    assert len(PROMPT_VERSION) == 12 and int(PROMPT_VERSION, 16) >= 0
    # 템플릿에는 회의마다 바뀌는 값이 자리 표시로만 들어 있다(전사문·회의 일시가 버전에 섞이지 않음)
    assert "{transcript}" in PROMPT_TEMPLATE and "{started_at}" in PROMPT_TEMPLATE


def test_api_response_does_not_expose_provenance():
    receipt = upload()
    service.process_job(receipt["jobId"], stt_factory=StubStt, extractor_factory=FakeExtractor)
    items = client.get(f"/api/meetings/{receipt['meetingId']}/action-items").json()
    assert items and all(set(i) == {"id", "task", "assignee", "dueDate", "status", "quote"} for i in items)


# ---------------- 구버전 파일(열 없음)의 복사본을 열기 ----------------
V1_10_0_SCHEMA = """
CREATE TABLE meetings (id TEXT PRIMARY KEY, body TEXT NOT NULL, transcript TEXT,
    speaker_names TEXT NOT NULL DEFAULT '{}');
CREATE TABLE action_items (
    seq INTEGER PRIMARY KEY AUTOINCREMENT, id TEXT UNIQUE, meeting_id TEXT, task TEXT,
    assignee TEXT, due_date TEXT, status TEXT, quote TEXT);
CREATE TABLE jobs (
    seq INTEGER PRIMARY KEY AUTOINCREMENT, id TEXT UNIQUE, meeting_title TEXT, audio_seconds INTEGER,
    elapsed_seconds INTEGER, status TEXT, attempt INTEGER, started_at TEXT, worker TEXT, error_log TEXT,
    meeting_id TEXT, engine TEXT);
CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
"""


def test_old_file_copy_keeps_rows_and_new_columns_are_null(tmp_path):
    original = tmp_path / "v1.10.0.db"
    con = sqlite3.connect(original)
    con.executescript(V1_10_0_SCHEMA)
    body = {"id": "mtg-old", "title": "예전 회의", "startedAt": "2026-09-01T10:00:00+09:00", "attendees": []}
    con.execute("INSERT INTO meetings (id, body, transcript, speaker_names) VALUES (?, ?, ?, ?)",
                ("mtg-old", json.dumps(body, ensure_ascii=False), "[00:00:01] 화자1: 예전 원문", '{"화자1": "권 부장"}'))
    quote = json.dumps({"speaker": "화자1", "timestamp": "00:00:01", "text": "예전 원문"}, ensure_ascii=False)
    con.executemany(
        "INSERT INTO action_items (id, meeting_id, task, assignee, due_date, status, quote) VALUES (?,?,?,?,?,?,?)",
        [("AI-old-1", "mtg-old", "예전 업무 1", "권 부장", "2026-09-05", "open", quote),
         ("AI-old-2", "mtg-old", "예전 업무 2", "", "", "done", quote)],
    )
    con.execute("INSERT INTO meta (key, value) VALUES ('seeded', '2026-09-01T00:00:00+00:00')")
    con.commit()
    con.close()

    copy = tmp_path / "copy.db"
    shutil.copyfile(original, copy)
    original_bytes = original.read_bytes()

    opened = Store(copy)  # 서버 시작과 같다: 빠진 열만 보강

    # 기존 데이터는 그대로
    items = opened.list_action_items("mtg-old", None, "asc")
    assert [(i["id"], i["task"], i["assignee"], i["dueDate"], i["status"]) for i in items] == [
        ("AI-old-1", "예전 업무 1", "권 부장", "2026-09-05", "open"),
        ("AI-old-2", "예전 업무 2", "", "", "done"),
    ]
    meeting = opened.get_meeting("mtg-old")
    assert meeting["transcriptText"] == "[00:00:01] 화자1: 예전 원문"
    assert meeting["speakerNames"] == {"화자1": "권 부장"}
    assert [m["id"] for m in opened.list_meetings()] == ["mtg-old"]  # 시드를 다시 넣지 않는다

    # 새 열은 생겼고 기존 행은 NULL(알 수 없음)
    with sqlite3.connect(copy) as check:
        columns = {r[1] for r in check.execute("PRAGMA table_info(action_items)")}
    assert set(PROVENANCE_COLUMNS) <= columns
    assert provenance_rows(copy, "mtg-old") == [(None, None, None)] * 2

    # 원본 파일은 건드리지 않았다
    assert original.read_bytes() == original_bytes
