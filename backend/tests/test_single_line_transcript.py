"""한 줄로 이어진 전사문(작업 B-1a): STT 가 줄바꿈 없이 "[hh:mm:ss] 화자N: 말 [hh:mm:ss] 화자M: 말 ..." 로 저장한 경우에도
참석자(화자 표기)·담당자 자동 채움·청크 분할의 화자가 줄 구분 전사문과 같게 읽혀야 한다. 가짜 LLM 만 쓴다(외부 호출 없음)."""
import json
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app.models import ActionItem, Event, Transcript
from app.pipeline.agenda_chunker import parse_utterances
from app.pipeline.chunked_extract import ChunkOptions, extract_chunked
from app.pipeline.llm_client import FakeLlmClient
from app.services import speakers as speakers_service
from tests.test_meeting_queries import make_meeting
from tests.test_speakers import call, put, team  # noqa: F401  (team 은 픽스처)
from tests.test_upload_processing import add_account, auth, env  # noqa: F401  (env 는 픽스처)

SINGLE = (
    "[00:00:00] 화자1: 오늘 회의를 시작하겠습니다. "
    "[00:00:10] 화자2: 개선안을 다음 주 금요일까지 정리해 주세요. [참고] 자료는 공유 폴더에 있습니다. "
    "[00:00:20] 화자3: 네, 제가 다음 주 금요일까지 정리하겠습니다. "
    "[00:00:30] 화자4: 시안은 [00:12] 쯤 확정하겠습니다. 시간: 오후입니다. "
    "[00:00:40] 화자1: 알겠습니다."
)
KST = timezone(timedelta(hours=9))
HELD = datetime(2026, 10, 12, 14, 0, tzinfo=KST)
OPTS = ChunkOptions(max_chars=6000, overlap_chars=500, repair_max_chars=4000, timeout=30, retry_max=1, minutes_max_chars=4000)


def make_single_line_meeting(env, registrant, extracted: list[str]) -> tuple[int, list[int]]:
    f = env["factory"]
    meeting_id = make_meeting(f, registrant, transcript=SINGLE, items=[{"title": f"업무{i}"} for i in range(len(extracted))])
    with f() as s:
        ids = list(s.scalars(select(ActionItem.id).where(ActionItem.meeting_id == meeting_id).order_by(ActionItem.id)))
        for item_id, name in zip(ids, extracted):  # 처리 단계처럼 item.created 사건에 추출 담당자 글자를 남긴다
            s.add(Event(
                tenant_id=s.get(ActionItem, item_id).tenant_id, entity_type="action_item", entity_id=item_id,
                event_type="item.created", payload={"assignee_name": name},
            ))
        s.commit()
    return meeting_id, ids


# ---------------- (a) 참석자(화자 표기) ----------------
def test_transcript_labels_read_every_speaker_from_a_single_line():
    class T:  # Transcript 와 같은 모양(segments 없음)
        segments = None
        full_text = SINGLE

    assert speakers_service.transcript_labels(T()) == ["화자1", "화자2", "화자3", "화자4"]


def test_line_based_transcript_is_read_exactly_as_before():
    class T:
        segments = None
        full_text = "[00:00:03] 화자1: 안녕\n[00:00:10] 화자2: 네\n[00:00:20] 화자1: 다시"

    assert speakers_service.transcript_labels(T()) == ["화자1", "화자2"]


def test_speakers_api_lists_all_labels_for_single_line_transcript(env, team):
    meeting_id, _ = make_single_line_meeting(env, team["mgr"], [])
    body = call(env, "GET", team["mgr"], f"/api/meetings/{meeting_id}/speakers").json()
    assert body["labels"] == ["화자1", "화자2", "화자3", "화자4"]  # 이전에는 ["화자1"] 만 나왔다


def test_display_text_replaces_only_label_positions_in_single_line():
    views = [speakers_service.SpeakerView(label="화자4", account_id=9, account_name="최임원", display_name="")]
    shown = speakers_service.display_text(SINGLE, views)
    assert "[00:00:30] 최임원: 시안은" in shown
    assert "[00:12] 쯤" in shown and "시간: 오후" in shown  # 말 속 시각·콜론은 건드리지 않는다


# ---------------- (b) 담당자 ----------------
def test_mapping_every_speaker_fills_assignees_from_extracted_labels(env, team):
    meeting_id, items = make_single_line_meeting(env, team["mgr"], ["화자3", "화자4", "화자2"])
    res = put(env, team["mgr"], meeting_id, [
        {"label": "화자3", "accountId": team["staff"].id},
        {"label": "화자4", "accountId": team["lee"].id},
    ])
    assert res.status_code == 200
    assert res.json()["labels"] == ["화자1", "화자2", "화자3", "화자4"]
    assert res.json()["autoAssignedItemIds"] == items[:2]  # 화자2 는 계정 매핑이 없어 채우지 않는다(화자 표기 유지)
    with env["factory"]() as s:
        assignees = [s.get(ActionItem, i).assignee_id for i in items]
    assert assignees == [team["staff"].id, team["lee"].id, None]


# ---------------- 청크 분할: 인용문 화자 식별(근거 있음/없음) ----------------
def test_chunker_reads_each_speaker_and_timestamp_from_single_line():
    utterances = parse_utterances(SINGLE)
    assert [u.speaker for u in utterances] == ["화자1", "화자2", "화자3", "화자4", "화자1"]
    assert [u.start_sec for u in utterances] == [0.0, 10.0, 20.0, 30.0, 40.0]
    assert "[00:12] 쯤" in utterances[3].text  # 말 속 시각 표기는 발언을 가르지 않는다(뒤에 라벨 콜론이 없다)


def reply(items):
    return json.dumps({"items": items, "minutes": {}}, ensure_ascii=False)


def item(task, assignee="", quote_speaker="", ts="", text=""):
    return {"task": task, "assignee": assignee, "due_date": "", "quote": {"speaker": quote_speaker, "timestamp": ts, "text": text}}


def test_assignee_from_quote_evidence_is_the_real_speaker_not_the_first_label():
    # 담당자를 비워 보낸 업무: 원문 근거(인용문)가 있으면 그 발언의 화자(화자3)로 식별한다. 이전에는 전체가 한 발언이라 화자1 로 쏠렸다.
    client = FakeLlmClient(responses=[reply([item("개선안 정리", "", "", "00:00:20", "네, 제가 다음 주 금요일까지 정리하겠습니다.")])])
    result = extract_chunked(SINGLE, HELD, client, OPTS)
    [chunked] = result.items
    assert chunked.candidate.assignee == "화자3" and chunked.evidence_verified is True


def test_assignee_label_is_kept_when_evidence_is_missing_and_never_blank_when_model_gave_it():
    # 근거 인용문이 없어도 모델이 준 화자N 은 유지한다(지우거나 다른 화자로 바꾸지 않는다)
    client = FakeLlmClient(responses=[reply([item("시안 확정", "화자4", "", "", "")])])
    result = extract_chunked(SINGLE, HELD, client, OPTS)
    [chunked] = result.items
    assert chunked.candidate.assignee == "화자4" and chunked.evidence_verified is False


def test_no_evidence_and_no_model_assignee_stays_unidentified_not_guessed():
    # 정책: 원문 근거가 있을 때만 식별. 근거도 담당자도 없으면 화자를 추측하지 않는다(빈 값 = 보완 필요로 표시)
    client = FakeLlmClient(responses=[reply([item("자료 정리", "", "", "", "")])])
    result = extract_chunked(SINGLE, HELD, client, OPTS)
    [chunked] = result.items
    assert chunked.candidate.assignee == ""
