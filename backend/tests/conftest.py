"""테스트 공통: app 을 불러오기 "전에" DATABASE_URL 을 이 실행 전용 임시 SQLite 파일로 바꾼다.
환경변수가 .env 보다 우선하므로 테스트는 backend/data/ 의 실제 DB를 열지 않는다."""
import os
import secrets
import shutil
import tempfile
from pathlib import Path

_TEST_DB_DIR = Path(tempfile.mkdtemp(prefix="backend-pytest-db-"))
os.environ["DATABASE_URL"] = f"sqlite:///{(_TEST_DB_DIR / 'test.db').as_posix()}"
os.environ["APP_ENV"] = "test"
# test 환경은 기본 서명 키를 거부하므로 실행마다 무작위 키를 쓴다(값은 출력하지 않음)
os.environ["SECRET_KEY"] = secrets.token_urlsafe(48)


def pytest_sessionfinish(session, exitstatus):
    """테스트 전용 임시 DB 폴더 정리"""
    shutil.rmtree(_TEST_DB_DIR, ignore_errors=True)
