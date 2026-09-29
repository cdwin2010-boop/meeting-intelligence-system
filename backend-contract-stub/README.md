# backend-contract-stub — API 계약 확인용 참고 서버

- 운영 백엔드가 **아닙니다.** 메모리 SQLite + `app/seed.json`(프론트 목 데이터와 동일)로 `docs/API-CONTRACT.md`의 6개 엔드포인트만 구현합니다.
- 기존 `backend/`(pytest 76건)를 덮어쓰지 마세요. 이 폴더는 별도 이름 그대로 두고 참고하세요.
- 참고할 곳: `app/config.py`(CORS를 .env로), `app/schemas/models.py`(camelCase alias), `app/db.py`(정렬 허용 목록, 조건부 UPDATE), `app/routers/api.py`(404/409/400 규칙), `tests/test_contract.py`.
- 인증 없음 → 운영에서는 `/api/admin/*`에 서버 권한 검증 필수.

실행: `pip install -r requirements.txt` → `pytest -q` → `uvicorn app.main:app --reload --port 8000`
