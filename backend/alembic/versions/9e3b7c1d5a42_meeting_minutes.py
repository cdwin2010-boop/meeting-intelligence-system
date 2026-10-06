"""meeting minutes: meeting_minutes 표(회의록당 1행, 5개 항목과 처리 엔진 기록) 추가.
기존 표·칸·데이터는 바꾸지 않는다(meetings 표를 다시 만들지 않도록 따로 둠). 변경 이력은 기존 events 표를 그대로 쓴다(칸 추가 없음).
downgrade 는 행이 있으면 거부하고, 비어 있을 때만 표를 없앤다.

Revision ID: 9e3b7c1d5a42
Revises: 0f82186010dc
Create Date: 2026-10-06 10:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '9e3b7c1d5a42'
down_revision: Union[str, Sequence[str], None] = '0f82186010dc'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table('meeting_minutes',
    sa.Column('meeting_id', sa.Integer(), nullable=False),
    sa.Column('tenant_id', sa.Integer(), nullable=False),
    sa.Column('purpose', sa.Text(), nullable=False),
    sa.Column('discussion', sa.Text(), nullable=False),
    sa.Column('decisions', sa.Text(), nullable=False),
    sa.Column('risks', sa.Text(), nullable=False),
    sa.Column('next_agenda', sa.Text(), nullable=False),
    sa.Column('engine', sa.String(length=30), server_default='', nullable=False),
    sa.Column('extract_model', sa.String(length=100), server_default='', nullable=False),
    sa.Column('prompt_version', sa.String(length=50), server_default='', nullable=False),
    sa.Column('generated_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_by', sa.Integer(), nullable=True),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True),
    sa.ForeignKeyConstraint(['meeting_id'], ['meetings.id'], name=op.f('fk_meeting_minutes_meeting_id_meetings')),
    sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id'], name=op.f('fk_meeting_minutes_tenant_id_tenants')),
    sa.ForeignKeyConstraint(['updated_by'], ['accounts.id'], name=op.f('fk_meeting_minutes_updated_by_accounts')),
    sa.PrimaryKeyConstraint('meeting_id', name=op.f('pk_meeting_minutes'))
    )
    with op.batch_alter_table('meeting_minutes', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_meeting_minutes_tenant_id'), ['tenant_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_meeting_minutes_updated_by'), ['updated_by'], unique=False)


def _refuse_if_data_exists() -> None:
    rows = op.get_bind().execute(sa.text("SELECT COUNT(*) FROM meeting_minutes")).scalar_one()
    if rows:
        raise RuntimeError(f"downgrade 거부: 회의록 5개 항목 {rows}건이 있어 지우면 기록이 사라집니다. 백업 후 사람이 직접 판단해 정리하세요.")


def downgrade() -> None:
    """Downgrade schema. 기록이 있으면 거부(데이터 삭제 없음)."""
    _refuse_if_data_exists()
    with op.batch_alter_table('meeting_minutes', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_meeting_minutes_updated_by'))
        batch_op.drop_index(batch_op.f('ix_meeting_minutes_tenant_id'))

    op.drop_table('meeting_minutes')
