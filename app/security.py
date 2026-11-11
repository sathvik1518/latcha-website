import hashlib, hmac, secrets
from datetime import datetime, timedelta, timezone
import jwt
from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from .config import get_settings

_bearer = HTTPBearer(auto_error=False)
_ITER = 240_000


def hash_password(pw: str) -> str:
    salt = secrets.token_hex(16)
    h = hashlib.pbkdf2_hmac("sha256", pw.encode(), salt.encode(), _ITER).hex()
    return f"pbkdf2${_ITER}${salt}${h}"


def verify_password(pw: str, stored: str) -> bool:
    try:
        _, it, salt, h = stored.split("$")
        calc = hashlib.pbkdf2_hmac("sha256", pw.encode(), salt.encode(), int(it)).hex()
        return hmac.compare_digest(calc, h)
    except Exception:
        return False


def make_token(username: str) -> str:
    s = get_settings()
    exp = datetime.now(timezone.utc) + timedelta(minutes=s.token_minutes)
    return jwt.encode({"sub": username, "exp": exp}, s.jwt_secret, algorithm="HS256")


def require_admin(cred: HTTPAuthorizationCredentials | None = Depends(_bearer)) -> str:
    s = get_settings()
    if not s.jwt_secret:
        raise HTTPException(503, "Admin is not configured")
    if cred is None:
        raise HTTPException(401, "Login required")
    try:
        data = jwt.decode(cred.credentials, s.jwt_secret, algorithms=["HS256"])
    except jwt.PyJWTError:
        raise HTTPException(401, "Invalid or expired token")
    if data.get("sub") != s.admin_username:
        raise HTTPException(401, "Invalid token")
    return data["sub"]
