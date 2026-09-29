# API 규격 (프론트 ↔ 백엔드 계약서) — v1.7

프론트 `frontend/lib/api-http.ts`와 백엔드가 **같은 표를 보고** 만듭니다. 기존 백엔드 경로가 다르면 이 표를 먼저 고치고 양쪽을 맞추세요.
기본 주소: `NEXT_PUBLIC_API_BASE_URL` (기본 `http://localhost:8000/api`). JSON 필드는 **camelCase**.

| 프론트 함수 | 메서드 | 경로 (기본 주소 뒤) | 성공 | 실패 |
|---|---|---|---|---|
| `fetchMeeting` | GET | `/meetings/{meetingId}` | 200 `Meeting` | 404 |
| `fetchActionItems` | GET | `/meetings/{meetingId}/action-items?sortKey=&direction=` | 200 `ActionItem[]` | 404, 400(잘못된 sortKey) |
| `deleteActionItem` | DELETE | `/action-items/{id}` | 204 (본문 없음) | 404 |
| `fetchAdminJobs` | GET | `/admin/jobs?sortKey=&direction=` | 200 `AdminJob[]` | 400, 401/403 |
| `retryJob` | POST | `/admin/jobs/{id}/retry` | 200 `AdminJob` (status=`queued`) | 404, 409(실패 상태가 아님) |
| `killJob` | POST | `/admin/jobs/{id}/kill` | 200 `AdminJob` (status=`failed`) | 404, 409(처리 중이 아님) |

## 규칙
1. **오류는 상태 코드만 본다.** 프론트는 404 → `not_found`, 409 → `invalid_state`로 바꾼다. 본문은 FastAPI 기본 `{"detail": ...}` 그대로 두면 된다.
2. **정렬 열은 허용 목록으로 검증한다.** 액션아이템: `id, task, assignee, dueDate, status` / 작업: `id, meetingTitle, audioSeconds, elapsedSeconds, status`. 그 외는 400. 값을 SQL에 이어 붙이지 않는다. `direction`은 `asc | desc`.
3. **확인과 변경은 한 번에 (경쟁 상태 방지).** `UPDATE ... SET status='failed' WHERE id=? AND status='processing'` 처럼 조건부로 실행하고, 바뀐 행이 0이면 (id가 없으면 404, 있으면 409).
4. **관리자 API(`/admin/*`)는 서버에서 권한을 검증한다.** 화면 숨김·CORS는 보안이 아니다. (v1.7 스텁에는 인증이 없다 → 운영 백엔드에서 구현)
5. **CORS 허용 출처는 `.env`(`CORS_ALLOW_ORIGINS`)로 관리한다.** `localhost:3000`과 `127.0.0.1:3000`은 서로 다른 출처다.

## 타입 (`frontend/lib/types.ts`와 동일)
```ts
Meeting    { id, title, startedAt(ISO), attendees: { id, name, role }[] }
ActionItem { id, task, assignee, dueDate(YYYY-MM-DD), status: "open"|"in_progress"|"done"|"overdue",
             quote: { speaker, timestamp(HH:MM:SS), text } }
AdminJob   { id, meetingTitle, audioSeconds, elapsedSeconds|null, status: "queued"|"processing"|"completed"|"failed",
             attempt, startedAt|null, worker: { id, host, engine: "gemini-api"|"faster-whisper", stage: "STT"|"LLM", gpu|null }|null,
             errorLog: string[] }
```
빈 값 주의: 프로젝트 지침상 `speaker` 등 미정 항목은 `null`이 아니라 `""`. 단 `elapsedSeconds/startedAt/worker/gpu`는 타입대로 `null` 허용.
