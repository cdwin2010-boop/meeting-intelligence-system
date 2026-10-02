# Changelog

## 미배포 (v2 개발 중)
- v2 `backend/` 뼈대 추가: 설정(pydantic-settings, `DATABASE_URL`·`APP_ENV`)·SQLAlchemy 2.0 DB 연결·Alembic 초기화(마이그레이션 없음)·`GET /api/health`·pytest 1건. 스텁·프론트 변경 없음.
- v2 `backend/` 핵심 테이블 7개(tenants·accounts·source_documents·meetings·meeting_participants·action_items·events)와 첫 Alembic 마이그레이션, 업무 "보완 필요" 계산 속성, 추가 전용 `append_event()`, pytest 8건 추가(backend 9건).
- v2 `backend/` ID 로그인(`POST /api/auth/login`, `GET /api/auth/me`, argon2 해시·PyJWT HS256 토큰), 직급 권한 의존성 `require_rank`, 고객사 범위 헬퍼 `scoped`, 3단계 검증용 `GET /api/_probe/manager`, 계정 생성 스크립트 `backend/scripts/create_account.py`, 설정 `SECRET_KEY`·`ACCESS_TOKEN_MINUTES`, pytest 23건 추가(backend 32건).
- v2 `backend/app/pipeline/` STT·업무 추출 순수 모듈 이식(stub `stt.py`·`extractor.py`·`gemini_client.py`·`service.py`의 오류 요약): 업무 후보 데이터클래스 반환, 상대 마감일 서버 계산, NO_SPEECH 판정에 설명 붙은 경우 포함, 설정 `GEMINI_KEY_MODE`·`GEMINI_API_KEY`·`GEMINI_PAID_API_KEY`·`GEMINI_MODEL`·`GEMINI_TIMEOUT_SEC`(모드 자동 전환 없음), 의존성 google-genai, pytest 56건 추가(backend 88건). DB·API 연결 없음.
- v2 `backend/` 음성 업로드 `POST /api/meetings/upload`(202, 확장자·크기·heldAt 오프셋·같은 고객사 참석자 검사, `UPLOAD_DIR/{tenant_id}/{uuid}.{ext}` 저장)와 처리 `process_meeting`(STT → NO_SPEECH면 no_content → 현지 시각 기준 업무 추출 → 업무 생성·담당자 이름 단일 일치 시 매핑 → 등록자 직급별 회의록 확정/확정 대기, 실패 시 분류 코드만 기록, 이벤트 기록), 마이그레이션 `jobs` 표·회의록 상태 failed/no_content, 설정 `STT_PROVIDER`·`UPLOAD_DIR`·`MAX_UPLOAD_MB`·`ALLOWED_AUDIO_EXT`·`APP_TIMEZONE`, 의존성 python-multipart·tzdata, pytest 26건 추가(backend 114건).
- v2 `backend/` 전사문 저장: `transcripts` 표(회의록당 1건, 구간 `segments`는 엔진이 줄 때만), STT 직후 저장해 말소리 없음·추출 실패여도 원문 보존, 이벤트 `transcript.saved`에는 길이·구간 수만. `tenants.audio_retention_days`(NULL=무기한, 자동 삭제 작업 없음, 법무 검토 후 추가 예정). 마이그레이션 downgrade는 데이터가 있으면 거부. pytest 12건 추가(backend 126건).
- v2 `backend/` 회의록 조회 API(읽기 전용): `GET /api/meetings`(상태·mine·needsCompletion 필터, heldAt 내림차순, page/size, 업무 수·보완 필요 수를 집계 쿼리 1회로), `GET /api/meetings/{id}`(개요·참석자·업무 missingFields·최근 이벤트 20건), `GET /api/meetings/{id}/transcript`. 열람 권한은 `app/auth/access.py` 한 곳(manager·executive 고객사 전체, staff 참석·담당·등록 회의록만), 권한 없음·다른 고객사는 404. 보완 필요 계산을 SQL 식으로도 쓰도록 `needs_supplement` hybrid 속성화. pytest 14건 추가(backend 140건).
- v2 `backend/` 쓰기 API: `PATCH /api/action-items/{id}`(업무명·담당자·기한·미확정 보완, 같은 고객사 활성 계정만, 공백만 업무명 거부), `POST /api/action-items/{id}/confirm`(보완 필요 409+missingFields, 멱등), `POST /api/meetings/{id}/confirm`(확정 대기만, 업무 보완과 무관, `withItems=true`면 완성 업무 함께 확정·건너뛴 id 반환), 수정 요청 기록·조회·해결(`/api/meetings/{id}/change-requests`, 상태·자동 확정 시계 영향 없음, 상위 직급 우선). 쓰기와 이벤트는 한 트랜잭션. pytest 32건 추가(backend 172건).
- v2 `backend/` 보완: 확정된 업무를 PATCH 로 빈칸(업무명·담당자·기한)이 되게 바꾸면 400 `{message, missingFields}`로 거부(데이터·이벤트 변경 없음). 다른 유효한 값으로 교체는 허용, pending 업무는 기존대로 비우기 허용. pytest 7건 추가(backend 179건).
- v2 `backend/` 자동 확정 백스톱 `app/jobs/auto_confirm.py`(`run_auto_confirm(session, now)`, `python -m app.jobs.auto_confirm [--dry-run]`): 회의록은 auto_confirm_at 경과 시 period_elapsed, 업무는 보완 완비·소속 회의록 정상일 때 마감일(KST 날짜)과 auto_confirm_at 중 먼저 오는 쪽(같은 날은 due_reached). 상태 조건 UPDATE로 멱등·경쟁 안전, 건별 트랜잭션, 실패는 id만 반환. 메일·스케줄러 연결 없음. pytest 20건 추가(backend 199건).
- v2 `backend/` 알림 1단계(메일 없음): `meeting_views`(상세 조회 성공 시 열람 기록, 첫 열람 유지·마지막 갱신)·`notices`(회의록·업무 확정 시 수동·등록 시·자동 모두 같은 트랜잭션에서 관리자 이상 관련자에게 확정 안내, 수행자 본인 제외) 표와 마이그레이션(데이터 있으면 downgrade 거부), `GET /api/me/notices`·`POST /api/me/notices/seen`, `GET /api/me/todos`(확정 대기·보완 필요·내 업무·자동 확정 미열람, 각 50건+total, 쿼리 수 고정). 열람 권한 EXISTS 서브쿼리 별칭화. pytest 14건 추가(backend 213건).
- v2 `backend/` 알림 2단계(실제 발송 기본 꺼짐): `mail_outbox` 표와 마이그레이션(데이터 있으면 downgrade 거부), 처리 완료로 확정 대기/확정이 된 회의록의 즉시 메일(참석자·담당자, 등록자 제외, 제목·로그인 링크만, dedupe 1회), `python -m app.jobs.daily_mail`(수신자별 KST 하루 1통, 관리자 확정 대기·보완 필요 건수, 담당자 미열람 건수), `python -m app.jobs.send_mail [--dry-run]`(MAIL_ENABLED=false면 skipped, 실패 3회 후 failed, 분류 코드만 기록, SmtpSender STARTTLS·FakeSender). 설정 `MAIL_ENABLED`·`SMTP_*`·`APP_BASE_URL`. pytest 18건 추가(backend 231건).
- v2 `backend/` CORS: 설정 `CORS_ORIGINS`(쉼표 구분, 기본 localhost·127.0.0.1의 3000, `*` 포함 시 시작 거부), CORSMiddleware(GET·POST·PATCH·OPTIONS, Authorization·Content-Type, credentials 끔). 실행 포트 8001 안내(`.env.example`). pytest 10건 추가(backend 241건).
- v2 `frontend/` 2단계: v1과 분리된 `lib/v2/`(errors·token(sessionStorage)·http(Bearer 자동 첨부, 30초 기한, 401 시 토큰 삭제·인증 만료 이벤트)·auth·types·next-path), `components/v2/AuthProvider.tsx`(useAuth, RequireAuth, next는 /v2 아래만), `app/v2/login`(시안 01)·`app/v2`(임시 로그인 확인 화면). 설정 `NEXT_PUBLIC_API_V2_BASE_URL`(기본 http://127.0.0.1:8001/api). E2E `e2e/v2-login.spec.ts` 13건(API는 page.route로 가로챔, 전체 18건). v1 화면·lib 변경 없음.
- v2 `frontend/` 3단계: 앱 틀 `components/v2/AppShell.tsx`(왼쪽 메뉴 할 일·회의록·회의록 올리기, 위쪽 로그인 사용자·로그아웃)와 `app/v2/(app)/layout.tsx`(RequireAuth 재사용, /v2/login 제외), 할 일 화면 `app/v2/(app)/page.tsx`(시안 02, `GET /api/me/todos`·`/api/me/notices` 조회 — 확정 안내·건수 카드·확정 대기·보완 필요(관리자 이상)·자동 확정됨(미열람)·내 업무 표(업무명·기한·상태 글자 라벨), 빈 목록·오류(다시 시도) 표시, 읽기 전용), `lib/v2/todos.ts`, 회의록·올리기 빈 자리 화면. 임시 `app/v2/page.tsx` 삭제. `.env.local.example`에 `NEXT_PUBLIC_API_V2_BASE_URL` 추가. E2E `e2e/v2-todos.spec.ts` 4건 추가, `v2-login.spec.ts`는 홈 확인 문구만 할 일 화면 기준으로 수정(전체 22건). v1 화면·lib·backend 변경 없음.
- v2 `frontend/` 4단계: 회의록 목록 `app/v2/(app)/meetings/page.tsx`(시안 03, `GET /api/meetings?page&size=20` 조회, 회의 일시(현지 시각)·회의명·상태 글자 라벨(전사·추출 중/확정 대기/자동 확정됨/확정 완료/처리 실패/내용 없음)·업무 건수, 이전·다음 쪽, 빈 목록·오류(다시 시도) 표시, 이전 요청 AbortController 취소), 행·회의명 클릭 시 `/v2/meetings/{id}` 이동(상세는 빈 자리 화면), `lib/v2/meetings.ts`. 상태 필터·내가 등록/담당 버튼은 이번 범위에서 제외. E2E `e2e/v2-meetings.spec.ts` 4건 추가(전체 26건). v1 화면·backend 변경 없음.
- v2 `frontend/` 5단계: 회의록 상세(조회 전용) `app/v2/(app)/meetings/[id]/page.tsx`(시안 05, `GET /api/meetings/{id}` — 제목·일시·확정 상태·목록으로 돌아가기, 개요(등록자·참석자·자동 확정 예정/확정 정보)·요약·결정사항, 업무 원장 표(상태·업무명·담당자·기한·보완 필요·근거 타임스탬프와 인용은 글자로만), 전사문 접고 펼치기(처음 펼칠 때 `GET /api/meetings/{id}/transcript` 1회, 구간 있으면 시각·화자별), 로딩·오류(다시 시도)·404(찾을 수 없음) 표시). 쓰기·재생 버튼 없음. `lib/v2/meetings.ts`에 상세·전사문 타입과 요청 함수, 목록·상세 공용 표시 규칙 `components/v2/meeting-display.ts`(목록 화면에서 옮김). E2E `e2e/v2-meeting-detail.spec.ts` 4건 추가, `v2-meetings.spec.ts` 행 클릭 테스트는 상세 응답 가로채기·제목 확인으로 수정(전체 30건). v1 화면·backend 변경 없음.
- v2 `frontend/` 6단계: 회의록 올리기 `app/v2/(app)/upload/page.tsx`(시안 04, `POST /api/meetings/upload` multipart — 음성 파일(선택·끌어다 놓기)·회의명(비우면 서버가 파일명 사용)·회의 일시(브라우저 현지 시각 → 오프셋 붙은 ISO `heldAt`), 올리는 중 표시·연타 방지, 202 성공 시 `/v2/meetings/{meetingId}` 이동, 형식(415)·용량(413)·서버 오류는 서버 문구 표시와 다시 시도). 허용 형식·용량은 서버 설정이 판정(프론트에 값 없음). 참석자 선택은 계정 목록 API가 없어 제외, 회의 유형 선택은 v2.0 이후. `lib/v2/meetings.ts`에 `uploadMeeting`, 설정 `NEXT_PUBLIC_V2_UPLOAD_TIMEOUT_MS`(기본 600000, `.env.local.example`에 추가). 상세 화면 원천 라벨에 `audio_minutes`=음성 추가. E2E `e2e/v2-upload.spec.ts` 5건 추가(전체 35건). v1 화면·backend 변경 없음.
- v2 참석자 선택: backend `GET /api/accounts`(`app/api/accounts.py`, 로그인 필수, `scoped()`로 같은 고객사 활성 계정만, 응답은 id·name·rank만 — 이메일·로그인 ID·비밀번호 해시 없음, 이름순), 라우터 등록만 `app/main.py`에 추가(기존 API·테이블 변경 없음), pytest 4건 추가(backend 245건). frontend `lib/v2/accounts.ts`, `components/v2/ParticipantPicker.tsx`(체크박스 칩 여러 명 선택·선택 사항, 목록 조회 실패 시 오류와 다시 불러오기만 표시하고 올리기는 막지 않음), 올리기 화면이 선택한 참석자를 `participantIds`로 반복 전송(없으면 보내지 않음). E2E 2건 추가·선택 없이 전송 확인 1건 보강(전체 37건). v1 화면 변경 없음.
- v2 `backend/` 화자 확정·계정 매핑: `meeting_speakers` 표(회의록별 화자 표기 → 같은 고객사 계정 또는 미등록 이름 글자, 둘 중 하나만 CHECK, (회의록, 표기) 유일)와 마이그레이션(데이터 있으면 downgrade 거부), `GET·PUT /api/meetings/{id}/speakers`(볼 수 없으면 404, 등록자·관리자 이상만 아니면 403, 전체 교체, 전사문에 있는 표기만, 같은 고객사 활성 계정만, 미등록 이름 1~50자, 어긋나면 400·저장 없음, 기존 매핑 저장자보다 낮은 직급은 409), 저장 시 계정 매핑 화자와 추출 담당자 글자(item.created 사건의 assignee_name)가 화자 표기 또는 계정 이름과 정확히 같고 계정이 하나뿐이면 pending·담당자 빈 업무만 자동 채움(덮어쓰지 않음, `item.updated` 사건 via=speaker_mapping, 미등록 이름은 담당자로 안 씀), 사건 `meeting.speakers_updated`(건수·업무 id만). 전사문 응답에 추가 필드 `displayText`(줄머리 화자 표기만 표시 이름으로, 미등록은 "이름(미등록)")·`speakers`(fullText·segments 원본 그대로). 업무 담당자 지정 `PATCH /api/action-items/{id}`는 이미 같은 고객사 활성 계정만 허용해 변경 없음. 기존 전사문 응답 동등 비교 테스트 1건은 추가 필드 포함으로 수정. pytest 20건 추가(backend 265건). 운영 DB에는 마이그레이션을 적용하지 않음.
- v2 `backend/` 확정 권한 정책 변경(판정만, API 주소·응답·테이블 변경 없음): `app/auth/access.py`에 `is_meeting_lead`·`can_confirm_meeting`·`can_write_item`. 회의록 확정(`POST /api/meetings/{id}/confirm`, withItems 포함)은 총괄 관리자와 지시자만 — 총괄 = 등록한 관리자, 담당자가 등록한 회의록이면 그 회의 참석 관리자(사용자 결정) — 그 밖의 관리자는 403(상태 검사보다 먼저). 업무 수정·확정(`PATCH /api/action-items/{id}`, `POST .../confirm`)은 지시자·총괄 관리자는 모든 업무, 그 밖의 관리자는 변경 전 담당자가 본인인 업무만(아니면 403). 담당자 직급 403·담당자 등록 시 확정 대기·자동 확정·열람 권한은 그대로. 옛 규칙을 가정한 기존 테스트 3건 수정(참석 관리자 또는 지시자가 확정하도록). pytest 10건 추가(backend 275건).
  - 현행 유지로 결정(2026-10-02): ① 총괄이 아닌 관리자도 본인 업무의 담당자를 다른 계정으로 바꿀 수 있음(판정은 변경 전 담당자 기준) ② 업무 수정·확정에는 직급 우선 규칙 없음(나중 저장이 남음, 직급 우선은 수정 요청 해결·화자 매핑만) ③ 화자 매핑은 등록자·관리자 이상 누구나 저장, 저장 시 빈 담당자 자동 채움 유지 ④ 수정 요청 해결은 관리자 이상 누구나 ⑤ 할 일의 확정 대기·보완 필요 목록은 관리자 이상 전원에게 표시.
- v2 `frontend/` 화자 확정 패널 `components/v2/SpeakerPanel.tsx`(회의록 상세): `GET /api/meetings/{id}/speakers`로 화자 표기·저장된 매핑을 불러와 표시, 화자마다 지정 안 함 / 같은 고객사 계정 / 계정 없음·이름 직접 입력(30자) 선택, `PUT` 전체 교체 저장 후 상세(업무 원장) 재조회·전사문 `displayText`로 갱신(구간이 있으면 `speakers` 표시 이름 적용), 저장 400·403·409·서버 오류는 서버 문구·입력값 유지, 조회 403은 권한 없음 안내만, 계정 목록 실패 시 오류·다시 불러오기만 표시하고 이름 입력은 계속 사용. `lib/v2/meetings.ts`에 `getSpeakers`·`saveSpeakers`·화자 타입, 전사문 타입에 `displayText`·`speakers`, `lib/v2/http.ts` 요청 메서드에 PUT 추가. E2E `e2e/v2-speakers.spec.ts` 6건 추가, 상세로 이동하는 기존 스펙 3개에 화자·계정 API 가로채기 추가(전체 43건). backend·v1 변경 없음. 주의: backend CORS 허용 메서드에 PUT 이 없어 실서버 연동 시 저장 요청이 브라우저에서 막힘(backend 별도 조치 필요).
- v2 `backend/` CORS 허용 메서드에 PUT 추가(GET·POST·PATCH·PUT·OPTIONS, 화자 매핑 저장 `PUT /api/meetings/{id}/speakers`가 브라우저에서 막히던 문제 해소). 출처·헤더·credentials 설정은 그대로. 기존 preflight 테스트의 허용 메서드 기대값 갱신, PUT preflight 허용 pytest 1건 추가(backend 276건). E2E 43건 통과.
- v2 `frontend/` 업무 담당자 지정·변경(회의록 상세 업무 원장): 담당자 칸에 "지정/변경" 버튼, 대화상자 `components/v2/AssigneeDialog.tsx`(열 때 `GET /api/accounts`로 같은 고객사 계정 목록, 한 명 선택, 현재 담당자와 같으면 저장 불가, 이름 글자 입력 없음). 저장 `PATCH /api/action-items/{id}` `{assigneeId}`(`lib/v2/action-items.ts` `updateAssignee`), 성공 시 서버가 준 업무로 그 행만 교체(보완 필요 표시도 서버 값). 400·403·409·서버 오류는 서버 문구 표시·선택값 유지, 계정 목록 실패 시 오류·다시 시도. 권한 판정은 서버. 담당자 비우기는 제공하지 않음. E2E `e2e/v2-assignee.spec.ts` 5건 추가(전체 48건). backend·v1 변경 없음.
- v2 `backend/` 권한 보강(판정만, API 주소·응답·테이블·자동 채움·마이그레이션 변경 없음): 화자 매핑 저장 `PUT /api/meetings/{id}/speakers`는 회의록 확정 권한(`can_confirm_meeting`: 지시자, 또는 총괄 — 등록한 관리자, 담당자 등록 회의록이면 참석한 관리자)이 있어야 하고 아니면 403(등록하지 않은 관리자·담당자 직급 등록자 거부). 조회 `GET`은 기존대로(등록자 또는 관리자 이상). 할 일 `GET /api/me/todos`의 확정 대기 회의록 묶음은 확정할 수 있는 회의록만(보완 필요 업무 묶음·열람 규칙은 그대로). 총괄 규칙을 SQL 조건 `meeting_lead_condition`·`can_confirm_condition`(`app/auth/access.py`) 한 곳에 두고 `is_meeting_lead`도 이를 사용. 옛 규칙을 가정한 기존 테스트 3건 수정(`test_todos_for_staff_and_manager`, `test_staff_registrant_allowed_but_other_staff_forbidden` → `test_staff_registrant_can_view_but_not_save_and_other_staff_forbidden`, `test_lower_rank_cannot_overwrite_higher_rank_mapping`), pytest 3건 추가(backend 279건). E2E 48건·tsc 통과. 담당자 등록 회의록의 저장 가능 관리자는 지시문의 "관리자 이상" 대신 확정 권한과 같은 "참석한 관리자"로 결정(2026-10-02, 사용자 확인).
- v2 `frontend/` 확정 동작(회의록 상세): 확정 대기 회의록 머리에 "회의록 확정" 버튼과 확인 대화상자(`components/v2/MeetingConfirmButton.tsx`, 취소에 먼저 포커스, 확인 버튼 "회의록 확정", `POST /api/meetings/{id}/confirm` — 회의록만 확정, 성공 시 응답으로 상태·확정 정보 갱신). 업무 원장에 "동작" 열: 확정 전 업무마다 "확정"(`POST /api/action-items/{id}/confirm`, 성공 시 그 행 교체)과 "수정 요청". 수정 요청 패널 `components/v2/ChangeRequestPanel.tsx`(`GET·POST /api/meetings/{id}/change-requests`, 대상 회의록 전체 또는 업무 하나, 코멘트 2000자, 목록에 작성자·시각·대상·해결 대기/수락됨/반려됨, 해결 동작은 없음). 상세 응답에 권한 값이 없어 버튼은 모두에게 보이고 403·409(보완 필요)·400 등은 서버 문구 표시·입력 유지. `lib/v2/meetings.ts`에 `confirmMeeting`·`listChangeRequests`·`createChangeRequest`, `lib/v2/action-items.ts`에 `confirmActionItem`. E2E `e2e/v2-confirm.spec.ts` 5건 추가(전체 53건). 기존 `v2-meeting-detail.spec.ts`의 "조회 전용: 확정·수정 버튼 없음" 검사를 재생 버튼 없음만으로 좁힘, 상세로 이동하는 기존 스펙 5개에 수정 요청 조회 가로채기 추가. backend·v1 변경 없음.
- v2 `frontend/` 수정 요청 팝업(회의록 상세): 업무 행 "수정 요청"과 수정 요청 패널 머리의 "회의록 전체 수정 요청" 버튼이 팝업 `components/v2/ChangeRequestDialog.tsx`를 연다(화면 스크롤 이동 없음). 대상(업무명 또는 회의록 전체)은 연 버튼이 정해 표시, 코멘트·"수정 요청 남기기"·"취소", 성공 시 닫히고 아래 목록 재조회·완료 문구, 403·400 등은 팝업 안에 서버 문구·입력 유지. 취소에 먼저 포커스·Esc·배경 클릭 닫기·연 버튼으로 포커스 복귀. `ChangeRequestPanel`은 목록 표시만 남김(작성 입력칸·대상 선택 제거). 공용 `Modal`에 선택 속성 `initialFocus="cancel"` 추가(기본 동작 변경 없음). E2E `v2-confirm.spec.ts` 수정 요청 테스트를 팝업 방식으로 바꾸고 회의록 전체 대상·취소/Esc/배경 닫기 2건 추가(전체 55건). backend·v1 변경 없음.
- v2 `frontend/` 수정 요청 팝업 내역: 팝업 하단 "수정 요청 내역"에 연 대상(업무 하나 또는 회의록 전체)의 요청만 작성자·작성 시각·해결 상태·코멘트로 표시(없으면 "남긴 수정 요청이 없습니다."), 내역이 길면 그 영역만 스크롤·팝업 높이는 화면 이내. 새 요청을 남기면 팝업은 열린 채 입력을 비우고 완료 문구와 함께 내역·화면 아래 패널에 반영, 닫기 버튼은 "닫기"(Esc·배경 클릭도 닫힘). 수정 요청 목록은 `useChangeRequests`로 회의록마다 한 번 받아 패널과 팝업이 함께 쓰고(팝업을 열 때 요청 없음) 남긴 뒤에만 다시 받음. 표시 규칙은 `ChangeRequestEntries`로 공용화(`ChangeRequestPanel.tsx`). E2E `v2-confirm.spec.ts` 수정 요청 테스트 3건을 새 동작에 맞게 수정, 대상별 내역·내역 없음·열 때 재요청 없음 1건 추가(전체 56건). backend·v1 변경 없음.
- v2 `frontend/` 수정 요청 팝업 내역 쪽 나누기: 한 대상의 요청이 10건을 넘으면 "범위 / 전체 건수"와 이전 · 쪽 번호 · 다음 표시(현재 쪽은 흰 테두리·굵게, `aria-current="page"`), 10건 이하이면 표시 없음. 열 때는 항상 첫 쪽, 정렬은 받은 순서(작성 순) 그대로, 새 요청을 남기면 팝업은 열린 채 새 요청이 있는 마지막 쪽으로 이동. `GET /api/meetings/{id}/change-requests`가 쪽 인자·전체 건수를 지원하지 않아(배열만 반환) 이미 받은 목록을 화면에서 10건씩 나눔. 화면 아래 패널은 변경 없음. E2E `v2-confirm.spec.ts` 3건 추가(10건 이하 미표시, 25건 첫 쪽·2쪽·다음·이전·다시 열면 첫 쪽, 새 요청 후 갱신; 전체 59건). backend·v1 변경 없음.
- v2 `frontend/` 전사문 원본 보기(회의록 상세): 전사문을 펼치면 상단에 현재 보기("원본"/"이름 적용본") 글자 표시. 지정된 화자(`speakers`)가 없으면 원본만 보이고 전환 버튼 없음, 있으면 기본 이름 적용본과 "원본 보기"↔"이름 적용본 보기" 전환. 원본은 `fullText`(구간이 있으면 `segments`의 화자 표기 그대로), 적용본은 `displayText`(구간이면 `speakers` 표시 이름) — 기존 응답 필드로 구분 가능해 backend 변경 없음. 화자를 저장하면 전사문을 다시 받아 이름 적용본 보기로 돌아옴. 다운로드는 제외. E2E `e2e/v2-transcript-view.spec.ts` 4건 추가(지정 전 원본만, 지정 후 적용본·원본 전환·복귀, 구간 전사문, 원본 보기 중 화자 저장 후 갱신; 전체 63건). backend·v1 변경 없음.
- v2 `frontend/` 수정 요청 답변·해결(회의록 상세 수정 요청 팝업): 내역의 해결 대기 요청마다 "답변·해결" → 답변(선택, 2000자)과 "수락"·"반려" (`POST /api/meetings/{id}/change-requests/{requestId}/resolve` `{decision, reason}`, `lib/v2/meetings.ts` `resolveChangeRequest`). 성공 시 응답으로 그 요청만 교체해 수락됨/반려됨과 처리자·처리 시각·답변 표시(이미 해결된 요청에도 표시). 403·409·422는 그 요청 아래에 서버 문구·답변 유지. 버튼은 모두에게 보이고 권한(관리자 이상)·직급 우선 판정은 서버. 본문 중간의 수정 요청 패널 제거, "회의록 전체 수정 요청" 버튼은 제목·확정 상태 줄 오른쪽(회의록 확정 옆)으로 이동. `ChangeRequestPanel.tsx` → `ChangeRequestHistory.tsx`(목록 훅 `useChangeRequests`에 `replace` 추가, 표시 `ChangeRequestEntries`). 내역 10건 쪽 나누기 유지. 할 일(`/api/me/todos`)·알림(`/api/me/notices`)에는 수정 요청 대기 항목이 없음(확인만, 변경 없음). E2E `v2-confirm.spec.ts`: 제거한 패널에 의존하던 기존 4건 수정, 3건 추가(수락 후 처리 정보, 403 후 반려, 상단 버튼·본문 영역 없음; 전체 66건). backend·v1 변경 없음.
- v2 수정 요청 대기 알림: backend `GET /api/me/todos`에 `pendingChangeRequests` 묶음 추가(기존 필드·묶음·권한 규칙·테이블 변경 없음, 마이그레이션 없음). 항목은 requestId·meetingId·meetingTitle·itemId(null=회의록 전체)·itemTitle·requester·createdAt·commentPreview(80자, 넘으면 "…"), 최대 50건+total, 오래된 요청 먼저, 해결 사건이 있는 요청 제외. 포함 판정은 해결 API와 같음: `meeting_actions.RESOLVE_MIN_RANK`("manager") 이상(`app/auth/deps.py`에 `require_rank`와 같은 판정 `has_rank` 추출) + 회의록 열람 가능(`meeting_visibility`) — 담당자에게는 빈 목록. 요청자·업무 이름은 id 모아 한 번씩 조회(쿼리 수 고정). `/api/me/notices`에는 넣지 않음(notices.kind CHECK 제약이 confirmed_notice만 허용해 마이그레이션 필요, 안내는 수신자별 행·확인 처리 방식이라 "해결되면 사라짐"과 맞지 않음). pytest 4건 추가(backend 283건). frontend 할 일 화면에 "수정 요청 대기" 묶음(회의록 제목·코멘트 앞부분·대상·요청자·요청 시각·해결 대기 점, 누르면 `/v2/meetings/{id}`), 0건이거나 필드가 없으면 숨김, `lib/v2/todos.ts`에 타입 추가. E2E `v2-todos.spec.ts` 2건 추가(전체 68건). v1 변경 없음.
- v2 `frontend/` 화자 지정 팝업(회의록 상세): 본문의 "화자 확정" 패널 제거, 개요 "참석자" 칸에 "화자 지정" 버튼과 지정된 화자 이름 한 줄 요약(`GET /api/meetings/{id}/speakers`를 회의록마다 한 번, 조회 실패·403이면 요약만 숨김). 버튼은 팝업 `components/v2/SpeakerDialog.tsx`(`SpeakerPanel.tsx`에서 이름 변경)를 연다: 열 때마다 화자 표기·저장된 매핑·계정 목록을 불러와 저장된 값으로 표시, 표기마다 같은 고객사 계정 / 계정 없음·이름 직접 입력(30자, "(미등록)"), `PUT` 전체 교체 저장 성공 시 요약을 저장 응답으로 바꾸고 업무 원장(상세 재조회)·전사문(적용본)을 새로 받은 뒤 닫힘, 결과 안내("화자를 저장했습니다 · 담당자 자동 지정 N건")는 참석자 칸에 표시. 400·403·409·422·서버 오류는 팝업 안 서버 문구·입력 유지, 조회 403은 권한 없음 안내만(저장 버튼 비활성), 계정 목록 실패 시 오류·다시 불러오기만 표시하고 이름 입력은 계속 사용. 취소에 먼저 포커스·Esc·배경 클릭 닫기·연 버튼으로 포커스 복귀. 권한 판정은 서버. 전사문 원본 보기·수정 요청·담당자 팝업 변경 없음. E2E `v2-speakers.spec.ts` 6건을 팝업 방식으로 수정·1건 추가(Esc·배경 닫기), `v2-transcript-view.spec.ts` 화자 저장 1건을 팝업 방식으로 수정(전체 69건). backend·v1 변경 없음.
- v2 `backend/` 수정 요청 대기에서 본인 요청 제외: `GET /api/me/todos`의 `pendingChangeRequests`(항목·total 모두)에서 요청자가 현재 사용자인 요청을 뺌. 다른 관리자·지시자에게는 그대로 보이며 회사 내 관리자 이상 전원 표시 규칙·다른 응답 필드·권한·테이블 변경 없음. pytest 1건 추가, 다른 고객사 격리 테스트 1건은 고객사B 확인을 요청자 본인 대신 같은 고객사의 다른 관리자로 수정(backend 284건). frontend·v1 변경 없음.
- v2 `backend/` 업무 종결·삭제: `POST /api/action-items/{id}/close`(확정된 업무만, 확정 전 409, 이미 종결이면 200 멱등, 삭제된 업무 409; 권한은 업무 수정·확정과 같은 `can_write_item` — 지시자·총괄·본인 담당 관리자), `POST /api/action-items/{id}/delete`(행은 남기고 status=deleted, 어떤 상태든 가능, 이미 삭제면 200 멱등; 권한은 회의록 확정과 같은 `can_confirm_meeting` — 지시자·총괄만). 응답은 기존 업무 형식(ActionItemOut). 누가·언제는 새 칸 `closed_by`·`closed_at`·`deleted_by`·`deleted_at`과 사건 `item.closed`·`item.deleted`(before/after 상태)에 기록. 마이그레이션 `4ad1176618ed`(칸 추가만, 기존 칸·데이터 변경 없음, 기록이 있으면 downgrade 거부). 종결된 업무는 수정(PATCH)·확정 409. 제외: 상세 업무 목록은 기존대로 삭제 제외·종결은 status=closed 로 구분, 할 일(내 업무·보완 필요·자동 확정 미열람)·자동 확정·일일 메일은 기존 상태 조건으로 이미 제외됨을 테스트로 확인, `/api/me/notices`는 종결·삭제된 업무의 확정 안내를 빼고 보여 줌(안내 행은 유지), 즉시 메일 수신자에서 종결 업무 담당자도 제외(`INACTIVE_ITEM_STATUSES`). pytest `tests/test_close_delete.py` 17건 추가(backend 301건). 운영 DB에는 마이그레이션을 적용하지 않음. frontend·v1 변경 없음.
- v2 `backend/` 회의록 보류·재개: `POST /api/meetings/{id}/hold`·`/resume`(`app/api/meeting_hold.py`). 권한은 회의록 확정과 같은 `can_confirm_meeting`(지시자·총괄, 그 밖의 관리자·담당자 403, 볼 수 없음·다른 고객사 404), 확정 대기·확정 회의록만(그 밖 409), 이미 보류면 200 멱등·보류 아니면 재개 200 변경 없음. 응답 `MeetingHoldResult`(onHold·onHoldBy/At·resumedBy/At·autoConfirmAt·clearedDueItemIds). 보류 상태와 마지막 보류·재개한 사람·시각은 새 표 `meeting_holds`(회의록당 1행)에, 이력은 사건 `meeting.on_hold`·`meeting.resumed`에 기록. `Meeting.on_hold`는 이 표에서 계산하는 읽기 전용 값. 마이그레이션 `7c021789291d`(표 추가만, meetings 등 기존 표·칸·데이터 변경 없음, 행이 있으면 downgrade 거부). 보류 중: 딸린 업무는 상태를 바꾸지 않고 보류로 보며 업무 수정·확정·종결·삭제, 회의록 확정, 화자 저장은 409("보류 중인 회의록입니다…"), 권한 판정(403) 뒤에 검사. 상세·목록에서 숨기지 않고 상세 응답에 onHold·onHoldBy/At·resumedBy/At 추가. 할 일(모든 묶음)·자동 확정(대상 계산과 갱신 시점 모두)·일일 메일 건수·안내 목록에서 제외(안내 행은 유지, 재개하면 다시 보임), 즉시 메일도 보류면 보내지 않음(방어). 재개: 확정 대기·확정 업무의 기한과 미확정 표시를 모두 비워 보완 필요로(확정 업무도 재개 때만 예외로 비움, 상태는 그대로, 업무마다 `item.updated` via=meeting_resumed), 종결·삭제 업무는 그대로. 확정 대기 회의록의 자동 확정 시각은 보류 기간만큼 뒤로 미룸. pytest `tests/test_meeting_hold.py` 12건 추가(backend 313건). 운영 DB에는 마이그레이션을 적용하지 않음. frontend·v1 변경 없음.
- v2 `backend/` 삭제된 업무의 수정 요청 정리: 대상 업무가 삭제(status=deleted)된 수정 요청을 할 일 `pendingChangeRequests`(항목·total)와 `GET /api/meetings/{id}/change-requests`에서 숨기고, 해결(`.../resolve`)은 404(없는 요청과 같게). 회의록 전체 대상·삭제되지 않은 업무(종결 포함)의 요청은 그대로. 조건은 `meeting_actions.on_deleted_item` 한 곳에 두고 세 곳이 함께 씀. 수정 요청 사건 행은 지우지 않음, 마이그레이션 없음, 응답 형식·권한 변경 없음. pytest 1건 추가(backend 314건). frontend·v1 변경 없음.
- v2 `backend/` 회의록 단계(진행중 active·종료 ended·보류 on_hold·삭제 deleted)와 종료·삭제: `GET /api/meetings?phase=`(기본 active; 담당자가 on_hold·deleted 요청하면 403), 목록 항목·상세에 `phase` 추가, 상세에 `endKind`·`endedBy/At`·`deletedBy/At` 추가. 담당자는 보류·삭제 회의록을 열람 조건(`meeting_visibility`)에서 빼서 상세·하위 자원·할 일 모두 404. 자동 종료: 업무 종결 API에서 삭제되지 않은 업무가 1건 이상이고 모두 종결이면 회의록 종료(구분 auto, 처리자 없음), 업무가 없으면 종료 안 함. 직권 종료 `POST /api/meetings/{id}/end`(확정 대기·확정만, 보류·삭제 409, 이미 종료 200 멱등; 확정 대기·확정 업무를 모두 종결(item.closed via=meeting_ended) 후 종료, 구분 manager). 삭제 `POST /api/meetings/{id}/delete`(처리 중 409, 보류·종료 포함 그 밖 상태 가능, 이미 삭제 200 멱등; 회의록·업무·수정 요청 행과 업무 상태는 그대로). 권한은 회의록 확정과 같은 `can_confirm_meeting`(지시자·총괄). 기록은 새 표 `meeting_closures`(회의록당 1행)와 사건 meeting.ended·meeting.deleted, `Meeting.ended`·`Meeting.deleted`는 계산 값. 마이그레이션 `0f82186010dc`(표 추가만, 기록이 있으면 downgrade 거부). 종료·삭제 회의록의 업무 수정·확정·종결·삭제, 회의록 확정, 화자 저장, 보류·재개는 409(`reject_if_locked`, 보류와 같은 방식). 할 일·자동 확정·일일/즉시 메일은 진행중 회의록만(`active_meeting_condition`), 안내는 보류·삭제 회의록 것을 숨김(종료는 유지). 옛 규칙을 가정한 기존 테스트 2건 수정(`test_meeting_hold.py::test_detail_shows_hold_and_items_kept` — 기본 목록은 진행중·담당자는 보류 상세 404, `test_meeting_queries.py::test_list_fields_and_sort_by_held_at_desc` — 목록 항목에 phase 추가). pytest `tests/test_meeting_lifecycle.py` 15건 추가(backend 329건). 운영 DB에는 이 마이그레이션을 적용하지 않음. frontend·v1 변경 없음.
- v2 `backend/` 처리 사유 필수: 업무 종결·삭제(`POST /api/action-items/{id}/close`·`/delete`), 회의록 보류(`/hold`)·직권 종료(`/end`)·삭제(`/delete`) 요청 본문에 `reason` 필수 (`ReasonBody`: 앞뒤 공백 제거 후 비면 거부, 최대 2000자 — 수정 요청 코멘트와 같은 방식으로 없음·공백·초과는 422). 재개·자동 종료는 사유를 받지 않음(재개는 누가·언제 기존 기록 유지, 자동 종료는 구분 auto). 사유는 새 칸·표 없이 해당 사건(item.closed·item.deleted·meeting.on_hold·meeting.ended·meeting.deleted, 직권 종료로 종결한 업무의 item.closed 포함)의 payload.reason 에 처리자·시각과 함께 보관(마이그레이션 없음, 소급 없음). 조회: 회의록 상세에 `onHoldReason`·`endReason`·`deleteReason`, 최근 사건 `recentEvents[].reason`(사유 있는 사건만, 그 밖 null) 추가, 보류·종료·삭제 API 응답에도 같은 사유 추가(`app/services/reasons.py`). 사유 필수화로 기존 테스트 4개 파일의 해당 호출에 사유를 추가하고, 사건 내용을 정확히 비교하던 3건(`test_close_delete.py` 2곳, `test_meeting_queries.py::test_detail_fields_missing_fields_and_events`)의 기대값에 reason 을 더함. pytest `tests/test_reasons.py` 13건 추가(backend 342건). frontend·v1 변경 없음.
- v2 `frontend/` 회의록 단계 탭과 상태 표시(조회·표시만): 목록 상단 탭 진행중(기본)·종료·보류·삭제(`role=tablist`, 현재 탭 aria-selected·흰 밑줄·굵게), 탭을 바꾸면 `GET /api/meetings?phase=active|ended|on_hold|deleted` 로 다시 조회하고 1쪽으로. 로그인 계정 직급이 담당자면 진행중·종료 탭만(서버도 403), 직급을 모르면 4개 모두 보이고 거부되면 서버 문구·다시 시도. 탭별 빈 목록 문구(진행중은 기존 "등록된 회의록이 없습니다."). 목록 표 맨 끝에 "단계" 열(글자 배지, 종료는 구분까지). 상세: 제목 줄에 단계 배지("종료 · 자동 종료"/"종료 · 관리자 직권 종료" 등), "처리 기록" 영역에 보류(재개됨 표시)·재개·종료·삭제의 처리자·시각·사유(있을 때만, 자동 종료는 처리자 "자동", 읽기 전용). 보류·종료·삭제 회의록의 쓰기 버튼은 그대로 두고 서버 409 문구를 각 버튼이 그대로 표시. `lib/v2/meetings.ts`에 `MeetingPhase`·`EndKind` 타입, 목록·상세 단계·사유 필드(없는 응답이면 진행중), `listMeetings(page, signal, phase="active")`; `meeting-display.ts`에 `PHASE_LABEL`·`END_KIND_LABEL`·`phaseText`. E2E `e2e/v2-meeting-phases.spec.ts` 10건 추가(기존 69건 수정 없음, 전체 79건). 종결·삭제·보류·재개·직권 종료 버튼과 사유 팝업은 다음 단계. backend·v1 변경 없음.
- v2 `frontend/` 회의록·업무 처리 동작과 사유 팝업(회의록 상세): 공용 사유 입력 확인 팝업 `components/v2/ReasonDialog.tsx`(동작 이름·대상·안내 문구, 사유 입력칸과 글자 수 "n / 2000", 빈·공백 사유면 확인 버튼 비활성, 앞뒤 공백 제거 후 전송, 403·404·409·422 는 팝업 안 서버 문구·입력 유지, 성공 시 상세·수정 요청 재조회 후 닫힘, tone=danger로 취소 먼저 포커스·Esc·배경 닫기·연 버튼으로 포커스 복귀). 제목 줄 `components/v2/MeetingActions.tsx`(관리자 이상만 표시): 더보기(⋯) 메뉴 — 진행중(확정 대기·확정) 보류·직권 종료·삭제, 처리 실패·내용 없음 삭제만, 처리 중 없음, 보류 중 "재개" 버튼+삭제, 종료 삭제, 삭제됨 없음; Enter·Space 열기, ↑↓ 이동, Esc(더보기 버튼으로 포커스)·Tab·바깥 클릭 닫기, 항목 선택 시 더보기 버튼으로 포커스를 옮긴 뒤 팝업. 확인 버튼 "보류하기"·"직권 종료하기"(안내 "확정 전 업무도 함께 종결됩니다.")·"삭제하기"·"재개하기"(사유 없음, 안내 "업무 기한이 모두 비워집니다. 날짜를 다시 설정해야 합니다."). 업무 원장 동작 열(관리자 이상만): "종결"(확정된 업무만, "업무 종결하기")·"삭제"(모든 업무, 팝업에 업무명), 종결 업무 상태 라벨 "완료" → "종결". 회의록 삭제 뒤 관리자는 상세에 남아 "삭제" 표시, 업무 삭제 뒤 원장에서 빠짐. `lib/v2/action-items.ts`에 `closeActionItem`·`deleteActionItem`, `lib/v2/meetings.ts`에 `holdMeeting`·`resumeMeeting`·`endMeeting`·`deleteMeeting`·`REASON_MAX`. 권한 판정은 서버. 업무 기한을 다시 넣는 화면은 아직 없음(이번에 만들지 않음). E2E `e2e/v2-meeting-actions.spec.ts` 8건 추가(기존 79건 수정 없음, 전체 87건). backend·v1 변경 없음.

## v1.10.1 — 2026-10-01 (추출 결과 출처 기록, 문서 보완)
- API 응답·계약서(`docs/API-CONTRACT.md`)·프론트 변경 없음. 스텁 내부 DB 기록만 추가.
### 추가
- 스텁 `action_items`에 NULL 허용 열 3개: `extract_model`(모델 이름), `prompt_version`, `extracted_at`(추출 시각, ISO 8601 UTC). 기존 열 보강 방식(`_ADDED_COLUMNS`)으로 추가하므로 예전 DB 파일은 서버 시작 때 열만 생기고, 기존 행과 시드는 NULL(알 수 없음).
- 추출 결과 저장(`Store.complete_if_processing`) 때 위 3개를 같은 트랜잭션에서 기록.
  - Gemini 추출기: 모델 이름은 설정값 `GEMINI_LLM_MODEL`(`make_extractor`가 넘긴 값), `prompt_version`은 추출 프롬프트 고정 템플릿(전사문·회의 일시 제외)의 SHA-256 앞 12자(`app/pipeline/extractor.py` `PROMPT_VERSION`, 자동 계산).
  - 가짜 추출기(`FakeExtractor`)와 가짜 처리기(`run_fake_worker`)는 `extract_model`·`prompt_version`을 `"fake"`로 기록.
  - 출처 속성이 없는 추출기(테스트용 가짜 등)는 NULL.
- `tests/test_extraction_provenance.py` 8건: Gemini 추출(가짜 클라이언트) 시 설정값의 모델 이름·프롬프트 버전·추출 시각 기록, fake 추출기·가짜 처리기는 `"fake"`, 출처 속성 없는 추출기는 NULL, 시드는 NULL, 프롬프트 버전 = 템플릿 SHA-256 앞 12자, API 응답에 새 필드 없음, 구버전(v1.10.0 스키마) 파일의 복사본을 열면 기존 데이터 유지·새 열 NULL·원본 파일 변화 없음.
### 변경
- `app/pipeline/extractor.py`: 프롬프트를 f-string에서 모듈 상수 `PROMPT_TEMPLATE` + `str.format`으로 바꿈(버전 계산용). 만들어지는 프롬프트 글자는 이전과 동일(이전 코드와 출력 비교로 확인).
- `docs/TEST-GUIDE.md`: pytest 기준 건수 125 → 133.
- `backend-contract-stub/.env.example`: `UPLOAD_DIR` 주석을 실제 기본값(`backend-contract-stub/data/uploads`, `app/config.py`)에 맞게 정정. 값은 그대로.
### 확인한 것
- 스텁 `pytest -q` 133건 통과(기존 125 + 신규 8, warning 1건 동일, 실제 Gemini 호출 없음). 프론트 `npx tsc --noEmit` 통과.
- 실제 `data/stub.db`의 복사본으로 열 보강 확인: 표 4개의 기존 행이 모두 그대로(회의 4·액션아이템 11·작업 12·meta 1), `action_items`에 새 열 3개 추가·전부 NULL, 다시 열어도 변화 없음. 실제 파일은 작업 전에 복사해 백업만 했고 바뀌지 않음(SHA-256 동일).
### 필드 테스트 2026-10-01
- 결과: 사용자가 실제 서버에서 시험하고 "이상 없음"으로 보고(사용자 확인. CLI가 직접 실행해 확인한 것은 아님).
- 시험 중 발견: 첫 시도에서 Gemini 접속 오류. 사용자 환경 파일에 `GEMINI_KEY_MODE` 줄이 두 번(paid, free) 있었음. 같은 설정 이름이 중복되면 뒤 값이 적용됨을 확인(저장소 밖 임시 파일 `KEY=1`/`KEY=2`로 python-dotenv `dotenv_values`와 pydantic-settings 모두 `2`, 설정 로더는 pydantic-settings `app/config.py`). 그래서 실제로는 free로 동작했을 가능성이 높고, 무료 키 줄이 주석 처리돼 있던 것도 원인 후보(둘 다 추정, 확인 못 함).
- 조치: 사용자가 중복 줄을 정리해 해결(사용자 보고). 코드 변경 없음.
- 개선 후보(미실행): 서버 시작 시 키 모드 값만 로그, 중복 설정 경고, 키 없음/네트워크 실패를 구분하는 오류 메시지.
### 알려진 제한사항 (v1.10.0 목록에 보충)
- 로컬 Whisper·Ollama 코드 없음: 화면의 Whisper(`faster-whisper`) 선택지는 스텁 응답(가짜 전사)이며 `STT_PROVIDER=gemini` 모드에서는 501로 거절됨. 작업 화면의 GPU 이름 표시는 고정 문자열.
- 서버 전체에 인증·권한 검사가 없음(`/api/admin/*` 포함). 외부 네트워크에 노출 금지.
- 액션아이템 삭제는 이력 없이 행을 삭제함.
- 원본 음성은 처리 완료 후 삭제됨(실패 시에는 Retry를 위해 남김).

## v1.10.0 — 2026-10-01 (v1 마감: 문서화·기준선 확정)
- 코드·설정·API 계약 변경 없음. 문서만 추가·정정하고 현재 상태를 v1 기준선으로 확정.
### 추가
- `docs/TEST-GUIDE.md`: pytest(125건)·프론트 `tsc`·Playwright E2E(5건, 목 모드 3100 포트 전용) 실행 방법, 스텁 서버 실행 순서, `GEMINI_KEY_MODE`(평소 `free`, `paid`는 사용자 승인 시에만), 테스트 데이터 정책(자동 초기화 금지, 초기화는 사용자 지시 시에만), "A파일 정답표"는 저장소 밖에 보관(이 저장소에 없음).
### 변경
- 루트 `README.md` "다음 단계" 5번: "프론트에는 자동 테스트가 없습니다" → Playwright E2E 5건(목 모드 한정)이 있다는 현재 사실로 정정.
### 확인한 것
- 스텁 `pytest -q` 125건 통과(warning 1건), 프론트 `npx tsc --noEmit` 통과, `npm run build` 성공, Playwright E2E 5건 통과(목 모드, 실행 전후 `data/stub.db` 변화 없음).
### 알려진 제한사항 (v2 첫 과제로 이관)
- 회의 실제 날짜: 재추출 때 넣은 회의 일시(2026-08-12, 수요일)와 원문의 요일(월요일)이 맞지 않음. 실제 날짜 미결(이 날짜 기준으로 계산한 마감일도 미확정).
- 긴 발화(약 700자)를 근거로 인용할 때 발화 전체를 그대로 인용하는 처리 방식 미결.
- 무음 파일은 실패가 아니라 완료(`completed`)·액션아이템 0건으로 끝남. 실패로 볼지 미결(`docs/API-CONTRACT.md` 7)절은 전사가 비었거나 `[NO_SPEECH]`이면 `failed`로 적고 있어 함께 정리 필요).
- T10(누락·오추출 15% 이하) 미달: v1.9.8 시점 22.m4a 전사 기준 정답 후보 11건 중 8건 일치, 오탐 0건, 누락 3건(진도율 표시, 다음 주 고객성공 계획, LNC&P 16일 재전달).
- Playwright E2E는 목 모드 한정. 실제 스텁 서버와 연결한 자동 화면 검증은 없음.
- pytest warning 1건: `StarletteDeprecationWarning`(starlette.testclient의 `httpx` 사용), 기능 영향 없음.
- 운영 백엔드(`backend/app`)는 아직 없고 FastAPI 스텁(`backend-contract-stub/`)만 있음.

