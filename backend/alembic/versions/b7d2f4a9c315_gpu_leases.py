"""gpu leases: GPU 사용권 표 gpu_leases 추가(작업 89-1). 자원당 1행(점유자·만료 시각). 기존 표·데이터는 바꾸지 않는다.
downgrade 는 표만 지운다(상태 값이라 이력이 없다).

Revision ID: b7d2f4a9c315
Revises: a9c3e5f7b218
Create Date: 2026-10-16 15:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b7d2f4a9c315'
down_revision: Union[str, Sequence[str], None] = 'a9c3e5f7b218'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table('gpu_leases',
    sa.Column('resource', sa.String(length=30), nullable=False),
    sa.Column('holder', sa.String(length=30), nullable=False),
    sa.Column('ref_id', sa.Integer(), nullable=True),
    sa.Column('acquired_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('resource', name=op.f('pk_gpu_leases'))
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table('gpu_leases')
