# 로컬 STT (STT_PROVIDER=local, 작업 L-1)

본체 구성: 로컬 GPU(최소 RTX 3070 8GB)에서 **STT → VRAM 반환 → 로컬 LLM** 순차 실행. 원격 5090 은 개발용, Gemini 는 GPU 가 없는 고객용 대체 옵션이다. 자동 전환·자동 폴백은 없다.
이번 단계는 `STT_PROVIDER` 기본값(fake)을 바꾸지 않는다. `local` 은 설정했을 때만 동작한다(기본값 변경은 L-3).

## 설정(.env, 값은 `backend/.env.example` 참고)
| 이름 | 허용값 | 기본 |
|---|---|---|
| STT_PROVIDER | fake / gemini / local | fake |
| STT_LOCAL_MODEL_SIZE | 모델 이름(large-v3, medium, small …) | large-v3 |
| STT_LOCAL_DEVICE | cuda / cpu | cuda |
| STT_LOCAL_COMPUTE_TYPE | float16 / int8_float16 / int8 | float16 |
| STT_LOCAL_LANGUAGE | 언어 코드 | ko |
| STT_LOCAL_BEAM_SIZE | 1~20 | 5 |
| STT_LOCAL_DEFAULT_SPEAKER | 화자 라벨 | 화자1 |
| STT_LOCAL_MODEL_DIR | (선택) 미리 받은 모델 폴더. 지정하면 `local_files_only` 오프라인 로드 | 빈 값 |

- 잘못된 값, `STT_PROVIDER=local` 인데 `PROCESSING_MODE` 가 queue 가 아닌 경우(요청 스레드에서 직접 실행 금지), CPU 에 float16 지정은 **시작 때 오류**로 거부한다.
- 오류 코드: `ERR-STT-NO-ENGINE`(faster-whisper 미설치), `ERR-STT-NO-GPU`(cuda 인데 GPU 없음), `ERR-STT-MODEL-LOAD`(모델 로드 실패), `ERR-STT-FILE-MISSING`, `ERR-STT-FAILED`(전사 중 오류). 조용한 CPU 전환은 하지 않는다.
- `STT_PROVIDER=local` 이면 추출기(`LLM_PROVIDER=auto`)도 local 이 되므로 `LOCAL_LLM_BASE_URL`·`LOCAL_LLM_MODEL` 이 필요하다.

## VRAM 프로필(초기 권장값, **실측 후 확정**)
| VRAM | 모델 | compute_type | 비고 |
|---|---|---|---|
| 8GB | large-v3 | int8_float16 | 초기값. STT 종료 후 반드시 해제한 뒤 LLM 로드 |
| 10GB | large-v3 | float16 | |
| 12GB | large-v3 | float16 | 여유 있음 |
| 16GB | large-v3 | float16 | beam_size 상향 가능 |

## VRAM 격리
`LocalStt.transcribe()` 는 성공·예외 모두 `finally` 에서 모델 참조 제거 → `gc.collect()` → `torch.cuda.empty_cache()`(torch 없으면 건너뜀)를 끝낸 뒤에야 반환한다. 처리 파이프라인은 그 반환 뒤에 추출 단계를 시작하므로 STT 와 LLM 이 동시에 올라가지 않는다.

## 출력 형식
한 줄 한 발화 `[HH:MM:SS] 화자1: 내용`(줄 내 개행 제거, 빈 세그먼트 제거, 모두 비면 `[NO_SPEECH]`). 화자 분리는 하지 않는다(모든 발화 기본 화자). 기존 파서·`speakers.py` 가 그대로 받는다.

## 의존성·라이선스
| 패키지 | 라이선스 | 비고 |
|---|---|---|
| faster-whisper | MIT | `backend/requirements.txt` 에 추가. 내부적으로 CTranslate2(MIT)·onnxruntime·tokenizers 를 끌어온다 |
| torch | BSD-3 | **선택**. 없어도 동작(CUDA 캐시 비우기만 건너뜀) |
| Whisper 모델 가중치(large-v3 등) | MIT(OpenAI) | 처음 사용할 때 내려받음. 오프라인은 `STT_LOCAL_MODEL_DIR` |

pyannote·whisperx·scipy·rapidfuzz 는 사용하지 않는다.

## 실모델 확인(승인 후 1회)
`python backend/scripts/check_local_stt.py <짧은 음성>` — 모델·소요·해제 전후 VRAM 만 출력한다.
