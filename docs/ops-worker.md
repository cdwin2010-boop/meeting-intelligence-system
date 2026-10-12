# 운영: 단독 워커 프로세스 (작업 75-1)

폐쇄망 온프레미스 단일 노드를 기준으로 한다. Celery·Redis 같은 외부 브로커를 쓰지 않고, DB 표 `task_queue` 를 큐로 쓴다.

## 실행

```
cd backend
python worker.py
```

- 웹 서버(`uvicorn app.main:app`)와 **별도 프로세스**다. 한 프로세스는 한 번에 작업 하나만 처리한다.
- 서버와 워커는 **같은 장비에서 같은 DB 파일**(`DATABASE_URL`)과 같은 `UPLOAD_DIR` 을 공유해야 한다.
- 종료는 Ctrl+C(SIGINT) 또는 SIGTERM. 현재 작업이 끝나기를 `WORKER_SHUTDOWN_GRACE` 초까지 기다리고, 넘으면 처리 중이던 작업을 대기로 되돌리고 종료한다.

## 설정(`backend/.env`)

| 이름 | 기본값 | 설명 |
|---|---|---|
| `PROCESSING_MODE` | `inline` | `inline` = 서버가 직접 처리(현행) / `queue` = 큐에 쌓고 워커가 처리 |
| `WORKER_POLL_SECONDS` | `1.5` | 대기 작업 폴링 간격(초) |
| `WORKER_STALE_SECONDS` | `1800` | 처리 중 작업을 멈춘 것으로 보는 시간(초) |
| `WORKER_MAX_ATTEMPTS` | `3` | 멈춘 작업을 다시 대기로 돌리는 최대 시도 수. 이상이면 실패 처리 |
| `WORKER_SHUTDOWN_GRACE` | `30` | 종료 신호 뒤 현재 작업을 기다리는 시간(초) |

## 운영 주의

- `PROCESSING_MODE=queue` 인데 워커가 떠 있지 않으면 업로드·재처리한 작업이 **대기 상태로 남는다**(서버는 직접 처리하지 않는다).
- queue 모드의 서버는 시작 때 대기·처리 중 작업을 실패로 바꾸지 않는다. 멈춘 작업의 복구는 워커 시작 때 한다.
- 처리 방식을 `queue` → `inline` 으로 바꾸면 큐에 남은 작업은 처리되지 않으니, 바꾸기 전에 대기 건수(`GET /api/system/engine` 의 `queuePending`·`queueProcessing`)가 0 인지 확인한다.
- 운영 DB 에는 마이그레이션 `b8f3a1c6d204`(작업 큐 표)를 백업 후 `alembic upgrade head` 로 적용해야 queue 모드를 쓸 수 있다.
- 로그에는 작업 id·회의록 id·분류 코드만 남기고 전사 본문·파일 경로는 남기지 않는다.

## 처리 순서와 GPU 메모리

음성: STT → (STT 엔진 `release()` + `release_gpu_memory()`) → 업무 추출. STT 와 LLM 을 동시에 올리지 않는다.
txt 자료 파일: STT 를 건너뛰고 전사문을 읽어 추출한다. `torch` 가 설치돼 있지 않아도 동작한다.
