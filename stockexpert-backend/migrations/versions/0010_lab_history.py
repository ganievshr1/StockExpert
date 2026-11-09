"""Optional H2 and immutable experiment/evaluation history."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
revision='0010'
down_revision='0009'
branch_labels=None
depends_on=None
J=sa.JSON().with_variant(JSONB(),'postgresql')
def upgrade():
    op.add_column('procurement_sessions',sa.Column('taxonomy_enabled',sa.Boolean(),nullable=False,server_default=sa.true()))
    op.create_table('run_snapshots',sa.Column('job_id',sa.String(36),sa.ForeignKey('jobs.id'),primary_key=True),sa.Column('session_id',sa.String(36),sa.ForeignKey('procurement_sessions.id'),nullable=False,index=True),sa.Column('payload',J,nullable=False),sa.Column('created_at',sa.DateTime(timezone=True),nullable=False))
    op.create_table('metric_revisions',sa.Column('id',sa.String(36),primary_key=True),sa.Column('session_id',sa.String(36),sa.ForeignKey('procurement_sessions.id'),nullable=False,index=True),sa.Column('job_id',sa.String(36),sa.ForeignKey('jobs.id'),nullable=False,index=True),sa.Column('source',sa.String(16),nullable=False),sa.Column('dataset_id',sa.String(36),sa.ForeignKey('expert_datasets.id')),sa.Column('action',sa.String(32),nullable=False),sa.Column('result',J,nullable=False),sa.Column('inputs',J,nullable=False),sa.Column('created_at',sa.DateTime(timezone=True),nullable=False))
    op.create_table('lab_experiments',sa.Column('id',sa.String(36),primary_key=True),sa.Column('session_id',sa.String(36),sa.ForeignKey('procurement_sessions.id'),nullable=False,index=True),sa.Column('job_id',sa.String(36),sa.ForeignKey('jobs.id'),nullable=False,index=True),sa.Column('requested_by',sa.Integer(),sa.ForeignKey('users.id'),nullable=False),sa.Column('source',sa.String(16),nullable=False),sa.Column('dataset_id',sa.String(36),sa.ForeignKey('expert_datasets.id')),sa.Column('status',sa.String(24),nullable=False,index=True),sa.Column('payload',J,nullable=False),sa.Column('result',J,nullable=False),sa.Column('summary',J,nullable=False),sa.Column('error',sa.Text()),sa.Column('created_at',sa.DateTime(timezone=True),nullable=False),sa.Column('finished_at',sa.DateTime(timezone=True)),sa.Column('dispatched_at',sa.DateTime(timezone=True)))
def downgrade():
    if op.get_bind().execute(sa.text('SELECT count(*) FROM run_snapshots')).scalar():
        raise RuntimeError('Понижение удалит историю расчётов. Сначала экспортируйте её.')
    for name in ('lab_experiments','metric_revisions','run_snapshots'):op.drop_table(name)
    with op.batch_alter_table('procurement_sessions') as batch:batch.drop_column('taxonomy_enabled')
