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
| `QUEUE_PRIORITY_IMMEDIATE` | `100` | 즉시 변환 요청이 대기 작업에 부여하는 우선순위(기본 0 보다 큰 정수). 같은 값끼리는 접수 순서 |
| `WORKER_HEARTBEAT_INTERVAL_SEC` | `5` | 워커가 생존 신호(`worker_heartbeats`)를 기록하는 간격(초) |
| `WORKER_HEARTBEAT_STALE_SEC` | `30` | 마지막 생존 신호가 이 시간보다 오래되면 `workerAlive=false`(간격의 몇 배로 둔다) |

## 운영 주의

- `PROCESSING_MODE=queue` 인데 워커가 떠 있지 않으면 업로드·재처리한 작업이 **대기 상태로 남는다**(서버는 직접 처리하지 않는다).
- queue 모드의 서버는 시작 때 대기·처리 중 작업을 실패로 바꾸지 않는다. 멈춘 작업의 복구는 워커 시작 때 한다.
- 처리 방식을 `queue` → `inline` 으로 바꾸면 큐에 남은 작업은 처리되지 않으니, 바꾸기 전에 대기 건수(`GET /api/system/engine` 의 `queuePending`·`queueProcessing`)가 0 인지 확인한다.
- 운영 DB 에는 마이그레이션 `b8f3a1c6d204`(작업 큐 표)를 백업 후 `alembic upgrade head` 로 적용해야 queue 모드를 쓸 수 있다.
- 즉시 변환(작업 88-1)은 대기 중인 작업을 순서만 앞으로 올린다. 진행 중인 작업은 끝까지 처리하고 그 직후 시작한다(동시 처리 없음). `workerAlive=false` 면 워커 프로세스가 멈춘 것이니 `python worker.py` 를 확인한다. 마이그레이션 `a9c3e5f7b218` 를 백업 후 적용해야 한다.
- 로그에는 작업 id·회의록 id·분류 코드만 남기고 전사 본문·파일 경로는 남기지 않는다.

## 처리 순서와 GPU 메모리

음성: STT → (STT 엔진 `release()` + `release_gpu_memory()`) → 업무 추출. STT 와 LLM 을 동시에 올리지 않는다.
txt 자료 파일: STT 를 건너뛰고 전사문을 읽어 추출한다. `torch` 가 설치돼 있지 않아도 동작한다.

## FFmpeg 오디오 전처리 (작업 75-1 파트 B)

- 기본은 꺼짐(`AUDIO_PREPROCESS_ENABLED=false`). 켜면 STT 입력 직전에만 전처리하고, 결과 임시 wav 파일은 STT 가 끝나면 지운다. 원본 업로드 파일은 수정하지 않는다.
- 필터 체인(신경망 필터 없음): `highpass=f=80, lowpass=f=8000, afftdn, dynaudnorm`, 출력 16kHz 모노 PCM 16bit wav, 덮어쓰기. 명령은 인자 목록으로 실행한다(shell 미사용, 경로의 공백·한글 가능).
- 실행 파일 위치: `backend/bin/ffmpeg.exe`(또는 `ffmpeg`)가 있고 `FFMPEG_PATH` 가 기본값이면 번들 파일을 쓴다. 아니면 `FFMPEG_PATH`(기본 `ffmpeg`, PATH 검색). 찾지 못하면 `FfmpegNotFoundError`.
- 긴 오디오는 ffmpeg 가 스트리밍 처리하므로 분할하지 않는다. 1회 실행 제한은 `FFMPEG_TIMEOUT_SECONDS`(기본 900).

| 이름 | 기본값 | 설명 |
|---|---|---|
| `AUDIO_PREPROCESS_ENABLED` | `false` | 전처리 사용 |
| `AUDIO_PREPROCESS_STRICT` | `false` | true 면 전처리 실패를 처리 실패로 본다 |
| `FFMPEG_PATH` | `ffmpeg` | ffmpeg 실행 파일 |
| `FFMPEG_TIMEOUT_SECONDS` | `900` | 1회 실행 제한(초) |

