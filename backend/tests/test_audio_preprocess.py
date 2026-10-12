"""FFmpeg 오디오 전처리(작업 75-1 파트 B). 단위 테스트는 subprocess 를 모의해 항상 실행하고, ffmpeg 가 있을 때만 실제 변환을 확인한다."""
import math
import struct
import subprocess
import wave
from pathlib import Path

import pytest

from app.config import Settings, settings
from app.models import Job
from app.pipeline import audio_preprocess as ap
from app.pipeline.audio_preprocess import AudioPreprocessError, FfmpegNotFoundError, preprocess_audio
from app.pipeline.fakes import fake_transcript
from app.services.processing import process_meeting
from tests.test_upload_processing import add_account, env, upload  # noqa: F401  (env 는 픽스처)

CHAIN = "highpass=f=80, lowpass=f=8000, afftdn, dynaudnorm"


class FakeRun:
    """subprocess.run 대역: 호출 인자를 기록하고, 지정한 동작을 한다."""

    def __init__(self, returncode=0, stderr=b"", write=True, raises=None):
        self.returncode, self.stderr, self.write, self.raises = returncode, stderr, write, raises
        self.calls: list[tuple[list[str], dict]] = []

    def __call__(self, cmd, **kwargs):
        self.calls.append((list(cmd), kwargs))
        if self.raises is not None:
            raise self.raises
        if self.write and self.returncode == 0:
            Path(cmd[-1]).write_bytes(b"RIFF....WAVE")
        return subprocess.CompletedProcess(cmd, self.returncode, stdout=b"", stderr=self.stderr)


@pytest.fixture
def fake_ffmpeg(monkeypatch):
    monkeypatch.setattr(ap, "resolve_ffmpeg", lambda path=None: "/opt/ffmpeg")


def _run(monkeypatch, fake: FakeRun) -> FakeRun:
    monkeypatch.setattr(ap.subprocess, "run", fake)
    return fake


# ---------------- 명령 구성 ----------------
def test_filter_chain_and_arguments_are_exact(fake_ffmpeg, monkeypatch, tmp_path):
    src = tmp_path / "in.m4a"
    src.write_bytes(b"audio")
    fake = _run(monkeypatch, FakeRun())
    out = preprocess_audio(src, tmp_path / "out.wav")
    [(cmd, kwargs)] = fake.calls
    assert ap.FILTER_CHAIN == CHAIN
    assert cmd[0] == "/opt/ffmpeg" and "-y" in cmd
    assert cmd[cmd.index("-i") + 1] == str(src)
    assert cmd[cmd.index("-af") + 1] == CHAIN
    assert cmd[cmd.index("-ar") + 1] == "16000" and cmd[cmd.index("-ac") + 1] == "1"
    assert cmd[cmd.index("-c:a") + 1] == "pcm_s16le" and cmd[-1] == str(tmp_path / "out.wav")
    assert not kwargs.get("shell")  # shell 사용 금지
    assert out == tmp_path / "out.wav" and src.read_bytes() == b"audio"  # 입력은 수정하지 않는다


def test_paths_with_spaces_and_korean_are_single_arguments(fake_ffmpeg, monkeypatch, tmp_path):
    folder = tmp_path / "회의 녹음 폴더"
    folder.mkdir()
    src = folder / "주간 회의 (최종).m4a"
    src.write_bytes(b"a")
    fake = _run(monkeypatch, FakeRun())
    preprocess_audio(src, folder / "출력 결과.wav")
    cmd = fake.calls[0][0]
    assert str(src) in cmd and str(folder / "출력 결과.wav") in cmd


def test_default_timeout_comes_from_settings(fake_ffmpeg, monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "ffmpeg_timeout_seconds", 123)
    src = tmp_path / "in.wav"
    src.write_bytes(b"a")
    fake = _run(monkeypatch, FakeRun())
    preprocess_audio(src, tmp_path / "o.wav")
    assert fake.calls[0][1]["timeout"] == 123
    preprocess_audio(src, tmp_path / "o.wav", timeout=7)
    assert fake.calls[1][1]["timeout"] == 7


# ---------------- 실패 처리 ----------------
def test_ffmpeg_not_found_raises_dedicated_error(monkeypatch, tmp_path):
    monkeypatch.setattr(ap.shutil, "which", lambda name: None)
    monkeypatch.setattr(ap, "_BUNDLED_DIR", tmp_path / "no-bin")
    src = tmp_path / "in.wav"
    src.write_bytes(b"a")
    with pytest.raises(FfmpegNotFoundError):
        preprocess_audio(src, tmp_path / "o.wav", ffmpeg_path=str(tmp_path / "missing-ffmpeg"))
    with pytest.raises(FfmpegNotFoundError):
        preprocess_audio(src, tmp_path / "o.wav")


def test_failure_message_has_only_stderr_tail_without_paths_and_cleans_temp(fake_ffmpeg, monkeypatch, tmp_path):
    src = tmp_path / "secret-name.m4a"
    src.write_bytes(b"a")
    stderr = "\n".join([f"line {i}" for i in range(20)] + [f"{src}: Invalid data found"]).encode()
    fake = _run(monkeypatch, FakeRun(returncode=1, stderr=stderr, write=False))
    created: list[str] = []
    real_mkstemp = ap.tempfile.mkstemp

    def spy(**kwargs):
        handle, name = real_mkstemp(**kwargs)
        created.append(name)
        return handle, name

    monkeypatch.setattr(ap.tempfile, "mkstemp", spy)
    with pytest.raises(AudioPreprocessError) as info:
        preprocess_audio(src)
    message = str(info.value)
    assert "Invalid data found" in message and "line 0" not in message and str(src) not in message and "secret-name" not in message
    assert len(created) == 1 and not Path(created[0]).exists()
    assert fake.calls


