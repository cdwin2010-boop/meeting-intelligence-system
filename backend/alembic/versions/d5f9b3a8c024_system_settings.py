"""system settings: system_settings 표(시스템 관리자 설정, 작업 82-1 파트 C) 추가.
기존 표·칸·데이터는 바꾸지 않는다. downgrade 는 행이 있으면 거부하고, 비어 있을 때만 표를 없앤다.

Revision ID: d5f9b3a8c024
Revises: c4e8a2f7b913
Create Date: 2026-10-12 17:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'd5f9b3a8c024'
down_revision: Union[str, Sequence[str], None] = 'c4e8a2f7b913'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table('system_settings',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('tenant_id', sa.Integer(), nullable=False),
    sa.Column('key', sa.String(length=60), nullable=False),
    sa.Column('value_bool', sa.Boolean(), nullable=False),
    sa.Column('updated_by', sa.Integer(), nullable=True),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.CheckConstraint("key IN ('external_llm_enabled', 'local_llm_external_host_allowed')", name=op.f('ck_system_settings_key_valid')),
    sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id'], name=op.f('fk_system_settings_tenant_id_tenants')),
    sa.ForeignKeyConstraint(['updated_by'], ['accounts.id'], name=op.f('fk_system_settings_updated_by_accounts')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_system_settings')),
    sa.UniqueConstraint('tenant_id', 'key', name='uq_system_settings_tenant_key')
    )
    with op.batch_alter_table('system_settings', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_system_settings_tenant_id'), ['tenant_id'], unique=False)


def _refuse_if_data_exists() -> None:
    rows = op.get_bind().execute(sa.text("SELECT COUNT(*) FROM system_settings")).scalar_one()
    if rows:
        raise RuntimeError(f"downgrade 거부: 시스템 설정 {rows}건이 있어 지우면 기록이 사라집니다. 백업 후 사람이 직접 판단해 정리하세요.")


def downgrade() -> None:
    """Downgrade schema. 기록이 있으면 거부(데이터 삭제 없음)."""
    _refuse_if_data_exists()
    with op.batch_alter_table('system_settings', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_system_settings_tenant_id'))

    op.drop_table('system_settings')
