"""모든 테스트 공통: 업로드 음성은 테스트마다 새 임시 폴더에 저장한다 (시스템 임시 폴더를 더럽히지 않음).
파이프라인 설정은 항상 기본값(fake)에서 시작한다. .env 에 gemini·키·키 모드가 적혀 있어도 테스트는 네트워크 없이 같은 조건으로 돈다.

(v1.9.10) 실제 DB 파일 보호: 테스트는 실제 등록 데이터(data/stub.db 또는 .env 의 STUB_DB_PATH)를 절대 열지 않는다.
app 모듈을 불러오기 "전에" STUB_DB_PATH 를 이 테스트 실행 전용 임시 파일로 바꾸고(환경변수가 .env 보다 우선),
혹시라도 저장소가 다른 파일을 가리키면 테스트를 하나도 돌리지 않고 멈춘다. 테스트의 store.reset() 은 이 임시 파일만 지운다."""
import os
import shutil
import tempfile
from pathlib import Path

_TEST_DB_DIR = Path(tempfile.mkdtemp(prefix="stub-pytest-db-"))
_TEST_DB_PATH = _TEST_DB_DIR / "test.db"
os.environ["STUB_DB_PATH"] = str(_TEST_DB_PATH)  # 반드시 아래 app import 보다 먼저

import pytest  # noqa: E402
from pydantic import SecretStr  # noqa: E402

from app.config import settings  # noqa: E402
from app.db import store  # noqa: E402

# 실제 DB를 가리키면 여기서 즉시 중단 (테스트 수집 단계에서 실패 → 어떤 reset 도 실행되지 않음)
if Path(store.path).resolve() != _TEST_DB_PATH.resolve():
    raise RuntimeError(f"pytest 가 임시 DB가 아닌 파일을 열었습니다: {store.path}. 실제 데이터 보호를 위해 중단합니다.")


def pytest_sessionfinish(session, exitstatus):
    """테스트 전용 임시 DB 폴더 정리 (실제 data 폴더와 무관)"""
    shutil.rmtree(_TEST_DB_DIR, ignore_errors=True)


@pytest.fixture(autouse=True)
def isolated_pipeline_settings(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "upload_dir", tmp_path / "uploads")
    monkeypatch.setattr(settings, "stt_provider", "fake")
    monkeypatch.setattr(settings, "llm_provider", "fake")
    monkeypatch.setattr(settings, "gemini_api_key", SecretStr(""))
    monkeypatch.setattr(settings, "gemini_paid_api_key", SecretStr(""))
    monkeypatch.setattr(settings, "gemini_key_mode", "free")
