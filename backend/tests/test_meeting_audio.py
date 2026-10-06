"""㊾ 회의록 음성 재생: 서명 주소 발급(열람 규칙은 전사문과 같음)과 Range 스트리밍. 임시 DB·임시 업로드 폴더만 쓴다."""
from datetime import timedelta

import pytest

from app.auth.tokens import create_access_token, create_audio_token, decode_access_token, decode_audio_token, InvalidTokenError
from app.models import Meeting, SourceDocument
from tests.test_meeting_queries import make_meeting
from tests.test_upload_processing import add_account, auth, env  # noqa: F401  (env 는 픽스처)

AUDIO = bytes(range(256)) * 8  # 2048바이트


@pytest.fixture
def team(env):
    f = env["factory"]
    return {
        "lead": add_account(f, "고객사A", "lead", "manager", name="박총괄"),
        "staff": add_account(f, "고객사A", "kim", "staff", name="김담당"),
        "stranger": add_account(f, "고객사A", "lee", "staff", name="이무관"),
        "outsider": add_account(f, "고객사B", "out", "executive", name="외부"),
    }


@pytest.fixture
def meeting_id(env, team):
    """총괄 등록, 담당자(staff) 참석. 음성 파일은 업로드 폴더 안 {tenant}/rec.m4a 로 저장하고 DB 에 상대 경로를 기록한다."""
    mid = make_meeting(env["factory"], team["lead"], participants=[team["staff"]], transcript="화자1: 안녕")
    folder = env["uploads"] / str(team["lead"].tenant_id)
    folder.mkdir(parents=True)
    (folder / "rec.m4a").write_bytes(AUDIO)
    set_path(env, mid, f"{team['lead'].tenant_id}/rec.m4a")
    return mid


def set_path(env, mid, value):
    with env["factory"]() as s:
        meeting = s.get(Meeting, mid)
        s.get(SourceDocument, meeting.source_document_id).file_path = value
        s.commit()


def issue(env, account, mid):
    return env["client"].get(f"/api/meetings/{mid}/audio-url", headers=auth(account))


def stream(env, url, **headers):
    return env["client"].get(f"/api{url}", headers=headers)


def signed_url(env, account, mid):
    res = issue(env, account, mid)
    assert res.status_code == 200, res.text
    return res.json()["url"]


@pytest.mark.parametrize("who", ["lead", "staff"])
def test_viewers_can_issue_and_play(env, team, meeting_id, who):
    body = issue(env, team[who], meeting_id).json()
    assert set(body) == {"url", "expiresInSec"} and body["expiresInSec"] == 600
    assert body["url"].startswith(f"/meetings/{meeting_id}/audio?token=")
    res = stream(env, body["url"])  # 로그인 헤더 없이 서명 주소만으로
    assert res.status_code == 200 and res.content == AUDIO
    assert res.headers["content-type"] == "audio/mp4" and res.headers["accept-ranges"] == "bytes"


def test_staff_without_relation_and_other_tenant_get_404(env, team, meeting_id):
    assert issue(env, team["stranger"], meeting_id).status_code == 404
    assert issue(env, team["outsider"], meeting_id).status_code == 404


def test_staff_denied_for_held_and_deleted_meeting_but_lead_allowed(env, team, meeting_id):
    client, lead = env["client"], team["lead"]
    url = signed_url(env, team["staff"], meeting_id)  # 보류 전에 발급
    assert client.post(f"/api/meetings/{meeting_id}/hold", headers=auth(lead), json={"reason": "검토"}).status_code == 200
    assert issue(env, team["staff"], meeting_id).status_code == 404
    assert stream(env, url).status_code == 404  # 이미 발급된 주소도 지금 기준으로 다시 판정
    assert issue(env, lead, meeting_id).status_code == 200  # 관리자 이상은 가능
    assert client.post(f"/api/meetings/{meeting_id}/resume", headers=auth(lead)).status_code == 200
    assert client.post(f"/api/meetings/{meeting_id}/delete", headers=auth(lead), json={"reason": "삭제"}).status_code == 200
    assert issue(env, team["staff"], meeting_id).status_code == 404
    assert issue(env, lead, meeting_id).status_code == 200


