"""회의록 분류(회의 유형·프로젝트 연결) 입력 검증과 저장. 업로드가 파일을 저장하기 "전에" validate 를 부르고, 회의록 생성과 같은 트랜잭션에서 save 한다.
- 유형이 없으면 프로젝트도 없어야 하고(422) 연결 표 행을 만들지 않는다(미지정).
- 허용 5종이 아니면 422. 프로젝트 유형이면 projectId 필수(422), 아니면 projectId 가 있으면 422.
- 프로젝트: 같은 고객사의 활성 프로젝트만. 다른 고객사·없는 것은 404(존재 여부를 알리지 않음), 대기·반려는 409, 올리는 사람이 참여자가 아니면 403."""
from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Account, Meeting, MeetingClassification, Project, ProjectMember
from app.models.common import MEETING_TYPES

PROJECT_TYPE = "project"


def _http(code: int, detail: str) -> HTTPException:
    return HTTPException(status_code=code, detail=detail)


def validate_classification(
    session: Session, account: Account, meeting_type: str | None, project_id: int | None
) -> tuple[str | None, int | None]:
    meeting_type = (meeting_type or "").strip() or None
    if meeting_type is None:
        if project_id is not None:
            raise _http(status.HTTP_422_UNPROCESSABLE_CONTENT, "projectId 는 회의 유형(meetingType)과 함께만 보낼 수 있습니다.")
        return None, None
    if meeting_type not in MEETING_TYPES:
        raise _http(status.HTTP_422_UNPROCESSABLE_CONTENT, f"meetingType 은 {', '.join(MEETING_TYPES)} 중 하나여야 합니다.")
    if meeting_type != PROJECT_TYPE:
        if project_id is not None:
            raise _http(status.HTTP_422_UNPROCESSABLE_CONTENT, "프로젝트 회의가 아니면 projectId 를 보낼 수 없습니다.")
        return meeting_type, None
    if project_id is None:
        raise _http(status.HTTP_422_UNPROCESSABLE_CONTENT, "프로젝트 회의는 projectId 가 필요합니다.")
    project = session.scalar(select(Project).where(Project.id == project_id, Project.tenant_id == account.tenant_id))
    if project is None:
        raise _http(status.HTTP_404_NOT_FOUND, "프로젝트를 찾을 수 없습니다")
    if project.status != "active":
        raise _http(status.HTTP_409_CONFLICT, "진행 중인 프로젝트에만 회의록을 연결할 수 있습니다")
    is_member = session.scalar(
        select(ProjectMember.id).where(ProjectMember.project_id == project.id, ProjectMember.account_id == account.id)
    )
    if is_member is None:
        raise _http(status.HTTP_403_FORBIDDEN, "프로젝트 참여자만 그 프로젝트 회의록을 올릴 수 있습니다")
    return meeting_type, project.id


def save_classification(
    session: Session, meeting: Meeting, account: Account, meeting_type: str | None, project_id: int | None
) -> None:
    """유형이 있을 때만 연결 행을 만든다(커밋은 호출부: 회의록 생성과 같은 트랜잭션)."""
    if meeting_type is None:
        return
    session.add(MeetingClassification(
        tenant_id=meeting.tenant_id, meeting_id=meeting.id, meeting_type=meeting_type, project_id=project_id, created_by=account.id,
    ))
    session.flush()
