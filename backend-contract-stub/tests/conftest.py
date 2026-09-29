"""모든 테스트 공통: 업로드 음성은 테스트마다 새 임시 폴더에 저장한다 (시스템 임시 폴더를 더럽히지 않음).
파이프라인 설정은 항상 기본값(fake)에서 시작한다. .env 에 gemini 가 적혀 있어도 테스트는 네트워크 없이 돈다."""
import pytest
from pydantic import SecretStr

from app.config import settings


@pytest.fixture(autouse=True)
def isolated_pipeline_settings(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "upload_dir", tmp_path / "uploads")
    monkeypatch.setattr(settings, "stt_provider", "fake")
    monkeypatch.setattr(settings, "llm_provider", "fake")
    monkeypatch.setattr(settings, "gemini_api_key", SecretStr(""))
