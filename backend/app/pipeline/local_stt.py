"""로컬 STT 엔진(작업 L-1): faster-whisper 로 음성 → "[HH:MM:SS] 화자1: 내용" 전사문.
참조: ref/diarization-2.0/audio_engine_core.py (화자 분리 없음, 모든 발화는 기본 화자 라벨).
- faster-whisper·torch 는 선택 의존성이다(없어도 이 모듈 import·테스트 가능). 실제 모델은 transcribe 때 처음 로드한다.
- 전사 종료(성공·예외 모두) 때 모델 참조 제거 → gc.collect() → torch.cuda.empty_cache(). 이 해제가 끝나야 transcribe() 가 반환하므로
  다음 단계(추출)는 구조적으로 VRAM 반환 뒤에만 시작된다.
- 오류는 ERR-STT-* 코드로 돌려주고, 조용한 CPU 전환·자동 폴백은 하지 않는다. 메시지에는 경로·키를 넣지 않는다.
"""
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from app.pipeline.gpu import release_gpu_memory
from app.pipeline.stt import NO_SPEECH, TranscriptResult

log = logging.getLogger("app.pipeline.local_stt")

ERR_NO_ENGINE = "ERR-STT-NO-ENGINE"      # faster-whisper 미설치
ERR_NO_GPU = "ERR-STT-NO-GPU"            # device=cuda 인데 GPU 사용 불가
ERR_MODEL_LOAD = "ERR-STT-MODEL-LOAD"    # 모델 로드 실패(없음·손상·VRAM 부족 등)
ERR_FILE_MISSING = "ERR-STT-FILE-MISSING"
ERR_TRANSCRIBE = "ERR-STT-FAILED"        # 전사 중 오류


class LocalSttError(RuntimeError):
    """로컬 STT 실패. job_error_code 가 jobs.error_code 로 남는다(영구 오류: 같은 입력으로 재시도해도 같다)."""

    kind = "permanent"

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.job_error_code = code


@dataclass(frozen=True)
class LocalSttConfig:
    model_size: str = "large-v3"
    device: str = "cuda"             # cuda | cpu
    compute_type: str = "float16"    # float16 | int8_float16 | int8
    language: str = "ko"
    beam_size: int = 5
    default_speaker: str = "화자1"
    model_dir: str = ""              # 지정 시 local_files_only 로 오프라인 로드


@dataclass(frozen=True)
class SttSegment:
    start: float
    end: float
    text: str


def config_from_settings(s: Any) -> LocalSttConfig:
    return LocalSttConfig(
        model_size=s.stt_local_model_size, device=s.stt_local_device, compute_type=s.stt_local_compute_type,
        language=s.stt_local_language, beam_size=s.stt_local_beam_size,
        default_speaker=s.stt_local_default_speaker, model_dir=s.stt_local_model_dir.strip(),
    )


def _cuda_available() -> bool:
    """GPU 사용 가능 여부. torch 가 없으면 CTranslate2 로 확인한다."""
    try:
        import torch

        return bool(torch.cuda.is_available())
    except Exception:  # noqa: BLE001 — torch 미설치
        pass
    try:
        import ctranslate2

        return ctranslate2.get_cuda_device_count() > 0
    except Exception:  # noqa: BLE001
        return False


def _default_model_factory(cfg: LocalSttConfig):
    try:
        from faster_whisper import WhisperModel  # MIT
    except ImportError as exc:
        raise LocalSttError(ERR_NO_ENGINE, "로컬 STT 엔진(faster-whisper)이 설치되어 있지 않습니다.") from exc
    if cfg.device == "cuda" and not _cuda_available():
        raise LocalSttError(ERR_NO_GPU, "STT_LOCAL_DEVICE=cuda 이지만 사용할 수 있는 GPU 가 없습니다. (자동으로 CPU 로 전환하지 않습니다)")
    try:
        if cfg.model_dir:  # 오프라인: 지정 폴더의 모델만 쓴다(다운로드 없음)
            return WhisperModel(cfg.model_dir, device=cfg.device, compute_type=cfg.compute_type, local_files_only=True)
        return WhisperModel(cfg.model_size, device=cfg.device, compute_type=cfg.compute_type)
    except Exception as exc:  # noqa: BLE001
        raise LocalSttError(
            ERR_MODEL_LOAD, f"로컬 STT 모델을 불러오지 못했습니다({type(exc).__name__}). 모델 이름·폴더·VRAM 을 확인하세요."
        ) from exc


