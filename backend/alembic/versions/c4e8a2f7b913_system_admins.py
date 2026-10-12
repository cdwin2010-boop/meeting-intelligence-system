"""system admins: system_admins 표(시스템 관리자 지정, 작업 82-1) 추가.
기존 표·칸·데이터는 바꾸지 않는다. downgrade 는 행이 있으면 거부하고, 비어 있을 때만 표를 없앤다.

Revision ID: c4e8a2f7b913
Revises: b8f3a1c6d204
Create Date: 2026-10-12 15:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c4e8a2f7b913'
down_revision: Union[str, Sequence[str], None] = 'b8f3a1c6d204'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table('system_admins',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('tenant_id', sa.Integer(), nullable=False),
    sa.Column('account_id', sa.Integer(), nullable=False),
    sa.Column('created_by', sa.Integer(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['account_id'], ['accounts.id'], name=op.f('fk_system_admins_account_id_accounts')),
    sa.ForeignKeyConstraint(['created_by'], ['accounts.id'], name=op.f('fk_system_admins_created_by_accounts')),
    sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id'], name=op.f('fk_system_admins_tenant_id_tenants')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_system_admins')),
    sa.UniqueConstraint('account_id', name=op.f('uq_system_admins_account_id'))
    )
    with op.batch_alter_table('system_admins', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_system_admins_tenant_id'), ['tenant_id'], unique=False)


def _refuse_if_data_exists() -> None:
    rows = op.get_bind().execute(sa.text("SELECT COUNT(*) FROM system_admins")).scalar_one()
    if rows:
        raise RuntimeError(f"downgrade 거부: 시스템 관리자 지정 {rows}건이 있어 지우면 기록이 사라집니다. 백업 후 사람이 직접 판단해 정리하세요.")


def downgrade() -> None:
    """Downgrade schema. 기록이 있으면 거부(데이터 삭제 없음)."""
    _refuse_if_data_exists()
    with op.batch_alter_table('system_admins', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_system_admins_tenant_id'))

    op.drop_table('system_admins')
