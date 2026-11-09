"""Add isolated regex experiments; no UPDATE/DELETE on existing user tables."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = '0007'
down_revision = '0006'
branch_labels = None
depends_on = None
J = sa.JSON().with_variant(JSONB, 'postgresql')


def upgrade():
    op.create_table('normalization_experiments',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('organization_id', sa.Integer(), sa.ForeignKey('organizations.id'), nullable=False),
        sa.Column('requested_by', sa.Integer(), sa.ForeignKey('users.id'), nullable=False),
        sa.Column('source_session_id', sa.String(36), nullable=False),
        sa.Column('source_title', sa.String(300), nullable=False),
        sa.Column('dataset_id', sa.String(36), sa.ForeignKey('expert_datasets.id'), nullable=False),
        sa.Column('request_id', sa.String(36), nullable=False),
        sa.Column('status', sa.String(24), nullable=False),
        sa.Column('method', sa.String(32), nullable=False),
        sa.Column('rules_version', sa.String(80), nullable=False),
        sa.Column('rules_sha256', sa.String(64), nullable=False),
        sa.Column('input_sha256', sa.String(64), nullable=False),
        sa.Column('input_snapshot', J, nullable=False),
        sa.Column('baseline', J, nullable=False),
        sa.Column('predictions', J, nullable=False),
        sa.Column('result', J, nullable=False),
        sa.Column('row_count', sa.Integer(), nullable=False),
        sa.Column('processed_rows', sa.Integer(), nullable=False),
        sa.Column('error', sa.Text()),
        *[sa.Column(k, sa.DateTime(timezone=True), nullable=(k != 'created_at'))
          for k in ('created_at', 'started_at', 'finished_at', 'heartbeat_at', 'dispatched_at')],
        sa.UniqueConstraint('organization_id', 'request_id', name='uq_regex_request'))
    for column in ('organization_id', 'source_session_id', 'dataset_id', 'status'):
        op.create_index('ix_normalization_experiments_'+column, 'normalization_experiments', [column])


def downgrade():
    op.drop_table('normalization_experiments')
