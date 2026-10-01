# 테스트 가이드 (v1.10.1 기준)

이 저장소에서 확인되는 테스트·실행 방법만 정리한다. 설정값(`.env`, `.env.local`)의 값은 적지 않고 변수 이름만 쓴다.

## 1. 스텁 백엔드 pytest

```bash
cd backend-contract-stub
pytest -q
```

- 현재 **133건 통과**가 기준이다(`CLAUDE.md`: 항상 통과해야 함).
- warning 1건이 나온다: `StarletteDeprecationWarning`(starlette.testclient에서 `httpx` 사용 관련). 기능 영향 없음.
- 테스트는 임시 DB 파일만 쓴다. `tests/conftest.py`가 app import 전에 `STUB_DB_PATH`를 테스트 전용 임시 파일로 바꾸고, 저장소가 다른 파일을 가리키면 수집 단계에서 중단한다. 실제 등록 데이터(`data/stub.db`)는 열지 않는다.
- 실제 Gemini를 호출하지 않는다(가짜 클라이언트 사용).

## 2. 프론트 타입 검사

```bash
cd frontend
npx tsc --noEmit      # 또는 npm run typecheck
```

- 오류 없이 끝나면(exit 0) 통과. E2E 파일(`e2e/`)도 포함해 검사한다.

## 3. Playwright E2E (프론트 화면)

- 파일: `frontend/playwright.config.ts`, `frontend/e2e/transcript-viewer.spec.ts`(전사 원문 창) **5건**.
- **목 모드(`NEXT_PUBLIC_USE_MOCK=true`) 화면에서만 동작한다.** 실서버·스텁 백엔드 데이터는 쓰지 않으며, 목 업로드와 화자 이름은 테스트 브라우저 메모리에만 생긴다.
- 브라우저는 새로 내려받지 않고 설치된 Chrome(`channel: "chrome"`)을 쓴다.

실행 방법(`playwright.config.ts` 주석 기준):

1. 테스트용 서버를 따로 띄워 둔 경우
   ```bash
   cd frontend
   E2E_BASE_URL=http://localhost:3100 npx playwright test
   ```
   이 서버는 반드시 목 모드로 빌드·실행된 것이어야 한다.
2. `E2E_BASE_URL`이 없으면 설정의 `webServer`가 목 모드 dev 서버를 **3100 포트**에 직접 띄운다(`NEXT_PUBLIC_USE_MOCK: "true"`를 프로세스 환경변수로 넘겨 `.env.local`보다 우선).
   ```bash
   cd frontend
   npx playwright test      # 또는 npm run test:e2e
   ```
   주의: 같은 폴더에서 `npm run dev`(3000)가 이미 돌고 있으면 두 서버가 `.next` 폴더를 함께 쓰므로 1) 방식을 쓴다.

- 결과 폴더(`test-results/`, `playwright-report/`)는 `.gitignore`로 커밋에서 제외된다.
- 실제 스텁 서버와 연결한 자동 화면 검증은 없다.

## 4. 스텁 서버 실행 순서

`backend-contract-stub/README.md` 기준:

```bash
cd backend-contract-stub
pip install -r requirements.txt    # v1.8부터 python-multipart 추가 — 다시 설치 필요
pytest -q
uvicorn app.main:app --reload --port 8000
```

- `.env`가 없으면 `.env.example`을 복사해 만든다(루트 `README.md`).
- 프론트를 스텁에 연결하려면 `frontend/.env.local`의 `NEXT_PUBLIC_USE_MOCK=false`로 바꾸고 프론트를 재시작한다.
- 처리기는 `.env`의 `STT_PROVIDER`·`LLM_PROVIDER`(`fake` | `gemini`, 기본 `fake`)로 고른다. `fake`면 외부 전송이 없다.

## 5. Gemini 키 모드 (`GEMINI_KEY_MODE`)

- 값은 `free` | `paid`(기본 `free`). 무료 키는 `GEMINI_API_KEY`, 유료 키는 `GEMINI_PAID_API_KEY`.
- **평소에는 `free`로 둔다. `paid`는 사용자가 승인했을 때만 쓴다.**
- 무료 키가 429에 걸려도 유료 키로 자동 전환하지 않는다(모드로만 고른다).
- 문제 해결: "Gemini 접속 오류가 나는데 키 모드를 바꿨는데도 그대로다" → 원인 후보: (1) `.env`에 같은 설정 이름이 두 번 있어 뒤 값이 적용됨 (2) 해당 모드의 키 줄이 주석 처리됨 (3) `.env`를 바꾼 뒤 서버를 재시작하지 않음. 확인은 `backend-contract-stub`에서 PowerShell `Select-String -Path .env -Pattern '^\s*GEMINI_KEY_MODE'`(모드 줄만 보이고 키 값은 나오지 않는다).

## 6. 테스트 데이터 정책

- 스텁 등록 데이터(`backend-contract-stub/data/stub.db`, 업로드 음성 `data/uploads`)는 실제 데이터로 취급한다. `data/`는 `.gitignore`로 커밋에서 제외된다.
- **자동 초기화 금지.** 서버를 다시 켜도 데이터는 유지되고, 시드는 DB 파일이 처음 만들어질 때 한 번만 들어간다.
- **초기화는 사용자가 지시할 때만** 아래 스크립트로 한다.
  ```bash
  cd backend-contract-stub
  python -m scripts.reset_db          # 경로·건수만 보여 주고 아무것도 바꾸지 않음
  python -m scripts.reset_db --yes    # 사용자 지시가 있을 때만: 시드로 되돌리고 업로드 음성 삭제
  ```
- pytest와 Playwright E2E는 이 데이터를 읽거나 바꾸지 않는다.

## 7. A파일 정답표

- `docs/API-CONTRACT.md`가 가리키는 "테스트 가이드 A파일 정답표"는 **저장소 밖에 보관되어 있으며 이 저장소에는 없다.** 내용은 이 문서에 옮기지 않는다.
