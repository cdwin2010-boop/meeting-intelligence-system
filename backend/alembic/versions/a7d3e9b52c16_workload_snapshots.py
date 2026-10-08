"""workload snapshots: workload_snapshots 표(업무 처리 현황 전일 집계 저장) 추가.
기존 표·칸·데이터는 바꾸지 않는다. downgrade 는 행이 있으면 거부하고, 비어 있을 때만 표를 없앤다.

Revision ID: a7d3e9b52c16
Revises: f6c2d8a41b75
Create Date: 2026-10-08 15:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a7d3e9b52c16'
down_revision: Union[str, Sequence[str], None] = 'f6c2d8a41b75'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table('workload_snapshots',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('tenant_id', sa.Integer(), nullable=False),
    sa.Column('snapshot_date', sa.Date(), nullable=False),
    sa.Column('account_id', sa.Integer(), nullable=False),
    sa.Column('department_id', sa.Integer(), nullable=False),
    sa.Column('completed_count', sa.Integer(), nullable=False),
    sa.Column('in_progress_count', sa.Integer(), nullable=False),
    sa.Column('overdue_count', sa.Integer(), nullable=False),
    sa.Column('due_soon_count', sa.Integer(), nullable=False),
    sa.Column('urgent_items', sa.JSON(), nullable=False),
    sa.Column('generated_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['account_id'], ['accounts.id'], name=op.f('fk_workload_snapshots_account_id_accounts')),
    sa.ForeignKeyConstraint(['department_id'], ['departments.id'], name=op.f('fk_workload_snapshots_department_id_departments')),
    sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id'], name=op.f('fk_workload_snapshots_tenant_id_tenants')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_workload_snapshots')),
    sa.UniqueConstraint('tenant_id', 'snapshot_date', 'account_id', 'department_id', name='uq_workload_snapshots_key')
    )
    with op.batch_alter_table('workload_snapshots', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_workload_snapshots_account_id'), ['account_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_workload_snapshots_department_id'), ['department_id'], unique=False)
        batch_op.create_index('ix_workload_snapshots_tenant_date', ['tenant_id', 'snapshot_date'], unique=False)


def _refuse_if_data_exists() -> None:
    rows = op.get_bind().execute(sa.text("SELECT COUNT(*) FROM workload_snapshots")).scalar_one()
    if rows:
        raise RuntimeError(f"downgrade 거부: 업무 처리 현황 집계 {rows}건이 있어 지우면 기록이 사라집니다. 백업 후 사람이 직접 판단해 정리하세요.")


def downgrade() -> None:
    """Downgrade schema. 기록이 있으면 거부(데이터 삭제 없음)."""
    _refuse_if_data_exists()
    with op.batch_alter_table('workload_snapshots', schema=None) as batch_op:
        batch_op.drop_index('ix_workload_snapshots_tenant_date')
        batch_op.drop_index(batch_op.f('ix_workload_snapshots_department_id'))
        batch_op.drop_index(batch_op.f('ix_workload_snapshots_account_id'))

    op.drop_table('workload_snapshots')
