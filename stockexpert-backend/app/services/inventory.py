from sqlalchemy import select,or_
from app.db import Item,StockPosition,ProcurementSession

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

