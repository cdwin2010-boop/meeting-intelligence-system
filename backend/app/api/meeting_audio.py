"""회의록 음성 재생: 서명 주소 발급(로그인 필요)과 Range 지원 스트리밍(서명 주소로만).
열람 규칙은 전사문 조회와 같다(get_visible_meeting, 못 보면 404). 새 권한 규칙은 없다.
파일 경로는 DB(source_documents.file_path)에 저장된 값만 쓰고, 요청에서 경로를 받지 않는다."""
from collections.abc import Iterator
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel, ConfigDict
from pydantic.alias_generators import to_camel
from sqlalchemy.orm import Session

from app.auth.access import get_visible_meeting
from app.auth.deps import get_current_account
from app.auth.tokens import AUDIO_TOKEN_MINUTES, InvalidTokenError, create_audio_token, decode_audio_token
from app.config import settings
from app.db import get_session
from app.models import Account, Meeting, SourceDocument

router = APIRouter(prefix="/api/meetings", tags=["meeting-audio"])

NO_AUDIO = "음성 파일이 없습니다"
_CHUNK = 64 * 1024
# 업로드 허용 확장자별 재생 형식(알 수 없으면 octet-stream)
_CONTENT_TYPES = {
    "m4a": "audio/mp4", "mp4": "audio/mp4", "mp3": "audio/mpeg", "wav": "audio/wav", "webm": "audio/webm", "ogg": "audio/ogg",
}


class AudioUrlOut(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)

    # API 기본 주소(…/api) 뒤에 붙이는 경로. 서명 토큰이 쿼리에 들어 있다
    url: str
    expires_in_sec: int


def _not_found(message: str) -> HTTPException:
    return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=message)


def _audio_file(session: Session, meeting: Meeting) -> Path:
    """DB 에 저장된 상대 경로를 UPLOAD_DIR 안의 실제 파일로. 비었거나 폴더 밖이거나 파일이 없으면 404."""
    document = session.get(SourceDocument, meeting.source_document_id)
    if document is None or not document.file_path:
        raise _not_found(NO_AUDIO)
    root = Path(settings.upload_dir).resolve()
    path = (root / document.file_path).resolve()
    if not path.is_relative_to(root) or not path.is_file():
        raise _not_found(NO_AUDIO)
    return path


@router.get("/{meeting_id}/audio-url", response_model=AudioUrlOut, response_model_by_alias=True)
def issue_audio_url(
    meeting_id: int,
    account: Account = Depends(get_current_account),
    session: Session = Depends(get_session),
) -> AudioUrlOut:
    meeting = get_visible_meeting(session, account, meeting_id)
    if meeting is None:
        raise _not_found("회의록을 찾을 수 없습니다")
    _audio_file(session, meeting)  # 파일이 없으면 발급 단계에서 알린다
    token = create_audio_token(account.id, meeting.id)
    return AudioUrlOut(url=f"/meetings/{meeting.id}/audio?token={token}", expires_in_sec=AUDIO_TOKEN_MINUTES * 60)


def _parse_range(header: str, size: int) -> tuple[int, int] | None:
    """'bytes=a-b' · 'bytes=a-' · 'bytes=-n' 한 구간만 지원. 잘못되거나 범위 밖이면 ValueError(416)."""
    unit, _, spec = header.partition("=")
    if unit.strip().lower() != "bytes" or "," in spec:
        raise ValueError("unsupported range")
    start_text, dash, end_text = spec.strip().partition("-")
    if not dash:
        raise ValueError("malformed range")
    if start_text == "":  # 마지막 n 바이트
        if not end_text.isdigit() or int(end_text) == 0:
            raise ValueError("malformed range")
        return max(size - int(end_text), 0), size - 1
    if not start_text.isdigit() or (end_text and not end_text.isdigit()):
        raise ValueError("malformed range")
    start = int(start_text)
    end = min(int(end_text), size - 1) if end_text else size - 1
    if start >= size or end < start:
        raise ValueError("unsatisfiable range")
    return start, end


def _iter_file(path: Path, start: int, end: int) -> Iterator[bytes]:
    remaining = end - start + 1
    with path.open("rb") as handle:
        handle.seek(start)
        while remaining > 0:
            chunk = handle.read(min(_CHUNK, remaining))
            if not chunk:
                break
            remaining -= len(chunk)
            yield chunk


@router.get("/{meeting_id}/audio")
def stream_audio(
    meeting_id: int,
    request: Request,
    token: str = Query(""),
    session: Session = Depends(get_session),
) -> Response:
    # 서명 주소로만 접근. 위조·만료·다른 회의록의 토큰은 401
    unauthorized = HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="재생 주소가 올바르지 않거나 만료되었습니다")
    try:
        account_id, token_meeting_id = decode_audio_token(token)
    except InvalidTokenError:
        raise unauthorized from None
    if token_meeting_id != meeting_id:
        raise unauthorized
    account = session.get(Account, account_id)
    if account is None or not account.is_active:
        raise unauthorized
    # 발급 뒤 보류·삭제·권한 변경이 있어도 지금 기준으로 다시 판정한다
    meeting = get_visible_meeting(session, account, meeting_id)
    if meeting is None:
        raise _not_found("회의록을 찾을 수 없습니다")
    path = _audio_file(session, meeting)

    size = path.stat().st_size
    content_type = _CONTENT_TYPES.get(path.suffix.lower().lstrip("."), "application/octet-stream")
    headers = {"Accept-Ranges": "bytes", "Cache-Control": "private, no-store"}
    range_header = request.headers.get("range")
    if range_header is None:
        headers["Content-Length"] = str(size)
        return StreamingResponse(_iter_file(path, 0, size - 1), media_type=content_type, headers=headers)
    try:
        start, end = _parse_range(range_header, size)
    except ValueError:
        return Response(status_code=status.HTTP_416_RANGE_NOT_SATISFIABLE, headers={**headers, "Content-Range": f"bytes */{size}"})
    headers["Content-Range"] = f"bytes {start}-{end}/{size}"
    headers["Content-Length"] = str(end - start + 1)
    return StreamingResponse(
        _iter_file(path, start, end), status_code=status.HTTP_206_PARTIAL_CONTENT, media_type=content_type, headers=headers
    )
