# API 규격 (프론트 ↔ 백엔드 계약서) — v1.7

프론트 `frontend/lib/api-http.ts`와 백엔드가 **같은 표를 보고** 만듭니다. 기존 백엔드 경로가 다르면 이 표를 먼저 고치고 양쪽을 맞추세요.
기본 주소: `NEXT_PUBLIC_API_BASE_URL` (기본 `http://127.0.0.1:8000/api`). JSON 필드는 **camelCase**.

| 프론트 함수 | 메서드 | 경로 (기본 주소 뒤) | 성공 | 실패 |
|---|---|---|---|---|
| `fetchMeeting` | GET | `/meetings/{meetingId}` | 200 `Meeting` | 404 |
| `fetchActionItems` | GET | `/meetings/{meetingId}/action-items?sortKey=&direction=` | 200 `ActionItem[]` | 404, 400(잘못된 sortKey) |
| `deleteActionItem` | DELETE | `/action-items/{id}` | 204 (본문 없음) | 404 |
| `fetchAdminJobs` | GET | `/admin/jobs?sortKey=&direction=` | 200 `AdminJob[]` | 400, 401/403 |
| `retryJob` | POST | `/admin/jobs/{id}/retry` | 200 `AdminJob` (status=`queued`) | 404, 409(실패 상태가 아님, 또는 업로드 작업의 음성 파일이 없어짐) |
| `killJob` | POST | `/admin/jobs/{id}/kill` | 200 `AdminJob` (status=`failed`) | 404, 409(처리 중이 아님) |

## 규칙
1. **오류는 상태 코드만 본다.** 프론트는 404 → `not_found`, 409 → `invalid_state`로 바꾼다. 본문은 FastAPI 기본 `{"detail": ...}` 그대로 두면 된다.
2. **정렬 열은 허용 목록으로 검증한다.** 액션아이템: `id, task, assignee, dueDate, status` / 작업: `id, meetingTitle, audioSeconds, elapsedSeconds, status`. 그 외는 400. 값을 SQL에 이어 붙이지 않는다. `direction`은 `asc | desc`.
3. **확인과 변경은 한 번에 (경쟁 상태 방지).** `UPDATE ... SET status='failed' WHERE id=? AND status='processing'` 처럼 조건부로 실행하고, 바뀐 행이 0이면 (id가 없으면 404, 있으면 409).
4. **관리자 API(`/admin/*`)는 서버에서 권한을 검증한다.** 화면 숨김·CORS는 보안이 아니다. (v1.7 스텁에는 인증이 없다 → 운영 백엔드에서 구현)
5. **CORS 허용 출처는 `.env`(`CORS_ALLOW_ORIGINS`)로 관리한다.** `localhost:3000`과 `127.0.0.1:3000`은 서로 다른 출처다.

## 타입 (`frontend/lib/types.ts`와 동일)
```ts
Meeting    { id, title, startedAt(ISO), attendees: { id, name, role }[], transcriptText: string|null }
ActionItem { id, task, assignee, dueDate(YYYY-MM-DD), status: "open"|"in_progress"|"done"|"overdue",
             quote: { speaker, timestamp(HH:MM:SS), text } }
AdminJob   { id, meetingTitle, audioSeconds, elapsedSeconds|null, status: "queued"|"processing"|"completed"|"failed",
             attempt, startedAt|null, worker: { id, host, engine: "gemini-api"|"faster-whisper", stage: "STT"|"LLM", gpu|null }|null,
             errorLog: string[] }
```
빈 값 주의: 프로젝트 지침상 `speaker` 등 미정 항목은 `null`이 아니라 `""`. 단 `elapsedSeconds/startedAt/worker/gpu`는 타입대로 `null` 허용.

---

## 음성 등록 API (v1.8)

> 상태: **계약 확정. 스텁 서버는 구현됨(2단계). 프론트(3단계)·실제 백엔드(4단계)는 아직 이 표를 따르지 않음.**
> 접수창구 비유: 파일을 **맡기면 접수증(작업 ID)을 바로 주고**, 실제 전사는 뒤에서 진행합니다. 손님이 전사가 끝날 때까지 창구 앞에서 기다리지 않습니다.

| 프론트 함수 (예정) | 메서드 | 경로 | 성공 | 실패 |
|---|---|---|---|---|
| `uploadMeetingAudio` | POST | `/meetings` (multipart/form-data) | **202** `UploadReceipt` | 400, 409, 413, 415 |
| `fetchJob` | GET | `/jobs/{jobId}` | 200 `JobStatus` | 404 |