## v1.9.10 이후 문서 정정 — 2026-09-30
- 문서 표기만 현재 기준으로 정정(코드·설정·계약 내용 변경 없음, 태그 v1.9.10은 그대로): 루트 `README.md` 제목 v1.7 → v1.9.10·스텁 pytest 88건 → 125건(3곳), `CLAUDE.md` "현재 v1.7" → v1.9.10, `docs/API-CONTRACT.md` 제목 v1.7 → v1.9.10.

## v1.9.10 — 2026-09-30 (스텁 등록 데이터 파일 저장, 재시작 복구)
### 변경
- 스텁 저장소를 메모리 SQLite → 파일 SQLite로. 설정 `stub_db_path`(환경변수 `STUB_DB_PATH`), 기본 `backend-contract-stub/data/stub.db`. 폴더가 없으면 만든다. API 계약·응답 형태는 그대로.
  - 서버 시작 시 표는 `CREATE TABLE IF NOT EXISTS`, 나중에 추가된 열(`meetings.transcript`·`speaker_names`, `jobs.meeting_id`·`engine`)은 없으면 `ALTER TABLE ADD COLUMN`으로 보강(기존 행 유지).
  - 시드는 DB가 비어 있을 때 한 번만(`meta.seeded` 표시). 재시작 때 다시 넣거나 덮어쓰지 않고, 이미 데이터가 있던 파일에는 넣지 않는다.
  - 자동 초기화 없음: `Store.reset()`은 사용자 실행 스크립트와 테스트(임시 DB)에서만 호출.
