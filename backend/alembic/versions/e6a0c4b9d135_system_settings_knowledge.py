"""system settings knowledge: 허용 키 확장(knowledge_integration_enabled, knowledge_api_endpoint)과 value_text 칸 추가(작업 83-1).
기존 마이그레이션은 바꾸지 않는다. 새 칸은 NULL 허용이라 기존 행은 그대로다.
downgrade 는 새 키의 행이 있으면 거부하고(값이 사라지므로), 없을 때만 CHECK 를 되돌리고 칸을 지운다.

Revision ID: e6a0c4b9d135
Revises: d5f9b3a8c024
Create Date: 2026-10-12 21:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'e6a0c4b9d135'
down_revision: Union[str, Sequence[str], None] = 'd5f9b3a8c024'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

OLD_KEYS = "key IN ('external_llm_enabled', 'local_llm_external_host_allowed')"
NEW_KEYS = (
    "key IN ('external_llm_enabled', 'local_llm_external_host_allowed', "
    "'knowledge_integration_enabled', 'knowledge_api_endpoint')"
)


def upgrade() -> None:
    """Upgrade schema."""
    with op.batch_alter_table('system_settings', schema=None) as batch_op:
        batch_op.add_column(sa.Column('value_text', sa.String(length=500), nullable=True))
        batch_op.drop_constraint(op.f('ck_system_settings_key_valid'), type_='check')
        batch_op.create_check_constraint(op.f('ck_system_settings_key_valid'), NEW_KEYS)


def downgrade() -> None:
    """Downgrade schema. 새 키의 행이 있으면 거부(데이터 삭제 없음)."""
    rows = op.get_bind().execute(
        sa.text("SELECT COUNT(*) FROM system_settings WHERE key IN ('knowledge_integration_enabled', 'knowledge_api_endpoint')")
    ).scalar_one()
    if rows:
        raise RuntimeError(f"downgrade 거부: 지식 연동 설정 {rows}건이 있어 되돌리면 기록이 사라집니다. 백업 후 사람이 직접 판단해 정리하세요.")
    with op.batch_alter_table('system_settings', schema=None) as batch_op:
        batch_op.drop_constraint(op.f('ck_system_settings_key_valid'), type_='check')
        batch_op.create_check_constraint(op.f('ck_system_settings_key_valid'), OLD_KEYS)
        batch_op.drop_column('value_text')
