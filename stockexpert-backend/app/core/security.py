import hashlib, hmac, secrets
from datetime import datetime, timedelta, timezone
from jose import jwt
from app.core.config import settings

def hash_password(password):
    salt=secrets.token_hex(16)
    digest=hashlib.scrypt(password.encode(),salt=salt.encode(),n=16384,r=8,p=1).hex()
    return f'scrypt${salt}${digest}'
def verify_password(password, encoded):
    try:
        _,salt,digest=encoded.split('$')
        actual=hashlib.scrypt(password.encode(),salt=salt.encode(),n=16384,r=8,p=1).hex()
        return hmac.compare_digest(actual,digest)
    except (ValueError,TypeError): return False

def create_access_token(user):
    return jwt.encode({'sub':str(user.id),'exp':datetime.now(timezone.utc)+timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)},settings.SECRET_KEY,algorithm='HS256')