`/admin/jobs`(관리자용 목록)와 별개입니다. 일반 사용자는 **자기 작업 하나의 진행 상태**만 `/jobs/{jobId}`로 봅니다.

### 1) 업로드 요청 (`POST /meetings`)
| 필드 | 필수 | 설명 |
|---|---|---|
| `file` | 필수 | 음성 파일. 확장자 `.mp3 .m4a .wav`만 허용 |
| `title` | 필수 | 회의 제목 (1~200자, 앞뒤 공백 제거) |
| `startedAt` | 필수 | 회의 일시 (ISO 8601, 예: `2026-09-29T14:00:00+09:00`). 마감일 계산의 기준 |
| `engine` | 선택 | `gemini-api`(기본) 또는 `faster-whisper` |
| `forceLocal` | 선택 | `true`이면 GPU 가드 경고(아래 3번)를 알고도 로컬로 진행 |

- 프론트는 `FormData`로 보내며 **`Content-Type` 헤더를 직접 적지 않는다.** (브라우저가 파일 구분선 값을 붙여야 함)
- 서버는 확장자만 믿지 않고 **파일 앞부분(시그니처)도 확인**한다. 이름만 `fake.mp3`인 텍스트 파일은 415.
- 최대 크기는 `.env`의 `MAX_UPLOAD_MB`(기본 500)로 정한다. 넘으면 413, 작업은 만들지 않는다.

### 2) 성공 응답 — 202 `UploadReceipt`
```ts
UploadReceipt { meetingId, jobId, status: "queued" }
```
- **전사가 끝나기를 기다리지 않고 바로** 돌려준다 (수 초 이내). 202는 "접수됨, 처리는 아직"이라는 뜻.
- 응답을 주기 전에 회의(`meetings`)와 작업(`jobs`)이 저장되어 있어야 한다. 그래서 곧바로 `/admin/jobs` 큐에 `queued` 또는 `processing`으로 보인다.

### 3) 실패 코드 (프론트는 상태 코드로 분기)
| 코드 | 언제 | 프론트가 하는 일 |
|---|---|---|
| 400 | `title`/`file`/`startedAt` 누락·형식 오류, `engine` 값 오류, 빈 파일(0바이트) | 필드별 안내 표시 (FastAPI가 422를 내면 400과 같게 취급) |
| 409 | **GPU 가드 경고**: `engine=faster-whisper`이고 예상 처리 시간이 20분 이상, `forceLocal`이 없음. 본문 `detail`: `{"code":"gpu_guard","expectedMinutes":<숫자>}` | 경고창에서 "Gemini로 전환 / 그래도 로컬로 / 취소" 선택. 이 경우 작업은 만들어지지 않음 |
| 413 | 파일이 `MAX_UPLOAD_MB` 초과 | "파일이 너무 큽니다 (최대 N MB)" |
| 415 | 확장자 불허 또는 시그니처가 음성이 아님 | "지원하지 않는 파일 형식" |
| 501 | (v1.9) 서버가 Gemini 파이프라인(`STT_PROVIDER=gemini`)인데 `engine=faster-whisper`. 로컬 엔진은 아직 지원하지 않음. GPU 가드(409) 검사가 먼저 | "로컬 엔진은 아직 지원하지 않습니다. Gemini로 올려 주세요" |
| 503 | (v1.9) 서버가 gemini 모드인데 `GEMINI_API_KEY`가 설정되지 않음 (서버는 켜져 있음) | "서버 설정 문제로 지금은 접수할 수 없습니다. 관리자에게 문의" |

- 오류가 나면 **회의·작업을 저장하지 않는다.** (반쯤 만들어진 작업 방지)
- 409는 기존 규칙 1(`invalid_state`)과 겹치므로, 프론트는 **업로드 함수에서만** 본문의 `detail.code == "gpu_guard"`를 먼저 확인한다.

### 3-1) 전사 원문 (`transcriptText`) — 확정
- 전사 원문은 **서버 파일이 아니라 `Meeting.transcriptText`(문자열, 없으면 `null`)** 로 응답에 담는다. 화면(모달 또는 토글 영역)이 `GET /meetings/{meetingId}`로 바로 읽는다.
- 전사 전(`queued`/`processing`)과 시드 회의는 `null`. `completed`가 되면 채워진다.
- 진행 조회(`/jobs/{jobId}`)에는 넣지 않는다 (폴링마다 긴 글을 주고받지 않기 위해). 원문이 길어지는 문제(1시간 녹음 등)는 4단계에서 다시 본다.