def test_issue_requires_login(env, meeting_id):
    assert env["client"].get(f"/api/meetings/{meeting_id}/audio-url").status_code == 401
    assert env["client"].get(f"/api/meetings/{meeting_id}/audio-url", headers={"Authorization": "Bearer nope"}).status_code == 401
    assert stream(env, f"/meetings/{meeting_id}/audio").status_code == 401  # 서명 없음


def test_range_206_matches_bytes(env, team, meeting_id):
    url = signed_url(env, team["lead"], meeting_id)
    res = stream(env, url, Range="bytes=100-199")
    assert res.status_code == 206 and res.content == AUDIO[100:200]
    assert res.headers["content-range"] == f"bytes 100-199/{len(AUDIO)}" and res.headers["content-length"] == "100"
    assert stream(env, url, Range="bytes=2000-").content == AUDIO[2000:]
    assert stream(env, url, Range="bytes=-48").content == AUDIO[-48:]
    res = stream(env, url, Range="bytes=2040-99999")  # 끝은 파일 크기로 줄인다
    assert res.status_code == 206 and res.content == AUDIO[2040:]


@pytest.mark.parametrize("value", ["bytes=2048-", "bytes=5000-6000", "bytes=200-100", "bytes=abc", "items=0-5", "bytes=0-5,10-20", "bytes=-0"])
def test_bad_range_416(env, team, meeting_id, value):
    url = signed_url(env, team["lead"], meeting_id)
    res = stream(env, url, Range=value)
    assert res.status_code == 416 and res.headers["content-range"] == f"bytes */{len(AUDIO)}"


def test_forged_expired_and_wrong_kind_tokens_rejected(env, team, meeting_id):
    lead = team["lead"]
    path = f"/meetings/{meeting_id}/audio"
    good = signed_url(env, lead, meeting_id).split("token=")[1]
    forged = good[:-3] + ("AAA" if not good.endswith("AAA") else "BBB")
    expired = create_audio_token(lead.id, meeting_id, timedelta(seconds=-5))
    other_meeting = create_audio_token(lead.id, meeting_id + 1)
    login = create_access_token(lead.id)
    for token in (forged, expired, other_meeting, login, "garbage"):
        assert stream(env, f"{path}?token={token}").status_code == 401, token
    assert stream(env, f"{path}?token={good}").status_code == 200
    # 서로 바꿔 쓸 수 없다
    with pytest.raises(InvalidTokenError):
        decode_access_token(good)
    with pytest.raises(InvalidTokenError):
        decode_audio_token(login)
    assert decode_audio_token(good) == (lead.id, meeting_id)


def test_missing_audio_404(env, team, meeting_id):
    lead = team["lead"]
    (env["uploads"] / str(lead.tenant_id) / "rec.m4a").unlink()
    res = issue(env, lead, meeting_id)
    assert res.status_code == 404 and res.json()["detail"] == "음성 파일이 없습니다"
    set_path(env, meeting_id, None)
    assert issue(env, lead, meeting_id).json()["detail"] == "음성 파일이 없습니다"


def test_stream_404_when_file_removed_after_issue(env, team, meeting_id):
    url = signed_url(env, team["lead"], meeting_id)
    (env["uploads"] / str(team["lead"].tenant_id) / "rec.m4a").unlink()
    res = stream(env, url)
    assert res.status_code == 404 and res.json()["detail"] == "음성 파일이 없습니다"


@pytest.mark.parametrize("bad", ["../secret.m4a", "../../secret.m4a", "1/../../secret.m4a"])
def test_path_outside_upload_dir_blocked(env, team, meeting_id, bad):
    outside = env["uploads"].parent / "secret.m4a"
    outside.write_bytes(b"secret")
    set_path(env, meeting_id, bad)
    assert issue(env, team["lead"], meeting_id).status_code == 404
    assert outside.read_bytes() == b"secret"


def test_absolute_path_blocked(env, team, meeting_id):
    outside = env["uploads"].parent / "abs.m4a"
    outside.write_bytes(b"secret")
    set_path(env, meeting_id, str(outside))
    assert issue(env, team["lead"], meeting_id).status_code == 404


def test_path_is_never_taken_from_request(env, team, meeting_id):
    url = signed_url(env, team["lead"], meeting_id)
    res = stream(env, url + "&path=../../etc/passwd&file=x")
    assert res.status_code == 200 and res.content == AUDIO