- 실패 시: 기본은 경고 로그(예외 종류만)를 남기고 원본 파일로 STT 를 진행한다. `AUDIO_PREPROCESS_STRICT=true` 면 작업이 실패(`internal_error`)로 기록된다. 오류 메시지에는 ffmpeg stderr 마지막 몇 줄만 담고 파일 경로는 가린다.
- 워커를 쓰는 환경에서는 워커 프로세스가 전처리하므로 ffmpeg 는 워커가 실행되는 장비에 있어야 한다.

## 하드웨어 감지와 권장 모델 프로파일 (작업 77-1)

```
cd backend
python scripts/detect_hardware.py          # 사람이 읽는 출력
python scripts/detect_hardware.py --json   # JSON 출력
```

- `nvidia-smi`(없으면 선택적 torch)로 GPU 를 읽어 권장 프로파일(`config/model_profiles.json`)과 권장 `.env` 키 값을 **출력만** 한다. 파일을 쓰지 않고 `.env` 를 바꾸지 않으니 확인 후 직접 반영한다.
- 다중 GPU 는 총 VRAM 이 가장 큰 GPU 만 대상으로 한다. 여유 VRAM 이 총량의 `HARDWARE_MIN_FREE_RATIO`(기본 0.7) 미만이면 한 단계 낮은 프로파일을 권한다.
- `LLM_PROFILE=gpu_12gb` 처럼 id 로 고정하면 감지하지 않는다(`--profile ID` 로도 지정 가능). 프로파일 값은 초안이며 실측으로 확정한다.
- 출력에는 선택된 프로파일의 LLM 주력 모델·옵션과 후보 모델이 함께 표시된다. 후보는 `[자유 라이선스]` 와 `[조건부 — 고객 요청 시에만]` 으로 구분되며 모두 초안(시험 비교 대상)이다.
- 현재는 도구 출력만 제공하며 처리 엔진 선택에는 반영되지 않는다.

## 라이선스(사용 허가) 발급·확인 (작업 80-1)

- 설계·형식·상태 규칙은 `docs/v2-ledger-state-design-draft.md` D-30. 라이선스는 사용 허가만 담고 기능 플래그는 없다.
- **발급(우리 쪽)**: 개인키는 저장소·`backend/data`·`.env` 에 두지 않는다. `python scripts/issue_license.py --private-key <개인키.pem> --licensee "고객사" --max-accounts 50 --expires 2027-12-31 --out <출력.lic> [--fingerprint <해시>]`.
- **고객 장비**: `python scripts/show_fingerprint.py` 로 장비 해시를 확인해 전달(바인딩이 필요할 때만). 받은 `.lic` 를 `LICENSE_PATH`(기본 `backend/data/license.lic`)에 두고 공개키를 `LICENSE_PUBLIC_KEY_PATH`(기본 `backend/config/license_public.pem`)에 둔다.
- **확인**: `python scripts/check_license.py [--json]`(읽기만, 종료 코드 0 = valid/expiring_soon). 화면·연동용은 `GET /api/system/license`(중간관리자 이상).
- 만료 시 읽기 전용 적용, 허용 계정 수 차단은 아직 없다(초과·만료는 표시만).

## 핵심 모듈 Cython 선별 빌드 (작업 80-1 파트 B)