### 3-2) 음성 저장·삭제 정책 (v1.9)
- 검사를 통과한 음성만 서버의 `UPLOAD_DIR/<jobId>.<확장자>`에 보관한다. 받는 중에는 임시 이름으로 쓰고, 어떤 검사(400·409·413·415·501·503)에 걸려도 지운다.
- 파일 경로는 **어떤 API 응답에도 넣지 않는다.**
- 작업이 `completed`가 되면 음성을 지운다. `failed`(무음, Gemini 오류, Kill 등)로 끝나면 Retry를 위해 남긴다.
- Retry(`POST /admin/jobs/{id}/retry`)할 때 업로드 작업의 음성 파일이 없으면 **409**로 거절하고 상태는 바꾸지 않는다 (다시 업로드해야 함).
- 실패 이유(`errorMessage`, `errorLog`)는 사람이 읽을 한 줄이다. API 키와 스택 트레이스는 넣지 않는다. 예: "말소리가 감지되지 않았습니다…", "Gemini API 사용 한도(할당량)를 초과했습니다(429)…".

### 4) 작업 상태 조회 — `GET /jobs/{jobId}`
```ts
JobStatus { id, meetingId, status: "queued"|"processing"|"completed"|"failed",
            stage: "STT"|"LLM"|null, errorMessage: string }   // 실패가 아니면 errorMessage는 ""
```
- `completed`가 되면 `/meetings/{meetingId}/action-items`에 결과가 있다. `failed`면 `errorMessage`에 사람이 읽을 이유(내부 스택 트레이스 금지).
- 화면은 몇 초 간격으로 조회(폴링)하되, 탭이 보일 때만 하고 `completed`/`failed`에서 멈춘다 (AdminConsole과 같은 방식).

### 5) 요청 기한 (타임아웃)
- 업로드는 큰 파일이라 기존 기본 10초로는 부족하다. 업로드에만 `NEXT_PUBLIC_UPLOAD_TIMEOUT_MS`(기본 600000 = 10분)를 따로 쓴다. 조회 계열은 기존 `NEXT_PUBLIC_API_TIMEOUT_MS`(10초) 유지.
- 서버가 응답한 뒤(202)의 전사 시간은 이 기한과 무관하다.

### 6) 이 계약에서 일부러 정하지 않은 것
- 화자 분리 결과의 응답 모양, 업로드 취소, 같은 파일 중복 업로드 처리.
- 인증: 스텁에는 없음. 운영에서는 업로드도 로그인 사용자만 가능해야 한다.

### 7) 스텁 서버가 하는 일 (참고)
- 확장자와 파일 앞부분(시그니처)을 검사하고, 통과한 음성을 위 3-2) 정책대로 보관·삭제한다. 길이는 파일 크기로 어림(16,000 바이트 ≈ 1초, GPU 가드 계산용).
- 처리기는 `.env`의 `STT_PROVIDER`·`LLM_PROVIDER`(각각 `fake` | `gemini`, 기본 `fake`)로 고른다.
  - **둘 다 `fake`(기본)**: 가짜 처리기. 접수 후 `FAKE_WORKER_STEP_SECONDS`(기본 3초)마다 `queued → processing(STT) → processing(LLM) → completed`. 완료 시 테스트 가이드 A파일 정답표와 같은 액션아이템 2건과 전사 원문을 만든다.
  - **하나라도 `gemini`**: `app/pipeline`이 STT(Gemini 파일 업로드 → 전사) → LLM(구조화 출력 `ActionItemList`로 추출)을 실행한다. 한 번에 한 작업만 처리하고 나머지는 `queued`로 기다린다. 전사가 비었거나 `[NO_SPEECH]`이면 `failed`. Gemini 오류(429·인증·타임아웃 등)도 `failed` + 사람이 읽을 요약 한 줄. 모델·키·기한은 `GEMINI_*` 설정으로 정한다.
- 처리 중 Kill 하면 처리기가 멈추고 결과를 저장하지 않는다 (단계마다 조건부 UPDATE).
- 업로드로 만든 작업을 Retry하면 처리기가 다시 돈다(음성 파일이 있어야 함). 시드 작업은 Retry해도 `queued`로 남는다.
- 수동 확인: `python -m scripts.smoke_gemini <음성파일> [회의일시 ISO]` — 서버 없이 `.env` 설정으로 STT → 추출 결과와 단계별 소요 시간을 출력한다(키 값은 출력하지 않음).
