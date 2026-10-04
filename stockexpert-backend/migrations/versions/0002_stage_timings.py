"""Persist stage timings without changing existing session data."""
from alembic import op
import sqlalchemy as sa
revision='0002'
down_revision='0001'
branch_labels=None
depends_on=None
def upgrade():
    op.create_table('stage_timings',
        sa.Column('id',sa.String(36),primary_key=True),
        sa.Column('session_id',sa.String(36),sa.ForeignKey('procurement_sessions.id'),nullable=False),
        sa.Column('job_id',sa.String(36),sa.ForeignKey('jobs.id'),nullable=False),
        sa.Column('attempt',sa.String(36),nullable=False),sa.Column('stage',sa.String(64),nullable=False),
        sa.Column('started_at',sa.DateTime(timezone=True),nullable=False),sa.Column('finished_at',sa.DateTime(timezone=True)),
        sa.Column('elapsed_seconds',sa.Float()),sa.Column('outcome',sa.String(32),nullable=False))
    op.create_index('ix_stage_timings_session_id','stage_timings',['session_id'])
    op.create_index('ix_stage_timings_job_id','stage_timings',['job_id'])
def downgrade():op.drop_table('stage_timings')

