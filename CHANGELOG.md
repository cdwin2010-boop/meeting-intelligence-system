# Changelog

## v1.9.2 — 2026-09-30 (4단계-B: 프론트 예외 화면)
### 추가
- 업로드 501/503 안내 문구 구분. 501 → "로컬 Faster-Whisper 엔진은 아직 지원되지 않습니다. Gemini API를 선택해 주세요.", 503 → "서버에 Gemini API 키가 설정되지 않아 처리할 수 없습니다. 관리자에게 문의해 주세요." 응답 본문은 읽지 않고 상태 코드로만 구분해 키 이름·값이 화면에 나오지 않음. 기존 409(gpu_guard)·400·413·415 처리는 그대로.
- 마감일이 빈 값("")인 액션아이템은 "미정"으로 표시.
- 처리 완료 후 액션아이템이 0건이면 "추출된 액션아이템이 없습니다. 회의 내용이 없거나 지시·수락이 확인되지 않았습니다." 안내 (사용자가 직접 지워 0건이 된 경우는 기존 문구).
- 작업 실패 시 서버가 준 실패 이유(`errorMessage`)와 다시 시도 안내, "다시 업로드" 버튼(입력값 유지, 폼 잠금 해제).
- api-mock 시나리오: 회의 제목에 `#501`, `#503`, `#nodue`, `#empty`, `#fail` 태그를 넣으면 서버 없이 각 예외 화면 재현.
### 실제 서버(Gemini)로 확인됨
- 0건 안내 문구, 마감일 표시(2026-10-09, 2026-10-01), 전사 원문 보기, 근거 인용.
### 확인하지 못함 (코드 작성, 타입 검사, build까지만)
- 501/503 문구 화면, 마감일 "미정" 화면, 실패 화면과 "다시 업로드" 버튼. 즉 Mock 시나리오 5가지의 화면 확인.
### 알려진 문제
- (a) 회의 목록 화면에 업로드한 회의가 나타나지 않음(서버에 `GET /api/meetings` 목록 조회가 없음, 원인 조사 전).
- (b) 전사 시간 표기가 실행마다 `[MM:SS]`와 `[HH:MM:SS]`로 달라짐.
- (c) lint 미설정 (`lint` 스크립트와 ESLint 없음).

## v1.9.1 — 2026-09-29 (Gemini 무료/유료 키, 모델 기본값)
### 추가
- 설정 `GEMINI_PAID_API_KEY`(유료 키), `GEMINI_KEY_MODE`(`free` | `paid`, 기본 `free`). `GEMINI_API_KEY`는 무료 키. 쓰는 키는 모드로만 고르며, 무료 키가 429에 걸려도 **유료 키로 자동 전환하지 않음**(free 모드의 429 실패 이유에 "GEMINI_KEY_MODE=paid로 바꾸면 유료 키를 씁니다" 안내).
- 503은 선택된 모드의 키가 비었을 때만. `detail`에 모드와 변수 이름만 적음. 작업 시작 로그에 "키 모드: free/paid"만 남김. 오류 문구 가리기(redact)는 두 키 모두에 적용.
- `tests/test_gemini_keys.py`(13건). conftest가 두 키와 모드도 테스트용 값으로 고정.
### 변경
- 모델 기본값 `gemini-3.8-flash` → `gemini-2.5-flash` (`GEMINI_STT_MODEL`, `GEMINI_LLM_MODEL`). 계정의 모델 목록에서 확인된 이름.
- smoke 스크립트는 "키 모드: free/paid, 키: 설정됨/없음"만 표시.

