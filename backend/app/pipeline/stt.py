"""
전사(STT) 엔진. 모든 엔진은 같은 모양을 따른다(stub app/pipeline/stt.py 이식):
    transcribe(audio_path, mime_type) -> str | TranscriptResult   # "[HH:MM:SS] 화자: 내용" 줄들, 말소리가 없으면 "[NO_SPEECH]"
                                                                   # 구간 정보를 줄 수 있는 엔진은 TranscriptResult 로 돌려준다
    provider_name                              # 전사문 저장 시 기록할 엔진 이름
    release()                                  # 자원 정리 (로컬 GPU 엔진이면 모델 해제)
"""
import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from app.config import settings
from app.pipeline.fakes import fake_transcript

log = logging.getLogger("app.pipeline")

NO_SPEECH = "[NO_SPEECH]"

TRANSCRIBE_PROMPT = f"""이 음성은 한국어 회의 녹음입니다. 들리는 발화를 빠짐없이 전사하세요.

규칙:
- 각 발화를 한 줄에 하나씩 "[HH:MM:SS] 화자: 내용" 형식으로 적습니다. 시각은 녹음 시작부터의 경과 시간입니다.
- 화자 이름이 음성에서 직접 언급되기 전에는 "화자1", "화자2"처럼 번호로 표기합니다.
- 들리지 않거나 알아들을 수 없는 부분을 추측해서 지어내지 않습니다.
- 말소리가 전혀 없으면 다른 글자 없이 정확히 "{NO_SPEECH}" 만 출력합니다.
- 전사문 외의 설명, 머리말, 마크다운은 쓰지 않습니다."""


def is_no_speech(transcript: str | None) -> bool:
    """말소리 없음 판정: 빈 전사(공백만 포함)이거나 "[NO_SPEECH]" 로 시작(뒤에 설명이 붙어도)하면 True.
    stub 은 정확히 "[NO_SPEECH]" 인 경우만 잡았으나, 모델이 설명을 덧붙이는 경우도 말소리 없음으로 본다."""
    text = (transcript or "").strip()
    return not text or text.startswith(NO_SPEECH)


@dataclass(frozen=True)
class TranscriptResult:
    """전사 결과. segments: [{"speaker", "start_sec", "end_sec", "text"}, ...], 엔진이 구간을 주지 않으면 None."""

    text: str
    segments: list[dict[str, Any]] | None = None


def as_transcript_result(value: "str | TranscriptResult | None") -> TranscriptResult:
    """엔진이 문자열만 돌려줘도 같은 모양으로 맞춘다(구간 없음 = None)."""
    if isinstance(value, TranscriptResult):
        return value
    return TranscriptResult(text=value or "", segments=None)


class SttEngine(Protocol):
    def transcribe(self, audio_path: Path, mime_type: str) -> "str | TranscriptResult": ...

    def release(self) -> None: ...


class GeminiFileError(RuntimeError):
    """Gemini가 올린 음성 파일을 처리하지 못함 (state=FAILED)"""


class FakeStt:
    """가짜 전사: 테스트 가이드 A파일 대본을 그대로 돌려준다 (네트워크 없음)."""

    provider_name = "fake"

    def transcribe(self, audio_path: Path, mime_type: str) -> str:
        return fake_transcript()

    def release(self) -> None:
        pass


def _state_name(state: Any) -> str:
    # FileState 열거형이든 문자열이든 "ACTIVE" 같은 이름으로 맞춘다
    return str(getattr(state, "value", state) or "")


class GeminiStt:
    """Gemini API 전사: 파일 업로드 → ACTIVE 대기 → generate_content → (finally) 업로드 파일 삭제.
    결과는 "[HH:MM:SS] 화자: 내용" 텍스트뿐이라 구간(종료 시각 포함) 정보는 주지 않는다."""

    provider_name = "gemini"

    def __init__(self, client: Any, model: str, poll_seconds: float = 1.0, max_wait_seconds: float | None = None):
        self._client = client
        self._model = model
        self._poll_seconds = poll_seconds
        self._max_wait_seconds = settings.gemini_timeout_sec if max_wait_seconds is None else max_wait_seconds

    def _wait_until_active(self, file: Any) -> Any:
        deadline = time.monotonic() + self._max_wait_seconds
        while _state_name(file.state) == "PROCESSING":
            if time.monotonic() >= deadline:
                raise TimeoutError("uploaded file did not become ACTIVE in time")
            time.sleep(self._poll_seconds)
            file = self._client.files.get(name=file.name)
        if _state_name(file.state) == "FAILED":
            raise GeminiFileError("Gemini could not process the uploaded audio")
        return file

    def transcribe(self, audio_path: Path, mime_type: str) -> str:
        from google.genai import types

        uploaded = None
        try:
            uploaded = self._client.files.upload(
                file=str(audio_path), config=types.UploadFileConfig(mime_type=mime_type)
            )
            uploaded = self._wait_until_active(uploaded)
            response = self._client.models.generate_content(
                model=self._model,
                contents=[uploaded, TRANSCRIBE_PROMPT],
                config=types.GenerateContentConfig(temperature=0),
            )
            return (response.text or "").strip()
        finally:
            # 성공·실패와 관계없이 Gemini 쪽에 올린 파일은 지운다 (지우기 실패는 결과에 영향을 주지 않음)
            if uploaded is not None and getattr(uploaded, "name", None):
                try:
                    self._client.files.delete(name=uploaded.name)
                except Exception as exc:  # noqa: BLE001
                    log.warning("gemini file cleanup failed: %s", type(exc).__name__)
            self.release()

    def release(self) -> None:
        # 클라우드 엔진이라 GPU 메모리를 쓰지 않는다. (로컬 Whisper 엔진은 여기서 del model, gc.collect(), torch.cuda.empty_cache())
        pass


def make_gemini_stt(client: Any | None = None) -> GeminiStt:
    """설정(GEMINI_MODEL·GEMINI_KEY_MODE)으로 Gemini 전사 엔진을 만든다. 어느 엔진을 쓸지는 호출부(4b)가 정한다."""
    if client is None:
        from app.pipeline.gemini_client import make_gemini_client

        client = make_gemini_client()
    return GeminiStt(client, settings.gemini_model)
