"""
수동 확인: .env 의 실제 설정으로 STT → 액션아이템 추출을 한 번 돌려 결과를 화면에 출력한다. (서버·DB 불필요)

사용법 (backend-contract-stub 폴더에서):
    python -m scripts.smoke_gemini <음성파일 경로> [회의일시 ISO]
예:
    python -m scripts.smoke_gemini C:\\audio\\A.m4a 2026-09-29T14:00:00+09:00

- STT_PROVIDER / LLM_PROVIDER 를 gemini 로 두어야 실제 Gemini 를 호출한다. (fake 면 가짜 결과)
- API 키 값은 출력하지 않는다 (설정 여부만 표시).
"""
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.config import settings
from app.pipeline.extractor import make_extractor
from app.pipeline.service import describe_error
from app.pipeline.stt import NO_SPEECH, make_stt
from app.upload_storage import AUDIO_MIME_TYPES
from app.uploads import extension_of, parse_started_at

KST = timezone(timedelta(hours=9))


def main(argv: list[str]) -> int:
    if len(argv) not in (1, 2):
        print(__doc__)
        return 2
    audio = Path(argv[0])
    if not audio.is_file():
        print(f"파일이 없습니다: {audio}")
        return 2
    ext = extension_of(audio.name)
    if ext not in AUDIO_MIME_TYPES:
        print(f"지원하지 않는 확장자입니다: {ext or '(없음)'} (mp3, m4a, wav)")
        return 2
    started_at = parse_started_at(argv[1]) if len(argv) == 2 else datetime.now(KST).replace(microsecond=0)
    if started_at is None:
        print("회의일시는 ISO 8601 형식이어야 합니다. 예: 2026-09-29T14:00:00+09:00")
        return 2

    print(f"STT_PROVIDER={settings.stt_provider} ({settings.gemini_stt_model if settings.stt_provider == 'gemini' else '가짜'})")
    print(f"LLM_PROVIDER={settings.llm_provider} ({settings.gemini_llm_model if settings.llm_provider == 'gemini' else '가짜'})")
    print(f"키 모드: {settings.gemini_key_mode}, 키: {'설정됨' if settings.has_gemini_key else '없음'}")
    print(f"음성: {audio.name} ({audio.stat().st_size:,} bytes, {AUDIO_MIME_TYPES[ext]}), 회의일시: {started_at.isoformat()}")
    if settings.uses_gemini and not settings.has_gemini_key:
        print(f"키 모드가 {settings.gemini_key_mode} 인데 {settings.gemini_key_env_name} 가 비어 있습니다. .env 를 확인하세요.")
        return 1

    # ---- STT ----
    t0 = time.monotonic()
    stt = None
    try:
        stt = make_stt()
        transcript = stt.transcribe(audio, AUDIO_MIME_TYPES[ext]).strip()
    except Exception as exc:  # noqa: BLE001
        print(f"\n[STT 실패] {describe_error(exc)} ({time.monotonic() - t0:.1f}초)")
        return 1
    finally:
        if stt is not None:
            stt.release()
    print(f"\n===== 전사문 (STT {time.monotonic() - t0:.1f}초) =====")
    print(transcript or "(빈 결과)")
    if not transcript or transcript == NO_SPEECH:
        print("\n말소리가 감지되지 않았습니다. 서버에서는 이 작업이 failed 가 됩니다.")
        return 1

    # ---- LLM ----
    t1 = time.monotonic()
    try:
        items = make_extractor().extract(transcript, started_at)
    except Exception as exc:  # noqa: BLE001
        print(f"\n[추출 실패] {describe_error(exc)} ({time.monotonic() - t1:.1f}초)")
        return 1
    print(f"\n===== 액션아이템 {len(items)}건 (LLM {time.monotonic() - t1:.1f}초) =====")
    for n, item in enumerate(items, start=1):
        q = item["quote"]
        print(f"{n}. {item['task']}")
        print(f"   담당: {item['assignee'] or '(미정)'} / 마감: {item['dueDate'] or '(미정)'}")
        print(f"   근거: [{q['timestamp']}] {q['speaker']}: {q['text']}")
    print(f"\n총 {time.monotonic() - t0:.1f}초")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
