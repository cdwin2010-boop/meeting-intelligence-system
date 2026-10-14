"""작업 L-1: 로컬 STT 어댑터. GPU·모델·faster-whisper 없이 가짜 모델을 주입해 시험한다."""
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from app.config import Settings
from app.pipeline import local_stt
from app.pipeline.local_stt import (
    ERR_FILE_MISSING, ERR_NO_ENGINE, ERR_NO_GPU, ERR_TRANSCRIBE, LocalStt, LocalSttConfig, LocalSttError,
)
from app.pipeline.stt import NO_SPEECH, is_no_speech
from app.pipeline.transcript_text import has_timestamps, parse_transcript
from app.pipeline.errors import describe_error, error_code
from app.services.speakers import transcript_labels


class FakeModel:
    def __init__(self, segs, events=None, fail=False):
        self._segs, self._events, self._fail = segs, events if events is not None else [], fail

    def transcribe(self, path, **kwargs):
        self._events.append("transcribe")
        if self._fail:
            raise RuntimeError("boom /secret/path")
        return iter(SimpleNamespace(start=a, end=b, text=t) for a, b, t in self._segs), SimpleNamespace(duration=100.0)


@pytest.fixture()
def audio(tmp_path: Path) -> Path:
    p = tmp_path / "a.wav"
    p.write_bytes(b"x")
    return p


def _engine(model, events=None, **kw):
    ev = events if events is not None else []
    return LocalStt(LocalSttConfig(), model_factory=lambda cfg: model, release_fn=lambda: ev.append("release"), **kw), ev


def test_format_and_roundtrip(audio):
    segs = [(0.0, 2.0, " 안녕하세요 \n 여러분 "), (65.4, 70.0, ""), (3725.0, 3730.0, "마무리   합니다")]
    eng, _ = _engine(FakeModel(segs))
    res = eng.transcribe(audio, "audio/wav")
    assert res.text.split("\n") == ["[00:00:00] 화자1: 안녕하세요 여러분", "[01:02:05] 화자1: 마무리 합니다"]
    assert [s["speaker"] for s in res.segments] == ["화자1", "화자1"]
    parsed = parse_transcript(res.text)  # 기존 B-1b 파서 왕복
    assert parsed.text == res.text and has_timestamps(parsed)  # 모든 줄이 시각+화자 형식이라 그대로 통과
    from app.pipeline.evidence_time import resolve_evidence_sec, segments_of
    sec, matched = resolve_evidence_sec(segments_of(res.text, res.segments), "마무리 합니다", None)
    assert matched and sec == 3725.0  # 근거 시각 대조도 어댑터 구간으로 동작
    assert transcript_labels(SimpleNamespace(segments=res.segments, full_text=res.text)) == ["화자1"]
    assert transcript_labels(SimpleNamespace(segments=None, full_text=res.text)) == ["화자1"]  # 구간 없이 텍스트만이어도 인식


def test_all_empty_is_no_speech(audio):
    eng, _ = _engine(FakeModel([(0, 1, "  "), (1, 2, "\n")]))
    res = eng.transcribe(audio, "audio/wav")
    assert res.text == NO_SPEECH and is_no_speech(res.text)


def test_release_after_success_and_order(audio):
    ev: list[str] = []
    eng, _ = _engine(FakeModel([(0, 1, "가")], ev), ev)
    eng.transcribe(audio, "audio/wav")
    assert ev == ["transcribe", "release"]  # 전사 → 해제 → (그 뒤에야 반환 = 다음 단계 시작)


def test_release_on_exception_and_masked_error(audio):
    ev: list[str] = []
    eng, _ = _engine(FakeModel([], ev, fail=True), ev)
    with pytest.raises(LocalSttError) as ei:
        eng.transcribe(audio, "audio/wav")
    assert ev == ["transcribe", "release"]
    assert error_code(ei.value) == ERR_TRANSCRIBE
    assert "secret" not in describe_error(ei.value)


