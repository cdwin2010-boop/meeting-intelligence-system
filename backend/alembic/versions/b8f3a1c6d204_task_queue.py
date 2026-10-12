"""task queue: task_queue 표(처리 작업 큐, 작업 75-1) 추가.
기존 표·칸·데이터는 바꾸지 않는다. downgrade 는 행이 있으면 거부하고, 비어 있을 때만 표를 없앤다.

Revision ID: b8f3a1c6d204
Revises: a7d3e9b52c16
Create Date: 2026-10-12 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b8f3a1c6d204'
down_revision: Union[str, Sequence[str], None] = 'a7d3e9b52c16'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table('task_queue',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('tenant_id', sa.Integer(), nullable=False),
    sa.Column('meeting_id', sa.Integer(), nullable=False),
    sa.Column('status', sa.String(length=20), nullable=False),
    sa.Column('progress', sa.Integer(), server_default='0', nullable=False),
    sa.Column('error_msg', sa.Text(), nullable=True),
    sa.Column('attempts', sa.Integer(), server_default='0', nullable=False),
    sa.Column('started_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.CheckConstraint("status IN ('PENDING', 'PROCESSING', 'DONE', 'FAILED')", name=op.f('ck_task_queue_status_valid')),
    sa.CheckConstraint('progress BETWEEN 0 AND 100', name=op.f('ck_task_queue_progress_range')),
    sa.ForeignKeyConstraint(['meeting_id'], ['meetings.id'], name=op.f('fk_task_queue_meeting_id_meetings')),
    sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id'], name=op.f('fk_task_queue_tenant_id_tenants')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_task_queue'))
    )
    with op.batch_alter_table('task_queue', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_task_queue_meeting_id'), ['meeting_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_task_queue_tenant_id'), ['tenant_id'], unique=False)
        batch_op.create_index('ix_task_queue_status_created', ['status', 'created_at'], unique=False)


def _refuse_if_data_exists() -> None:
    rows = op.get_bind().execute(sa.text("SELECT COUNT(*) FROM task_queue")).scalar_one()
    if rows:
        raise RuntimeError(f"downgrade 거부: 작업 큐 {rows}건이 있어 지우면 기록이 사라집니다. 백업 후 사람이 직접 판단해 정리하세요.")


def downgrade() -> None:
    """Downgrade schema. 기록이 있으면 거부(데이터 삭제 없음)."""
    _refuse_if_data_exists()
    with op.batch_alter_table('task_queue', schema=None) as batch_op:
        batch_op.drop_index('ix_task_queue_status_created')
        batch_op.drop_index(batch_op.f('ix_task_queue_tenant_id'))
        batch_op.drop_index(batch_op.f('ix_task_queue_meeting_id'))

    op.drop_table('task_queue')
