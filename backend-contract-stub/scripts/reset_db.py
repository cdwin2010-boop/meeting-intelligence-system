"""
스텁 DB 초기화 (사용자가 직접 실행할 때만). 서버 시작·재시작·--reload·테스트에서는 절대 부르지 않는다.

사용법 (backend-contract-stub 폴더에서, 가능하면 서버를 끈 상태로):
    python -m scripts.reset_db          # 무엇이 지워지는지만 보여 주고 아무것도 하지 않는다
    python -m scripts.reset_db --yes    # 실제로 초기화

하는 일 (--yes 일 때):
  1) STUB_DB_PATH(기본 data/stub.db)의 모든 회의·전사 원문·액션아이템·작업·화자 이름을 지우고 기본 시드로 되돌린다.
  2) UPLOAD_DIR(기본 data/uploads)에 남은 업로드 음성(실패 작업의 Retry 용)과 받다 만 임시 파일을 지운다.
     → 초기화 뒤 작업 번호가 다시 시작되므로, 예전 음성이 새 작업 번호와 겹쳐 잘못 쓰이는 것을 막는다.
전사 원문·키는 화면에 출력하지 않는다(건수와 경로만).
"""
import sqlite3
import sys
from pathlib import Path

from app.config import settings
from app.upload_storage import AUDIO_MIME_TYPES


def _counts(path: Path) -> dict[str, int]:
    """지워질 건수 (파일이 없거나 표가 없으면 0)"""
    if not path.is_file():
        return {}
    con = sqlite3.connect(path)
    try:
        out = {}
        for table in ("meetings", "action_items", "jobs"):
            try:
                out[table] = con.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            except sqlite3.OperationalError:
                out[table] = 0
        return out
    finally:
        con.close()


def _upload_files(folder: Path) -> list[Path]:
    """업로드 폴더의 음성·임시 파일만 (그 밖의 파일은 건드리지 않음)"""
    if not folder.is_dir():
        return []
    return [
        f for f in folder.iterdir()
        if f.is_file() and (f.suffix.lower() in AUDIO_MIME_TYPES or f.name.startswith(".incoming-"))
    ]


def main(argv: list[str]) -> int:
    db_path = Path(settings.stub_db_path)
    upload_dir = Path(settings.upload_dir)
    counts = _counts(db_path)
    files = _upload_files(upload_dir)

    print(f"DB 파일: {db_path}")
    print(f"  현재 건수: 회의 {counts.get('meetings', 0)}, 액션아이템 {counts.get('action_items', 0)}, 작업 {counts.get('jobs', 0)}")
    print(f"업로드 폴더: {upload_dir} (지울 음성·임시 파일 {len(files)}개)")

    if argv != ["--yes"]:
        print("\n아무것도 바꾸지 않았습니다. 정말 초기화하려면: python -m scripts.reset_db --yes")
        print("(서버가 켜져 있으면 먼저 끄는 것을 권합니다.)")
        return 0 if not argv else 2

    from app.db import Store  # --yes 일 때만 DB를 연다

    Store(db_path).reset()
    for f in files:
        f.unlink(missing_ok=True)
    print("\n초기화했습니다: 기본 시드 회의 1건으로 되돌렸고, 업로드 음성·임시 파일을 지웠습니다.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
