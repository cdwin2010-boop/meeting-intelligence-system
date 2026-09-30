# 프로젝트 규칙: 음성 회의록 AI 의사결정 추적관리 시스템

## 구조와 실행 명령
- backend-contract-stub/: FastAPI + SQLite(파일, `STUB_DB_PATH` 기본 `data/stub.db`, 재시작해도 유지·자동 초기화 없음) + STT/LLM 파이프라인(fake / Gemini) 참고 서버 (Python 3.11+, `.venv`). **현재 실행되는 서버는 이 스텁이다.**
- backend/: 운영 백엔드 자리. **아직 이 저장소에 없다.**
- frontend/: Next.js(App Router) + TypeScript + Tailwind CSS v4
- 프론트 실행: `cd frontend && npm run dev` (http://localhost:3000)
- 프론트 검사: `cd frontend && npm run typecheck`
- 백엔드(스텁) 실행: `cd backend-contract-stub && uvicorn app.main:app --reload --port 8000`
- 백엔드(스텁) 테스트: `cd backend-contract-stub && pytest -q` (현재 125건이 항상 통과해야 함. 테스트는 임시 DB만 쓰고 실제 `data/stub.db`는 건드리지 않음)

## 작업 원칙
- 코드를 바꾸면 해당 검사 명령(typecheck / pytest)을 실행하고 결과를 보고한다.
- 모델명, API 키, 허용 주소 같은 설정값은 하드코딩하지 않는다. 백엔드는 `.env` + pydantic-settings, 프론트는 `.env.local`의 `NEXT_PUBLIC_*`.
- `.env`, `.env.local`의 값은 읽어서 출력하거나 대화에 인용하지 않는다. 변수 이름만 다룬다.
- 요청 범위의 변경만 한다. 관련 없는 리팩터링은 하지 않는다.
- 새 의존성(패키지)을 추가하기 전에 이유를 설명하고 승인을 받는다.

## 백엔드 규칙
- STT(Faster-Whisper)와 로컬 LLM을 GPU 메모리에 동시에 올리지 않는다(순차 실행). STT 종료 시 `finally: stt.release()`에서 `del model`, `gc.collect()`, `torch.cuda.empty_cache()`를 수행한다.
- 구조화 출력은 Pydantic 스키마(`ActionItemList`)를 쓰고, 미정 항목은 None이 아니라 빈 문자열("")로 채운다.
- 기밀 회의는 외부 전송 없이 로컬 파이프라인(Whisper + Ollama)으로 처리할 수 있는 옵션을 유지한다.
- 관리자 API(`/api/admin/*`)는 서버에서 권한을 검증한다. 프론트 화면 숨김만으로 보호하지 않는다.

## API 규약 (프론트 ↔ 백엔드)
- 기본 주소: `NEXT_PUBLIC_API_BASE_URL` (기본값 http://127.0.0.1:8000/api)
- JSON 필드는 camelCase로 내려준다(예: meetingTitle). 파이썬 내부는 snake_case를 유지하고 Pydantic alias로 변환한다.
- 오류: 404 not_found, 409 invalid_state(이미 종료된 작업 등), 401/403 인증·권한, 5xx 서버 오류.
- 프론트 `lib/api-mock.ts`, `lib/api-http.ts`의 모든 요청 함수는 `signal?: AbortSignal`을 받아 `fetch`에 그대로 전달한다. AbortError는 다시 감싸지 말고 그대로 던진다(호출부의 `isAbortError`가 판별한다).

## 프론트엔드 규칙
- Tailwind v4: `tailwind.config.ts`는 없다. 토큰은 `app/mono-dark/tokens.css`(`--mn-*`), 유틸리티는 `bg-mn-*`, `text-mn-*`, `border-mn-*`, `rounded-mn-*`, `font-mn-mono`.
- 컴포넌트에 hex 색을 직접 쓰지 않는다. 항상 토큰을 쓴다.
- 공용 컴포넌트는 `@/components/mono`에서 import. 화면별 컴포넌트는 `components/meeting`, `components/admin`.
- 주석은 한국어로, 핵심 로직 위주로 단다.

## Mono Dark 디자인
- 배경 #000000, 카드 #0A0A0A + 1px solid #262626, 본문 #EDEDED, 보조 #A1A1A1.
- 그림자 금지. 유일한 예외는 키보드 포커스 링(`--mn-focus-ring`, box-shadow로 구현).
- 유채색은 StatusDot(Blue/Teal/Red)과 파괴적 버튼(Red #EE0000)에만. Primary Accent는 #FFFFFF.
- 상태 점: Processing=Blue #0070F3, Completed=Teal #50E3C2, Failed=Red #EE0000, Queued=회색(`--mn-text-muted`). 색과 함께 반드시 글자 라벨을 표시한다.
- 숫자·ID·Job ID·시간·타임스탬프·에러 로그는 Geist Mono.
- 표: 행 높이 48px, 단일 열 3-State 정렬(없음→오름→내림→없음)과 `aria-sort`, 조회는 AbortController로 이전 요청 취소.
- 파괴적 모달: `tone="danger"`, 확인 버튼은 구체적 행동명("Kill Processing Job", "Delete action item"), Cancel에 먼저 포커스, ESC는 최상단 모달만 닫음, 닫으면 여는 버튼으로 포커스 복귀.

## 버전
- 현재 v1.9.10. 변경 내역은 CHANGELOG.md, 화면·API 규격은 docs/API-CONTRACT.md.
- `frontend/.env.local`의 `NEXT_PUBLIC_USE_MOCK=true`(기본)면 목 데이터, `false`면 FastAPI 서버와 통신한다. 화면 코드는 항상 `@/lib/api`만 import한다.
- `backend-contract-stub/`은 API 규격을 확인하는 참고용 서버다. 운영 백엔드(`backend/`, 아직 이 저장소에 없음)를 대체하지 않으며 덮어쓰지 않는다.

