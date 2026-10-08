"""자료 파일(txt) 전사문 읽기(작업 73-1). STT 를 건너뛰고 올린 전사문(txt)을 기존 STT 결과와 같은 모양(TranscriptResult)으로 바꾼다.

해석 규칙
- 인코딩: UTF-8(BOM 허용) → 실패하면 CP949 → 그래도 실패하면 오류. 빈 파일·공백뿐·NUL 바이트(바이너리)·글자 수 초과는 오류.
- 줄 형식(STT 전사문과 같다): "[mm:ss] 화자N: 내용", "[hh:mm:ss] 이름: 내용". 시각이 없는 "화자N: 내용"·"이름: 내용"도 읽는다.
  · 시각이 있는 줄은 "[hh:mm:ss] 화자: 내용"으로 맞춰 쓴다(근거 시각 산정·화자 표시가 그대로 동작).
  · 시각이 없는 줄은 "화자: 내용"으로 쓰고 구간(segments)에 화자만 남긴다(시각 null → 근거 시각 없음).
  · 라벨이 없는 줄은 직전 발언의 연속으로 붙이고, 첫 줄부터 라벨이 없으면 "화자1" 발언으로 본다.
- 전사 본문은 로그·예외 문구에 넣지 않는다.
"""
import re
from typing import Any

from app.config import settings
from app.pipeline.evidence_time import parse_seconds
from app.pipeline.stt import TranscriptResult

DEFAULT_SPEAKER = "화자1"
_MAX_LABEL_LEN = 20
_TIMED = re.compile(r"^\s*\[\s*(\d{1,3}:\d{2}(?::\d{2})?)\s*\]\s*(.*)$")
_LABELED = re.compile(r"^\s*([^:：\n]+?)\s*[:：]\s*(.*)$")
# 시각 없는 줄을 발언 라벨로 볼 조건: 공백·문장부호 없는 짧은 이름 또는 "화자 N"("내일까지 제출: 금요일" 같은 연속 줄 오인 방지)
_PLAIN_LABEL = re.compile(r"^(?:화자\s*\d+|[^\s.,!?;()\[\]]+)$")


class TranscriptTextError(ValueError):
    """사용자에게 그대로 보여도 되는 문구만 담는다(전사 본문은 넣지 않는다)."""


def decode_transcript(raw: bytes) -> str:
    """바이트 → 글자(UTF-8 BOM 허용, 실패하면 CP949). 빈 파일·바이너리·글자 수 초과는 TranscriptTextError."""
    if b"\x00" in raw:
        raise TranscriptTextError("텍스트 파일이 아닙니다(바이너리로 보입니다).")
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        try:
            text = raw.decode("cp949")
        except UnicodeDecodeError:
            raise TranscriptTextError("txt 파일의 문자 인코딩을 읽을 수 없습니다") from None
    if not text.strip():
        raise TranscriptTextError("빈 파일입니다(내용이 없습니다).")
    if len(text) > settings.transcript_max_chars:
        raise TranscriptTextError(f"글자 수가 너무 많습니다(최대 {settings.transcript_max_chars}자).")
    return text


def _split_timed(rest: str) -> tuple[str | None, str]:
    """시각 뒤 나머지에서 (라벨, 내용). 라벨이 없으면 (None, 전체)."""
    match = _LABELED.match(rest)
    if match and 0 < len(match.group(1).strip()) <= _MAX_LABEL_LEN:
        return match.group(1).strip(), match.group(2).strip()
    return None, rest.strip()


def _split_plain(line: str) -> tuple[str | None, str]:
    match = _LABELED.match(line)
    if match:
        label = match.group(1).strip()
        if 0 < len(label) <= _MAX_LABEL_LEN and _PLAIN_LABEL.match(label):
            return label, match.group(2).strip()
    return None, line.strip()


def _hms(sec: float) -> str:
    total = int(sec)
    return f"{total // 3600:02d}:{total % 3600 // 60:02d}:{total % 60:02d}"


def has_timestamps(result: TranscriptResult) -> bool:
    """시각이 있는 발언이 하나라도 있는가(없으면 근거 시각을 비운다)."""
    return any(line.startswith("[") for line in result.text.splitlines())


def parse_transcript(text: str) -> TranscriptResult:
    """전사문 글자 → TranscriptResult. 시각이 없는 줄이 하나라도 있으면 구간(segments)을 함께 준다(화자 목록 때문)."""
    utterances: list[dict[str, Any]] = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        timed = _TIMED.match(line)
        start: float | None = None
        if timed and (start := parse_seconds(timed.group(1))) is not None:
            label, content = _split_timed(timed.group(2))
        else:
            start = None
            label, content = _split_plain(line)
        if label is None:
            if utterances and not (timed and start is not None):
                utterances[-1]["text"] = f"{utterances[-1]['text']} {content}".strip()  # 직전 발언의 연속
                continue
            label = utterances[-1]["speaker"] if utterances else DEFAULT_SPEAKER
        utterances.append({"speaker": label, "start_sec": start, "text": content})

    lines = [
        f"[{_hms(u['start_sec'])}] {u['speaker']}: {u['text']}" if u["start_sec"] is not None else f"{u['speaker']}: {u['text']}"
        for u in utterances
    ]
    segments = None
    if any(u["start_sec"] is None for u in utterances):
        segments = []
        for index, u in enumerate(utterances):
            end = None
            if u["start_sec"] is not None and index + 1 < len(utterances):
                end = utterances[index + 1]["start_sec"]
            segments.append({"speaker": u["speaker"], "start_sec": u["start_sec"], "end_sec": end, "text": u["text"]})
    return TranscriptResult(text="\n".join(lines), segments=segments)
