"""
처리 파이프라인 순수 모듈(4a단계, backend-contract-stub/app/pipeline 에서 이식). DB·FastAPI 에 의존하지 않는다.
  stt.py           전사 엔진 (FakeStt / GeminiStt) + NO_SPEECH 판정
  extractor.py     업무 추출 (FakeExtractor / GeminiExtractor) + 구조화 스키마 ActionItemList + 후처리
  dates.py         마감일 정규화(기준일로 상대 날짜 → YYYY-MM-DD)
  gemini_client.py Gemini 클라이언트(GEMINI_KEY_MODE 로 고른 키만 사용)
  errors.py        예외 → 사람이 읽을 한 줄 요약, 키 가리기
  fakes.py         가짜 전사문·가짜 업무(테스트 가이드 A파일)
"""
