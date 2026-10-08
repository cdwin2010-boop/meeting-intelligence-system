# v3.0 UI 업무 현황 화면 (/v2/workload, 작업 69-3)

하루 한 번 집계한 전일 통계 저장값을 보여 주는 읽기 전용 화면이다. 집계 규칙과 권한은 `docs/v2-ledger-state-design-draft.md` 부록 D-25 를 따른다.

## 메뉴
- 왼쪽 메뉴(모바일은 드로어)에서 "할 일" 바로 아래 "업무 현황"(`/v2/workload`). 현재 위치는 `aria-current="page"`. 기존 메뉴 항목의 이름·동작은 그대로다.

## 사용 API
`GET /api/workload?departmentId=` 하나만 쓴다. 권한 판단은 화면이 아니라 서버가 하며, 화면은 응답의 `scope` 로만 범위를 정한다.

| 필드 | 쓰임 |
|---|---|
| `snapshotDate` | 집계 기준일. `null` 이면 "아직 집계된 통계가 없습니다. 집계는 매일 새벽에 갱신됩니다." 만 보이고 KPI·바·하단 안내는 숨긴다 |
| `scope.kind` / `scope.departments` / `scope.department` | 범위(company·department·self)와 부서 선택 목록·현재 부서 |
| `kpis` | assigned·completionRate(null 이면 "—")·overdue·avgOpenPerPerson·memberCount |
| `members[]` | accountId·name·completed·inProgress·overdue·open·overloaded (서버가 미완료 많은 순으로 준다) |
| `urgentItems[]` | itemId·title·dueDate·kind(overdue·due_soon)·days·meetingId·meetingTitle·assignee (서버가 지연 먼저·기한 오래된 순으로 준다) |

## 화면 구성
1. KPI 카드 4개: 총 배정 업무, 완료율(%), 지연 건수, 1인당 평균 잔여 업무(구성원 수 보조 표시).
2. 구성원별 가로 스택 바: 완료·진행 중·지연 비율. 바에는 `role="img"` 와 "이름: 완료 N건, 진행 중 N건, 지연 N건" 대체 텍스트가 있고, 바 아래에 색과 함께 글자 라벨·건수 칩이 보인다. 업무가 0건이면 빈 바와 "업무 없음". `overloaded` 이면 "미완료 과다" 배지(글자 라벨). 색은 68-1 칩 토큰(성공·정보·위험)만 쓴다.
3. 집중 관리: `urgentItems` 목록. 업무명·담당자·기한(mono)·"지연 N일" 또는 "D-N" 칩·회의록 제목. 각 항목은 `/v2/meetings/{meetingId}` 링크이고 접근성 이름은 항목마다 다르다("업무명 업무, 담당 이름, 기한 날짜, 라벨, 회의록 제목"). 비면 "지연·임박 업무가 없습니다".
4. 맨 아래 안내(`footer`): "전일(YY년 MM월 DD일 기준) 통계 실적입니다." 날짜는 `snapshotDate` 를 문자열 그대로 자른 값이라 시간대에 흔들리지 않는다. 같은 영역에 범위와 한정(담당자가 계정으로 확정된 업무만 집계, 직권 종료·삭제·보류 회의록·대체된 업무 제외, 종결 구분 도입 전 종결 업무는 완료로 집계하지 않음, 지연은 기한 다음 날부터)을 짧은 문장으로 적는다.
5. 상태: 로딩("현황을 불러오는 중…"), 오류(`role="alert"` 와 "다시 시도"), 403(서버 문구, 재시도 버튼 없음).
6. 768px 미만에서는 구성원 행이 이름 아래에 바가 오는 카드로 쌓이고, 가로 넘침이 없다.

## 범위별 노출
| 응답 scope | 부서 선택 | 제목 아래 표시 |
|---|---|---|
| company(지시자 기본) | 보임: "전사" + 부서 목록 | — |
| department, 부서 목록이 둘 이상(또는 전사를 한 번이라도 받은 지시자) | 보임 | — |
| department, 부서 목록이 하나 | 숨김 | — |
| self | 숨김 | "내 현황" |

부서를 고르면 `departmentId` 로 다시 조회하고 이전 요청은 취소한다. "전사"를 고르면 `departmentId` 없이 조회한다.

## 아직 하지 않은 것
기간 선택, 개인 간 순위·점수화, 다운로드, 조직 관리 화면, 실시간 갱신(집계는 하루 한 번).
