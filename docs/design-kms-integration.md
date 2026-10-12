# KMS 연동 설계 (작업 83-1, 선반영 단계)

AI DOTS 와 지식 관리 시스템(KMS)을 연결하는 방침과, 이번에 먼저 반영한 설정 키·호출 인터페이스·계약 스키마를 정리한다. **실제 HTTP 호출·화면·API 라우터·outbox 는 아직 없다.**

## 1. 확정된 방침(바꾸지 않는다)
1. **자료는 복제하지 않고 연합 조회를 우선한다.** 필요할 때 원본 시스템에 묻고, 결과만 카드로 보여 준다.
2. **권한 판단은 자료의 원본 시스템 한 곳에서만 한다.** 응답에는 권한 정보가 아니라 "원본 시스템이 이미 권한을 통과시킨 결과"만 담는다. 이 사용자가 열람할 수 없는 자료는 존재 여부도 응답에 나타나지 않는다.
3. **시스템 간 호출은 서비스 토큰으로 하고, 사용자는 `external_sub` 로만 알린다(대리 조회).** 사용자 토큰은 전달하지 않는다. 이름·이메일도 보내지 않는다.
4. **외부 AI(LLM) 허용과 완전히 분리한다.** `external_llm_enabled` 와 지식 연동 설정은 서로의 값에 영향이 없다.

## 2. 설정(시스템 관리자만 변경, 사유 필수, 변경 이력 기록)
| 키 | 종류 | 기본 | 비고 |
|---|---|---|---|
| `knowledge_integration_enabled` | 참·거짓 | 거짓 | 연동 스위치. 꺼져 있으면 어떤 경로로도 외부 호출이 없다 |
| `knowledge_api_endpoint` | 문자열 | 빈 값 | 사설망·루프백 주소만 허용(로컬 LLM 주소 검사와 같은 함수 `check_base_url`). 위반·형식 오류는 422. 이력에는 주소 전체를 남기지 않는다 |

- 저장: `system_settings`(행이 없으면 `.env` 초기값 `KNOWLEDGE_INTEGRATION_ENABLED`·`KNOWLEDGE_API_ENDPOINT`). 마이그레이션 `e6a0c4b9d135`(허용 키 CHECK 확장 + `value_text` 칸 추가, 기존 마이그레이션은 수정하지 않음, downgrade 는 새 키 행이 있으면 거부).
- 변경은 기존 `PUT /api/admin/settings/{key}`(시스템 관리자 전용). 조회 `GET /api/admin/settings` 는 기존 `items`(두 키)를 그대로 두고 지식 연동 키를 추가 칸 `knowledge` 로 따로 내려준다(기존 화면 호환).
- `.env`: `KNOWLEDGE_SERVICE_TOKEN`(비어 있음, 값은 어디에도 출력·저장하지 않음), `KNOWLEDGE_TIMEOUT_SECONDS`(기본 3).

## 3. 호출 인터페이스(`backend/app/integrations/knowledge.py`)
- `KnowledgeClient.search_references(query, external_sub, limit=3) -> ReferenceSearchResult`(`cards`: `ReferenceCard` 목록 = title·date·summary(1줄)·url·source, `status`·`message`).
- `NullKnowledgeClient`(기본): 스위치가 꺼져 있으면 "연동 꺼짐" 결과만 돌려주고 어떤 호출도 하지 않는다. 켜져 있어도 실제 구현이 없는 동안은 "구현 전" 결과(호출 없음). `FakeKnowledgeClient`: 테스트용 고정 응답.
- 검색어는 **사용자가 버튼을 눌렀을 때 입력·선택한 값만** 받는다. 안건·업무·회의록 내용이 자동으로 이 인터페이스에 전달되는 코드는 없다(`app/integrations` 밖에서 import 하는 곳이 없음을 테스트로 고정).
- 입력 검증: 검색어 1~200자, `external_sub` 필수, 건수 1~10. 위반은 호출 전에 거절한다.

## 4. 계약 스키마(`docs/contracts/`)
| 파일 | 방향 | 용도 |
|---|---|---|
| `meeting-search-v1.json` | KMS → AI DOTS | 회의록 검색. 응답 카드 `source="aidots"` |
| `knowledge-reference-v1.json` | AI DOTS → KMS | 관련 규정 조회. 요청·응답 형식은 위와 대칭, `source="kms"` |

- 요청: `schema_version`("1")·`query`·`subject.external_sub`·`limit`(기본 3, 1~10). 응답 카드: `id`·`title`·`date`(YYYY-MM-DD)·`summary`·`url`·`source`. 알 수 없는 칸(예: `can_read`·`permissions`)은 거부한다.
- 예시: `examples/<계약>.valid.json`, `examples/<계약>.invalid.json`. 테스트가 같은 파일로 검증한다.
- 검증 라이브러리(`jsonschema`)가 설치돼 있지 않아 pydantic 모델(`app/integrations/contracts.py`)을 원본으로 두고 스키마 파일의 속성·필수·상수·제약이 모델과 같은지 검사한다.

## 5. 운영 규칙(구현 때 지킨다)
- **사용자별 메모리 캐시 1~3분**: 같은 사용자·같은 검색어의 결과만 재사용. 사용자 간 공유 금지, 디스크 저장 금지, 서버 재시작 때 사라져도 된다.
- **문서 결과와 회의 결과를 묶음으로 분리**해 보여 준다(출처가 다름).
- **호출 제한 약 3초**(`KNOWLEDGE_TIMEOUT_SECONDS`). 시간 초과·오류 때 화면은 "지식 조회를 하지 못했습니다"만 보이고 본 기능은 막지 않는다.
- **"관련 규정 보기" 버튼을 눌렀을 때만 조회**한다(자동 조회·미리 불러오기 없음).

## 6. 복제는 선택 기능(보류)
연합 조회로 충분하다는 것이 기본이고, 복제는 성능·가용성 때문에 필요해질 때만 고려한다. 복제를 하게 되면 다음이 **필수 요건**이다.
1. 자료마다 **revision**(변경될 때마다 증가)을 두어 오래된 사본을 덮어쓰지 않는다.
2. **중복 방지 키**(원본 시스템 + 원본 id + revision)로 재전달에도 한 번만 반영한다.
3. **철회 이벤트**: 원본에서 삭제·열람 불가가 되면 사본도 즉시 없앤다(권한 변경 반영).
4. **draft → 확정 전환 이벤트**: 확정 전 자료와 확정 자료를 구분하고 전환을 알린다.
5. **events 표 outbox**: 변경과 같은 트랜잭션으로 발송 대기 이벤트를 기록하고 별도 워커가 전달한다(기존 사건 원장 사용, 새 표 없이).

## 7. 아직 하지 않은 것
- 실제 HTTP 호출 구현(서비스 토큰 헤더, 타임아웃 적용, 오류 분류), 사용자별 캐시 구현
- 연동 화면("관련 규정 보기" 버튼·카드 목록), 연동용 API 라우터(KMS 가 호출하는 회의록 검색 엔드포인트)
- 서비스 토큰 발급·교체 절차, `external_sub` 매핑(통합 로그인 설계 `docs/design-sso-oidc.md` 와 함께 결정)
- 복제와 outbox, 관리 화면의 지식 연동 설정 항목
