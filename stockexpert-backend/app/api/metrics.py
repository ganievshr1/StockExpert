from fastapi import APIRouter,Depends
from fastapi.responses import Response
from app.api.deps import get_current_user,own_session
from app.db import get_db
from app.services.metrics import measurement,workbook
router=APIRouter()
@router.get('/metrics/{sid}')
def metrics(sid:str,db=Depends(get_db),user=Depends(get_current_user)):
    own_session(db,sid,user);return measurement(db,sid)
@router.get('/metrics/{sid}/export')
def export(sid:str,db=Depends(get_db),user=Depends(get_current_user)):
    own_session(db,sid,user);data=workbook(measurement(db,sid))
    return Response(data,media_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',headers={'Content-Disposition':'attachment; filename="stockexpert-metrics.xlsx"'})

