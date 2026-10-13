# Clean Light 디자인 (작업 87-1, 1단계)

고객 의견("화면 디자인이 좋지 않다")에 대응해 v2 화면의 기본 테마를 밝고 정돈된 **Clean Light** 로 바꾼다.
동작·API·라우트·`data-testid`·접근성 속성은 그대로이고 스타일과 마크업만 정리한다.

## 테마 정책
- 기본은 라이트(Clean Light). 저장소 `mi.v2.theme = "dark"` 를 가진 사용자는 다크(Mono Dark)를 유지한다. 전환 버튼 동작 불변.
- 라이트 값은 `app/mono-dark/theme-v2.css` 의 `html[data-v2-theme="light"]` 범위에만 있다. 다크 값은 `tokens.css` 의 `:root` 에 있고 v1 화면도 이 값을 쓴다 → 서로 영향 없음.
- 컴포넌트에는 색을 직접 쓰지 않는다(`bg-mn-*`, `text-mn-*`, `border-mn-*`). 하드코딩 색 가드 E2E(`v2-theme.spec.ts`)가 검사한다.
- 이 문서의 라이트 값이 `docs/v3-ui-theme.md`(68-1 Soft Warm Neutral)의 라이트 값을 대체한다. 다크 값과 전환·저장 규칙은 그 문서가 그대로 유효하다.

## 토큰 (라이트)
| 이름 | 토큰 | 값 | 용도 |
|---|---|---|---|
| ground | `--mn-background` | #F6F7F9 | 페이지 바탕 |
| surface | `--mn-surface` | #FFFFFF | 카드·표·메뉴 영역 |
| border | `--mn-border` | #E3E6EB | 1px 영역 구분선 |
| ink | `--mn-text` | #1B2430 | 본문 글자 |
| ink-muted | `--mn-text-muted` | #5B6675 | 보조 글자, 표 머리글 |
| brand | `--mn-accent` | #2F5BEA | 주 버튼·알림 뱃지(위 글자 `--mn-on-accent` #FFFFFF) |
| brand-tint | `--mn-brand-tint`, `--mn-selected` | #E9EFFF | 현재 메뉴·요약 숫자 상자 바탕 |
| brand-ink | `--mn-brand-ink`, `--mn-link` | #1F3FA8 | 현재 메뉴·숫자·링크 글자 |
| avatar | `--mn-avatar` | #DDE3EE | 담당자 아바타 바탕 |
| focus-ring | `--mn-focus`, `--mn-focus-ring` | #2F5BEA | 키보드 포커스 링(유일한 box-shadow) |
| 컨트롤 테두리 | `--mn-control-border` | #7A8494 | 입력·버튼 테두리(3:1 이상) |
| hover | `--mn-surface-elevated` | #EEF1F5 | 메뉴·행 hover, 모달 |

### 상태 쌍 (바탕 / 글자) — `StatusChip` 만 사용
| 라벨 | tone | 토큰 | 바탕 | 글자 |
|---|---|---|---|---|
| 승인 대기 | `pending` | `chip-warning` | #FFF3D6 | #7A4B00 |
| 수정 요청 | `revision` | `chip-revision` | #EDE7FB | #4A2D9C |
| 보완 필요 | `needs` | `chip-danger` | #FDE8E8 | #9B1C1C |
| 진행 중 | `progress` | `chip-info` | #E6EEFF | #1F3FA8 |
| 완료 | `done` | `chip-success` | #E3F5EA | #17613A |

### 모서리·간격·글꼴
- 모서리: 8(메뉴 항목·아이콘 버튼, `rounded-mn-item`) / 10(주요 버튼·입력, `rounded-mn-control`) / 12(카드·표, `rounded-mn-card`) / 알약(상태 라벨·뱃지·아바타, `rounded-mn-badge`·`rounded-full`). 다크는 기존 6/6/8/4 유지.
- 그림자·그라데이션 금지. 영역은 1px border.
- 간격은 4px 눈금(4·8·12·16·20·24·32·40).
- 글꼴: IBM Plex Sans KR 400/500/600/700, `@fontsource/ibm-plex-sans-kr` 자체 호스팅(외부 CDN 없음, `app/v2/layout.tsx` 에서 korean·latin 만 import). 대체 Noto Sans KR → system-ui. 숫자·ID·시각은 Geist Mono 유지.
- 글자 크기: 제목 26/34 700(`.mn-page-title`), 소제목 17/24 700, 본문 15/23, 보조 13/20, 상태 라벨 13/500.

## 컴포넌트 규칙
- **화면 틀**: 메뉴 영역 너비 240px, 바탕 surface(`bg-mn-shell`) + 1px 테두리. 메뉴 항목 높이 44px·모서리 8, 현재 화면은 brand-tint 바탕 + brand-ink 글자 + `aria-current="page"`. 알림 뱃지(처리 중 건수·모바일 처리 현황)는 brand 바탕 흰 글자 알약. 768px 미만은 기존 드로어 동작. 보조창 좌우 전환(`left|right`)은 기능 그대로 → 기본 위치(오른쪽)는 바꾸지 않았다.
- **요약 카드** `SummaryCard`: 왼쪽 44×44 숫자 상자(brand-tint, 숫자는 mono) + 제목 + 보조 문장. 그리드는 `repeat(auto-fit, minmax(200px, 1fr))`.
- **표**: 행 높이 64px, 머리글 13px ink-muted, 담당자는 28px 원형 `Avatar` + 이름, 값이 없으면 "미확정"(ink-muted). 좁은 화면은 표 안 가로 스크롤.
- **상태 라벨** `StatusChip`(`components/v2/StatusChip.tsx`): 위 다섯 쌍만. 글자 라벨 필수, 색만으로 구분하지 않는다.
- 숫자·ID·시각·에러 로그는 mono.

## 대비 검사
`frontend/e2e/v2-87-clean-light-contrast.spec.ts` 가 `theme-v2.css` 의 라이트 값으로 글자 쌍 4.5:1, 컨트롤 테두리·포커스 링 3:1 을 계산한다(ink/surface, ink-muted/ground, 흰 글자/brand, 상태 5쌍 등). 값을 바꾸면 이 테스트가 먼저 깨진다.

## 스크린샷·가로 넘침
`frontend/e2e/v2-87-todo-screens.spec.ts`: 할 일 화면 375/768/1280px × 라이트/다크 가로 넘침 검사. 스크린샷은 `frontend/test-results/design-87-1/` 에 저장(.gitignore 대상, 커밋 제외).

## 2단계 대상 화면 (이번 단계에서는 토큰만 따라감)
1. 업무 현황 `/v2/workload` (KPI·가로 스택 바)
2. 회의록 목록 `/v2/meetings`
3. 회의록 상세 `/v2/meetings/[id]` (업무 원장·요약·발언 보기·재생)
4. 올리기 `/v2/upload`
5. 프로젝트 목록·등록·상세 `/v2/projects`
6. 시스템 관리 `/v2/admin/*` (계정·부서·설정·라이선스)
7. 로그인 `/v2/login`, 팝업(`Modal`) 공통
8. 보조 칩 정리: 프로젝트 상태 칩·대체됨/취소 칩을 다섯 상태 쌍과 `StatusChip` 으로 통합할지 결정
