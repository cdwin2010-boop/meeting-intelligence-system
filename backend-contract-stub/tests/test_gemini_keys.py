"""Gemini 키 두 개(무료/유료)와 GEMINI_KEY_MODE 테스트. 네트워크 없이 가짜 클라이언트로 확인한다."""
import json
import logging

import pytest
from fastapi.testclient import TestClient
from google.genai import errors as genai_errors
from pydantic import SecretStr

from app.config import settings
from app.db import store
from app.main import app
from app.pipeline import service
from app.pipeline.gemini_client import make_gemini_client
from app.uploads import fake_transcript

client = TestClient(app)

M4A = b"\x00\x00\x00\x18ftypM4A \x00\x00\x00\x00" + b"\x00" * 1000
FORM = {"title": "키 모드 회의", "startedAt": "2026-09-29T14:00:00+09:00"}
FREE_KEY = "free-test-key-DO-NOT-LEAK-1111"
PAID_KEY = "paid-test-key-DO-NOT-LEAK-2222"


@pytest.fixture(autouse=True)
def fresh(monkeypatch):
    store.reset()
    monkeypatch.setattr(settings, "fake_worker_enabled", False)
    monkeypatch.setattr(settings, "stt_provider", "gemini")
    monkeypatch.setattr(settings, "llm_provider", "gemini")


def set_keys(monkeypatch, mode: str, free: str = "", paid: str = "") -> None:
    monkeypatch.setattr(settings, "gemini_key_mode", mode)
    monkeypatch.setattr(settings, "gemini_api_key", SecretStr(free))
    monkeypatch.setattr(settings, "gemini_paid_api_key", SecretStr(paid))


def post_upload():
    return client.post("/api/meetings", data=FORM, files={"file": ("A.m4a", M4A)})


def job_count() -> int:
    return len(client.get("/api/admin/jobs").json())


class StubStt:
    def __init__(self, result="", error=None):
        self.result, self.error = result, error

    def transcribe(self, audio_path, mime_type):
        if self.error:
            raise self.error
        return self.result

    def release(self):
        pass


class StubExtractor:
    def extract(self, transcript, started_at):
        return []


def quota_error(message: str = "Resource exhausted"):
    return genai_errors.ClientError(429, {"error": {"code": 429, "status": "RESOURCE_EXHAUSTED", "message": message}})


# ---------------- conftest 고정 ----------------
def test_conftest_pins_keys_and_mode(monkeypatch):
    # fresh 픽스처가 provider 만 바꿨으므로 키·모드는 conftest 가 고정한 값이어야 한다 (.env 영향 없음)
    assert settings.gemini_key_mode == "free"
    assert settings.gemini_api_key.get_secret_value() == ""
    assert settings.gemini_paid_api_key.get_secret_value() == ""
    assert settings.all_gemini_keys() == []


# ---------------- 503: 선택된 모드의 키만 본다 ----------------
def test_free_mode_without_free_key_is_503_even_if_paid_key_exists(monkeypatch):
    set_keys(monkeypatch, "free", free="", paid=PAID_KEY)
    r = post_upload()
    assert r.status_code == 503
    assert "GEMINI_KEY_MODE=free" in r.json()["detail"] and "GEMINI_API_KEY" in r.json()["detail"]
    assert PAID_KEY not in r.text and job_count() == 9


def test_paid_mode_without_paid_key_is_503_even_if_free_key_exists(monkeypatch):
    set_keys(monkeypatch, "paid", free=FREE_KEY, paid="")
    r = post_upload()
    assert r.status_code == 503
    assert "GEMINI_KEY_MODE=paid" in r.json()["detail"] and "GEMINI_PAID_API_KEY" in r.json()["detail"]
    assert FREE_KEY not in r.text and job_count() == 9


def test_paid_mode_with_paid_key_is_accepted_and_processed(monkeypatch):
    set_keys(monkeypatch, "paid", free="", paid=PAID_KEY)
    monkeypatch.setattr(service, "make_stt", lambda: StubStt(result=fake_transcript()))
    monkeypatch.setattr(service, "make_extractor", lambda: StubExtractor())
    r = post_upload()
    assert r.status_code == 202
    assert client.get(f"/api/jobs/{r.json()['jobId']}").json()["status"] == "completed"


