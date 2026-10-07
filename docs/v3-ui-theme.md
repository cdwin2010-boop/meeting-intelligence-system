# v3.0 v2 화면 테마 토큰 (라이트 기본 + Mono Dark 보존)

작업 68-1. v2 화면의 기본은 "Soft Warm Neutral" 라이트이고, 기존 Mono Dark 는 테마 토글로 보존한다(앞서의 "다크 단일" 조정을 대체한 결정). 컴포넌트 구조는 바꾸지 않고 CSS 변수(`--mn-*`) 값을 테마별로 정의했다. 그림자는 포커스 링 하나뿐이다.

## 선택자·저장 방식
- 선택자: `html[data-v2-theme="light"|"dark"]`(`app/mono-dark/theme-v2.css`). 이 속성은 v2 화면(`app/v2/layout.tsx`)만 붙이고 v2 를 떠나면 지우므로 v1 화면은 바뀌지 않는다. 모달이 `document.body` 로 포털되어 v2 루트 요소 밖에 그려지므로 속성은 `html` 에 둔다. `color-scheme` 도 테마에 맞춘다(네이티브 날짜 선택기·스크롤바·체크박스·오디오 플레이어가 따른다).
- 기본값(속성이 없는 v1 화면 포함)은 `tokens.css` 의 `:root` 값 = 다크(Mono Dark)다. 라이트 값은 `html[data-v2-theme="light"]` 에서만 덮어쓴다.
- 저장 키: 기기별 브라우저 저장소 `mi.v2.theme` = `"light"` | `"dark"`. 값이 없거나 잘못됐거나 저장소 접근이 막히면 라이트. 운영체제 설정(`prefers-color-scheme`)은 따르지 않는다.
- 첫 페인트 전 적용: `app/v2/layout.tsx` 가 인라인 스크립트(`THEME_INIT_SCRIPT`)를 body 맨 앞에 넣어 화면이 그려지기 전에 속성을 붙인다. 루트 `html` 에 `suppressHydrationWarning` 을 둬 속성 때문에 하이드레이션 경고가 나지 않게 했다. `ThemeProvider` 의 첫 렌더 상태는 서버와 같은 "light" 이고 마운트 직후(페인트 전) 속성에서 실제 값을 읽는다.
- 토글 버튼: 보이는 글자는 전환될 대상("다크 모드"/"라이트 모드"), 접근성 이름은 "화면 테마 전환, 현재 라이트|다크", `aria-pressed` 없음. 데스크톱은 헤더의 사용자 표시 왼쪽, 모바일은 드로어 하단(사용자 표시·로그아웃과 같은 묶음, DOM 에 하나).

## 토큰
기존 이름은 그대로이고 아래가 추가됐다: `--mn-control-border`(Tailwind `border-mn-control`), `--mn-text-disabled`, `--mn-link`, `--mn-selected`, `--mn-chip-{neutral,info,success,warning,muted,danger,superseded,cancelled}-{bg,fg}`. 컨트롤(입력·선택·체크박스 칩·보조 버튼·드롭존) 테두리는 `border-mn-control` 을 쓴다(다크 값은 카드 보더와 같아 모양이 바뀌지 않는다).

| 토큰 | 라이트 | 다크(현재 값) | 쓰임 |
|---|---|---|---|
| `--mn-background` | #FAF9F6 | #000000 | 페이지 배경 |
| `--mn-surface` | #FFFFFF | #0A0A0A | 카드 |
| `--mn-surface-elevated` | #F4F2EE | #171717 | hover·모달·메뉴 |
| `--mn-border` | #E5E7EB | #262626 | 카드 보더·구분선(장식) |
| `--mn-control-border` | #7A8494 | #262626 | 컨트롤 테두리 |
| `--mn-text` | #0F172A | #EDEDED | 본문 |
| `--mn-text-muted` | #475569 | #A1A1A1 | 보조 글자 |
| `--mn-text-disabled` | #94A3B8 | #6B6B6B | 비활성 글자(WCAG 예외) |
| `--mn-link` | #1D4ED8 | #8AB4FF | 링크 |
| `--mn-selected` | #ECEAE4 | #171717 | 선택 행 |
| `--mn-accent` / `--mn-on-accent` | #1E293B / #FFFFFF | #FFFFFF / #000000 | 주 버튼 |
| `--mn-accent-hover` | #0F172A | #E5E5E5 | 주 버튼 hover |
| `--mn-red` / `--mn-on-red` | #B91C1C / #FFFFFF | #EE0000 / #FFFFFF | 위험 버튼, 실패 점 |
| `--mn-red-hover` | #991B1B | #CC0000 | 위험 버튼 hover |
| `--mn-blue` | #4F6F9F | #0070F3 | 진행 점(소프트 슬레이트 블루) |
| `--mn-teal` | #5E8C6A | #50E3C2 | 완료 점(세이지 그린) |
| `--mn-focus` | #334155 | #A3A3A3 | 포커스 링 색 |
| `--mn-focus-ring` | 0 0 0 1px #FAF9F6, 0 0 0 3px #334155 | 0 0 0 1px #000, 0 0 0 3px #A3A3A3 | 키보드 포커스(유일한 box-shadow) |
| `--mn-overlay` | #0F172A66 | #000000B3 | 모달 배경 |

