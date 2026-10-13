# 개발 폴더 분리 (worktree 이원화)

같은 저장소를 폴더 두 개로 나눠 쓴다. 테스트 폴더는 형님이 서버를 띄워 시험하는 안정판이고, 개발 폴더는 코드를 고치는 곳이다.

| 구분 | 테스트 폴더 | 개발 폴더 |
|---|---|---|
| 경로 | `C:\meeting-intelligence-system-v1.7` | `C:\meeting-intelligence-system-dev` |
| 브랜치 | `main` | `dev` (git worktree) |
| 용도 | 형님 테스트(서버 실행) | 모든 개발·자동 테스트 |
| 포트 | 백엔드 8001·스텁 8000·프론트 3000·E2E 3100 | 백엔드 8101·스텁 8100·프론트 3200·E2E 3201 |
| DB·.env | 테스트용 실제 데이터(개발 폴더가 읽지 않는다) | 새로 만든 개발 DB, `ENGINE_TEST_MODE=false`, Gemini 키 모드 free |

## 규칙
1. **앞으로 모든 개발은 개발 폴더(dev 브랜치)에서만 한다.** 코드·테스트·문서 수정, 커밋, 자동 테스트 모두 여기서 한다.
2. **테스트 폴더는 소스를 직접 수정하지 않는다.** 형님 승인 후 백업을 한 다음 dev 브랜치를 병합(`--ff-only` 우선)해서만 갱신한다.
3. **병합 때 `.env`·DB·업로드 파일·엔진 시험 설정 파일(`data/engine_test.json`)은 건드리지 않는다.** 모두 Git 제외 대상이므로 병합으로는 바뀌지 않으며, 수작업으로 복사·덮어쓰지 않는다.
4. **마이그레이션이 포함되면** 백업 후 형님 승인을 받아 적용(`alembic upgrade head`)과 서버·워커 재시작을 한 번에 한다.
5. 개발 폴더에서 테스트 폴더의 DB·`.env`·업로드를 읽거나 복사하지 않는다. 개발 DB 는 마이그레이션으로 새로 만든다.
6. 8000/8001/3000/3100 포트는 개발 폴더에서 쓰지 않는다(E2E 는 `E2E_PORT=3201`).

## 병합 절차 (문서만, 실행은 형님 승인 후)
```
# 0) 형님 승인 확인, 테스트 서버(8001/8000/3000) 상태 확인
# 1) 백업: 백업 브랜치 갱신 + C:\DOTS_backup 오래된 슬롯에 소스 압축본(.env·키·node_modules·DB 제외)
# 2) 테스트 폴더에서(작업 트리가 깨끗한지 확인: git status)
cd C:\meeting-intelligence-system-v1.7
git merge --ff-only dev        # 불가하면 중단하고 보고(수동 병합은 별도 승인)
# 3) 변경 확인
git diff --stat <병합 전 커밋>..HEAD
# 4) 마이그레이션이 있으면: DB 백업 → alembic upgrade head → 서버·워커 재시작을 한 번에(형님 승인)
#    프론트 의존 패키지가 바뀌었으면 frontend 에서 npm ci
# 5) 서버 재시작 후 확인
```
- 개발 폴더에서 새 의존 패키지를 추가했다면(승인된 경우) 병합 후 테스트 폴더에서 `pip install -r`·`npm ci` 가 필요하다.
- 테스트 폴더에서 발견한 문제는 개발 폴더에서 고친 뒤 같은 절차로 반영한다(테스트 폴더에서 바로 고치지 않는다).

## 개발 폴더 실행 예
```
# 백엔드(개발 DB: backend/data/app.db)
cd C:\meeting-intelligence-system-dev\backend
.venv\Scripts\python.exe -m uvicorn app.main:app --port 8101
# 프론트 (frontend/.env.local 이 8101/8100 을 가리킨다)
cd C:\meeting-intelligence-system-dev\frontend
npx next dev -p 3200
# E2E(목 모드)
$env:E2E_PORT=3201; npx playwright test
```