- 업로드 음성 기본 폴더 `UPLOAD_DIR`: 시스템 임시 폴더 → `backend-contract-stub/data/uploads` (OS가 임시 폴더를 비우면 재시작 뒤 Retry가 깨지던 문제).
- `.gitignore`: `backend-contract-stub/data/`, `*.db-journal`·`*.db-wal`·`*.db-shm` 추가(`*.db`는 기존).
- 서버 시작 복구(`app/main.py` lifespan → `app/pipeline/service.py` `recover_interrupted_jobs()` → `Store.fail_interrupted_uploads()`): 업로드로 만든 작업(`meeting_id` 있음) 중 `processing`/`queued`로 남은 것만 `failed`로 바꾸고 사유 "서버 재시작으로 중단됨"을 `errorLog`에 기록. 시드 작업(`meeting_id` 없음)과 완료·실패 작업은 그대로. 음성 파일은 지우지 않으므로 Retry 가능(파일이 없으면 기존 규칙대로 409). 로그에는 복구한 작업 ID와 건수만. Store를 여는 것만으로는 실행되지 않음(스크립트가 다른 프로세스의 작업을 건드리지 않게).
- `README.md`(스텁), `CLAUDE.md`: 저장 방식(파일 SQLite)·초기화·재시작 복구 안내, 스텁 pytest 기준 건수 88 → 125.
### 추가
- `scripts/reset_db.py`: 사용자가 직접 실행하는 초기화. 인자 없이 실행하면 경로·건수만 보여 주고 아무것도 바꾸지 않음, `--yes`일 때만 DB를 시드로 되돌리고 업로드 폴더의 음성·임시 파일(`.mp3/.m4a/.wav`, `.incoming-*`)만 삭제(작업 번호가 다시 시작돼 예전 음성과 겹치는 것 방지). 전사 원문·키는 출력하지 않음.
- `tests/conftest.py`: app import 전에 `STUB_DB_PATH`를 테스트 전용 임시 파일로 바꾸고, 저장소가 다른 파일을 가리키면 테스트 수집 단계에서 중단. 끝나면 임시 폴더 삭제.
- `tests/test_restart_recovery.py` 5건: 재시작 시 업로드 작업만 failed(대기·처리 중 모두), 시드·완료 작업은 그대로, 두 번 시작해도 다시 바꾸지 않음, 복구 작업 Retry(음성 있음 → 다시 처리해 completed / 없음 → 409, 상태는 failed 유지), 테스트는 임시 DB 사용.
- `tests/test_persistence.py` 9건: 재시작 후 데이터·화자 이름·삭제 유지, 시드 1회, 작업 번호 이어짐, 예전 스키마 열 보강(데이터 유지), 초기화는 `--yes`만(다른 파일은 유지), pytest가 실제 DB를 쓰지 않음, `--reload`가 data 파일을 감시하지 않음.
### 확인한 것
- 스텁 `pytest -q` 125건 통과(기존 111 + 신규 14). 실행 전후 `backend-contract-stub/data/`가 생기지 않음(실제 DB 경로를 열지 않음).
- `git check-ignore`로 `data/` 아래 DB·저널·업로드 파일이 모두 무시되는 것 확인.
- 실행 중인 스텁 서버는 `--reload` 없이 떠 있어 이번 파일 변경으로 재시작되지 않았고, 보유 데이터는 기본 시드뿐이었음(읽기 조회로 확인). uvicorn `--reload` 기본 감시 대상은 `*.py`뿐(설치된 uvicorn 0.54.0 소스 확인).
### 확인하지 못한 것
- 실제 서버를 새 코드로 다시 켠 뒤의 동작(사용자가 재시작할 때 `data/stub.db`가 새로 만들어지고 시드 1건으로 시작).
- 실제 서버 재시작 시 복구 로그 출력(테스트에서는 lifespan 실행으로 확인).