상태 칩(연한 배경 + 진한 글자, 글자 라벨 필수. `.mn-chip` + `.mn-chip-<종류>`, 대체됨·취소는 취소선. 실제 화면 사용은 이후 대체 UI 단계):

| 종류 | 쓰임 | 라이트 배경/글자 | 다크 배경/글자 |
|---|---|---|---|
| neutral | 대기·확정 대기 | #EEF0F3 / #3F4A5A | #171717 / #A1A1A1 |
| info | 진행·처리 중 | #E3EAF3 / #2F4A6B | #0A1F3D / #7AB3FF |
| success | 확정·완료 | #E3EFE6 / #2D5A3A | #0B2E27 / #50E3C2 |
| warning | 보류 | #F6EBD3 / #7A4B05 | #33250A / #F5C26B |
| muted | 종료 | #ECEAE6 / #57534E | #171717 / #A1A1A1 |
| danger | 실패 | #F8E4E1 / #8E2A1F | #3A0A0A / #FF8A8A |
| superseded | 대체됨(웜 앰버, 취소선) | #F3E8D0 / #6B4A10 | #33250A / #D9A441 |
| cancelled | 직권 취소·종결(뉴트럴 그레이, 취소선) | #ECEBE6 / #57534E | #171717 / #8A8A8A |

## 대비 비율 (WCAG, 계산값)
라이트(배경 #FAF9F6 / 보조 배경 #F4F2EE / 카드 #FFFFFF):

| 조합 | 비율 | 기준 |
|---|---|---|
| 본문 #0F172A | 16.96 / 15.97 / 17.85 | 4.5 |
| 보조 #475569 | 7.20 / 6.78 / 7.58 | 4.5 |
| 링크 #1D4ED8 | 6.37 / 5.99 / 6.70 | 4.5 |
| 비활성 #94A3B8 | 2.44 / 2.29 / 2.56 | 예외(대비 요건 없음) |
| 컨트롤 보더 #7A8494 | 3.59 / 3.38 / 3.78 | 3.0 |
| 포커스 링 #334155 | 9.83 / 9.26 / 10.35 | 3.0 |
| 주 버튼 흰 글자 / #1E293B | 14.63 | 4.5 |
| 위험 버튼 흰 글자 / #B91C1C | 6.47 (hover #991B1B 8.31) | 4.5 |
| 상태 칩 글자/배경 | neutral 7.87, info 7.49, success 6.74, warning 6.26, muted 6.35, danger 6.87, superseded 6.61, cancelled 6.35 | 4.5 |
| 상태 점(장식, 글자 라벨 동반) 흰 배경 | 슬레이트 블루 5.11, 세이지 3.86, 빨강 6.47 | 3.0 권장 |

다크(현재 값 그대로): 본문 #EDEDED — #000 17.94 / #0A0A0A 16.91, 보조 #A1A1A1 — 8.13 / 7.66, 주 버튼 21.0, 위험 버튼 4.53, 포커스 링 #A3A3A3 — 8.33 / 7.85, 칩 글자/배경 neutral 6.94, info 7.62, success 9.14, warning 9.09, muted 6.94, danger 7.54, superseded 6.62, cancelled 5.19.
- **알려진 항목(이후 3단계에서 보정)**: 다크 컨트롤 테두리 #262626 은 검정 위 약 1.4 로 3:1 미달이다. 다크 값은 이번에 바꾸지 않았다.

## 조정한 가이드 값과 사유
- 세이지 그린 #22C55E: 흰 배경 글자로 쓰면 약 2.3:1 이라 글자로 쓰지 않는다. 성공 칩은 연한 배경 #E3EFE6 + 진한 글자 #2D5A3A(6.74), 완료 점은 #5E8C6A.
- 위험(빨강) 버튼: 라이트에서 #B91C1C(흰 글자 6.47). 
- 카드 보더 #E5E7EB: 장식 구분선으로만 쓴다. 컨트롤 테두리는 3:1 이상이 필요해 별도 토큰 #7A8494 로 분리했다.
- 보조 글자: 가이드의 #1E293B 는 본문(#0F172A)과 구분이 약해 보조 글자는 슬레이트 #475569 로 했다(모든 배경에서 4.5 이상).
- 포커스 링은 흰 안쪽 링 + 슬레이트 바깥 링으로 라이트 배경 대비 9:1 이상.
- 대체됨 칩은 웜 앰버 계열(#F3E8D0 / #6B4A10)로 정의했다(모노 #78716C 취소선 대신, 글자 대비 4.5 이상 확보).
