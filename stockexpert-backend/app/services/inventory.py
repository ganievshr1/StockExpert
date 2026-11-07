import hashlib
from sqlalchemy import select,or_,func
from app.db import Item,StockPosition,ProcurementSession


def stock_origin_ids(db,session):
    """Return the few shared inventory origins, without locking inventory rows."""
    roots=set(db.scalars(
        select(StockPosition.base_session_id)
        .join(Item,Item.stock_id==StockPosition.id)
        .where(Item.session_id==session.id,
               StockPosition.organization_id==session.organization_id)
        .distinct()
    ))
    # Before first normalization an original catalog has no ledger rows yet.
    # A session can also contain new residuals alongside copied stock.
    if not roots or db.scalar(select(Item.id).where(
        Item.session_id==session.id,Item.kind=='residual',Item.stock_id.is_(None),
        Item.passport['attributes'].as_string().is_not(None)
    ).limit(1)):
        roots.add(session.id)
    return roots


def _stock_lock_key(organization_id,origin_id):
    value=f'stockexpert:stock-origin:{organization_id}:{origin_id}'.encode()
    return int.from_bytes(hashlib.sha256(value).digest()[:8],'big',signed=True)


def lock_stock_origins(db,session):
    """Serialize job admission with clearing shared stock until commit/rollback.

    Call after the caller's own session row lock, before checking active jobs.
    No other session row locks are acquired here. All origin advisory locks use
    the same numeric order, including sessions referencing multiple origins.
    Production uses PostgreSQL READ COMMITTED; its active-job query after a
    waiting advisory lock observes the preceding admission transaction's commit.
    SQLite tests exercise functional behavior, not these cross-process locks.
    """
    roots=stock_origin_ids(db,session)
    if db.get_bind().dialect.name=='postgresql':
        keys=sorted({_stock_lock_key(session.organization_id,root) for root in roots})
        for key in keys:
            db.execute(select(func.pg_advisory_xact_lock(key)))
    return roots

def ensure_inventory(db,s):
    # Call while holding the session lock or its worker lease. Existing snapshots retain stock_id.
    for item in db.scalars(select(Item).where(Item.session_id==s.id,Item.kind=='residual',Item.stock_id.is_(None),Item.passport['attributes'].as_string().is_not(None))):
        position=db.get(StockPosition,item.id)
        if not position:
            position=StockPosition(id=item.id,organization_id=s.organization_id,base_session_id=s.id);db.add(position);db.flush()
        item.stock_id=position.id
    db.flush()

def available_item_ids(db):
    return select(Item.id).outerjoin(StockPosition,StockPosition.id==Item.stock_id).where(or_(Item.stock_id.is_(None),StockPosition.state=='AVAILABLE'))

def stock_state(db,item):
    position=db.get(StockPosition,item.stock_id) if item and item.stock_id else None
    return position.state if position else 'AVAILABLE'

