"""추출 프롬프트 템플릿 엔진(작업 75-2). 현행 Gemini 경로(pipeline/extractor.py)는 이 모듈을 쓰지 않고 그대로 둔다.

- BASE_TEMPLATE 은 현행 추출 프롬프트와 글자 단위로 같다(골든 테스트). render_base_prompt 의 결과가 extractor.build_prompt 와 같다.
- 청크(안건) 단위 추론용 render_chunk_prompt 는 같은 본문에 구간 정보·구간 전용 지시·응답 형식 지시(```json 블록과 스키마 설명)를 더한다.
- 회의 유형별 분기(작업 81-1): meeting_type 이 project·external 이면 관점 문단(META_TYPE_NOTES)을 더하고, 그 밖의 유형은 같은 텍스트다.
- 순수 함수만 쓴다(전역 상태 변경·동적 import 없음).
"""
from datetime import datetime

from app.pipeline.dates import WEEKDAYS_KO

# 현행 extractor.PROMPT_TEMPLATE 과 같은 텍스트. {started_at}·{weekday}·{transcript} 자리만 바뀐다(다른 중괄호를 넣지 않는다).
BASE_TEMPLATE = """다음은 한국어 회의 전사문입니다. 액션아이템을 추출하세요.

회의 일시: {started_at} ({weekday}요일)

[1. 무엇을 잡는가]
전사문 전체를 처음부터 끝까지 훑어, 아래 기준에 맞는 것을 빠짐없이 모두 찾습니다. 앞부분에서 몇 개 찾았다고 멈추지 않습니다.
포함:
- 지시-수락: 누군가 일을 지시(요청)하고 다른 사람이 수락한 것.
  제안·권유형 요청도 지시로 봅니다. 예: "~하시는 게 좋을 것 같아요", "~볼 수 있으면 좋겠네요", "~해 주세요".
  상대가 "네", "알겠습니다", "네, 그렇게 하겠습니다"로 받으면 지시-수락으로 포함합니다.
- 확정 약속: 실무 보고 중 구체적인 기한 또는 대상이 있는 약속.
  예: "~하겠습니다", "~할 예정입니다", "~까지 하겠습니다", "오늘 다시 연락해 보겠습니다".
  시키지 않았어도 본인이 할 일과 기한(또는 대상)을 함께 말한 자발적 약속도 포함합니다.
  예: "오늘 마무리될 것 같습니다", "이번 주 중에 하겠습니다", "~까지 등록해야 됩니다".
- 한 발화 안에 약속·업무가 여러 개면 각각 별도 항목으로 뽑습니다. 긴 보고 발화 하나에서 항목 하나만 뽑고 끝내지 않습니다.
  업체·프로젝트·고객명이 다르면 다른 항목입니다.
제외:
- 이미 끝난 일의 보고 ("보냈습니다", "처리했습니다", "정리 완료했고요").
- 기한도 대상도 없는 막연한 의견 ("생각해 보겠습니다", "검토해 봐야겠네요").
- 잡담 (점심 메뉴, 인사, 날씨 등).
- 일반론 설명이나 강의.

[2. 담당자(assignee) 표기]
화자 번호보다 실제 호칭을 우선합니다. 다음 순서를 따릅니다.
a) 먼저 전사문 전체를 훑어 "화자 표기 → 이름/직함" 대응을 정합니다. 근거로 인정하는 경우는 다음뿐입니다.
   - 누군가 이름·직함으로 부르고("권 부장님", "한 팀장") 바로 이어서 특정 화자가 답하는 경우
   - 화자 본인이 이름·직함을 밝히는 경우
   - 다른 사람이 "○○ 씨가 하고 있고"처럼 특정인을 지목하고, 그 사람이 이어서 말하는 경우
   근거가 한 번뿐이고 답한 사람이 여럿이라 누구인지 불분명하면 대응을 만들지 않습니다.
b) 대응이 있으면 assignee 에 그 호칭을 씁니다. 예: "권 부장", "한 팀장", "박다솜".
   존칭("님")은 빼고, 성과 직함이 함께 나오면 함께 씁니다.
   성 없이 직함만 알 수 있고 같은 직함이 여러 명일 수 있으면 호칭으로 쓰지 말고 c)를 따릅니다.
c) 이름을 알 수 없으면 전사문의 화자 표기(예: "화자5")를 그대로 씁니다. 이름을 지어내지 않습니다.
d) 누가 맡는지 자체가 불분명하면(여러 명이 "네"라고 답한 경우 등) assignee 를 빈 문자열("")로 둡니다.
e) 같은 화자는 회의 전체에서 항상 같은 표기를 씁니다.
- 지시-수락이면 수락한 사람, 확정 약속이면 약속한 사람이 담당자입니다.

[3. 작업(task) 표기]
- 해야 할 일을 한 문장으로 씁니다.
- 업체·고객명은 전사문 표기 그대로 씁니다. 비슷한 이름(예: "EZ"와 "이지트")을 임의로 합치거나 바꾸지 않습니다.

[4. 마감일(due_date)]
- "오늘", "내일", "이번 주 목요일", "다음 주 금요일" 같은 상대 표현은 회의 일시를 기준으로 계산해 YYYY-MM-DD 로 씁니다.
  "오늘"은 회의일, "이번 주"는 회의일이 속한 월요일~일요일, "다음 주"는 그다음 월요일~일요일입니다.
- 날짜 하나로 정할 근거가 없으면(언급 없음, 요일 없이 "다음 주"만 있음 등) null 이 아니라 빈 문자열("")로 둡니다. 날짜를 짐작해 넣지 않습니다.

[5. 근거 인용(quote)]
- 근거가 된 발화를 전사문에서 글자 그대로 인용합니다(speaker, timestamp, text). 고쳐 쓰거나 요약하지 않습니다.
  지시-수락이면 지시 발화, 확정 약속이면 약속한 발화를 인용합니다.
- speaker 와 timestamp 는 전사문 표기 그대로 씁니다(호칭으로 바꾸지 않음).

[6. 마지막 점검]
- 목록을 다 만든 뒤 전사문을 다시 처음부터 훑어 "~하겠습니다", "~할 예정", "~까지", "알겠습니다", "네, 그렇게" 같은 표현이 붙은 문장을 찾습니다.
- 그중 [1]의 포함 기준에 맞는데 목록에 빠진 것만 추가합니다. 이미 뽑은 항목과 겹치는 것은 추가하지 않습니다.
- 추가하는 항목도 [1]의 제외 기준과 [2]~[5]의 담당자·작업·마감일·인용 규칙을 그대로 따릅니다.

[7. 빈 결과]
- 액션아이템이 없으면 items 를 빈 배열로 둡니다.

전사문:
{transcript}"""

