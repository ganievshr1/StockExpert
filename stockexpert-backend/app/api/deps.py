from fastapi import Depends, HTTPException
from fastapi.security import OAuth2PasswordBearer
from jose import jwt, JWTError
from sqlalchemy import select
from app.core.config import settings
from app.db import get_db, User, ProcurementSession

oauth2_scheme=OAuth2PasswordBearer(tokenUrl='/api/v1/auth/token')
def get_current_user(token=Depends(oauth2_scheme),db=Depends(get_db)):
    try:
        payload=jwt.decode(token,settings.SECRET_KEY,algorithms=['HS256'],options={'require_exp':True,'require_sub':True})
        user=db.get(User,int(payload['sub']))
        if not user or not user.is_active: raise ValueError()
        return user
    except (JWTError,ValueError,KeyError,TypeError):
        raise HTTPException(401,'Invalid token',headers={'WWW-Authenticate':'Bearer'})
def require_editor(user):
    if user.role not in ('buyer','dmto_expert','admin'): raise HTTPException(403,'Read-only role')
def require_expert(user):
    if user.role not in ('dmto_expert','admin'): raise HTTPException(403,'Expert role required')
def own_session(db,sid,user,lock=False):
    stmt=select(ProcurementSession).where(ProcurementSession.id==sid,ProcurementSession.organization_id==user.organization_id)
    if lock: stmt=stmt.with_for_update()
    obj=db.scalar(stmt)
    if not obj: raise HTTPException(404,'Session not found')
    if lock and obj.status in ('CLEANING','CLEANUP_FAILED'):raise HTTPException(409,'Session cleanup must finish first')
    return obj

