"""meeting guest participants: meeting_guest_participants 표(계정 없는 참석자 이름) 추가.
기존 표·칸·데이터는 바꾸지 않는다(meeting_participants·meetings 표를 다시 만들지 않도록 따로 둠). downgrade 는 행이 있으면 거부하고, 비어 있을 때만 표를 없앤다.

Revision ID: b4d8f2a6c901
Revises: 9e3b7c1d5a42
Create Date: 2026-10-06 15:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b4d8f2a6c901'
down_revision: Union[str, Sequence[str], None] = '9e3b7c1d5a42'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table('meeting_guest_participants',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('tenant_id', sa.Integer(), nullable=False),
    sa.Column('meeting_id', sa.Integer(), nullable=False),
    sa.Column('name', sa.String(length=100), nullable=False),
    sa.Column('created_by', sa.Integer(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['created_by'], ['accounts.id'], name=op.f('fk_meeting_guest_participants_created_by_accounts')),
    sa.ForeignKeyConstraint(['meeting_id'], ['meetings.id'], name=op.f('fk_meeting_guest_participants_meeting_id_meetings')),
    sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id'], name=op.f('fk_meeting_guest_participants_tenant_id_tenants')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_meeting_guest_participants')),
    sa.UniqueConstraint('meeting_id', 'name', name='uq_meeting_guest_participants_meeting_name')
    )
    with op.batch_alter_table('meeting_guest_participants', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_meeting_guest_participants_created_by'), ['created_by'], unique=False)
        batch_op.create_index(batch_op.f('ix_meeting_guest_participants_meeting_id'), ['meeting_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_meeting_guest_participants_tenant_id'), ['tenant_id'], unique=False)


def _refuse_if_data_exists() -> None:
    rows = op.get_bind().execute(sa.text("SELECT COUNT(*) FROM meeting_guest_participants")).scalar_one()
    if rows:
        raise RuntimeError(f"downgrade 거부: 계정 없는 참석자 이름 {rows}건이 있어 지우면 기록이 사라집니다. 백업 후 사람이 직접 판단해 정리하세요.")


def downgrade() -> None:
    """Downgrade schema. 기록이 있으면 거부(데이터 삭제 없음)."""
    _refuse_if_data_exists()
    with op.batch_alter_table('meeting_guest_participants', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_meeting_guest_participants_tenant_id'))
        batch_op.drop_index(batch_op.f('ix_meeting_guest_participants_meeting_id'))
        batch_op.drop_index(batch_op.f('ix_meeting_guest_participants_created_by'))

    op.drop_table('meeting_guest_participants')
