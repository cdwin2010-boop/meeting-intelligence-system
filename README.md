# 음성 회의록 AI 의사결정 추적관리 시스템 — v1.7

회의 음성 → STT → LLM 의사결정/액션아이템 추출 → 담당자 매핑 → 추적·관리까지 이어지는 시스템의 **화면(프론트엔드)과 API 규격** 패키지입니다.
"구축 가이드 — 초보자용 개정판"의 1~7단계와 이 폴더가 1:1로 대응합니다.

## 폴더 구조

```
meeting-intelligence-system-v1.7/
├── README.md               ← 지금 읽는 문서
├── CHANGELOG.md            ← v1.7에서 바뀐 것 / 알려진 한계
├── CLAUDE.md               ← Claude CLI가 매번 읽는 프로젝트 규칙 (루트에 그대로 둘 것)
├── .gitignore              ← node_modules, .env, venv 등을 Git에서 제외
├── docs/API-CONTRACT.md    ← 프론트 ↔ 백엔드 API 계약서 (경로·오류코드·타입)
├── frontend/               ← Next.js 15 + Tailwind v4 (회의록 상세 "/", 회의별 상세 "/meetings/[id]", 음성 등록 "/upload", 관리자 콘솔 "/admin")
├── backend-contract-stub/  ← API 계약 확인용 참고 서버 (운영 백엔드가 아님, 아래 설명)
└── backend/                ← (직접 만드는 폴더) 기존 FastAPI 백엔드를 여기에 복사
```

> `backend/`는 이 ZIP에 **일부러 넣지 않았습니다.** 이미 가지고 있는 백엔드(pytest 76건)를 덮어쓰지 않기 위해서입니다.
> 기존 백엔드 폴더를 이 안에 `backend/`라는 이름으로 복사하세요. (`venv/`는 복사하지 말고 새로 만드세요)

## 1분 시작 (프론트엔드, 백엔드 없이 목 데이터로)

VS Code에서 이 폴더를 열고 터미널(**Ctrl + 백틱**, 숫자 1 왼쪽 키)에서:

```bash
cd frontend
npm install          # 부품 내려받기 (몇 분)
npm run typecheck    # 타입 검사: 아무 출력 없이 끝나면 통과
npm run dev          # http://localhost:3000  (음성 등록: /upload, 회의별 상세: /meetings/[id], 관리자: /admin)
```

> ⚠️ 이 패키지는 npm이 막힌 환경에서 만들어져 **`npm install` / `typecheck` / 첫 `next build`는 실행해 보지 못했습니다.**
> 첫 실행에서 오류가 나면 메시지 전체를 Claude CLI에 붙여넣어 "고쳐 줘"라고 하세요. (가이드 2단계, 막힘 확인표)

## 목 데이터 ↔ 실제 서버 전환

`frontend/.env.local.example`을 복사해 `frontend/.env.local`을 만듭니다.

| 변수 | 기본값 | 의미 |
|---|---|---|
| `NEXT_PUBLIC_USE_MOCK` | `true` | `true` 목 데이터 / `false` 실제 FastAPI |
| `NEXT_PUBLIC_API_BASE_URL` | `http://127.0.0.1:8000/api` | 실제 서버 기본 주소 (끝에 `/` 없이) |
| `NEXT_PUBLIC_ADMIN_POLL_MS` | `10000` | 관리자 콘솔 자동 새로고침 주기(ms), `0`이면 끔 |
| `NEXT_PUBLIC_DEFAULT_MEETING_ID` | `mtg-2026-0925` | `/` 화면이 여는 회의 ID |

값을 바꾸면 `npm run dev`를 **Ctrl + C로 끄고 다시** 실행해야 반영됩니다. `NEXT_PUBLIC_` 값은 브라우저에 노출되니 비밀 값을 넣지 마세요.

## 백엔드와 연결해 보기 (계약 스텁)

`backend-contract-stub/`은 `docs/API-CONTRACT.md`의 6개 엔드포인트를 **메모리 DB로** 구현한 참고 서버입니다.
"화면 ↔ API 규격이 맞는지"를 운영 백엔드 없이 확인하고, 운영 백엔드가 따라야 할 모양(camelCase 변환, 정렬 허용 목록, 조건부 UPDATE, 409)을 코드로 보여 줍니다.

```bash
cd backend-contract-stub
python -m venv venv
# PowerShell: .\venv\Scripts\Activate.ps1   /  Git Bash: source venv/Scripts/activate   /  macOS·Linux: source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env            # PowerShell: Copy-Item .env.example .env
pytest -q                       # API 계약 테스트 (먼저 통과하는지 확인)
uvicorn app.main:app --reload --port 8000    # http://localhost:8000/docs
```

그 다음 `frontend/.env.local`에서 `NEXT_PUBLIC_USE_MOCK=false`로 바꾸고 프론트를 재시작하면 `/`, `/admin`이 스텁 서버 데이터로 동작합니다.
스텁은 **인증이 없습니다.** 운영 백엔드에서는 `/api/admin/*`에 서버 권한 검증을 반드시 구현하세요(화면 숨김·CORS는 보안이 아닙니다).

## Git 시작

```bash
git init
git add .
git status     # node_modules, .env 가 목록에 없는지 확인
git commit -m "chore: import meeting-intelligence v1.7"
```

## 다음 단계 (권장 순서)

1. `npm run typecheck` 통과 확인 → 첫 커밋
2. 기존 백엔드를 `backend/`로 복사 → `pytest -q` 76건 통과 확인 → CORS를 `.env`(`CORS_ALLOW_ORIGINS`)로 옮기기
3. `docs/API-CONTRACT.md`대로 운영 백엔드에 엔드포인트 추가 (Claude CLI에 계약서를 붙여 요청)
4. 관리자 API 서버 권한 검증(RBAC) + 감사 로그
5. 프론트 자동 테스트(Playwright) 추가 — 지금 프론트에는 저장소에 포함된 자동 테스트가 없습니다