## v1.9.9 — 2026-09-30 (화자 이름 지정, 전사 원문 다운로드 두 가지·창 크기 조절, E2E)
### 추가
- 계약서(`docs/API-CONTRACT.md` "화자 이름"): `Meeting.speakerNames`(객체, 없으면 `{}`) 필드 추가, `PUT /meetings/{meetingId}/speakers` 추가. 기존 필드는 그대로(하위 호환).
  - 요청 `{"speakers": {"화자1": "권영우 부장"}}`, 전체 교체. 키 `^화자\d+$`만, 값은 앞뒤 공백 제거 후 1~30자(빈 값이면 그 키 삭제). 위반 400(아무것도 저장 안 함), 없는 회의 404(검증보다 먼저). 성공 200 `{"speakerNames": {...}}`(실제 저장된 값).
- 스텁(`backend-contract-stub`)
  - `meetings.speaker_names` 열(기본 `'{}'`), `Store.set_speaker_names()`. 상세 조회에 `speakerNames` 포함, 시드 회의는 `{}`.
  - `PUT /api/meetings/{id}/speakers`: 본문을 직접 읽어 형식 오류도 422가 아니라 400으로 응답. `\d`는 ASCII 숫자만(프런트 정규식과 같게).
  - CORS `allow_methods`에 `PUT` 추가(실서버 모드에서 브라우저가 PUT을 보낼 수 있게).
  - `tests/test_speaker_names.py` 19건: 저장·조회, 공백 제거, 전체 교체·빈 값 삭제, 잘못된 키 400(일부 저장 안 됨), 30자 초과 400·30자 허용, 본문 형식 오류 400, 없는 회의 404, 다른 회의·원본(assignee·전사 원문) 영향 없음.
