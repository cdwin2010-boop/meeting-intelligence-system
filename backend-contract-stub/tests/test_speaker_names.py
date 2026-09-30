"""화자 이름 계약 테스트: docs/API-CONTRACT.md "화자 이름" (v1.9.9).
처리기는 fake(네트워크 없음), 대기 0초라 업로드 응답 직후 처리가 끝나 있다."""
import pytest
from fastapi.testclient import TestClient

from app.config import settings
from app.db import store
from app.main import app

client = TestClient(app)

M4A = b"\x00\x00\x00\x18ftypM4A \x00\x00\x00\x00" + b"\x00" * 1000
SEED_ID = "mtg-2026-0925"


@pytest.fixture(autouse=True)
def fresh(monkeypatch):
    store.reset()
    monkeypatch.setattr(settings, "fake_worker_step_seconds", 0)
    monkeypatch.setattr(settings, "fake_worker_enabled", True)


def upload(title="화자 이름 테스트 회의") -> str:
    r = client.post(
        "/api/meetings",
        data={"title": title, "startedAt": "2026-09-29T14:00:00+09:00"},
        files={"file": ("A.m4a", M4A, "application/octet-stream")},
    )
    assert r.status_code == 202
    return r.json()["meetingId"]


def put_speakers(meeting_id: str, speakers):
    return client.put(f"/api/meetings/{meeting_id}/speakers", json={"speakers": speakers})


def speaker_names(meeting_id: str) -> dict:
    r = client.get(f"/api/meetings/{meeting_id}")
    assert r.status_code == 200
    return r.json()["speakerNames"]


def test_seed_and_new_meetings_start_with_empty_mapping():
    assert speaker_names(SEED_ID) == {}
    assert speaker_names(upload()) == {}


def test_save_then_get_returns_trimmed_mapping():
    r = put_speakers(SEED_ID, {"화자1": "  권영우 부장  ", "화자12": "한 팀장"})
    assert r.status_code == 200
    assert r.json() == {"speakerNames": {"화자1": "권영우 부장", "화자12": "한 팀장"}}
    assert speaker_names(SEED_ID) == {"화자1": "권영우 부장", "화자12": "한 팀장"}


def test_put_replaces_whole_mapping_and_empty_value_deletes_key():
    put_speakers(SEED_ID, {"화자1": "권영우 부장", "화자2": "한 팀장"})
    # 전체 교체: 화자2는 빈 값(공백)이라 삭제, 요청에 없는 키도 남지 않는다
    r = put_speakers(SEED_ID, {"화자1": "권 부장", "화자2": "   "})
    assert r.status_code == 200 and r.json() == {"speakerNames": {"화자1": "권 부장"}}
    assert speaker_names(SEED_ID) == {"화자1": "권 부장"}
    assert put_speakers(SEED_ID, {}).json() == {"speakerNames": {}}
    assert speaker_names(SEED_ID) == {}


@pytest.mark.parametrize("key", ["화자", "화자A", " 화자1", "화자1 ", "Speaker1", "화자١", "김도현"])
def test_invalid_key_is_400_and_nothing_saved(key):
    put_speakers(SEED_ID, {"화자1": "권영우 부장"})
    r = put_speakers(SEED_ID, {"화자2": "한 팀장", key: "이름"})
    assert r.status_code == 400
    assert speaker_names(SEED_ID) == {"화자1": "권영우 부장"}  # 일부만 저장되지 않는다


def test_name_longer_than_30_chars_is_400_but_30_is_ok():
    assert put_speakers(SEED_ID, {"화자1": "가" * 31}).status_code == 400
    assert speaker_names(SEED_ID) == {}
    # 앞뒤 공백은 제거한 뒤 센다
    assert put_speakers(SEED_ID, {"화자1": "  " + "가" * 30 + "  "}).status_code == 200
    assert speaker_names(SEED_ID) == {"화자1": "가" * 30}


@pytest.mark.parametrize("body", [[], {"speakers": []}, {"speakers": "화자1"}, {"names": {}}, {"speakers": {"화자1": 3}}])
def test_malformed_body_is_400(body):
    assert client.put(f"/api/meetings/{SEED_ID}/speakers", json=body).status_code == 400


def test_non_json_body_is_400():
    r = client.put(f"/api/meetings/{SEED_ID}/speakers", content=b"not json", headers={"Content-Type": "application/json"})
    assert r.status_code == 400


def test_unknown_meeting_is_404():
    assert put_speakers("nope", {"화자1": "권영우 부장"}).status_code == 404


def test_other_meetings_and_originals_are_untouched():
    other = upload()
    items_before = client.get(f"/api/meetings/{other}/action-items").json()
    transcript_before = client.get(f"/api/meetings/{other}").json()["transcriptText"]

    put_speakers(SEED_ID, {"화자1": "권영우 부장"})

    assert speaker_names(other) == {}
    # 매핑만 저장한다: 액션아이템 assignee·전사 원문 원본은 그대로
    assert client.get(f"/api/meetings/{other}/action-items").json() == items_before
    assert client.get(f"/api/meetings/{other}").json()["transcriptText"] == transcript_before
    seed_items = client.get(f"/api/meetings/{SEED_ID}/action-items").json()
    assert all(item["assignee"] != "권영우 부장" for item in seed_items)
