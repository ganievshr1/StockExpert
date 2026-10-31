import hashlib,json
from sqlalchemy import select
from app.db import Audit, ProcurementSession, now

def digest(sid,user,event,details,stamp,previous):
    return hashlib.sha256(json.dumps([sid,user,event,details,stamp,previous],ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()).hexdigest()
def record(db,sid,event,details,user_id=None):
    db.scalar(select(ProcurementSession).where(ProcurementSession.id==sid).with_for_update())
    previous=db.scalar(select(Audit).where(Audit.session_id==sid).order_by(Audit.id.desc()).limit(1))
    prev=previous.sha256_hash if previous else ''
    stamp=now().isoformat()
    db.add(Audit(session_id=sid,user_id=user_id,event_type=event,event_details=details,created_at=stamp,prev_hash=prev,sha256_hash=digest(sid,user_id,event,details,stamp,prev)))
def verify(rows):
    prev=''
    for row in rows:
        if row.prev_hash!=prev or row.sha256_hash!=digest(row.session_id,row.user_id,row.event_type,row.event_details,row.created_at,prev): return False,prev
        prev=row.sha256_hash
    return True,prev

