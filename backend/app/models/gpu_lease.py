from datetime import datetime

from sqlalchemy import Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.models.common import UTCDateTime, utcnow


class GpuLease(Base):
    """GPU 사용권(작업 89-1). 자원(resource, 현재는 "gpu" 1개)당 1행: 지금 어떤 작업이 점유 중인지와 만료 시각.
    워커(STT·추출)와 회의록 질의가 같은 GPU 를 겹쳐 쓰지 않게 하는 직렬화 표식이다. 이 표는 services/gpu_lease.py 에서만 다룬다.
    만료 시각이 지난 행은 점유가 아니다(프로세스가 죽어도 스스로 풀린다). 질문·답변 같은 내용은 담지 않는다."""

    __tablename__ = "gpu_leases"

    resource: Mapped[str] = mapped_column(String(30), primary_key=True)
    holder: Mapped[str] = mapped_column(String(30))  # worker | qa
    ref_id: Mapped[int | None] = mapped_column(Integer, nullable=True)  # worker 면 작업 ID, qa 면 비움
    acquired_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime())