_TRANSCRIPT_MARK = "\n전사문:\n{transcript}"

SYSTEM_PROMPT = "당신은 한국어 회의 전사문을 분석하는 도우미입니다. 지시한 JSON 형식만 출력하고 다른 설명은 쓰지 않습니다."

CHUNK_INSTRUCTIONS = """[구간 추출 지시]
- 이 구간의 발언만 근거로 삼습니다. 다른 구간의 내용을 추측하지 않습니다.
- 근거 인용문(quote.text)은 이 구간 전사문에서 글자 그대로 복사합니다. 고쳐 쓰거나 요약하지 않습니다.
- 구간 앞부분이 이전 구간과 겹칠 수 있습니다. 겹친 발언에서도 규칙에 맞는 업무는 뽑습니다(중복은 서버가 합칩니다).
- 전사문의 각 줄 앞에 붙은 #번호가 줄 번호(Line ID)입니다. 근거 인용(quote)에는 그 줄 번호를 숫자만 line_id 에 쓰고, quote.text 에는 #번호·시각·화자 표기를 넣지 않고 발화 내용만 씁니다."""

RESPONSE_FORMAT = """[응답 형식]
아래 스키마의 JSON 객체 하나만 ```json 코드 블록 안에 출력합니다. 코드 블록 밖에는 아무것도 쓰지 않습니다.
- minutes: 이 구간의 회의록 항목. purpose(회의 목적), discussion(주요 논의사항), decisions(결정사항), risks(리스크), next_agenda(다음 안건). 각 값은 문자열 하나이고 정보가 없으면 빈 문자열. 응답이 잘려도 남도록 items 보다 먼저 씁니다.
- items: 업무 배열(없으면 빈 배열). 각 항목은 task(문자열), assignee(문자열), due_date(YYYY-MM-DD 또는 빈 문자열), quote(speaker, timestamp, text, line_id).
- 값을 모르면 null 이 아니라 빈 문자열("")로 둡니다.

```json
{"minutes": {"purpose": "", "discussion": "", "decisions": "", "risks": "", "next_agenda": ""}, "items": [{"task": "", "assignee": "", "due_date": "", "quote": {"speaker": "", "timestamp": "", "text": "", "line_id": ""}}]}
```"""


