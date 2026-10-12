"""처리 단계 표시값(작업 84-1): 상세 응답 processing.stage 는 기존 값(작업 상태·전사문 저장 사건)에서 파생한다."""
import pytest

from app.pipeline.extractor import FakeExtractor
from app.services.processing import process_meeting
from tests.test_transcript_txt import NoStt, post_txt
from tests.test_upload_processing import HELD_AT, M4A, BrokenExtractor, add_account, auth, env, upload  # noqa: F401  (env 는 픽스처)
from tests.test_transcript_xlsx import HEADER, SCRIPT_ROWS, post_xlsx, workbook_bytes


class BrokenStt:
    provider_name = "fake"

    def transcribe(self, path, mime):
        raise RuntimeError("stt boom")

    def release(self):
        pass


@pytest.fixture
def mgr(env):
    return add_account(env["factory"], "고객사A", "mgr", "manager", name="박관리")


def stage_of(env, account, meeting_id):
    body = env["client"].get(f"/api/meetings/{meeting_id}", headers=auth(account)).json()
    return body["processing"]["status"], body["processing"]["stage"], body["processing"]["errorCode"]


def test_queued_job_is_in_convert_stage(env, mgr):
    res = upload(env, mgr)
    assert stage_of(env, mgr, res.json()["meetingId"]) == ("queued", "convert", None)


def test_failure_before_transcript_stops_in_convert_and_after_it_in_extract(env, mgr):
    res = upload(env, mgr)
    assert process_meeting(res.json()["jobId"], session_factory=env["factory"], stt_factory=BrokenStt) == "failed"
    status, stage, code = stage_of(env, mgr, res.json()["meetingId"])
    assert (status, stage) == ("failed", "convert") and code == "internal_error"
    res2 = upload(env, mgr)
    assert process_meeting(res2.json()["jobId"], session_factory=env["factory"], extractor_factory=BrokenExtractor) == "failed"
    assert stage_of(env, mgr, res2.json()["meetingId"])[:2] == ("failed", "extract")


def test_completed_is_done_and_reprocess_after_extract_failure_is_not_marked_extract_early(env, mgr):
    res = post_xlsx(env, mgr, workbook_bytes([HEADER, *SCRIPT_ROWS]))
    mid = res.json()["meetingId"]
    assert process_meeting(res.json()["jobId"], session_factory=env["factory"], stt_factory=NoStt(), extractor_factory=BrokenExtractor) == "failed"
    assert stage_of(env, mgr, mid)[:2] == ("failed", "extract")  # 자료 파일은 변환 단계를 건너뛰고 추출에서 멈춘다
    again = env["client"].post(f"/api/meetings/{mid}/reprocess", headers=auth(mgr))
    assert again.status_code == 202
    # 새 작업이 아직 시작 전이면 이전 작업의 전사문과 상관없이 변환(대기) 단계
    assert stage_of(env, mgr, mid)[:2] == ("queued", "convert")
    assert process_meeting(again.json()["jobId"], session_factory=env["factory"], stt_factory=NoStt(), extractor_factory=FakeExtractor) == "completed"
    assert stage_of(env, mgr, mid) == ("completed", "done", None)


def test_txt_source_follows_the_same_stages(env, mgr):
    res = post_txt(env, mgr, "화자1: 안녕하세요 오늘 회의 시작합니다".encode("utf-8"))
    assert process_meeting(res.json()["jobId"], session_factory=env["factory"], stt_factory=NoStt(), extractor_factory=BrokenExtractor) == "failed"
    assert stage_of(env, mgr, res.json()["meetingId"])[:2] == ("failed", "extract")
