# 로컬 LLM — Ollama 어댑터 (LLM_PROVIDER=ollama, 작업 L-2)

본체 구성: 로컬 GPU 에서 STT → VRAM 반환 → 로컬 LLM 순차 실행(L-1 문서 `local-stt.md` 참고). 이번 단계는 **어댑터 구현만**이다.
`LLM_PROVIDER` 기본값(auto)과 기존 원격·로컬(OpenAI 호환, llama_cpp) 경로는 그대로이고, 자동 폴백은 없다.
**사양별 프로필 값(num_ctx·num_gpu·keep_alive 등)은 L-3 실측 후 확정한다.**

## 왜 네이티브 API 인가
OpenAI 호환(`/v1`)은 `num_ctx` 를 요청에서 정할 수 없어 Ollama 기본 맥락(작음)으로 조용히 잘린다. 어댑터는 `POST /api/chat`(`stream=false`)의 `options` 에 `num_ctx`·`num_predict`·`temperature` 를 항상 넣고, JSON 모드는 `format=json` 으로 보낸다.

## 설정(.env, 값은 `backend/.env.example` 참고)
| 이름 | 기본 | 설명 |
|---|---|---|
| LLM_PROVIDER | auto | `ollama` 로 두면 이 어댑터 사용 |
| OLLAMA_BASE_URL | http://127.0.0.1:11434 | 서버 주소. 루프백·사설망만 허용(그 밖은 LOCAL_LLM_ALLOW_EXTERNAL_HOST 규칙) |
| OLLAMA_MODEL | (없음) | 필수(ollama 선택 시) |
| OLLAMA_NUM_CTX | 8192 | 맥락 길이. 구간 글자 수는 이 값의 90% 안에서 자동 계산 |
| OLLAMA_NUM_PREDICT | 4096 | 최대 생성 토큰. NUM_CTX 보다 작아야 함(맥락의 1/4 이하로 다시 줄여 예약) |
| OLLAMA_NUM_GPU | (빈 값) | 비우면 Ollama 자동 배치. 지정할 때만 `options.num_gpu` 전달 |
| OLLAMA_KEEP_ALIVE | 5m | 모델 유지 시간 |
| OLLAMA_TIMEOUT_SEC | 300 | 요청 1건 제한 시간 |
| OLLAMA_UNLOAD_AFTER_EXTRACT | true | 추출 종료 때 `keep_alive=0` 으로 모델을 내림 |

잘못된 값(정수 아님, NUM_PREDICT ≥ NUM_CTX, 모델 이름 없음, 주소 형식 오류)은 시작 때 거부한다.

## 동작
- 추출 전 `GET /api/tags` 로 서버 연결과 모델 존재를 확인한다(회의 내용은 보내지 않음).
- 안건 분할·사전 예산(90%)·잘림 감지·오류 본문 마스킹(500자)·제한 재시도는 기존 추출 엔진 부품을 그대로 쓴다.
- 잘림: `done_reason=length` 또는 `eval_count ≥ num_predict` → 출력 잘림, `prompt_eval_count ≥ num_ctx×0.98` → 맥락 한계.
- 추출 종료(성공·예외 모두) 때 `OLLAMA_UNLOAD_AFTER_EXTRACT=true` 이면 언로드 요청을 보내고, 그 응답 뒤에야 반환한다. 워커는 작업을 직렬 처리하므로 다음 작업(STT)은 VRAM 반환 뒤에 시작된다.

## 오류 코드
`ERR-LLM-NO-SERVER`(연결 실패) · `ERR-LLM-NO-MODEL`(모델 없음) · `ERR-LLM-TIMEOUT` · `ERR-LLM-TRUNCATED`(잘림·맥락 한계) · `ERR-LLM-FAILED`(그 밖). 메시지·상세에 주소·키를 넣지 않는다.

## 컨텍스트·VRAM 주의
- `num_ctx` 를 키우면 KV 캐시만큼 VRAM 이 늘어난다. 8GB 에서 STT 모델과 겹치지 않게 순차 실행하며, 큰 모델·큰 맥락 조합은 L-3 실측 전에 확정하지 않는다.
- 모델이 VRAM 에 다 안 올라가면 Ollama 가 CPU 로 나눠 올려 매우 느려진다. 이때만 `OLLAMA_NUM_GPU` 로 조정한다.
- 연결 확인: `python backend/scripts/check_ollama.py`(tags 조회만).
