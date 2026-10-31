"""Persist a session's LLM configuration without changing existing results."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = '0005'
down_revision = '0004'
branch_labels = None
depends_on = None

def upgrade():
    op.add_column('procurement_sessions', sa.Column(
        'llm_config', sa.JSON().with_variant(JSONB, 'postgresql'),
        nullable=False, server_default=sa.text("'{}'")))

def downgrade():
    op.drop_column('procurement_sessions', 'llm_config')

