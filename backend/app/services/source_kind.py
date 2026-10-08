"""회의록 자료 종류(작업 73-1): audio(음성 파일, 기본) | transcript_txt(자료 파일 txt, STT 없음).
새 표 없이 원천 문서의 저장 파일 확장자(.txt)로 판별한다(음성 허용 확장자에는 txt 가 없다). 기존 회의록은 모두 audio."""
from pathlib import PurePath

SOURCE_AUDIO = "audio"
SOURCE_TRANSCRIPT_TXT = "transcript_txt"
SOURCE_KINDS = (SOURCE_AUDIO, SOURCE_TRANSCRIPT_TXT)
# 전사문(transcripts.stt_provider)에 남기는 엔진 표시: STT 를 거치지 않았다는 뜻
TRANSCRIPT_TXT_ENGINE = "자료 파일(STT 없음)"


def source_kind_of_path(file_path: str | None) -> str:
    return SOURCE_TRANSCRIPT_TXT if PurePath(file_path or "").suffix.lower() == ".txt" else SOURCE_AUDIO