def test_timeout_raises_and_removes_output(fake_ffmpeg, monkeypatch, tmp_path):
    src = tmp_path / "in.wav"
    src.write_bytes(b"a")
    out = tmp_path / "o.wav"
    out.write_bytes(b"partial")
    _run(monkeypatch, FakeRun(raises=subprocess.TimeoutExpired("ffmpeg", 1)))
    with pytest.raises(AudioPreprocessError, match="시간 초과"):
        preprocess_audio(src, out, timeout=1)
    assert not out.exists() and src.exists()


def test_empty_output_is_failure(fake_ffmpeg, monkeypatch, tmp_path):
    src = tmp_path / "in.wav"
    src.write_bytes(b"a")
    _run(monkeypatch, FakeRun(write=False))
    with pytest.raises(AudioPreprocessError):
        preprocess_audio(src, tmp_path / "o.wav")


def test_settings_defaults():
    s = Settings(_env_file=None)
    assert s.audio_preprocess_enabled is False and s.audio_preprocess_strict is False
    assert s.ffmpeg_path == "ffmpeg" and s.ffmpeg_timeout_seconds == 900


# ---------------- 파이프라인 연결 ----------------
class RecordingStt:
    def __init__(self):
        self.inputs: list[tuple[str, str, bool]] = []

    def __call__(self):
        return self

    def transcribe(self, path, mime):
        self.inputs.append((Path(path).name, mime, Path(path).is_file()))
        return fake_transcript()

    def release(self):
        pass


@pytest.fixture
def mgr(env):
    return add_account(env["factory"], "고객사A", "mgr", "manager", name="박관리")


def _process(env, stt, job_id):
    return process_meeting(job_id, session_factory=env["factory"], stt_factory=stt)


def test_disabled_by_default_does_not_call_preprocess(env, mgr, monkeypatch):
    def boom(*a, **k):
        raise AssertionError("기본값에서는 전처리를 부르면 안 됩니다")

    monkeypatch.setattr("app.services.processing.preprocess_audio", boom)
    stt = RecordingStt()
    res = upload(env, mgr)
    assert _process(env, stt, res.json()["jobId"]) == "completed"
    assert stt.inputs[0][1] == "audio/mp4" and stt.inputs[0][0].endswith(".m4a")


def test_enabled_feeds_preprocessed_file_to_stt_and_deletes_it(env, mgr, monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "audio_preprocess_enabled", True)
    made: list[Path] = []

    def fake_preprocess(path):
        out = tmp_path / "pre.wav"
        out.write_bytes(b"RIFF")
        made.append(out)
        return out

    monkeypatch.setattr("app.services.processing.preprocess_audio", fake_preprocess)
    stt = RecordingStt()
    res = upload(env, mgr)
    assert _process(env, stt, res.json()["jobId"]) == "completed"
    assert stt.inputs == [("pre.wav", "audio/wav", True)]
    assert made and not made[0].exists()


def test_failure_falls_back_to_original_unless_strict(env, mgr, monkeypatch):
    monkeypatch.setattr(settings, "audio_preprocess_enabled", True)

    def failing(path):
        raise AudioPreprocessError("ffmpeg 전처리 실패")

    monkeypatch.setattr("app.services.processing.preprocess_audio", failing)
    stt = RecordingStt()
    res = upload(env, mgr)
    assert _process(env, stt, res.json()["jobId"]) == "completed"
    assert stt.inputs[0][0].endswith(".m4a") and stt.inputs[0][1] == "audio/mp4"  # 원본으로 진행

    monkeypatch.setattr(settings, "audio_preprocess_strict", True)
    stt2 = RecordingStt()
    res2 = upload(env, mgr)
    assert _process(env, stt2, res2.json()["jobId"]) == "failed"
    assert stt2.inputs == []
    with env["factory"]() as s:
        assert s.get(Job, res2.json()["jobId"]).status == "failed"


def test_ffmpeg_missing_is_not_fatal_by_default(env, mgr, monkeypatch):
    monkeypatch.setattr(settings, "audio_preprocess_enabled", True)
    monkeypatch.setattr(settings, "ffmpeg_path", "definitely-not-installed-ffmpeg")
    monkeypatch.setattr(ap, "_BUNDLED_DIR", Path("no-such-dir"))
    stt = RecordingStt()
    res = upload(env, mgr)
    assert _process(env, stt, res.json()["jobId"]) == "completed"
    assert stt.inputs[0][0].endswith(".m4a")


# ---------------- 실제 ffmpeg(있을 때만) ----------------
def _ffmpeg_or_none():
    try:
        return ap.resolve_ffmpeg()
    except FfmpegNotFoundError:
        return None


@pytest.mark.skipif(_ffmpeg_or_none() is None, reason="ffmpeg 가 설치돼 있지 않음")
def test_real_ffmpeg_outputs_16k_mono_wav(tmp_path):
    src = tmp_path / "입력 사인파.wav"
    with wave.open(str(src), "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(44100)
        frames = bytearray()
        for i in range(44100 * 3):
            v = int(12000 * math.sin(2 * math.pi * 440 * i / 44100))
            frames += struct.pack("<hh", v, v)
        w.writeframes(bytes(frames))
    before = src.read_bytes()
    out = preprocess_audio(src, tmp_path / "결과 파일.wav")
    with wave.open(str(out), "rb") as r:
        assert r.getframerate() == 16000 and r.getnchannels() == 1 and r.getsampwidth() == 2
        assert r.getnframes() > 16000 * 2
    assert src.read_bytes() == before