- 프런트(`frontend`)
  - `lib/speaker-names.ts`(순수 함수, import 없음): `applySpeakerNames`(단일 패스 치환, 매핑에 없는 화자N·`""`는 그대로, "화자12"를 "화자1"로 자르지 않음), `findSpeakerLabels`, `normalizeSpeakerNames`(서버와 같은 검증), `duplicateSpeakerNames`.
  - `lib/speaker-names.test.mjs` 9건(이름 적용 변환이 줄바꿈 등 다른 글자를 바꾸지 않음, 다운로드 파일 이름, 근거 인용 화자 표시 포함): 새 패키지 없이 Node 내장 test runner로 실행(`node --test lib/speaker-names.test.mjs`, Node 22.18+/23.6+의 TS 타입 제거 실행 사용).
  - `types.ts` `Meeting.speakerNames`, `api-http.ts`/`api-mock.ts`/`api.ts`에 `saveSpeakerNames(meetingId, speakers, signal?)`. http는 400/422 → `invalid_input`, 옛 서버 응답에 `speakerNames`가 없으면 `{}`로 채움. mock은 같은 검증 규칙으로 브라우저 메모리에 저장.
  - `components/meeting/SpeakerNamesPanel.tsx`: 전사 원문·담당자에서 찾은 화자N(+ 이미 저장된 키) 목록, 화자별 입력칸(최대 30자), "화자 이름 저장" 버튼(Enter로도 저장), 저장 중/저장했습니다/오류를 `StatusDot` 라벨로 표시(`role="status"`), 같은 이름 중복 시 경고만 표시. 화자N이 없으면 안내 문구만.
  - `MeetingDetail.tsx`: 저장 성공 시 표의 담당자·근거 인용(Evidence quote) 화자·전사 원문 모달에 바로 반영. 원본 `items`(`quote.speaker` 포함)·`transcriptText`와 API 응답은 그대로 두고 표시용 복사본에만 적용.
  - `TranscriptViewer.tsx`: 모달 표시에 매핑 적용. 다운로드는 원본/이름 적용 두 가지, 두 버튼은 전사 원문 영역(모달) 상단. 기존 "전사 원문 보기" 옆 다운로드 버튼은 제거(중복 방지). 원문이 없거나 비면 버튼 없음.
    - "원본 다운로드": `{제목}_전사원문.txt`, 화자N 원본 그대로(기존 다운로드와 같은 내용), BOM 유지.
    - "이름 적용 다운로드": `{제목}_전사원문_이름적용.txt`, 다운로드 직전에 `applySpeakerNames`만 적용(줄 나누기 등 다른 가공 없음), BOM 유지. 저장된 화자 이름이 없으면 비활성화하고 "저장된 화자 이름이 없어 원본과 같습니다" 표시.
    - 원문 창 네 모서리/네 변 마우스 크기 조절, 초기 크기는 기존과 동일: 창의 네 변(상·하·좌·우)과 네 모서리를 끌어 가로·세로 크기 조절. 마우스를 올리면 방향별 커서(ns/ew/nwse/nesw-resize). 끄는 변만 움직이고 반대쪽 변은 고정(가운데 정렬 창을 translate로 옮겨 맞춤). 원문 상자가 함께 늘고 줄며 넘치면 상자 안에서 스크롤. 본문 최소 288×256px, 창은 화면 여백(16px) 밖으로 나가지 않음. 어떤 크기에서도 원문 상자만 줄어들어 위쪽 다운로드 버튼과 "닫기" 버튼은 잘리지 않음. 열 때마다 기본 크기·가운데 위치(폭 = 기존 창 28rem, 원문 상자 기본 60vh·최소 12rem·최대 화면 높이 - 18rem)로 시작. 끌기는 pointer events + `setPointerCapture`로 창·브라우저 밖으로 나가도 이어지고, 끄는 동안 글자 선택 없음, 바깥에서 놓아도 창이 닫히지 않음. 크기 조절 후 브라우저 창이 작아지면 화면 안으로 다시 맞춤. 손잡이는 마우스·터치 전용(키보드는 기본 크기 + 상자 스크롤). 줄바꿈 표시는 기존 그대로.
    - `lib/resize-rect.ts`(순수 함수): `resizeRect`(반대쪽 변 고정·최소/화면 한도), `fitRect`(화면 안으로 맞춤), `offsetFromCenter`. `lib/resize-rect.test.mjs` 8건.
    - 공용 `components/mono/Modal.tsx`: 선택 속성 `panelClassName`(기본값 `"w-full max-w-md"` = 기존과 동일), `panelStyle`(기본 없음) 추가. 창 폭 제한과 가운데 위치가 Modal 안에 고정돼 있어 TranscriptViewer만으로는 창을 넓히거나 반대쪽 변을 고정한 채 옮길 수 없어서 추가. 두 속성을 넘기는 곳은 TranscriptViewer뿐이며 다른 모달(삭제·Kill·GPU 가드)은 영향 없음.
    - 공용 `Modal.tsx` 버그 수정: 바깥(배경) 클릭으로 닫으면 포커스가 여는 버튼으로 돌아오지 않고 body로 가던 문제. 배경 mousedown의 기본 동작을 막아 Esc로 닫을 때와 같게 복귀(모든 모달에 적용, E2E로 발견).
    - 크기 조절 손잡이에 `data-edge`(top/bottom/left/right/topleft 등) 속성: E2E 테스트가 손잡이를 찾는 용도(표시 변화 없음).
    - 창을 열면 처음 포커스가 "원본 다운로드"가 아니라 "닫기" 버튼으로 감(TranscriptViewer에서 열린 직후 포커스 이동, 공용 `Modal` 미수정). 닫으면 포커스는 "전사 원문 보기"로 돌아감.
  - `lib/download-text.ts`: `transcriptFileName(title, meetingId, "original" | "named")` 추가. `downloadTextFile`은 그대로.
  - `lib/api-mock.ts`: 목 업로드 제목 태그 `#speakers` 시나리오 추가(전사 원문·담당자·근거 인용 화자가 실명 대신 화자1/화자2/화자3). 목 모드에서 화자 이름 기능을 확인·자동 테스트하기 위함.
  - Playwright E2E(`@playwright/test` 1.63.0 devDependency, 브라우저는 설치된 Chrome 사용·별도 다운로드 없음): `playwright.config.ts`, `e2e/transcript-viewer.spec.ts` 5건, `npm run test:e2e`. 목 모드 전용이며 실서버·백엔드 데이터는 쓰지 않음(목 업로드는 테스트 브라우저 메모리에만). 검증: 창 맨 위 다운로드 버튼 2개, 원본(화자N)·이름 적용본(지정 이름) 실제 다운로드 내용·파일명·BOM·줄 수, 근거 인용 화자 이름 표시, 네 변·네 모서리 드래그 시 반대쪽 변 고정, 최소 크기·화면 여백 제한과 버튼 잘림 없음, 처음 크기 448px, Esc·배경 클릭 닫기와 포커스 복귀, 끌기를 바깥에서 놓아도 닫히지 않음. 실행 중인 dev 서버(3000)와 `.next`를 함께 쓰지 않도록 `E2E_BASE_URL`로 별도 서버를 지정할 수 있음(없으면 목 모드 dev 서버를 3100에 직접 띄움).
  - `.gitignore`: Playwright 결과 폴더(`test-results/`, `playwright-report/`).
