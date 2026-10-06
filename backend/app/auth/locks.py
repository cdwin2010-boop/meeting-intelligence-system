"""보류·종료·삭제 회의록 잠금 판정(쓰기 API 가 409 로 거부하는 조건). app/api 가 아니라 auth 에 두어 허용 동작 목록(app/auth/actions.py)이
api 를 가져오지 않게 한다. 동작·문구는 예전 app/api/meeting_hold.py 에 있던 것과 같다."""
from fastapi import HTTPException, status

from app.models import Meeting

HOLDABLE_STATUSES = ("awaiting_confirmation", "confirmed")
ON_HOLD_MESSAGE = "보류 중인 회의록입니다. 재개한 뒤에 다시 시도하세요"
ENDED_MESSAGE = "종료된 회의록입니다. 수정할 수 없습니다"
DELETED_MESSAGE = "삭제된 회의록입니다. 수정할 수 없습니다"


def reject_if_locked(meeting: Meeting | None, *, allow_on_hold: bool = False) -> None:
    """보류·종료·삭제된 회의록(과 그 업무)에 대한 변경 요청을 409 로 거부한다. 권한 판정(403) 뒤에 부른다.
    allow_on_hold: 보류·재개 API 처럼 보류 상태 자체를 다루는 곳에서만 True."""
    if meeting is None:
        return
    if meeting.deleted:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=DELETED_MESSAGE)
    if meeting.on_hold and not allow_on_hold:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=ON_HOLD_MESSAGE)
    if meeting.ended:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=ENDED_MESSAGE)
