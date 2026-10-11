"""업로드 저장 파일명 규칙: {회의록id}_{YYYYMMDDHH}_{순번3자리}{확장자} (작업 76-1).
저장 파일명은 id·숫자·확장자만으로 만들고 사용자가 입력한 문자열(원본 파일명)은 넣지 않는다."""
import os
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from app.config import settings

_MAX_ATTEMPTS = 1000  # 순번 충돌을 피해 다음 순번을 시도하는 최대 횟수


def stored_filename(meeting_id: int, registered_at: datetime, sequence: int, ext: str) -> str:
    """회의록 id·등록 시각(설정 시간대 기준 년월일시)·순번·확장자로 저장 파일명을 만든다. 순번은 3자리 이상(999 초과는 자릿수 증가)."""
    ext = ext.lower().lstrip(".")
    if not ext.isascii() or not ext.isalnum():
        raise ValueError("확장자에는 영문·숫자만 쓸 수 있습니다.")
    if meeting_id < 1 or sequence < 1:
        raise ValueError("회의록 id 와 순번은 1 이상이어야 합니다.")
    if registered_at.tzinfo is None:
        registered_at = registered_at.replace(tzinfo=timezone.utc)
    stamp = registered_at.astimezone(ZoneInfo(settings.app_timezone)).strftime("%Y%m%d%H")
    return f"{meeting_id}_{stamp}_{sequence:03d}.{ext}"


def claim_stored_file(staged: Path, folder: Path, meeting_id: int, registered_at: datetime, ext: str, start_sequence: int = 1) -> Path:
    """임시 저장 파일(staged)을 최종 이름으로 옮긴다. 같은 이름이 이미 있으면 덮어쓰지 않고 다음 순번을 쓴다.
    끝내 자리를 못 찾으면 FileExistsError(staged 는 호출부가 정리한다)."""
    for sequence in range(start_sequence, start_sequence + _MAX_ATTEMPTS):
        target = folder / stored_filename(meeting_id, registered_at, sequence, ext)
        try:
            os.link(staged, target)  # 대상이 있으면 FileExistsError: 덮어쓰지 않는다
        except FileExistsError:
            continue
        except OSError:
            # 하드링크를 못 쓰는 파일시스템: 존재 확인 후 이동(회의록 id 가 고유해 같은 이름을 노리는 경쟁은 없다)
            if target.exists():
                continue
            os.replace(staged, target)
            return target
        staged.unlink(missing_ok=True)
        return target
    raise FileExistsError("저장 파일 이름을 정하지 못했습니다.")
