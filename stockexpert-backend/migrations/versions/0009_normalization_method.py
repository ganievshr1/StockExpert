"""Select the H1 engine; historical sessions and jobs remain LLM."""
from alembic import op
import sqlalchemy as sa

revision = '0009'
down_revision = '0008'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('procurement_sessions', sa.Column('normalization_method', sa.String(16),
                                                   nullable=False, server_default='llm'))


def downgrade():
    # A downgrade would erase which extractor produced new session results.
    connection = op.get_bind()
    if connection.execute(sa.text("SELECT count(*) FROM procurement_sessions WHERE normalization_method != 'llm'")).scalar():
        raise RuntimeError('Есть сессии с регулярками. Понижение удалило бы метод H1 из истории.')
    with op.batch_alter_table('procurement_sessions') as batch:
        batch.drop_column('normalization_method')
