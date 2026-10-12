"""회의록 자료 종류(작업 73-1, 84-1): audio(음성 파일, 기본) | transcript_txt(자료 파일 txt, STT 없음) | transcript_xlsx(자료 파일 엑셀, STT 없음).
새 표 없이 원천 문서의 저장 파일 확장자(.txt·.xlsx)로 판별한다(음성 허용 확장자에는 없다). 기존 회의록은 모두 audio."""
from pathlib import PurePath

SOURCE_AUDIO = "audio"
SOURCE_TRANSCRIPT_TXT = "transcript_txt"
SOURCE_TRANSCRIPT_XLSX = "transcript_xlsx"
SOURCE_KINDS = (SOURCE_AUDIO, SOURCE_TRANSCRIPT_TXT, SOURCE_TRANSCRIPT_XLSX)
# STT 를 거치지 않는 자료 파일 종류(처리 파이프라인은 이 둘을 같은 경로로 처리한다)
TRANSCRIPT_KINDS = (SOURCE_TRANSCRIPT_TXT, SOURCE_TRANSCRIPT_XLSX)
# 전사문(transcripts.stt_provider)에 남기는 엔진 표시: STT 를 거치지 않았다는 뜻
TRANSCRIPT_TXT_ENGINE = "자료 파일(STT 없음)"


def source_kind_of_path(file_path: str | None) -> str:
    suffix = PurePath(file_path or "").suffix.lower()
    if suffix == ".txt":
        return SOURCE_TRANSCRIPT_TXT
    if suffix == ".xlsx":
        return SOURCE_TRANSCRIPT_XLSX
    return SOURCE_AUDIO