# ---------------- 클라이언트에 전달되는 키 ----------------
class CapturingClient:
    last_kwargs: dict = {}

    def __init__(self, **kwargs):
        CapturingClient.last_kwargs = kwargs


@pytest.mark.parametrize("mode,expected", [("paid", PAID_KEY), ("free", FREE_KEY)])
def test_client_receives_the_selected_key(monkeypatch, mode, expected):
    from google import genai

    set_keys(monkeypatch, mode, free=FREE_KEY, paid=PAID_KEY)
    monkeypatch.setattr(genai, "Client", CapturingClient)  # 진짜 클라이언트 대신 인자만 기록 (네트워크 없음)
    make_gemini_client()
    assert CapturingClient.last_kwargs["api_key"] == expected


# ---------------- 429 안내 ----------------
def test_429_in_free_mode_suggests_paid_mode_without_key(monkeypatch):
    set_keys(monkeypatch, "free", free=FREE_KEY, paid=PAID_KEY)
    message = service.describe_error(quota_error(f"quota for {FREE_KEY}"))
    assert "무료 키 한도" in message and "GEMINI_KEY_MODE=paid" in message
    assert FREE_KEY not in message and PAID_KEY not in message


def test_429_in_paid_mode_has_no_switch_hint(monkeypatch):
    set_keys(monkeypatch, "paid", free=FREE_KEY, paid=PAID_KEY)
    message = service.describe_error(quota_error())
    assert "(429)" in message and "GEMINI_KEY_MODE" not in message and "무료 키" not in message


def test_429_in_free_mode_does_not_switch_to_paid_key(monkeypatch):
    # 실패로 끝날 뿐, 유료 키로 다시 시도하지 않는다 (STT 는 한 번만 불린다)
    set_keys(monkeypatch, "free", free=FREE_KEY, paid=PAID_KEY)
    calls = []

    def stt_factory():
        calls.append(settings.selected_gemini_key())
        return StubStt(error=quota_error())

    monkeypatch.setattr(settings, "fake_worker_enabled", False)
    monkeypatch.setattr(service, "make_stt", stt_factory)
    job_id = post_upload().json()["jobId"]
    job = next(j for j in client.get("/api/admin/jobs").json() if j["id"] == job_id)
    assert job["status"] == "failed" and "GEMINI_KEY_MODE=paid" in job["errorLog"][0]
    assert calls == [FREE_KEY]  # 한 번, 무료 키로만
    body = json.dumps(job, ensure_ascii=False)
    assert FREE_KEY not in body and PAID_KEY not in body


# ---------------- 가리기(redact) ----------------
def test_redact_hides_both_keys(monkeypatch):
    set_keys(monkeypatch, "free", free=FREE_KEY, paid=PAID_KEY)
    text = service.redact(f"a={FREE_KEY} b={PAID_KEY}")
    assert FREE_KEY not in text and PAID_KEY not in text and text.count("***") == 2


@pytest.mark.parametrize("mode", ["free", "paid"])
def test_error_message_hides_both_keys_in_either_mode(monkeypatch, mode):
    set_keys(monkeypatch, mode, free=FREE_KEY, paid=PAID_KEY)
    error = genai_errors.ClientError(400, {"error": {"code": 400, "status": "INVALID_ARGUMENT",
                                                     "message": f"bad request {FREE_KEY} {PAID_KEY}"}})
    message = service.describe_error(error)
    assert FREE_KEY not in message and PAID_KEY not in message and "bad request" in message


# ---------------- 로그에는 모드만 ----------------
def test_job_start_logs_mode_only(monkeypatch):
    set_keys(monkeypatch, "paid", free=FREE_KEY, paid=PAID_KEY)
    records: list[str] = []

    class ListHandler(logging.Handler):
        def emit(self, record):
            records.append(record.getMessage())

    handler = ListHandler()
    service.log.addHandler(handler)
    try:
        monkeypatch.setattr(service, "make_stt", lambda: StubStt(result=fake_transcript()))
        monkeypatch.setattr(service, "make_extractor", lambda: StubExtractor())
        post_upload()
    finally:
        service.log.removeHandler(handler)
    joined = "\n".join(records)
    assert "키 모드: paid" in joined
    for key in (FREE_KEY, PAID_KEY):
        assert key not in joined and key[:8] not in joined and key[-4:] not in joined
