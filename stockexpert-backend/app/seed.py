from sqlalchemy import select
from app.db import SessionLocal,Organization,User
from app.core.config import settings
from app.core.security import hash_password
with SessionLocal() as db:
    user=db.scalar(select(User).where(User.username==settings.ADMIN_USERNAME))
    if not user:
        org=Organization(name='StockExpert — локальный пилот');db.add(org);db.flush()
        db.add(User(username=settings.ADMIN_USERNAME,password_hash=hash_password(settings.ADMIN_PASSWORD),organization_id=org.id,role='admin'))
        db.commit()

