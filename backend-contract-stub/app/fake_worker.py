"""
가짜 처리기: 실제 STT/LLM 없이 '작업이 흘러가는 모습'만 흉내 낸다.
   queued --(대기)--> processing(STT) --(대기)--> processing(LLM) --(대기)--> completed
운영 백엔드에서는 이 자리에 Faster-Whisper 또는 Gemini 전사 + LLM 추출이 들어간다.

핵심 원칙: 단계마다 '조건부 UPDATE'를 쓴다. 관리자가 중간에 Kill 하면 조건이 맞지 않아 처리기가 스스로 멈춘다
(취소된 작업에 결과를 덮어쓰지 않는다).
"""
import time
from datetime import datetime, timezone

from app.config import settings
from app.db import store
from app.pipeline.extractor import FAKE_PROVENANCE
from app.uploads import fake_action_items, fake_transcript, parse_started_at


def run_fake_worker(job_id: str) -> None:
    if not settings.fake_worker_enabled:
        return  # 작업이 queued 로 남는다 (테스트/시연용)
    step = settings.fake_worker_step_seconds

    time.sleep(step)
    if not store.start_if_queued(job_id):
        return  # 그 사이 상태가 바뀜(예: 이미 시작됨) → 중복 실행 방지

    time.sleep(step)
    if not store.set_stage_if_processing(job_id, "LLM"):
        return  # 중간에 Kill 됨

    time.sleep(step)
    meeting_id = store.job_meeting_id(job_id)
    meeting = store.get_meeting(meeting_id) or {}
    started_at = parse_started_at(meeting.get("startedAt", ""))
    if started_at is None:
        return
    # 가짜 추출이므로 모델 이름·프롬프트 버전은 "fake" 로 기록한다
    store.complete_if_processing(
        job_id, fake_transcript(), fake_action_items(started_at),
        extract_model=FAKE_PROVENANCE, prompt_version=FAKE_PROVENANCE,
        extracted_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
    )