## v1.9.0 — 2026-09-29 (4단계-A: Gemini 처리 파이프라인, 스텁)
### 추가
- `backend-contract-stub/app/pipeline/` — `stt.py`(FakeStt / GeminiStt: 파일 업로드 → ACTIVE 대기 → 전사 → finally 원격 파일 삭제), `extractor.py`(Pydantic `ActionItemList` 구조화 출력, FakeExtractor / GeminiExtractor, 날짜·빈 task 후처리), `service.py`(`process_job`: STT → LLM, 조건부 UPDATE, 동시 처리 1건, 단계별 소요 시간 로그, 실패 요약·키 가림), `gemini_client.py`.
- 설정: `STT_PROVIDER`, `LLM_PROVIDER`(기본 `fake`), `GEMINI_API_KEY`(SecretStr), `GEMINI_STT_MODEL`, `GEMINI_LLM_MODEL`, `GEMINI_TIMEOUT_SECONDS`, `UPLOAD_DIR`.
- 업로드 음성 보관(`app/upload_storage.py`): `UPLOAD_DIR/<jobId>.<확장자>`. completed면 삭제, failed면 Retry용으로 보관, 검사 실패 시 즉시 삭제.
- 오류 코드: 501(gemini 모드 + `engine=faster-whisper`), 503(gemini 모드인데 키 없음), Retry 시 음성 파일이 없으면 409.
- `store.fail_if_processing` (조건부 UPDATE).
- `scripts/smoke_gemini.py` — `.env` 설정으로 STT → 추출을 수동 확인(키 값 미출력).
- 테스트 `tests/test_pipeline.py`(34건, 네트워크·키 없이 가짜 클라이언트 주입), `tests/conftest.py`(테스트마다 임시 `UPLOAD_DIR`, 파이프라인 설정 fake 고정).
- `requirements.txt`에 `google-genai>=2.25.0` 추가 → **`pip install -r requirements.txt` 다시 실행 필요.**
### 변경
- `docs/API-CONTRACT.md` — 실패 코드 표에 501·503, "3-2) 음성 저장·삭제 정책", 7) 스텁 설명 갱신.
- `backend-contract-stub/requirements.txt` 주석을 ASCII로 바꿈. Python 3.11 기본 pip(24.0)가 한국어 Windows에서 이 파일을 cp949로 읽어 `UnicodeDecodeError`로 설치가 멈추던 문제(v1.8 한국어 주석부터 발생). pip 26 이상에서는 원래 문제 없음.
- `.gitignore`에 `meeting-uploads/` 추가 (`UPLOAD_DIR`를 저장소 안에 둘 때 녹음 파일이 커밋되지 않게).
### 알려진 한계
- 실제 Gemini 호출은 이 작업에서 실행하지 않았습니다(키 없음). `python -m scripts.smoke_gemini`로 확인 필요. MIME 표(.mp3 `audio/mp3`, .m4a `audio/mp4`, .wav `audio/wav`)가 API에서 거절되면 그 오류가 그대로 실패 이유에 표시됩니다.
- 프론트는 501·503을 아직 구분하지 않습니다(일반 오류 안내). 다음 단계에서 반영.

