from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
revision='0004'
down_revision='0003'
branch_labels=None
depends_on=None
J=sa.JSON().with_variant(JSONB,'postgresql')
def upgrade():
    op.create_table('quality_reviews',sa.Column('id',sa.String(36),primary_key=True),sa.Column('session_id',sa.String(36),sa.ForeignKey('procurement_sessions.id'),nullable=False),sa.Column('job_id',sa.String(36),sa.ForeignKey('jobs.id'),nullable=False),sa.Column('kind',sa.String(16),nullable=False),sa.Column('target_id',sa.String(36),nullable=False),sa.Column('prediction',J,nullable=False),sa.Column('answer',J,nullable=False),sa.Column('reviewer_id',sa.Integer(),sa.ForeignKey('users.id'),nullable=False),sa.Column('updated_at',sa.DateTime(timezone=True),nullable=False),sa.UniqueConstraint('job_id','kind','target_id',name='uq_quality_target'))
    op.create_table('quality_completions',sa.Column('job_id',sa.String(36),sa.ForeignKey('jobs.id'),primary_key=True),sa.Column('session_id',sa.String(36),sa.ForeignKey('procurement_sessions.id'),nullable=False),sa.Column('reviewer_id',sa.Integer(),sa.ForeignKey('users.id'),nullable=False),sa.Column('finalized_at',sa.DateTime(timezone=True),nullable=False))
    for table in ['quality_reviews','quality_completions']:
        op.create_index('ix_'+table+'_session_id',table,['session_id'])
    op.create_index('ix_quality_reviews_job_id','quality_reviews',['job_id'])
def downgrade():
    op.drop_table('quality_completions');op.drop_table('quality_reviews')

