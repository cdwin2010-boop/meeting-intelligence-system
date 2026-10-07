"""모델 공통: UTC 시각 타입, 열거형 값 목록, CHECK 제약 생성 도우미."""
from datetime import datetime, timezone

from sqlalchemy import CheckConstraint, DateTime
from sqlalchemy.types import TypeDecorator


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class UTCDateTime(TypeDecorator):
    """시각은 항상 UTC로 저장하고, 읽을 때도 UTC 시각(tz 포함)으로 돌려준다.
    SQLite는 시간대를 저장하지 않으므로 읽은 값에 UTC를 붙인다(다른 엔진에서도 같은 결과)."""

    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        if value.tzinfo is None:
            raise ValueError("시간대 없는 시각은 저장할 수 없습니다(UTC 시각을 넘겨 주세요).")
        return value.astimezone(timezone.utc)

    def process_result_value(self, value, dialect):
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)


def enum_check(column: str, values: tuple[str, ...], name: str, nullable: bool = False) -> CheckConstraint:
    """열거형은 DB native enum 대신 문자열 + CHECK 제약으로 둔다(엔진 중립)."""
    allowed = ", ".join(f"'{v}'" for v in values)
    expr = f"{column} IN ({allowed})"
    if nullable:
        expr = f"{column} IS NULL OR {expr}"
    return CheckConstraint(expr, name=name)


# ---- 열거형 값 (설계 문서 부록 C) ----
# 직급: 담당자 / 중간관리자 / 지시자
ACCOUNT_RANKS = ("staff", "manager", "executive")
# 원천 문서 출처: 음성 회의록 / 고객사 업로드 문서
SOURCE_ORIGINS = ("audio_minutes", "uploaded")
# 회의록 상태: 전사·추출 중 → 확정 대기 → 확정 완료. 처리 실패(failed)·말소리 없음(no_content)
MEETING_STATUSES = ("processing", "awaiting_confirmation", "confirmed", "failed", "no_content")
# 확정 경로: 관리자 확인 / 등록 시 확정 / 기간 경과 / 마감 도래
CONFIRM_KINDS = ("manager", "registration", "period_elapsed", "due_reached")
# 업무 상태
ACTION_ITEM_STATUSES = ("pending", "confirmed", "closed", "deleted")
# 끝난 업무(종결·삭제): 할 일·자동 확정·알림·메일 대상에서 뺀다
INACTIVE_ITEM_STATUSES = ("closed", "deleted")
# 처리 작업 상태: 대기 → 실행 중 → 완료 / 실패 / 말소리 없음
JOB_STATUSES = ("queued", "running", "completed", "failed", "no_content")
# ---- 조직·프로젝트(작업 66-2) ----
# 부서 종류: 일반 부서 / 임원 그룹(프로젝트 참여자 후보에서 따로 보인다)
DEPARTMENT_KINDS = ("normal", "executive")
# 부서 안 역할: 부서장(부서마다 1명, 서비스 계층에서 보장) / 부서원
DEPARTMENT_ROLES = ("head", "member")
# 프로젝트 상태: 승인 대기(부서원 등록) → 진행(active) 또는 반려
PROJECT_STATUSES = ("pending_approval", "active", "rejected")
# 프로젝트 참여 역할: 총괄 / 관리자 / 참여자(lead·manager 는 관리자 이상 직급만)
PROJECT_MEMBER_ROLES = ("lead", "manager", "member")
