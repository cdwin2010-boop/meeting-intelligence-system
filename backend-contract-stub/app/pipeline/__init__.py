"""
처리 파이프라인: 업로드 음성 → STT(전사) → LLM(액션아이템 추출) → 저장.
  stt.py       전사 엔진 (FakeStt / GeminiStt)
  extractor.py 추출기 (FakeExtractor / GeminiExtractor) + 구조화 스키마 ActionItemList
  service.py   작업 1건 처리(process_job), 실패 기록, 동시 처리 1건 제한
"""
