"""FFmpeg 오디오 전처리(작업 75-1 파트 B): 신경망 필터 없이 단일 필터 체인만 쓴다.
체인: highpass=f=80, lowpass=f=8000, afftdn, dynaudnorm → 16kHz 모노 PCM 16bit wav.

- 명령은 인자 리스트로 만들어 shell 을 쓰지 않는다(경로의 공백·한글·특수문자에 안전).
- 입력 파일은 수정하지 않는다. 임시 출력은 실패·타임아웃에서 지운다(성공하면 호출부가 지운다).
- 오류 메시지에는 ffmpeg stderr 의 마지막 몇 줄만 담고 경로는 가린다.
"""
import logging
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

from app.config import settings

log = logging.getLogger("app.audio_preprocess")

FILTER_CHAIN = "highpass=f=80, lowpass=f=8000, afftdn, dynaudnorm"
SAMPLE_RATE = 16000
CHANNELS = 1
_STDERR_TAIL_LINES = 5
_BUNDLED_DIR = Path(__file__).resolve().parent.parent.parent / "bin"  # backend/bin/
_DEFAULT_FFMPEG = "ffmpeg"


class AudioPreprocessError(RuntimeError):
    """전처리 실패(ffmpeg 오류·시간 초과). 메시지에는 경로·개인 정보가 없다."""


class FfmpegNotFoundError(AudioPreprocessError):
    """ffmpeg 실행 파일을 찾지 못했다."""


def resolve_ffmpeg(ffmpeg_path: str | None = None) -> str:
    """실행 파일 경로. 명시한 값 > backend/bin 의 번들 파일(설정이 기본값일 때) > FFMPEG_PATH(PATH 검색 포함)."""
    configured = ffmpeg_path or settings.ffmpeg_path or _DEFAULT_FFMPEG
    if ffmpeg_path is None and configured == _DEFAULT_FFMPEG:
        for name in ("ffmpeg.exe", "ffmpeg"):
            bundled = _BUNDLED_DIR / name
            if bundled.is_file():
                return str(bundled)
    found = shutil.which(configured)
    if found:
        return found
    if Path(configured).is_file():
        return str(configured)
    raise FfmpegNotFoundError("ffmpeg 를 찾을 수 없습니다(FFMPEG_PATH 또는 backend/bin 확인).")


def build_command(ffmpeg: str, input_path: Path, output_path: Path) -> list[str]:
    return [
        ffmpeg, "-y", "-nostdin", "-hide_banner", "-loglevel", "error",
        "-i", str(input_path),
        "-vn",
        "-af", FILTER_CHAIN,
        "-ar", str(SAMPLE_RATE), "-ac", str(CHANNELS),
        "-c:a", "pcm_s16le", "-f", "wav",
        str(output_path),
    ]


def _tail(stderr: str | bytes | None, *hidden: Path) -> str:
    text = stderr.decode("utf-8", errors="replace") if isinstance(stderr, bytes) else (stderr or "")
    for path in hidden:  # 파일 경로 전체는 넣지 않는다
        text = text.replace(str(path), "<file>")
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    return " | ".join(lines[-_STDERR_TAIL_LINES:])[:500]


def preprocess_audio(
    input_path: str | Path,
    output_path: str | Path | None = None,
    ffmpeg_path: str | None = None,
    timeout: float | None = None,
) -> Path:
    """입력을 전처리해 16kHz 모노 wav 로 저장하고 그 경로를 돌려준다. output_path 가 없으면 임시 파일을 만든다(호출부가 지운다)."""
    source = Path(input_path)
    ffmpeg = resolve_ffmpeg(ffmpeg_path)
    limit = timeout if timeout is not None else settings.ffmpeg_timeout_seconds
    created_temp = output_path is None
    if created_temp:
        handle, name = tempfile.mkstemp(prefix="mi-pre-", suffix=".wav")
        os.close(handle)
        target = Path(name)
    else:
        target = Path(output_path)  # type: ignore[arg-type]
    try:
        completed = subprocess.run(
            build_command(ffmpeg, source, target),
            stdin=subprocess.DEVNULL, capture_output=True, timeout=limit, check=False,  # shell 사용 안 함
        )
        if completed.returncode != 0:
            raise AudioPreprocessError(f"ffmpeg 전처리 실패(코드 {completed.returncode}): {_tail(completed.stderr, source, target)}")
        if not target.is_file() or target.stat().st_size == 0:
            raise AudioPreprocessError("ffmpeg 전처리 결과가 비어 있습니다.")
        return target
    except subprocess.TimeoutExpired:
        target.unlink(missing_ok=True)
        raise AudioPreprocessError(f"ffmpeg 전처리 시간 초과({limit:g}초)") from None
    except FileNotFoundError:
        target.unlink(missing_ok=True)
        raise FfmpegNotFoundError("ffmpeg 를 실행할 수 없습니다(FFMPEG_PATH 확인).") from None
    except BaseException:
        target.unlink(missing_ok=True)
        raise
