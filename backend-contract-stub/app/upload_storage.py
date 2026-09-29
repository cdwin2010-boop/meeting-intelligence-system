"""
업로드 음성 파일 보관함: UPLOAD_DIR/<jobId>.<확장자>
- 받는 중에는 임시 이름(.incoming-*)으로 쓰고, 검사를 모두 통과해 작업이 만들어진 뒤에 <jobId> 이름으로 바꾼다.
- completed 가 되면 지우고, failed 면 Retry 를 위해 남긴다.
- 경로는 서버 안에서만 쓴다. API 응답에는 절대 넣지 않는다.
"""
import uuid
from pathlib import Path

from app.config import settings

# 확장자 → MIME 표 (Gemini 파일 업로드에 그대로 쓴다. 거절되면 다른 값으로 바꿔 시도하지 않고 오류를 보고한다)
AUDIO_MIME_TYPES = {".mp3": "audio/mp3", ".m4a": "audio/mp4", ".wav": "audio/wav"}


def upload_dir() -> Path:
    path = Path(settings.upload_dir)
    path.mkdir(parents=True, exist_ok=True)
    return path


def incoming_path(ext: str) -> Path:
    """받는 중인 파일의 임시 경로 (작업 ID가 정해지기 전)"""
    return upload_dir() / f".incoming-{uuid.uuid4().hex}{ext}"


def job_path(job_id: str, ext: str) -> Path:
    return upload_dir() / f"{job_id}{ext}"


def find_upload(job_id: str) -> Path | None:
    """작업의 업로드 파일. 없으면 None. (허용 확장자만 찾는다)"""
    for ext in AUDIO_MIME_TYPES:
        path = job_path(job_id, ext)
        if path.is_file():
            return path
    return None


def delete_upload(job_id: str) -> None:
    path = find_upload(job_id)
    if path is not None:
        path.unlink(missing_ok=True)