### 확인한 것
- 스텁 `pytest -q` 111건 통과(기존 92 + 신규 19, 실제 Gemini 호출 없음). v1.9.10 이후 기준 125건 회귀 통과.
- 프런트 `npx tsc --noEmit` 통과(E2E 파일 포함).
- 프런트 `node --test lib/speaker-names.test.mjs lib/resize-rect.test.mjs` 17건 통과(화자 이름 9 + 창 크기 계산 8).
- Playwright E2E 5건 통과(목 모드 빌드를 3100에서 실행해 확인). 첫 실행에서 배경 클릭 후 포커스 복귀 실패 → `Modal.tsx` 수정 후 5건 통과.
- `npm run build` 첫 실행 성공(Next.js 15.5.26, 타입 검사 포함, 6개 경로). 실행 중인 dev 서버의 `.next`를 덮어쓰지 않도록 `.env*`·`node_modules`·`.next`를 뺀 임시 복사본에서 목 모드로 실행.
### 확인하지 못한 것
- 사용자 통합 테스트(실서버 모드 포함 브라우저 확인)는 예정.
- lint 스크립트는 프런트에 없음.
- Slack/Notion 반영 미구현.
- 재추출 시 매핑 동작 미검증(재추출하면 화자 번호가 바뀔 수 있음).
- 사용자가 삭제·수정한 항목과 매핑의 관계 규칙 미설계.
- 알려진 제한: 담당자 정렬은 서버의 원본 값 기준. 목 모드의 시드 회의는 상세 화면을 서버에서 그리므로 새로고침하면 브라우저 메모리의 매핑이 보이지 않음.