## v1.8.0 — 2026-09-29 (프론트 음성 등록 화면, 전사 원문 보기, /meetings/[id])
### 추가
- `/upload` — 음성 등록 화면(`components/upload/UploadForm.tsx`). 파일(mp3·m4a·wav)·회의 제목·회의 일시(KST, `+09:00` ISO로 전송)·엔진 선택. 제출 전 검사(입력칸 아래 글자 안내, `aria-describedby`), 제출 중 "Uploading…"·이중 제출 방지·Cancel upload(AbortController), 오류 코드별 한국어 안내.
- GPU 가드 경고 모달 — "Gemini API로 전환해 업로드 / 그래도 로컬로 진행(`forceLocal`) / 취소", 첫 포커스는 취소.
- `components/upload/UploadProgress.tsx` — 접수증의 Job ID로 `fetchJob`을 `NEXT_PUBLIC_UPLOAD_POLL_MS`(기본 3초)마다 조회. 탭이 보일 때만, `completed`/`failed`에서 멈춤, 실패 시 조용히 재시도. 완료되면 "결과 보기"(`/meetings/{meetingId}`).
- `/meetings/[id]` — 회의별 상세 화면(서버 컴포넌트). 없으면 404. 목 모드에서는 업로드한 회의가 브라우저 메모리에만 있으므로 `MockMeetingLoader`가 브라우저에서 다시 조회.
- 전사 원문 보기(`components/meeting/TranscriptViewer.tsx`) — `Meeting.transcriptText`를 모달에서 Geist Mono로 표시. 원문이 없으면 버튼 비활성화 + "전사 전이거나 원문이 없습니다".
- 진입 링크 — 회의 상세 상단 "음성 등록", `/upload`의 "회의 목록으로"(현재는 `/` 기본 회의로 이동).
- (3단계-A) `lib/` 통신 코드: `uploadMeetingAudio`, `fetchJob`, `GpuGuardError`, 업로드 오류 코드, 목 업로드 흐름, `.env.local.example`에 `NEXT_PUBLIC_UPLOAD_TIMEOUT_MS`·`NEXT_PUBLIC_UPLOAD_POLL_MS`.
### 알려진 한계
- 목 모드에서 업로드한 회의·작업은 브라우저 메모리에만 있어 새로고침하면 사라지고, 관리자 큐(`/admin`)에는 보이지 않습니다.
- 회의 목록 화면은 아직 없습니다("회의 목록으로"는 `/`로 이동).

## v1.8.0-draft — 2026-09-29 (계약 + 스텁 구현, 프론트는 아직)
### 추가
- `docs/API-CONTRACT.md` — "음성 등록 API" 절: `POST /meetings`(multipart, 202 접수증), `GET /jobs/{jobId}`, 오류 코드(400·409 GPU 가드·413·415), 업로드 전용 타임아웃. `Meeting.transcriptText`(문자열|null)로 전사 원문 제공을 확정.
- `backend-contract-stub` — 위 계약 구현: `app/uploads.py`(확장자·시그니처 검사, 가짜 결과), `app/fake_worker.py`(가짜 처리기), 저장소 확장(조건부 UPDATE), `tests/test_upload.py`. 설정 4개 추가(`MAX_UPLOAD_MB`, `GPU_GUARD_THRESHOLD_MINUTES`, `FAKE_WORKER_ENABLED`, `FAKE_WORKER_STEP_SECONDS`).
- `requirements.txt`에 `python-multipart` 추가 → **`pip install -r requirements.txt`를 다시 실행해야 서버가 켜집니다.**
### 알려진 한계
- 이 패키지를 만든 환경은 PyPI가 막혀 있어 **스텁 서버와 pytest(기존 12 + 신규)는 실행해 보지 못했습니다.** 순수 로직(`uploads.py`)만 단독으로 검증했습니다. 사용자 PC에서 `pytest -q` 확인 필요.
- 프론트(`/upload` 화면, `uploadMeetingAudio`, `fetchJob`, 전사 원문 보기)는 3단계.

## v1.7.2 — 2026-09-29
### 수정
- `.gitignore` — `!.env.local.example` 추가. 기존 `.env.*` 패턴이 예시 파일까지 제외해서 Git에 추적되지 않던 문제(가이드가 복사하라고 안내하는 파일이 저장소에 빠짐)를 고침. 실제 `.env.local`은 계속 제외.
- API 기본 주소 기본값을 `localhost` → `127.0.0.1`로 변경 (`api-http.ts`, `.env.local.example`, README, CLAUDE.md, API-CONTRACT.md). Windows에서 서버 컴포넌트가 `localhost`를 IPv6(`::1`)로 먼저 찾아 `ECONNREFUSED`(사용자 화면 500)가 나던 문제 방지.
### 확인됨
- 사용자 PC에서 `npm run typecheck` 통과(오류 0건), 스텁 `pytest -q` 12건 통과, 화면 ↔ 스텁 연동(`USE_MOCK=false`) 확인.

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