def format_timestamp(seconds: float) -> str:
    s = max(int(seconds), 0)
    return f"{s // 3600:02d}:{(s % 3600) // 60:02d}:{s % 60:02d}"


def segments_to_result(segments: list[SttSegment], speaker: str) -> TranscriptResult:
    """세그먼트 → 한 줄 한 발화 전사문 + 구간 정보. 줄 안 개행·연속 공백은 한 칸 공백으로, 빈 세그먼트는 버린다."""
    lines: list[str] = []
    out: list[dict[str, Any]] = []
    for seg in segments:
        text = " ".join((seg.text or "").split())
        if not text:
            continue
        lines.append(f"[{format_timestamp(seg.start)}] {speaker}: {text}")
        out.append({"speaker": speaker, "start_sec": float(seg.start), "end_sec": float(seg.end), "text": text})
    if not lines:
        return TranscriptResult(text=NO_SPEECH, segments=None)
    return TranscriptResult(text="\n".join(lines), segments=out)


class LocalStt:
    """faster-whisper 전사 엔진(SttEngine). model_factory 로 가짜 모델을 주입해 GPU·모델 없이 시험한다."""

    provider_name = "local"

    def __init__(
        self,
        cfg: LocalSttConfig,
        model_factory: Callable[[LocalSttConfig], Any] | None = None,
        release_fn: Callable[[], Any] | None = None,
        progress_callback: Callable[[float], None] | None = None,
    ):
        self._cfg = cfg
        self._factory = model_factory or _default_model_factory
        self._release = release_fn or release_gpu_memory
        self._progress = progress_callback

    def transcribe(self, audio_path: Path, mime_type: str) -> TranscriptResult:
        if not Path(audio_path).is_file():
            raise LocalSttError(ERR_FILE_MISSING, "전사할 음성 파일을 찾을 수 없습니다.")
        model = None
        try:
            model = self._factory(self._cfg)
            try:
                raw, info = model.transcribe(
                    str(audio_path), language=self._cfg.language, beam_size=self._cfg.beam_size, vad_filter=True,
                    vad_parameters={"min_silence_duration_ms": 500}, condition_on_previous_text=False,
                )
                total = float(getattr(info, "duration", 0.0) or 0.0)
                segs: list[SttSegment] = []
                for seg in raw:  # 제너레이터: 여기서 실제 전사가 진행된다
                    segs.append(SttSegment(float(seg.start), float(seg.end), seg.text or ""))
                    if self._progress and total > 0:
                        self._progress(min(float(seg.end) / total, 1.0))
            except LocalSttError:
                raise
            except Exception as exc:  # noqa: BLE001
                raise LocalSttError(ERR_TRANSCRIBE, f"로컬 STT 전사 중 오류가 발생했습니다({type(exc).__name__}).") from exc
            return segments_to_result(segs, self._cfg.default_speaker)
        finally:
            model = None  # 참조 제거 → gc → CUDA 캐시 비움. 끝난 뒤에만 반환(=다음 단계 시작)된다
            self.release()

    def release(self) -> None:
        try:
            self._release()
        except Exception as exc:  # noqa: BLE001 — 해제 실패가 전사 결과를 막지 않는다
            log.warning("로컬 STT GPU 메모리 해제 실패(무시): %s", type(exc).__name__)


def make_local_stt(s: Any) -> LocalStt:
    return LocalStt(config_from_settings(s))