## v1.9.8 — 2026-09-30 (초안: LLM 추출 프롬프트 누락 줄이기)
### 변경
- `backend-contract-stub/app/pipeline/extractor.py` `build_prompt()`에 누락을 줄이는 규칙 추가. 기존 포함·제외 정책, 담당자·작업·마감일·인용 규칙, 스키마(`ActionItemList`)·API 응답 형태는 그대로, 미정 값은 계속 `""`.
  - [1] 지시-수락: 제안·권유형 요청("~하시는 게 좋을 것 같아요", "~볼 수 있으면 좋겠네요", "~해 주세요")도 지시로 보고, "네", "알겠습니다"로 받으면 포함.
  - [1] 확정 약속: 시키지 않은 자발적 약속도 할 일 + 기한(또는 대상)이 있으면 포함("오늘 마무리될 것 같습니다", "이번 주 중에 하겠습니다", "~까지 등록해야 됩니다").
  - [1] 한 발화에 약속·업무가 여러 개면 각각 별도 항목. 업체·프로젝트·고객명이 다르면 다른 항목.
  - [1] 제외 예시에 "정리 완료했고요" 추가(끝난 일 보고).
  - [6] 마지막 점검(새 절): 목록을 만든 뒤 전사문을 다시 훑어 "~하겠습니다 / ~할 예정 / ~까지 / 알겠습니다 / 네, 그렇게"가 붙은 문장에서 빠진 것만 추가, 겹치면 추가 안 함, 추가 항목도 기존 규칙 적용. 기존 [6. 빈 결과]는 [7]로.
- `tests/test_extractor_prompt.py`: 프롬프트 문구 테스트 2건 추가.
### 확인한 것
- `pytest -q` 92건 통과(기존 90 + 2, 실제 Gemini 호출 없음).
- A.mp3 스모크(실제 Gemini, 키 모드 paid, 회의일시 2026-09-29T14:00:00+09:00): 2건 — 서연 2026-10-09, 민수 2026-10-01, 점심 메뉴 제외. 이전과 같음(STT 17.8초, LLM 6.0초). free 모드로는 2회 모두 STT 단계 503 실패.
- 33분 회의 재추출(`--transcript-file`, 키 모드 paid, 회의일시 2026-08-12T10:00:00+09:00, 사용자가 실행 명령 지정): 11건, LLM 68.4초(v1.9.5는 9건, 62.8초).
  - 정답 후보 G1~G11 중 8/11 일치(G1·G2·G5·G6·G7·G9·G10·G11 일치, 놓친 것 G3·G4·G8, 정답 후보 외 타당한 추가 3건, 잘못 뽑힌 항목 0건). v1.9.5에서 빠졌던 농심 템플릿 등록(G7)과 지오컴즈 계획서(G11)가 새로 잡힘.
  - 타당한 추가 3건: DBWD 일정 회신, 커널 서버 분리 확인, 트라이코디 로직 완료.
  - [02:49] 긴 발화 하나에서 3건(농심, DBWD, 커널), [28:46] 발화에서 2건(이매직스, 트라이코디)을 따로 뽑음.
### 확인하지 못한 것
- 33분 회의에서 아직 빠진 것 3건: 진도율 표시 관리 [22:15](G3), 다음 주 고객성공 계획 보고 [25:25](G4), LNC&P 템플릿 16일까지 재전달 [02:49](G8).
- G3·G4는 v1.9.5에 이어 두 번 연속 누락.
- #11(편집기 비교 자료 발표) 마감일 2026-09-02는 임시 회의 일시(2026-08-12) 기준으로 계산한 값이라 실제 날짜 미확정.
- #11 작업 문장에 인용 발화에 없는 "연구소와 조율"이 포함됨.
- 인용이 발화 전체(긴 인용)인 점, 담당자가 전부 화자N(호칭 대응 없음)인 점은 미결정(인용 규칙은 이번 범위 밖, 별도 결정 사항).
- 농심 템플릿은 "화요일 오후까지"라고 했지만 마감일이 `""`로 나옴.

## v1.9.7 — 2026-09-30 (회의 상세: 회의 목록으로 돌아가기 링크)
### 추가
- 회의 상세 화면(`/meetings/[id]`) 맨 위에 "← 회의 목록으로" 링크(`href="/"`). `components/meeting/MeetingDetail.tsx`에 추가.
  - 음성 등록 화면(`components/upload/UploadForm.tsx`)의 같은 링크와 문구·마크업(`<nav>` + `next/link`)·클래스(`mn-focus rounded-mn-control text-sm text-mn-muted hover:text-mn-text`)를 그대로 맞춤. `UploadForm.tsx`는 수정 안 함.
  - 실서버 모드(`page.tsx`)와 목 모드(`MockMeetingLoader`) 모두 `MeetingDetail`을 렌더링하므로 두 모드에서 같은 링크가 보임. 서버 호출 추가 없음.
