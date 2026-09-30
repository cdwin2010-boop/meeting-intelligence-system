"""
참고용 저장소: 메모리 SQLite + seed.json.
운영 백엔드에서는 이 파일 대신 실제 DB 계층을 씁니다. 여기서 봐야 할 것은 두 가지입니다.
  1) 정렬 열은 사용자 입력이 아니라 "허용 목록(dict)"에서 꺼낸 값만 SQL에 쓴다.
  2) kill/retry는 "조건부 UPDATE + 바뀐 행 수(rowcount)"로 확인과 변경을 한 번에 한다.
"""
import json
import sqlite3
import threading
from datetime import datetime, timedelta, timezone
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


_KST = timezone(timedelta(hours=9))


def _instant(value: str) -> datetime:
    """정렬용 시각. 시간대가 없으면 KST(화면 입력 기준)로 보고, 읽을 수 없으면 가장 과거로 보낸다."""
    try:
        parsed = datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return datetime.min.replace(tzinfo=timezone.utc)
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=_KST)


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
                CREATE TABLE meetings (id TEXT PRIMARY KEY, body TEXT NOT NULL, transcript TEXT,
                    speaker_names TEXT NOT NULL DEFAULT '{}');
                CREATE TABLE action_items (
                    seq INTEGER PRIMARY KEY AUTOINCREMENT, id TEXT UNIQUE, meeting_id TEXT, task TEXT,
                    assignee TEXT, due_date TEXT, status TEXT, quote TEXT);
                CREATE TABLE jobs (
                    seq INTEGER PRIMARY KEY AUTOINCREMENT, id TEXT UNIQUE, meeting_title TEXT, audio_seconds INTEGER,
                    elapsed_seconds INTEGER, status TEXT, attempt INTEGER, started_at TEXT, worker TEXT, error_log TEXT,
                    meeting_id TEXT, engine TEXT);
                """
            )
            m = _SEED["meeting"]
            d.execute("INSERT INTO meetings (id, body) VALUES (?, ?)", (m["id"], json.dumps(m, ensure_ascii=False)))
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
            row = self._db.execute(
                "SELECT body, transcript, speaker_names FROM meetings WHERE id = ?", (meeting_id,)
            ).fetchone()
        if row is None:
            return None
        return {**json.loads(row["body"]), "transcriptText": row["transcript"],
                "speakerNames": json.loads(row["speaker_names"])}

    def set_speaker_names(self, meeting_id: str, names: dict[str, str]) -> bool:
        """화자 이름 매핑을 통째로 바꾼다(전체 교체). 회의가 없으면 False. 액션아이템·전사 원본은 건드리지 않는다."""
        with self._lock:
            cur = self._db.execute(
                "UPDATE meetings SET speaker_names = ? WHERE id = ?",
                (json.dumps(names, ensure_ascii=False), meeting_id),
            )
            self._db.commit()
            return cur.rowcount == 1

    def list_meetings(self) -> list[dict]:
        """회의 목록 (GET /meetings). 작업이 여러 개면 가장 최근(seq 최대) 작업의 상태, 작업이 없는 시드 회의는 None.
        정렬은 startedAt 내림차순, 같으면 id 내림차순으로 고정한다."""
        with self._lock:
            rows = self._db.execute(
                """
                SELECT m.body,
                       (SELECT j.status FROM jobs j WHERE j.meeting_id = m.id ORDER BY j.seq DESC LIMIT 1) AS job_status
                FROM meetings m
                """
            ).fetchall()
        meetings = []
        for r in rows:
            body = json.loads(r["body"])
            meetings.append(
                {"id": body["id"], "title": body["title"], "startedAt": body["startedAt"], "jobStatus": r["job_status"]}
            )
        # startedAt은 시간대 표기(+09:00, Z 등)가 섞일 수 있어 문자열이 아니라 시각으로 비교한다
        meetings.sort(key=lambda m: (_instant(m["startedAt"]), m["id"]), reverse=True)
        return meetings

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

    # ---------- 음성 등록 (POST /meetings) ----------
    def create_upload(self, title: str, started_at_iso: str, engine: str, audio_seconds: int) -> tuple[str, str]:
        """회의 + 작업을 '한 번에' 저장하고 (meetingId, jobId)를 돌려준다. 중간에 실패하면 둘 다 저장되지 않는다."""
        with self._lock:
            d = self._db
            try:
                next_no = d.execute("SELECT COALESCE(MAX(seq), 0) + 1 FROM jobs").fetchone()[0]
                today = datetime.now(timezone.utc).strftime("%Y%m%d")
                job_id = f"JOB-{today}-{next_no:03d}"
                meeting_id = f"mtg-{today}-{next_no:03d}"
                body = {"id": meeting_id, "title": title, "startedAt": started_at_iso, "attendees": []}
                d.execute("INSERT INTO meetings (id, body) VALUES (?, ?)", (meeting_id, json.dumps(body, ensure_ascii=False)))
                d.execute(
                    "INSERT INTO jobs (id, meeting_title, audio_seconds, elapsed_seconds, status, attempt, started_at,"
                    " worker, error_log, meeting_id, engine) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                    (job_id, title, audio_seconds, None, "queued", 1, None, "null", "[]", meeting_id, engine),
                )
                d.commit()
            except Exception:
                d.rollback()
                raise
        return meeting_id, job_id

    def job_meeting_id(self, job_id: str) -> str:
        with self._lock:
            r = self._db.execute("SELECT meeting_id FROM jobs WHERE id = ?", (job_id,)).fetchone()
        return (r["meeting_id"] or "") if r else ""

    def get_job_status(self, job_id: str) -> dict | None:
        with self._lock:
            r = self._db.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        if r is None:
            return None
        worker = json.loads(r["worker"])
        errors = json.loads(r["error_log"])
        return {
            "id": r["id"], "meetingId": r["meeting_id"] or "", "status": r["status"],
            "stage": worker["stage"] if worker else None,
            "errorMessage": errors[-1] if errors and r["status"] == "failed" else "",
        }

    # ---------- 가짜 처리기가 쓰는 '조건부 UPDATE' (kill 과 경쟁해도 안전) ----------
    def start_if_queued(self, job_id: str) -> bool:
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        with self._lock:
            row = self._db.execute("SELECT engine FROM jobs WHERE id = ?", (job_id,)).fetchone()
            engine = (row["engine"] if row else None) or "gemini-api"
            worker = {"id": "stub-worker-1", "host": "stub-host", "engine": engine, "stage": "STT",
                      "gpu": "RTX 3070 8GB" if engine == "faster-whisper" else None}
            cur = self._db.execute(
                "UPDATE jobs SET status = 'processing', started_at = ?, elapsed_seconds = 0, worker = ?"
                " WHERE id = ? AND status = 'queued'",
                (now, json.dumps(worker, ensure_ascii=False), job_id),
            )
            self._db.commit()
            return cur.rowcount == 1

    def set_stage_if_processing(self, job_id: str, stage: str) -> bool:
        with self._lock:
            row = self._db.execute("SELECT worker FROM jobs WHERE id = ? AND status = 'processing'", (job_id,)).fetchone()
            if row is None:
                return False
            worker = json.loads(row["worker"])
            worker["stage"] = stage
            # 잠금 안에서 읽고 바로 쓰므로 그 사이에 끼어드는 요청이 없다. 조건(status)도 다시 건다.
            cur = self._db.execute(
                "UPDATE jobs SET worker = ? WHERE id = ? AND status = 'processing'",
                (json.dumps(worker, ensure_ascii=False), job_id),
            )
            self._db.commit()
            return cur.rowcount == 1

    def fail_if_processing(self, job_id: str, log_line: str) -> bool:
        """처리 중일 때만 failed 로 바꾸고 실패 이유 한 줄을 남긴다. (그 사이 Kill 되었으면 False, 덮어쓰지 않음)"""
        with self._lock:
            cur = self._db.execute(
                "UPDATE jobs SET status = 'failed', error_log = ? WHERE id = ? AND status = 'processing'",
                (json.dumps([log_line], ensure_ascii=False), job_id),
            )
            self._db.commit()
            return cur.rowcount == 1

    def complete_if_processing(self, job_id: str, transcript: str, items: list[dict]) -> bool:
        """처리 중일 때만 completed 로 바꾸고, 같은 트랜잭션에서 전사 원문과 액션아이템을 저장한다."""
        with self._lock:
            d = self._db
            try:
                row = d.execute("SELECT meeting_id, started_at FROM jobs WHERE id = ?", (job_id,)).fetchone()
                elapsed = 0
                if row and row["started_at"]:
                    elapsed = max(0, int((datetime.now(timezone.utc) - datetime.fromisoformat(row["started_at"])).total_seconds()))
                cur = d.execute(
                    "UPDATE jobs SET status = 'completed', elapsed_seconds = ? WHERE id = ? AND status = 'processing'",
                    (elapsed, job_id),
                )
                if cur.rowcount != 1:  # 그 사이 kill 되었다면 결과를 저장하지 않는다
                    d.rollback()
                    return False
                meeting_id = row["meeting_id"]
                d.execute("UPDATE meetings SET transcript = ? WHERE id = ?", (transcript, meeting_id))
                for n, a in enumerate(items, start=1):
                    d.execute(
                        "INSERT INTO action_items (id, meeting_id, task, assignee, due_date, status, quote) VALUES (?,?,?,?,?,?,?)",
                        (f"AI-{job_id[4:]}-{n}", meeting_id, a["task"], a["assignee"], a["dueDate"], "open",
                         json.dumps(a["quote"], ensure_ascii=False)),
                    )
                d.commit()
                return True
            except Exception:
                d.rollback()
                raise


store = Store()
