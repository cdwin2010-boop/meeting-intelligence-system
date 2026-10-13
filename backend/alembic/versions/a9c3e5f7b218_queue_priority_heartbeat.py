"""queue priority and worker heartbeat: task_queue 에 priority·promoted_at 칸과 인덱스, worker_heartbeats 표 추가(작업 88-1).
기존 칸·데이터는 바꾸지 않는다(priority 기본 0 → 기존 대기 작업의 순서는 그대로 접수 순서).
downgrade 는 즉시 변환으로 올린 작업(priority 가 0 이 아닌 행)이 있으면 거부하고, 없을 때만 칸·표를 없앤다(생존 신호 표는 상태 값이라 그대로 지운다).

Revision ID: a9c3e5f7b218
Revises: e6a0c4b9d135
Create Date: 2026-10-13 15:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a9c3e5f7b218'
down_revision: Union[str, Sequence[str], None] = 'e6a0c4b9d135'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # 표를 다시 만들지 않는 직접 ALTER 로 칸만 더한다(기존 행·외래 키를 건드리지 않음)
    op.add_column('task_queue', sa.Column('priority', sa.Integer(), server_default='0', nullable=False))
    op.add_column('task_queue', sa.Column('promoted_at', sa.DateTime(timezone=True), nullable=True))
    op.create_index('ix_task_queue_status_priority_created', 'task_queue', ['status', 'priority', 'created_at'], unique=False)

    op.create_table('worker_heartbeats',
    sa.Column('worker_id', sa.String(length=100), nullable=False),
    sa.Column('beat_at', sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('worker_id', name=op.f('pk_worker_heartbeats'))
    )


def downgrade() -> None:
    """Downgrade schema. 즉시 변환으로 올린 작업이 있으면 거부(우선순위 기록이 사라지므로)."""
    rows = op.get_bind().execute(sa.text("SELECT COUNT(*) FROM task_queue WHERE priority <> 0")).scalar_one()
    if rows:
        raise RuntimeError(f"downgrade 거부: 즉시 변환으로 올린 작업 {rows}건이 있어 되돌리면 우선순위 기록이 사라집니다. 백업 후 사람이 직접 판단해 정리하세요.")
    op.drop_table('worker_heartbeats')
    op.drop_index('ix_task_queue_status_priority_created', table_name='task_queue')
    op.drop_column('task_queue', 'promoted_at')
    op.drop_column('task_queue', 'priority')
