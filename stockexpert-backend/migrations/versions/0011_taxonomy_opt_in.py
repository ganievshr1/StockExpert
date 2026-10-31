"""Default new sessions to H1 and H3; preserve every saved H2 choice."""
from alembic import op
import sqlalchemy as sa
revision = '0011'
down_revision = '0010'
branch_labels = None
depends_on = None

def upgrade():
    with op.batch_alter_table('procurement_sessions') as batch:
        batch.alter_column('taxonomy_enabled', existing_type=sa.Boolean(), existing_nullable=False, server_default=sa.false())

def downgrade():
    with op.batch_alter_table('procurement_sessions') as batch:
        batch.alter_column('taxonomy_enabled', existing_type=sa.Boolean(), existing_nullable=False, server_default=sa.true())
