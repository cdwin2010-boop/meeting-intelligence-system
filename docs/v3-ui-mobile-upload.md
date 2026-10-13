# 모바일 올리기 정책·변환 대기 안내 (작업 88-2)

대상: `frontend/` v2 화면. 백엔드 API는 작업 88-1(`POST /api/meetings/{id}/process-now`, 계약 `docs/contracts/process-now-v1.json`)을 그대로 쓴다.

## 적용 범위
- Wi-Fi 안내와 등록 보류는 **모바일에서만** 켠다: 화면 너비 768px 미만 또는 터치 기기(`(max-width: 767px), (pointer: coarse)`, 86-1a의 녹음하기와 같은 판정). PC 동작은 바뀌지 않는다.
- 안내 대상 자료 종류는 **음성 파일(audio)** 이다(통신 요금이 문제 되는 큰 파일). 자료 파일(txt·엑셀)은 안내 없이 바로 올린다.

## Wi-Fi 판정 규칙 (`lib/v2/network.ts`, 한 곳)
| `navigator.connection.type` | 동작 |
|---|---|
| `wifi`, `ethernet` (대소문자·공백 무시) | 팝업 없이 올린다 |
| `cellular` | 안내 팝업 |
| 미지원·`undefined`·`unknown`·`none`·`other`·읽기 오류 | 안내 팝업(요금 방지 우선, 항상) |

- 팝업 문구(고정): "Wi-Fi 환경에서 등록하셔야 통신 요금이 발생하지 않습니다. 지금 바로 업로드하시겠습니까?"
- 버튼: **[등록 보류]**(처음 포커스, 올리지 않고 기기 보류 목록에 저장) / **[업로드 계속]**(즉시 업로드). Esc·배경 클릭은 팝업만 닫는다(올리지도 보류하지도 않음, 입력 유지). 기존 `Modal` 규칙(포커스 트랩·Esc·열었던 버튼으로 포커스 복귀).
- 판정 함수는 가짜 값을 주입할 수 있다(`shouldShowWifiNotice(source)`, 단위 테스트 `lib/network.test.mjs`, E2E는 `addInitScript` 로 `navigator.connection` 주입).

## 등록 보류 목록 (`lib/v2/pending-uploads.ts`)
- 저장소: IndexedDB `mi-v2-pending-uploads` / 저장소 `uploads`(파일 Blob + 메타: 파일명·크기·자료 종류·회의 유형·제목·회의 일시·참석자·프로젝트·보류 시각). localStorage에는 파일을 넣지 않는다. 계정별로 나눠 보인다(같은 기기 공용 대비).
- 모든 접근은 try/catch. 저장소를 못 쓰면 [등록 보류]를 막고 "이 기기에서는 보류할 수 없습니다. Wi-Fi에서 다시 시도해 주세요." 를 보인다.
- 한도(상수 한 곳 `PENDING_LIMITS`): **최대 5건, 합계 300MB**. 환경변수 `NEXT_PUBLIC_PENDING_UPLOAD_MAX_COUNT`·`NEXT_PUBLIC_PENDING_UPLOAD_MAX_MB` 로 바꿀 수 있다. 넘으면 저장하지 않고 어떤 한도인지(건수/용량, 현재 값) 안내. 계산은 순수 함수 `lib/v2/pending-limits.ts`(단위 테스트 `lib/pending-limits.test.mjs`).
- 화면: 올리기 화면 아래 "등록 보류 목록 (N건)"(1건 이상일 때, 모바일만). 항목: 파일명·크기·보류 시각, [지금 올리기]·[삭제].
  - [지금 올리기]: 같은 업로드 흐름(Wi-Fi 판정 포함). 성공하면 목록에서 제거, 실패하면 목록에 남기고 항목 아래에 사유. **자동 재업로드는 없다.**
  - [삭제]: "보류 파일 삭제" 확인 팝업("기기에 임시 저장된 파일만 지워집니다", 취소에 먼저 포커스).

## 업로드 성공 안내·상태 표시
- 202 직후 회의록 상세로 이동하면서 한 번만 보이는 안내(고정): "녹취 파일이 정상적으로 등록되었습니다. 변환 작업 대기열에 등록되었으며, 순차적으로 변환이 진행됩니다." (`lib/v2/upload-notice.ts`, 세션 저장소 1회용)
- 작업 상태는 기존 `queued`(대기 중)·`running`(처리 중)·`completed`(처리 완료)·`failed`(처리 실패) 4단계를 `JobStatusChip`(87-1 `StatusChip` 사용) 한 곳으로 통일한다: 회의록 상세 처리 영역과 왼쪽 메뉴 처리 현황. `no_content` 는 "내용 없음".
- 대기 중이면 "앞에 N건"(`queueAhead`), 즉시 변환으로 올려졌으면 "우선 처리"(`prioritized`), `workerAlive=false` 이면 "처리기 응답이 없어 대기 중입니다. 관리자에게 문의해 주세요."(대기 상태에서만).

## [즉시 변환]
- 서버가 허용 동작 `process_now` 를 내려준 사용자·회의록에서만 보인다(직급 판단을 화면에서 다시 하지 않는다). 담당자 화면에는 없다.
- 위치: 회의록 상세 처리 영역, 왼쪽 메뉴 처리 현황의 대기 항목. 왼쪽 메뉴는 처리 현황 응답에 허용 동작이 없어, 대기(우선 처리 아님) 항목마다 회의록 상세를 한 번 조회해 `allowedActions` 로 확인한다.
- 응답 `waitingForPrior=true` → 서버 `message`("시스템의 선행 작업완료 후 처리하겠습니다.")를 그대로, `false` → "변환을 바로 시작합니다.". 409·403·404는 서버 사유 그대로. 요청 중 버튼 비활성(중복 클릭 방지).
