# backend-contract-stub — API 계약 확인용 참고 서버

- 운영 백엔드가 **아닙니다.** 파일 SQLite(v1.9.10, 기본 `data/stub.db`, 설정 `STUB_DB_PATH`) + `app/seed.json`(프론트 목 데이터와 동일)로 `docs/API-CONTRACT.md`의 엔드포인트(기존 6개 + 음성 등록 2개)를 구현합니다. 음성 등록은 실제 STT 없이 가짜 처리기가 흉내만 냅니다(`app/fake_worker.py`).
- 기존 `backend/`(pytest 76건)를 덮어쓰지 마세요. 이 폴더는 별도 이름 그대로 두고 참고하세요.
- 참고할 곳: `app/config.py`(CORS를 .env로), `app/schemas/models.py`(camelCase alias), `app/db.py`(정렬 허용 목록, 조건부 UPDATE), `app/routers/api.py`(404/409/400 규칙), `tests/test_contract.py`, 음성 등록은 `app/uploads.py`·`app/fake_worker.py`·`tests/test_upload.py`.
- 인증 없음 → 운영에서는 `/api/admin/*`에 서버 권한 검증 필수.
- 저장(v1.9.10): 등록 데이터는 서버를 다시 켜도 유지됩니다. 시드는 DB 파일이 처음 만들어질 때 한 번만 들어가고, 자동 초기화는 없습니다. 초기화는 `python -m scripts.reset_db --yes`(인자 없이 실행하면 건수만 보여 줌)로만 합니다. 서버 시작 때 이전 서버에서 처리 중·대기였던 **업로드** 작업만 failed("서버 재시작으로 중단됨")로 바꿔 Retry할 수 있게 합니다(시드·완료 작업은 그대로, 음성 파일이 없으면 Retry는 409). 업로드 음성은 `data/uploads`(설정 `UPLOAD_DIR`). `data/`는 실제 회의 내용이 들어가므로 `.gitignore`로 커밋에서 제외됩니다. pytest는 임시 DB 파일만 씁니다.

실행: `pip install -r requirements.txt` (v1.8부터 `python-multipart` 추가됨 — 다시 설치 필요) → `pytest -q` → `uvicorn app.main:app --reload --port 8000`
