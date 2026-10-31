from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
revision='0003'
down_revision='0002'
branch_labels=None
depends_on=None

def upgrade():
    op.create_table('stock_positions',sa.Column('id',sa.String(36),primary_key=True),sa.Column('organization_id',sa.Integer(),sa.ForeignKey('organizations.id'),nullable=False),sa.Column('base_session_id',sa.String(36),nullable=False),sa.Column('state',sa.String(16),nullable=False),sa.Column('used_by_session_id',sa.String(36),nullable=True),sa.Column('used_at',sa.DateTime(timezone=True),nullable=True),sa.Column('created_at',sa.DateTime(timezone=True),nullable=False))
    for column in ('organization_id','base_session_id','state'):op.create_index('ix_stock_positions_'+column,'stock_positions',[column])
    with op.batch_alter_table('items') as batch:
        batch.add_column(sa.Column('stock_id',sa.String(36),nullable=True))
        batch.create_foreign_key('fk_items_stock_id','stock_positions',['stock_id'],['id'])
        batch.create_index('ix_items_stock_id',['stock_id'])

    op.add_column('procurement_sessions',sa.Column('base_snapshot',sa.JSON().with_variant(JSONB,'postgresql'),nullable=False,server_default=sa.text("'{}'")))
def downgrade():
    with op.batch_alter_table('items') as batch:
        batch.drop_index('ix_items_stock_id')
        batch.drop_constraint('fk_items_stock_id',type_='foreignkey')
        batch.drop_column('stock_id')
    op.drop_table('stock_positions')

    op.drop_column('procurement_sessions','base_snapshot')

