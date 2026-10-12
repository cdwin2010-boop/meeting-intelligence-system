"""자료 파일(txt·엑셀) 회의록의 전사문 준비(작업 73-1, 84-1). 처리 파이프라인이 STT 대신 부른다.
재처리는 이미 저장된 전사문(엔진 표시가 자료 파일)이 있으면 그것을 쓰고, 없으면 보관된 파일(txt·xlsx)을 다시 읽는다."""
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from app.models import Transcript
from app.pipeline.stt import TranscriptResult
from app.pipeline.transcript_text import build_result, decode_transcript, parse_transcript
from app.pipeline.transcript_xlsx import xlsx_to_utterances
from app.services.source_kind import TRANSCRIPT_TXT_ENGINE


def read_source_transcript(factory: sessionmaker, meeting_id: int, path: Path) -> TranscriptResult:
    with factory() as session:
        saved = session.scalar(select(Transcript).where(Transcript.meeting_id == meeting_id))
        if saved is not None and saved.stt_provider == TRANSCRIPT_TXT_ENGINE and saved.full_text:
            return TranscriptResult(text=saved.full_text, segments=saved.segments)
    raw = path.read_bytes()
    if path.suffix.lower() == ".xlsx":
        return build_result(xlsx_to_utterances(raw))  # 엑셀은 열을 그대로 발언 목록으로(글자로 합쳐 다시 해석하지 않는다)
    return parse_transcript(decode_transcript(raw))
