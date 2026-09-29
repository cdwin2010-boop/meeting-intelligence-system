"""
참고용 저장소: 메모리 SQLite + seed.json.
운영 백엔드에서는 이 파일 대신 실제 DB 계층을 씁니다. 여기서 봐야 할 것은 두 가지입니다.
  1) 정렬 열은 사용자 입력이 아니라 "허용 목록(dict)"에서 꺼낸 값만 SQL에 쓴다.
  2) kill/retry는 "조건부 UPDATE + 바뀐 행 수(rowcount)"로 확인과 변경을 한 번에 한다.
"""
import json
import sqlite3
import threading
from pathlib import Path

_SEED = json.loads((Path(__file__).parent / "seed.json").read_text(encoding="utf-8"))

# 허용 정렬 열: API 이름(camelCase) -> SQL 식. 여기에 없으면 400.
ACTION_ITEM_SORT = {
    "id": "id",
    "task": "task",
    "assignee": "assignee",
    "dueDate": "due_date",
    "status": "CASE status WHEN 'overdue' THEN 0 WHEN 'in_progress' THEN 1 WHEN 'open' THEN 2 ELSE 3 END",
}
JOB_SORT = {
    "id": "id",
    "meetingTitle": "meeting_title",
    "audioSeconds": "audio_seconds",
    "elapsedSeconds": "COALESCE(elapsed_seconds, -1)",
    "status": "CASE status WHEN 'failed' THEN 0 WHEN 'processing' THEN 1 WHEN 'queued' THEN 2 ELSE 3 END",
}


class Store:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._db = sqlite3.connect(":memory:", check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self.reset()

    def reset(self) -> None:
        with self._lock:
            d = self._db
            d.executescript(
                """
                DROP TABLE IF EXISTS action_items; DROP TABLE IF EXISTS jobs; DROP TABLE IF EXISTS meetings;
                CREATE TABLE meetings (id TEXT PRIMARY KEY, body TEXT NOT NULL);
                CREATE TABLE action_items (
                    seq INTEGER PRIMARY KEY AUTOINCREMENT, id TEXT UNIQUE, meeting_id TEXT, task TEXT,
                    assignee TEXT, due_date TEXT, status TEXT, quote TEXT);
                CREATE TABLE jobs (
                    seq INTEGER PRIMARY KEY AUTOINCREMENT, id TEXT UNIQUE, meeting_title TEXT, audio_seconds INTEGER,
                    elapsed_seconds INTEGER, status TEXT, attempt INTEGER, started_at TEXT, worker TEXT, error_log TEXT);
                """
            )
            m = _SEED["meeting"]
            d.execute("INSERT INTO meetings VALUES (?, ?)", (m["id"], json.dumps(m, ensure_ascii=False)))
            for a in _SEED["actionItems"]:
                d.execute(
                    "INSERT INTO action_items (id, meeting_id, task, assignee, due_date, status, quote) VALUES (?,?,?,?,?,?,?)",
                    (a["id"], m["id"], a["task"], a["assignee"], a["dueDate"], a["status"], json.dumps(a["quote"], ensure_ascii=False)),
                )
            for j in _SEED["jobs"]:
                d.execute(
                    "INSERT INTO jobs (id, meeting_title, audio_seconds, elapsed_seconds, status, attempt, started_at, worker, error_log)"
                    " VALUES (?,?,?,?,?,?,?,?,?)",
                    (j["id"], j["meetingTitle"], j["audioSeconds"], j["elapsedSeconds"], j["status"], j["attempt"],
                     j["startedAt"], json.dumps(j["worker"], ensure_ascii=False), json.dumps(j["errorLog"], ensure_ascii=False)),
                )
            d.commit()

    # ---------- 회의 / 액션아이템 ----------
    def get_meeting(self, meeting_id: str) -> dict | None:
        with self._lock:
            row = self._db.execute("SELECT body FROM meetings WHERE id = ?", (meeting_id,)).fetchone()
        return json.loads(row["body"]) if row else None

    def list_action_items(self, meeting_id: str, sort_key: str | None, direction: str) -> list[dict]:
        order = f"ORDER BY {ACTION_ITEM_SORT[sort_key]} {'DESC' if direction == 'desc' else 'ASC'}, seq" if sort_key else "ORDER BY seq"
        with self._lock:
            rows = self._db.execute(f"SELECT * FROM action_items WHERE meeting_id = ? {order}", (meeting_id,)).fetchall()
        return [
            {"id": r["id"], "task": r["task"], "assignee": r["assignee"], "dueDate": r["due_date"],
             "status": r["status"], "quote": json.loads(r["quote"])}
            for r in rows
        ]

    def delete_action_item(self, item_id: str) -> bool:
        with self._lock:
            cur = self._db.execute("DELETE FROM action_items WHERE id = ?", (item_id,))
            self._db.commit()
            return cur.rowcount > 0

    # ---------- 작업 큐 ----------
    @staticmethod
    def _job(r: sqlite3.Row) -> dict:
        return {
            "id": r["id"], "meetingTitle": r["meeting_title"], "audioSeconds": r["audio_seconds"],
            "elapsedSeconds": r["elapsed_seconds"], "status": r["status"], "attempt": r["attempt"],
            "startedAt": r["started_at"], "worker": json.loads(r["worker"]), "errorLog": json.loads(r["error_log"]),
        }

    def get_job(self, job_id: str) -> dict | None:
        with self._lock:
            r = self._db.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        return self._job(r) if r else None

    def list_jobs(self, sort_key: str | None, direction: str) -> list[dict]:
        order = f"ORDER BY {JOB_SORT[sort_key]} {'DESC' if direction == 'desc' else 'ASC'}, seq" if sort_key else "ORDER BY seq"
        with self._lock:
            rows = self._db.execute(f"SELECT * FROM jobs {order}").fetchall()
        return [self._job(r) for r in rows]

    def kill_if_processing(self, job_id: str, log_line: str) -> bool:
        """processing 일 때만 failed 로 바꾼다. 바뀐 행이 없으면 False (없거나, 이미 끝난 것)."""
        with self._lock:
            cur = self._db.execute(
                "UPDATE jobs SET status = 'failed', error_log = ? WHERE id = ? AND status = 'processing'",
                (json.dumps([log_line], ensure_ascii=False), job_id),
            )
            self._db.commit()
            return cur.rowcount == 1

    def retry_if_failed(self, job_id: str) -> bool:
        """failed 일 때만 queued 로 되돌린다(시도 횟수 +1)."""
        with self._lock:
            cur = self._db.execute(
                "UPDATE jobs SET status = 'queued', attempt = attempt + 1, elapsed_seconds = NULL,"
                " started_at = NULL, worker = 'null', error_log = '[]' WHERE id = ? AND status = 'failed'",
                (job_id,),
            )
            self._db.commit()
            return cur.rowcount == 1


store = Store()