- 대상은 3개뿐이다(`backend/setup_cython.py` 의 `TARGET_MODULES` 한 곳): `app/pipeline/prompts.py`(프롬프트 템플릿 엔진), `app/licensing/verifier.py`(라이선스 검증), `app/pipeline/chunked_extract.py`(의사결정·액션아이템 추출 로직). 파서 `json_parser.py` 는 대상이 아니다(추출 로직 모듈이 핵심 판정 규칙을 담고 있어 이쪽을 선택). 전체 프로젝트 컴파일은 하지 않는다(동적 import 충돌 방지).
- 빌드: `cd backend && python setup_cython.py --list`(대상 출력) / `python setup_cython.py --out build/cython`. 원본 `.py` 는 수정·삭제하지 않는다. Cython 과 C 컴파일러가 필요하며 없으면 오류 코드 3(Cython 없음)·4(컴파일 실패)로 끝난다.
- **투명 import**: 같은 폴더에 확장 모듈(`.pyd`/`.so`)과 `.py` 가 함께 있으면 확장 모듈이 먼저 import 된다. 산출물이 없으면 `.py` 가 import 된다. FastAPI·worker 코드는 어느 쪽이든 바뀌지 않는다(컴파일 없이도 동일 동작).
- **배포 패키지 구성**: ① backend 소스를 복사하고 ② 대상 3개의 `.py` 를 지운 뒤 ③ 산출물을 같은 상대 경로(`app/pipeline/`, `app/licensing/`)에 둔다. 나머지 모듈은 `.py` 그대로다.
- **주의**: 산출물은 플랫폼·파이썬 버전별이다(Windows `.pyd`, Linux `.so`, 파일명에 `cp311-win_amd64` 같은 태그). 배포 대상 OS·파이썬 버전마다 그 환경에서 빌드한다. 컴파일된 모듈은 소스 줄 단위 디버깅·트레이스백 줄 번호가 제한된다(문제 분석은 컴파일 없는 `.py` 로 재현). 컴파일은 읽기 난이도를 높일 뿐 우회가 불가능한 보호는 아니다.
- **라이선스 키와의 관계**: 배포 패키지에는 공개키(`LICENSE_PUBLIC_KEY_PATH`)만 포함한다. 개인키는 포함하지 않고 발급 담당 장비에만 둔다.
- 대상 모듈은 동적 import·`exec`·`eval`·전역 상태 변경·함수 안 import 를 쓰지 않는다(`tests/test_cython_build.py` 정적 점검). 대상 모듈에 새 코드를 넣을 때 이 규칙을 지킨다.

## 로컬 LLM(OpenAI 호환) 연결 설정과 확인 (작업 81-1)

`backend/.env` 에 직접 쓴다(값을 출력·커밋하지 않는다). 아래 주소는 자리표시자다.

```
LLM_PROVIDER=local
LOCAL_LLM_BASE_URL=http://<로컬-LLM-서버-주소>:<포트>/v1
LOCAL_LLM_MODEL=<서버에 올린 모델 이름>
LOCAL_LLM_API_KEY=not-needed
```

- 확인: `cd backend && python scripts/check_llm.py`(`/models` 만 호출, 회의 내용 전송 없음). 정상이면 연결·인증과 모델 존재 여부가 나온다. 오류는 일시(transient)/영구(permanent)로 구분해 표시한다.
- 기본은 루프백·사설망 주소만 허용한다. 개발 중 외부 터널 주소를 쓰려면 `LOCAL_LLM_ALLOW_EXTERNAL_HOST=true` 가 필요하며, **이 경우 전사 내용이 그 주소(제3자 서비스일 수 있음)로 전송**된다. 실제 회의 자료로는 쓰지 말고 개발 확인용으로만 쓰며, 끝나면 `false` 로 되돌린다. 시작 때 경고 로그가 남는다.
- 호스트 판정은 DNS 조회 없이 문자열·IP 기준이다(한계는 설계 문서 D-31).
- 설정 변경 후에는 서버(와 워커)를 재시작해야 반영된다.

## 최초 시스템 관리자 지정 (작업 82-1)

시스템 관리자는 회의록 내용을 볼 수 없는 전용 계정이다(사용권한 staff). 최초 1명은 서버에서 도구로 지정한다(이후는 관리 API).

```
cd backend
alembic upgrade head        # 먼저 DB 를 최신으로(운영 DB 는 백업 후)
python scripts/create_system_admin.py --tenant "고객사" --login-id sysadmin --create --name "시스템 관리자" --email a@example.com
python scripts/create_system_admin.py --tenant "고객사" --login-id sysadmin --create --name "시스템 관리자" --email a@example.com --apply
```

- 첫 명령은 dry-run(저장 안 함), `--apply` 를 붙여야 저장한다. 비밀번호는 `--apply` 때 터미널에서 직접 입력한다(인자·로그·출력에 남지 않음, 최소 `PASSWORD_MIN_LENGTH` 자).
- 기존 담당자(staff) 계정을 지정하려면 `--create` 와 이름·이메일 없이 `--login-id` 만 준다. 상위 권한 계정은 지정할 수 없다.
- 외부 연동 허용은 기본 꺼짐이다. 협의된 프로젝트만 시스템 관리자가 `PUT /api/admin/settings/external_llm_enabled` 로 켠다(`.env` 의 `EXTERNAL_POLICY_ENFORCED=true` 일 때 처리에 적용).

