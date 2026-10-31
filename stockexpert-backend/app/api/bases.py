from fastapi import APIRouter,Depends,Query
from sqlalchemy import select,func,case,or_
from app.db import get_db,ProcurementSession,Item,Job,StockPosition
from app.api.deps import get_current_user
from app.services.execution import ACTIVE_STATUSES
router=APIRouter()

@router.get('/normalized-bases')
def bases(limit:int=Query(100,ge=1,le=200),offset:int=Query(0,ge=0),db=Depends(get_db),user=Depends(get_current_user)):
    active=select(Job.session_id).where(Job.status.in_(ACTIVE_STATUSES))
    counts=select(Item.session_id,func.count().label('total'),func.sum(case((Item.passport['attributes'].as_string().is_not(None),1),else_=0)).label('normalized')).outerjoin(StockPosition,StockPosition.id==Item.stock_id).where(Item.kind=='residual',Item.source_text!='',or_(Item.stock_id.is_(None),StockPosition.state=='AVAILABLE')).group_by(Item.session_id).subquery()
    stmt=select(ProcurementSession,counts.c.normalized).join(counts,counts.c.session_id==ProcurementSession.id).where(ProcurementSession.organization_id==user.organization_id,ProcurementSession.approved==True,ProcurementSession.status.notin_(['CLEANING','CLEANUP_FAILED']),ProcurementSession.id.notin_(active),counts.c.total==counts.c.normalized,counts.c.normalized>0).order_by(ProcurementSession.created_at.desc(),ProcurementSession.id).limit(limit).offset(offset)
    return [{'session_id':s.id,'title':s.title,'normalized_rows':int(n),'taxonomy_version':s.taxonomy_version,'created_at':s.created_at,'features':[f['label'] for f in s.schema_doc.get('features',[])]} for s,n in db.execute(stmt)]