# 회의 유형별 관점 지시(로컬 경로 전용). project·external 만 추가 문단이 있고 그 밖의 유형(정기·비정기·기타·미지정)은 추가 문단이 없어
# 기존 프롬프트와 글자 단위로 같다. 5개 항목 이름과 출력 스키마는 바꾸지 않는다.
META_TYPE_NOTES = {
    "project": """[회의 유형 관점: 프로젝트 회의]
- 마일스톤(단계별 목표·일정)과 진행 상황, 액션아이템, 담당자, 기한을 중심으로 봅니다.
- 주요 논의사항(discussion)에는 마일스톤별 진행·지연 내용을, 결정사항(decisions)에는 확정된 일정·담당·기한 변경을 담습니다.
- 담당자와 기한이 함께 언급된 업무를 놓치지 않습니다.""",
    "external": """[회의 유형 관점: 외부 미팅]
- 고객(상대 회사)의 니즈, 견적, 계약 조건, 후속 요청을 중심으로 봅니다.
- 고객의 니즈·견적·계약에 관한 논의와 후속 요청은 주요 논의사항(discussion)과 결정사항(decisions)에 담습니다.
- 우리 쪽이 해야 할 후속 조치(자료 송부, 견적 제출 등)는 업무로 뽑습니다.""",
}


def _weekday(held_at: datetime) -> str:
    return WEEKDAYS_KO[held_at.weekday()]


def render_base_prompt(transcript: str, held_at: datetime) -> str:
    """현행 추출 프롬프트와 같은 결과."""
    return BASE_TEMPLATE.format(started_at=held_at.isoformat(), weekday=_weekday(held_at), transcript=transcript)


def render_chunk_prompt(
    transcript: str,
    held_at: datetime,
    *,
    agenda_title: str = "",
    chunk_index: int = 1,
    chunk_total: int = 1,
    participants: tuple[str, ...] = (),
    meeting_type: str = "",
) -> str:
    """청크(안건 구간) 추론용 프롬프트. chunk_index 는 1부터. meeting_type 이 project·external 이면 관점 문단을 더한다."""
    head = BASE_TEMPLATE.split(_TRANSCRIPT_MARK)[0].format(
        started_at=held_at.isoformat(), weekday=_weekday(held_at), transcript=""
    )
    info: list[str] = []
    if participants:
        info.append("참석자: " + ", ".join(participants))
    chunk_line = f"구간: {chunk_index}/{chunk_total}"
    if agenda_title:
        chunk_line += f" (안건: {agenda_title})"
    info.append(chunk_line)
    parts = [head, "\n".join(info), "", CHUNK_INSTRUCTIONS, ""]
    note = META_TYPE_NOTES.get(meeting_type or "")
    if note:
        parts += [note, ""]  # 유형 관점 문단(project·external 만)
    parts += [RESPONSE_FORMAT, "", "전사문:", transcript]
    return "\n".join(parts)
