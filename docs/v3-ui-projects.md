# v3.0 프로젝트 화면 (작업 69-1)

백엔드 API(66-2, 66-3, 66-3b)에 맞춘 프로젝트 화면이다. 회의 유형 선택·프로젝트 회의록 올리기는 69-2.

## 화면 구성
- **왼쪽 메뉴**: "프로젝트"(회의록 아래, 모바일 드로어 포함, 현재 위치는 `aria-current="page"`).
- **목록** `/v2/projects`: 탭(전체·진행 중·승인 대기·반려, 개수 표시), 표 "프로젝트 목록"(프로젝트·상태 칩·등록 부서·총괄·내 역할·참여자 수), 768 미만은 카드 변환, "프로젝트 등록" 버튼. 서버가 준 승인자(`approver`)가 나일 때만 승인 대기 항목에 "승인 필요" 표시. 승인 대기 탭이 승인 큐이고 승인·반려는 상세에서 한다.
- **등록** `/v2/projects/new`: 이름(필수·공백 금지·최대 120자), 설명(선택·최대 2000자), 등록 부서(내 소속만), 참여자(우리 부서 / 다른 부서 · 부서별 / 임원 그룹 체크 목록, 선택 사항, 본인은 자동 참여라 목록에서 뺌). 부서를 바꾸면 후보를 다시 불러오고 선택을 비운다. 부서장이면 "등록하면 바로 진행 중이 되고 본인이 총괄이 됩니다", 부서원이면 "부서장의 승인 후 진행됩니다". 소속 부서가 없으면 "소속 부서가 없어 프로젝트를 등록할 수 없습니다. 관리자에게 문의하세요"와 함께 제출 수단을 없앤다. 서버 거부는 폼 위 알림(`role="alert"`), 제출 중 중복 클릭 방지, 성공하면 상세로 이동.
- **상세** `/v2/projects/{id}`: 헤더(이름·상태 칩·등록 부서·총괄·등록자·설명), 참여자 표 "참여자 목록"(이름·직급·역할), 연결된 회의록 표 "프로젝트 회의록 목록"(회의명·회의 일시·단계·상태, 20건 단위 쪽 나눔, 기본 진행중 단계). 404 는 안내와 목록 링크.

## 사용 API와 응답 필드
| API | 쓰임 | 주요 필드 |
|---|---|---|
| `GET /api/projects` | 목록 | id, name, status, departmentName, lead, approver, myRole, memberCount |
| `GET /api/projects/{id}` | 상세 | 위 필드 + description, registeredBy, members[accountId·name·rank·role], allowedActions |
| `POST /api/projects` | 등록 | name, description, departmentId, memberIds → 상세 응답 |
| `POST …/approve`, `…/reject` | 승인·반려 | reject 는 reason 필수 |
| `POST …/members`, `…/members/{id}/remove` | 참여자 추가(accountId, role)·제거 | 제거는 DELETE 대신 POST |
| `POST …/change-lead` | 총괄 변경 | newLeadId, reason |
| `GET /api/projects/{id}/meetings` | 프로젝트 회의록 | 회의록 목록과 같은 항목·page·size |
| `GET /api/me/org` | 등록 부서 선택 | departmentId, name, role(head·member) |
| `GET /api/projects/candidates?departmentId=` | 참여자 후보 | ownDepartment·otherDepartments·executiveGroup |
| `GET /api/accounts` | 총괄 변경 대상(관리자 이상만 걸러 씀), 후보 API 대체 | id, name, rank |

변경 응답은 모두 프로젝트 상세라 화면은 그 값으로 바로 갱신한다.

## 허용 동작별 버튼 노출(서버 `allowedActions` 로만 판정)
| 허용 동작 | 노출 | 동작 |
|---|---|---|
| `approve_project` | [승인] | 승인 요청 → 진행 중 |
| `reject_project` | [반려] | 사유 팝업(필수) → 반려 |
| `manage_members` | [참여자 추가], 행의 [제거](총괄 행 제외) | 추가 팝업, 제거 확인 팝업("참여자 제거") |
| `change_lead` | [총괄 변경] | 새 총괄(관리자 이상)·사유 팝업 |
| (없음) 승인 대기 | "승인 대기 중, 승인자: {이름 또는 부서장}" 안내만 | — |

## 상태·역할 라벨
- 상태 칩(`.mn-chip`): 승인 대기=중립, 진행 중=정보, 반려=취소(취소선) 토큰. 한글 라벨을 항상 함께 쓴다.
- 역할: lead=총괄, manager=관리자, member=참여자. 직급은 담당자·중간관리자·지시자.

## 오류 처리
- 목록·상세·회의록 목록 조회 실패는 `role="alert"` 와 "다시 시도". 상세 404 는 안내.
- 등록·승인·반려·참여자·총괄 변경의 서버 거부(403·409·422 등)는 서버 메시지를 그대로 보인다(폼 위 알림 또는 팝업 안).
- 참여자 추가 후보 API 를 쓸 수 없으면(예: 총괄이 등록 부서 소속이 아님) 같은 고객사 계정 목록으로 대신한다. 관리자 역할에 직급이 모자라면 서버 문구를 그대로 보인다.

## 아직 하지 않은 것
회의 유형 선택·프로젝트 회의록 올리기(69-2), 유사 업무 대체 화면, 오른쪽 패널의 승인 큐, 조직 관리 화면. 프로젝트 회의록 목록의 단계 탭(보류·종료 등)과 반려 사유 표시(API 에 없음)도 없다.
