# Changelog

## v1.7.1 — 2026-09-29
### 수정
- `frontend/lib/api-http.ts` — 요청 기한(타임아웃, 기본 10초) 추가. 서버가 응답을 안 주면 화면이 "Loading…"에서 영원히 멈추던 문제를 "오류 안내"로 바꿈. 화면이 취소한 요청은 규칙대로 `AbortError` 그대로 통과.
- `frontend/.env.local.example` — `NEXT_PUBLIC_API_TIMEOUT_MS` 추가.
### 참고
- "CSS가 전혀 안 먹고 데이터가 안 뜸"이 동시에 나오면 코드가 아니라 `/_next/static/*`(CSS·JS) 로딩 실패인 경우가 대부분입니다. 개발 서버를 끄고 `.next` 폴더를 지운 뒤 다시 실행하세요. (`npm run dev` 실행 중에 `npm run build`를 돌리면 `.next`가 뒤섞여 이 증상이 생깁니다.)

## v1.7 — 2026-09-29
가이드 "초보자용 개정판"의 14개 정정 사항을 프로젝트 파일에 반영했습니다.

### 추가
- `.gitignore` — node_modules, .next, venv, `.env*`(단 `.env.example` 제외), 로컬 DB, CLAUDE.local.md 제외
- `CLAUDE.md` — 개정본 (그림자 규칙의 포커스 링 예외, Queued=`--mn-text-muted`, 실행/검사 명령, API 규약, GPU 순차 실행, pytest 76건 유지)
- `README.md`, `CHANGELOG.md`, `docs/API-CONTRACT.md`(API 계약서: 경로·오류코드·정렬 허용 목록·타입)
- `frontend/.env.local.example` — `NEXT_PUBLIC_USE_MOCK`, `API_BASE_URL`, `ADMIN_POLL_MS`, `DEFAULT_MEETING_ID`
- `frontend/lib/api-http.ts` — 실제 HTTP 클라이언트. `signal` 전달, AbortError 그대로 통과, 404→`not_found`, 409→`invalid_state`, 경로 id 인코딩, 204 처리
- `fetchMeeting` (목/실서버 모두) — 첫 화면 상단(제목·일시·참석자)도 API에서 가져옴
- `backend-contract-stub/` — 계약을 구현한 참고 서버(FastAPI + pydantic-settings, camelCase alias, 정렬 허용 목록, 조건부 UPDATE→409, CORS `.env`) + 계약 테스트 `pytest`

### 변경
- `frontend/lib/api.ts` → 파사드. 목(`api-mock.ts`)/실서버(`api-http.ts`)를 환경변수로 선택. 화면 코드의 import 경로(`@/lib/api`)는 그대로.
- `ApiError`, `isAbortError`를 `lib/api-errors.ts`로 분리 (`@/lib/api`에서 계속 export)
- `app/page.tsx` — `fetchMeeting`으로 회의 정보 조회(서버 컴포넌트, 404면 `notFound()`)
- `AdminConsole` — 10초 **조용한 자동 새로고침**: 표 흐림/버튼 글자 변경 없음, 탭이 보일 때만, 이전 요청 취소로 겹침 방지, 실패해도 기존 표 유지, `ADMIN_POLL_MS=0`으로 끔
- `package.json` — name `meeting-intelligence-frontend`, version `1.7.0`

### 알려진 한계
- 이 패키지는 npm/PyPI가 막힌 환경에서 만들어져 **`npm install`, `npm run typecheck`, `next build`, Tailwind 스타일 렌더링, 백엔드 스텁의 `pytest`는 실행해 보지 못했습니다.** 브라우저 동작(정렬·펼침·모달 포커스·킬/재시도·자동 새로고침)과 HTTP 클라이언트 동작은 별도 하니스(React + esbuild + Playwright)로 확인했습니다.
- 관리자 API 인증/권한(RBAC)은 미구현 (스텁에 없음).
- 프론트 자동 테스트 파일은 저장소에 포함되어 있지 않음.
- 회의별 주소(`/meetings/[id]`)는 아직 없음 — `/`가 기본 회의 하나를 엽니다.