### 확인한 것
- `npx tsc --noEmit` 통과.
- 목 모드 화면 동작(링크 표시, `/`로 이동) (사용자 확인).
### 확인하지 못한 것
- 실서버 모드 화면 동작.
- `npm run build` (dev 서버 보호를 위해 미실행).

## v1.9.6 — 2026-09-30 (회의 상세: 전사 원문 다운로드)
### 추가
- 회의 상세 화면 "전사 원문 보기" 옆에 "전사 원문 다운로드" 버튼(`components/meeting/TranscriptViewer.tsx`, 기존 `Button` secondary/sm 재사용, `aria-label="전사 원문 다운로드 (.txt 파일)"`).
  - 전사 원문(`transcriptText`)이 `null`이거나 빈 문자열(공백만 포함)이면 버튼을 숨김 → 시드 회의·처리 전 회의에는 안 보임.
  - 화면에 이미 받아 온 원문을 가공 없이 저장(줄바꿈 추가·변환 없음). 서버 엔드포인트 추가 없음 → 목/실서버 모드 동일.
  - UTF-8 + 맨 앞 BOM(`U+FEFF`), 파일명 `{회의 제목}_전사원문.txt`(`\ / : * ? " < > |` 제거, 제목이 비면 회의 ID).
- `lib/download-text.ts`: `toSafeFileBaseName()`, `downloadTextFile()` (Blob + 임시 링크, 끝나면 `URL.revokeObjectURL`).
- `MeetingDetail.tsx`가 `TranscriptViewer`에 회의 제목·ID를 넘김.
### 확인한 것
- `npx tsc --noEmit` 통과.
- 실서버에서 다운로드 실행 및 저장.
- 저장한 파일을 윈도우 메모장으로 열었을 때 한글 정상 (사용자 확인).
### 확인하지 못한 것
- 시드 회의에서 버튼 숨김.
- 목 모드 동작.
- `npm run build` (dev 서버 보호를 위해 미실행).

## v1.9.5 — 2026-09-30 (초안: LLM 추출 프롬프트 보강)
### 변경
- `backend-contract-stub/app/pipeline/extractor.py` `build_prompt()` 규칙을 6개 절로 다시 씀. 스키마(`ActionItemList`)·후처리·API 응답 형태는 그대로, 미정 값은 계속 `""`.
  - 포함 기준: 지시-수락 + 구체적인 기한 또는 대상이 있는 **확정 약속**("~하겠습니다", "~할 예정입니다", "~까지 하겠습니다", "오늘 다시 연락해 보겠습니다"). 전사문 전체를 처음부터 끝까지 빠짐없이 훑도록 지시.
  - 제외 기준: 이미 끝난 일의 보고, 기한·대상 없는 막연한 의견, 잡담, 일반론 강의.
  - 담당자: 먼저 "화자 표기 → 이름/직함" 대응을 정하고(인정 근거 3가지, 불분명하면 대응 안 함), 호칭 우선("권 부장", 존칭 제외) → 모르면 화자 표기 그대로(지어내기 금지) → 누가 맡는지 불분명하면 `""`. 같은 화자는 항상 같은 표기.
  - 작업: 업체·고객명은 원문 표기 그대로(비슷한 이름 합치기 금지).
  - 마감일: "오늘"(=회의일)·이번 주/다음 주 요일 계산, 날짜 하나로 정할 근거가 없으면(요일 없는 "다음 주" 포함) `""`.
  - 근거 인용: 지시-수락은 지시 발화, 확정 약속은 약속 발화를 원문 그대로. speaker·timestamp는 전사 표기 그대로.
### 추가
- `scripts/smoke_gemini.py --transcript-file <전사 텍스트> [회의일시]`: STT 없이 추출(LLM)만 실행. 기존 음성 파일 사용법은 그대로.
- `tests/test_extractor_prompt.py`(2건): 프롬프트에 정책 문구가 들어 있는지 문자열 검사(LLM 호출 없음). pytest 90건 통과.
### 확인한 것
- pytest 90건 통과(기존 88건 + 신규 2건).
- A.mp3 스모크(실제 Gemini, STT+추출): 2건 — 서연 2026-10-09, 민수 2026-10-01, 점심 메뉴 제외. 이전과 같음.
- `--transcript-file` 옵션 실제 실행: STT 없이 추출만 동작함(paid 모드, 사용자 승인 후).
- 33분 회의 전사 재추출(회의일시 2026-08-12T10:00+09:00): 9건, LLM 62.8초. 확정 약속("오늘 다시 연락해 볼 생각", "오늘 다시 통화할 예정")과 "오늘" 마감일 계산이 잡힘. 담당자는 모두 화자 표기(호칭 대응 없음), 누가 맡는지 불분명한 1건은 `""`. 누락 의심 2건(화자3 "화요일 오후까지 농심 템플릿 두 종류 등록", 화자8 "오늘 사업 수행 계획서 마무리·전달"), 한 발화에서 나온 2건이 같은 긴 발화 전체를 근거로 인용함.
### 확인하지 못한 것
- 확정 약속·호칭 대응 규칙이 실제 회의에서 의도대로 동작하는지.
### 알려진 문제
- 33분 회의 재추출(9건)에서 아직 빠진 것으로 보이는 약속 5건: 진도율 표시 [22:15], 고객성공 계획 [25:25], 농심 템플릿 등록 [02:49], LNC&P 16일 재전달 [02:49], 지오컴즈 계획서 [30:33]. 따라서 누락·오추출 15% 이하 기준은 미달.
- 호칭 대응은 0건 (원문에 직접 근거가 부족해 규칙대로 화자 번호 유지).
- 인용이 700자 전체 발화로 길어지는 경우가 있음.
- 입력 회의 일시 2026-08-12는 수요일인데 원문은 "월요일"이라 실제 회의 날짜 확인 필요.

## v1.9.4 — 2026-09-30 (회의 목록 2단계: 프론트엔드)
### 추가
- `lib/types.ts` `MeetingSummary { id, title, startedAt, jobStatus }` (계약서 "회의 목록" 기준).
- `fetchMeetings(signal?)`: `api-http.ts`(`GET /meetings`, 기존 `request()` 오류 처리 그대로), `api-mock.ts`(시드 회의 + 목 모드 업로드 회의를 합쳐 startedAt 시각 내림차순, 같으면 id 내림차순), `api.ts` 내보내기.
- `components/meeting/MeetingList.tsx`(클라이언트 컴포넌트): 제목(`/meetings/{id}` 링크) / 일시(Mono, KST) / 상태(StatusDot + 한국어 라벨 대기·처리 중·완료·실패, `null`은 "처리 기록 없음"). 행 높이 48px, 서버 순서 그대로. 불러오는 중 / 오류(한국어 문구 + "다시 시도") / 빈 목록 안내. 언마운트 시 AbortController로 요청 취소.
### 변경
- `/`(`app/page.tsx`)가 기본 회의 상세 대신 회의 목록을 보여 줌. 목록 머리글에 "음성 등록" 링크.
- 음성 등록 화면의 "← 회의 목록으로"(`/`)는 이제 실제 목록으로 가므로 코드 변경 없음.
- `NEXT_PUBLIC_DEFAULT_MEETING_ID`는 코드에서 더 이상 쓰지 않음. `.env.local.example`과 README 표에는 남아 있음(정리는 별도 작업).
### 확인한 것 (실서버 + Gemini 유료 키, 브라우저 화면)
- `npx tsc --noEmit` 통과.
- `/`가 회의 목록으로 표시됨(제목 링크·일시·상태), 시드 회의는 "처리 기록 없음".
- 음성 등록으로 올린 회의가 목록에 나타나고 상세 화면(액션아이템 2건, 마감일, 근거 인용)까지 이동됨.
- 무료 키 한도 초과(429) 시 실패 화면: 실패 이유 문구와 "다시 업로드" 버튼이 실제로 표시됨 (v1.9.2에서 못 본 항목).
### 확인하지 못한 것
- 목 모드 화면(`fetchMeetings` mock 경로).
- 목록 오류 화면과 "다시 시도" 버튼, 빈 목록 안내 화면.
- `npm run build` (dev 서버 보호를 위해 미실행).
### 알려진 한계
- 정렬 옵션·검색·페이지 나눔 없음(계약에 없음).
- 목 모드에서 업로드한 회의는 브라우저 메모리에만 있어 새로고침하면 목록에서 사라짐.
- 목록은 한 번만 조회함(처리 중 상태가 자동으로 바뀌지 않음, 새로고침 필요).

## v1.9.3 — 2026-09-30 (회의 목록 1단계: 계약서 + 스텁)
### 추가
- `GET /meetings` (`fetchMeetings`) → 200 `MeetingSummary[]`. 같은 경로의 `POST /meetings`(업로드)는 그대로 202.
- 타입 `MeetingSummary { id, title, startedAt, jobStatus }`. `jobStatus`는 그 회의의 **가장 최근 작업** 상태(`queued | processing | completed | failed`), 업로드 작업이 없는 시드 회의는 `null`.
- 순서: `startedAt` 내림차순, 같으면 `id` 내림차순 고정. `startedAt`은 문자열이 아니라 시각으로 비교하고, 시간대 표기가 없으면 KST로 본다.
- 스텁: `app/db.py` `list_meetings()`, `app/schemas/models.py` `MeetingSummary`(camelCase alias), `app/routers/api.py` `GET /api/meetings`.
- `docs/API-CONTRACT.md`에 엔드포인트·타입·"회의 목록" 절 추가, "6) 일부러 정하지 않은 것"에 정렬 옵션·페이지 나눔·검색 명시.
- 테스트 `tests/test_meetings_list.py`(6건). pytest 88건 통과.
### 알려진 한계
- 프론트의 `/` 화면은 아직 목록을 부르지 않음(다음 단계).
- 정렬 옵션·페이지 나눔·검색 없음.
- `startedAt`은 사용자가 입력한 회의 일시라서, 과거 일시로 올린 회의는 방금 올렸어도 목록 아래쪽에 표시됨.

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
