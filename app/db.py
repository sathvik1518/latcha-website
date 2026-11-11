from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker
from .config import get_settings

_s = get_settings()
_kw: dict = {"pool_pre_ping": True}  # Neon sleeps; this re-opens dropped connections
if _s.db_url.startswith("sqlite"):
    _kw["connect_args"] = {"check_same_thread": False}
else:
    _kw.update(pool_size=2, max_overflow=2, pool_recycle=240)  # small: serverless + pooled Neon URL

engine = create_engine(_s.db_url, **_kw)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
