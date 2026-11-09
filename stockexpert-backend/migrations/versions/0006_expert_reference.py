"""Organization-scoped expert references and post-hoc evaluations; no data reset."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = '0006'
down_revision = '0005'
branch_labels = None
depends_on = None
J = sa.JSON().with_variant(JSONB, 'postgresql')


def upgrade():
    op.create_table('expert_datasets',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('organization_id', sa.Integer(), sa.ForeignKey('organizations.id'), nullable=False),
        sa.Column('created_by', sa.Integer(), sa.ForeignKey('users.id'), nullable=False),
        sa.Column('title', sa.String(200), nullable=False),
        sa.Column('version', sa.String(80), nullable=False),
        sa.Column('checksum', sa.String(64), nullable=False),
        sa.Column('payload', J, nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint('organization_id', 'checksum', name='uq_expert_dataset_checksum'))
    op.create_index('ix_expert_datasets_organization_id', 'expert_datasets', ['organization_id'])
    op.create_table('expert_evaluations',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('dataset_id', sa.String(36), sa.ForeignKey('expert_datasets.id'), nullable=False),
        sa.Column('session_id', sa.String(36), sa.ForeignKey('procurement_sessions.id'), nullable=False),
        sa.Column('requested_by', sa.Integer(), sa.ForeignKey('users.id'), nullable=False),
        sa.Column('status', sa.String(32), nullable=False),
        sa.Column('result', J, nullable=False),
        sa.Column('error', sa.Text()),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint('dataset_id', 'session_id', name='uq_expert_evaluation'))
    op.create_index('ix_expert_evaluations_dataset_id', 'expert_evaluations', ['dataset_id'])
    op.create_index('ix_expert_evaluations_session_id', 'expert_evaluations', ['session_id'])


def downgrade():
    op.drop_table('expert_evaluations')
    op.drop_table('expert_datasets')
