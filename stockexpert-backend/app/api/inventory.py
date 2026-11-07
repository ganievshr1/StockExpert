from fastapi import APIRouter,Depends,HTTPException
from sqlalchemy import select,update
from app.db import get_db,ProcurementSession,StockPosition,Item,Match,Job,now
from app.api.deps import get_current_user,require_editor,require_expert,own_session
from app.services.inventory import ensure_inventory,lock_stock_origins
from app.services.execution import ACTIVE_STATUSES
from app.services.audit import record
router=APIRouter()

@router.post('/matching/{sid}/candidates/{mid}/use')
def use_stock(sid:str,mid:str,db=Depends(get_db),user=Depends(get_current_user)):
    require_expert(user);s=own_session(db,sid,user,lock=True)
    m=db.get(Match,mid)
    if not m or m.session_id!=sid:raise HTTPException(404,'Пара не найдена')
    job=db.get(Job,m.job_id)
    if job.status!='COMPLETED':raise HTTPException(409,'Дождитесь завершения подбора')
    if m.decision!='RECOMMENDED':raise HTTPException(409,'Сначала сохраните рекомендацию эксперта')
    if m.result.get('state')=='CONTRADICTED':raise HTTPException(409,'Есть технические противоречия; использование заблокировано')
    ensure_inventory(db,s);item=db.get(Item,m.item_id)
    result=db.execute(update(StockPosition).where(StockPosition.id==item.stock_id,StockPosition.organization_id==user.organization_id,StockPosition.state=='AVAILABLE').values(state='USED',used_by_session_id=sid,used_at=now()))
    if result.rowcount!=1:raise HTTPException(409,'Позиция уже использована или удалена из общей базы')
    record(db,sid,'STOCK_USED',{'stock_id':item.stock_id,'match_id':mid,'scope':'entire stock row'},user.id);db.commit()
    return {'stock_id':item.stock_id,'state':'USED','removed_from_active_stock':True}

@router.delete('/normalized-bases/{sid}/stock')
def clear_stock(sid:str,db=Depends(get_db),user=Depends(get_current_user)):
    require_editor(user);s=own_session(db,sid,user,lock=True)
    # Shares transaction locks with enqueue/retry in every snapshot of this stock.
    # The active-job check and CLEARED update are protected by the same lock.
    roots=lock_stock_origins(db,s)
    if db.scalar(select(Job.id).where(Job.session_id==sid,Job.status.in_(ACTIVE_STATUSES)).limit(1)):raise HTTPException(409,'Сначала остановите обработку сессии')
    ensure_inventory(db,s)
    # Check every session sharing the chosen stock, not only the source session.
    linked=select(Item.session_id).join(StockPosition,StockPosition.id==Item.stock_id).where(StockPosition.base_session_id.in_(roots),StockPosition.organization_id==user.organization_id)
    if db.scalar(select(Job.id).where(Job.session_id.in_(linked),Job.status.in_(ACTIVE_STATUSES)).limit(1)):
        raise HTTPException(409,'Остановите обработку во всех сессиях этой базы перед очисткой')
    changed=db.execute(update(StockPosition).where(StockPosition.base_session_id.in_(roots),StockPosition.organization_id==user.organization_id,StockPosition.state=='AVAILABLE').values(state='CLEARED'))
    record(db,sid,'SHARED_STOCK_CLEARED',{'base_session_ids':sorted(roots),'rows':changed.rowcount},user.id);db.commit()
    return {'cleared':True,'rows':changed.rowcount,'message':'Активная база очищена. История сессий сохранена. Создайте сессию с новым Excel.'}

