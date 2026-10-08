"""item closures: item_closures 표(업무 종결 구분: completed·forced) 추가.
기존 표·칸·데이터는 바꾸지 않는다(action_items 표를 다시 만들지 않도록 따로 둠). downgrade 는 행이 있으면 거부하고, 비어 있을 때만 표를 없앤다.

Revision ID: f6c2d8a41b75
Revises: e5b1c7d29a64
Create Date: 2026-10-08 13:10:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'f6c2d8a41b75'
down_revision: Union[str, Sequence[str], None] = 'e5b1c7d29a64'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table('item_closures',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('tenant_id', sa.Integer(), nullable=False),
    sa.Column('item_id', sa.Integer(), nullable=False),
    sa.Column('kind', sa.String(length=20), nullable=False),
    sa.Column('closed_by', sa.Integer(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.CheckConstraint("kind IN ('completed', 'forced')", name=op.f('ck_item_closures_kind_valid')),
    sa.ForeignKeyConstraint(['closed_by'], ['accounts.id'], name=op.f('fk_item_closures_closed_by_accounts')),
    sa.ForeignKeyConstraint(['item_id'], ['action_items.id'], name=op.f('fk_item_closures_item_id_action_items')),
    sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id'], name=op.f('fk_item_closures_tenant_id_tenants')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_item_closures')),
    sa.UniqueConstraint('item_id', name=op.f('uq_item_closures_item_id'))
    )
    with op.batch_alter_table('item_closures', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_item_closures_tenant_id'), ['tenant_id'], unique=False)


def _refuse_if_data_exists() -> None:
    rows = op.get_bind().execute(sa.text("SELECT COUNT(*) FROM item_closures")).scalar_one()
    if rows:
        raise RuntimeError(f"downgrade 거부: 업무 종결 구분 기록 {rows}건이 있어 지우면 기록이 사라집니다. 백업 후 사람이 직접 판단해 정리하세요.")


def downgrade() -> None:
    """Downgrade schema. 기록이 있으면 거부(데이터 삭제 없음)."""
    _refuse_if_data_exists()
    with op.batch_alter_table('item_closures', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_item_closures_tenant_id'))

    op.drop_table('item_closures')