def test_release_on_model_load_failure(audio):
    ev: list[str] = []

    def bad(cfg):
        raise LocalSttError("ERR-STT-MODEL-LOAD", "x")

    eng = LocalStt(LocalSttConfig(), model_factory=bad, release_fn=lambda: ev.append("release"))
    with pytest.raises(LocalSttError):
        eng.transcribe(audio, "audio/wav")
    assert ev == ["release"]


def test_missing_file_code(tmp_path):
    eng, _ = _engine(FakeModel([]))
    with pytest.raises(LocalSttError) as ei:
        eng.transcribe(tmp_path / "none.wav", "audio/wav")
    assert ei.value.job_error_code == ERR_FILE_MISSING


def test_release_failure_does_not_break_result(audio):
    def boom():
        raise RuntimeError("x")

    eng = LocalStt(LocalSttConfig(), model_factory=lambda c: FakeModel([(0, 1, "가")]), release_fn=boom)
    assert eng.transcribe(audio, "audio/wav").text.endswith("가")


def test_progress_callback(audio):
    got: list[float] = []
    eng, _ = _engine(FakeModel([(0, 50, "가"), (50, 100, "나")]), progress_callback=got.append)
    eng.transcribe(audio, "audio/wav")
    assert got == [0.5, 1.0]


def test_no_gpu_error_code(monkeypatch, audio):
    monkeypatch.setitem(sys.modules, "faster_whisper", SimpleNamespace(WhisperModel=lambda *a, **k: object()))
    monkeypatch.setattr(local_stt, "_cuda_available", lambda: False)
    with pytest.raises(LocalSttError) as ei:
        local_stt._default_model_factory(LocalSttConfig(device="cuda"))
    assert ei.value.job_error_code == ERR_NO_GPU  # 조용한 CPU 전환 없음


def test_missing_faster_whisper_code(monkeypatch):
    monkeypatch.setitem(sys.modules, "faster_whisper", None)  # import 실패
    with pytest.raises(LocalSttError) as ei:
        local_stt._default_model_factory(LocalSttConfig())
    assert ei.value.job_error_code == ERR_NO_ENGINE


def test_offline_model_dir_uses_local_files_only(monkeypatch, tmp_path):
    seen = {}

    def fake_model(target, **kw):
        seen.update(target=target, **kw)
        return object()

    monkeypatch.setitem(sys.modules, "faster_whisper", SimpleNamespace(WhisperModel=fake_model))
    local_stt._default_model_factory(LocalSttConfig(device="cpu", compute_type="int8", model_dir=str(tmp_path)))
    assert seen["local_files_only"] is True and seen["target"] == str(tmp_path)


def test_release_without_torch(monkeypatch):
    monkeypatch.setitem(sys.modules, "torch", None)
    from app.pipeline.gpu import release_gpu_memory

    assert release_gpu_memory() is False  # torch 미설치여도 예외 없음


def test_settings_default_unchanged_and_validation():
    s = Settings(_env_file=None)
    assert s.stt_provider == "fake"
    assert (s.stt_local_device, s.stt_local_language, s.stt_local_default_speaker) == ("cuda", "ko", "화자1")
    for bad in ({"stt_local_device": "tpu"}, {"stt_local_compute_type": "int4"}, {"stt_local_beam_size": 0},
                {"stt_local_default_speaker": " "}, {"stt_provider": "whisper"}):
        with pytest.raises(ValidationError):
            Settings(_env_file=None, **bad)
    with pytest.raises(ValidationError):  # local 은 별도 워커(queue)에서만
        Settings(_env_file=None, stt_provider="local", processing_mode="inline", local_llm_base_url="http://127.0.0.1:1", local_llm_model="m")
    with pytest.raises(ValidationError):
        Settings(_env_file=None, stt_provider="local", processing_mode="queue", stt_local_device="cpu", stt_local_compute_type="float16",
                 local_llm_base_url="http://127.0.0.1:1", local_llm_model="m")
    ok = Settings(_env_file=None, stt_provider="local", processing_mode="queue", local_llm_base_url="http://127.0.0.1:1", local_llm_model="m")
    assert ok.stt_provider == "local"
