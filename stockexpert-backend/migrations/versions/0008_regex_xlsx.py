"""Allow standalone XLSX regex runs; preserve every historical experiment."""
from alembic import op
import sqlalchemy as sa

revision = '0008'
down_revision = '0007'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('normalization_experiments') as batch:
        batch.alter_column('source_session_id', existing_type=sa.String(36), nullable=True)
        batch.alter_column('dataset_id', existing_type=sa.String(36), nullable=True)


def downgrade():
    # Never delete standalone experiment history to satisfy old NOT NULL columns.
    count = op.get_bind().execute(sa.text('SELECT count(*) FROM normalization_experiments WHERE source_session_id IS NULL OR dataset_id IS NULL')).scalar()
    if count:
        raise RuntimeError('Есть самостоятельные XLSX-эксперименты. Откат 0008 остановлен для сохранения истории.')
    with op.batch_alter_table('normalization_experiments') as batch:
        batch.alter_column('source_session_id', existing_type=sa.String(36), nullable=False)
        batch.alter_column('dataset_id', existing_type=sa.String(36), nullable=False)
