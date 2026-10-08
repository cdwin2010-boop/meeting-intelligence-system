"""자료 파일(txt) 회의록의 전사문 준비(작업 73-1). 처리 파이프라인이 STT 대신 부른다.
재처리는 이미 저장된 전사문(엔진 표시가 자료 파일)이 있으면 그것을 쓰고, 없으면 보관된 txt 를 다시 읽는다."""
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from app.models import Transcript
from app.pipeline.stt import TranscriptResult
from app.pipeline.transcript_text import decode_transcript, parse_transcript
from app.services.source_kind import TRANSCRIPT_TXT_ENGINE


def read_source_transcript(factory: sessionmaker, meeting_id: int, path: Path) -> TranscriptResult:
    with factory() as session:
        saved = session.scalar(select(Transcript).where(Transcript.meeting_id == meeting_id))
        if saved is not None and saved.stt_provider == TRANSCRIPT_TXT_ENGINE and saved.full_text:
            return TranscriptResult(text=saved.full_text, segments=saved.segments)
    return parse_transcript(decode_transcript(path.read_bytes()))
