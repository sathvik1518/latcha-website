"""Latcha Points: sign up, earn a few points every day you visit, spend 500 points for Rs 50 off an order.

All the numbers are editable by the owner (table `settings`). Points are only ever changed through add_tx(),
which also writes a history row, so a customer's balance always equals the sum of their history.
"""
from datetime import datetime, timedelta, timezone

import jwt
from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import inspect, select, text
from sqlalchemy.orm import Session

from . import models as m
from .config import get_settings
from .db import get_db

_bearer = HTTPBearer(auto_error=False)
IST = timezone(timedelta(hours=5, minutes=30))

DEFAULTS = {"enabled": 1, "signup_bonus": 50, "visit_points": 10, "redeem_points": 500, "redeem_rupees": 50,
            "birthday_points": 50, "comeback_days": 14, "comeback_points": 25}
LIMITS = {"enabled": (0, 1), "signup_bonus": (0, 5000), "visit_points": (0, 1000),
          "redeem_points": (1, 100000), "redeem_rupees": (1, 5000),
          "birthday_points": (0, 5000), "comeback_days": (2, 365), "comeback_points": (0, 5000)}
TOKEN_DAYS = 30


def upgrade_schema(engine) -> None:
    """Adds the new columns to tables that already exist (create_all only makes new tables)."""
    insp = inspect(engine)
    adds = {"orders": [("customer_id", "INTEGER REFERENCES customers(id)"), ("points_used", "INTEGER NOT NULL DEFAULT 0"),
                       ("points_discount", "INTEGER NOT NULL DEFAULT 0"), ("points_refunded", "BOOLEAN NOT NULL DEFAULT FALSE"),
                       ("status_at", "TIMESTAMP")],
            "reviews": [("customer_id", "INTEGER REFERENCES customers(id)")],
            "customers": [("birthday", "VARCHAR(5)")]}
    for table, cols in adds.items():
        have = {c["name"] for c in insp.get_columns(table)}
        for name, ddl in cols:
            if name not in have:
                with engine.begin() as c:
                    c.execute(text(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}"))


# ---------- settings ----------
def rules(db: Session) -> dict:
    out = dict(DEFAULTS)
    for r in db.scalars(select(m.Setting)):
        if r.key in out:
            try:
                out[r.key] = int(r.value)
            except ValueError:
                pass
    return out


def save_rules(db: Session, patch: dict) -> dict:
    for k, v in patch.items():
        row = db.get(m.Setting, k)
        if row:
            row.value = str(int(v))
        else:
            db.add(m.Setting(key=k, value=str(int(v))))
    db.commit()
    return rules(db)


# ---------- points ----------
def today_ist() -> str:
    return datetime.now(IST).strftime("%Y-%m-%d")


def add_tx(db: Session, c: m.Customer, delta: int, reason: str, note: str = "") -> int:
    """Change the balance and write the history row. The balance never goes below zero."""
    delta = max(delta, -c.points)
    if delta:
        c.points += delta
        db.add(m.PointTx(customer_id=c.id, delta=delta, reason=reason, note=note[:120]))
    return delta


def award_visit(db: Session, c: m.Customer) -> int:
    """Once per Indian calendar day. Returns the points given (0 if already given today or switched off)."""
    r = rules(db)
    today = today_ist()
    if not r["enabled"] or c.last_visit == today:
        return 0
    gap = days_between(c.last_visit, today)
    c.last_visit = today
    c.visits += 1
    got = add_tx(db, c, r["visit_points"], "visit", "Daily visit") if r["visit_points"] else 0
    if gap is not None and gap >= r["comeback_days"] and r["comeback_points"]:   # "we miss you": back after a long break
        got += add_tx(db, c, r["comeback_points"], "comeback", f"Welcome back after {gap} days")
    return got + award_birthday(db, c, r)


def days_between(a: str | None, b: str) -> int | None:
    try:
        return (datetime.strptime(b, "%Y-%m-%d") - datetime.strptime(a, "%Y-%m-%d")).days if a else None
    except ValueError:
        return None


def award_birthday(db: Session, c: m.Customer, r: dict | None = None) -> int:
    """Birthday points, once a year, on the day. Not on the day the account was made, so signing up to cheat gets nothing."""
    r = r or rules(db)
    today = datetime.now(IST)
    if not r["enabled"] or not r["birthday_points"] or not c.birthday or c.birthday != today.strftime("%m-%d"):
        return 0
    created = c.created_at.astimezone(IST).strftime("%Y-%m-%d") if c.created_at else ""
    if created == today.strftime("%Y-%m-%d"):
        return 0
    note = f"Happy birthday {today.year}"
    if db.scalar(select(m.PointTx.id).where(m.PointTx.customer_id == c.id, m.PointTx.reason == "birthday", m.PointTx.note == note)):
        return 0
    return add_tx(db, c, r["birthday_points"], "birthday", note)


def redeem(db: Session, c: m.Customer, charge: int) -> tuple[int, int]:
    """(points to spend, rupees off) for an order costing `charge` rupees: whole blocks only, never more than the bill."""
    r = rules(db)
    if not r["enabled"] or charge <= 0:
        return 0, 0
    blocks = min(c.points // r["redeem_points"], charge // r["redeem_rupees"])
    return blocks * r["redeem_points"], blocks * r["redeem_rupees"]


def refund_if_cancelled(db: Session, o: m.Order, new_status: str) -> None:
    """A cancelled order gives its points back once; un-cancelling takes them again."""
    if not o.points_used or not o.customer_id:
        return
    c = db.get(m.Customer, o.customer_id)
    if c is None:
        return
    if new_status == "cancelled" and not o.points_refunded:
        add_tx(db, c, o.points_used, "refund", f"Order #{o.id} cancelled")
        o.points_refunded = True
    elif new_status != "cancelled" and o.points_refunded:
        add_tx(db, c, -o.points_used, "redeem", f"Order #{o.id} back on")
        o.points_refunded = False


# ---------- sign-in ----------
def make_token(c: m.Customer) -> str:
    st = get_settings()
    if not st.jwt_secret:
        raise HTTPException(503, "Accounts are not set up yet. Please try again later.")
    exp = datetime.now(timezone.utc) + timedelta(days=TOKEN_DAYS)
    return jwt.encode({"sub": f"c:{c.id}", "typ": "cust", "exp": exp}, st.jwt_secret, algorithm="HS256")


def _decode(cred) -> int | None:
    st = get_settings()
    if cred is None or not st.jwt_secret:
        return None
    try:
        d = jwt.decode(cred.credentials, st.jwt_secret, algorithms=["HS256"])
        return int(str(d["sub"])[2:]) if d.get("typ") == "cust" and str(d.get("sub", "")).startswith("c:") else None
    except (jwt.PyJWTError, ValueError, KeyError):
        return None


def optional_customer(cred: HTTPAuthorizationCredentials | None = Depends(_bearer), db: Session = Depends(get_db)):
    cid = _decode(cred)
    return db.get(m.Customer, cid) if cid else None


def require_customer(c=Depends(optional_customer)) -> m.Customer:
    if c is None:
        raise HTTPException(401, "Please sign in")
    return c


# ---------- phone code (OTP) for sign-up ----------
OTP_MINUTES, OTP_MAX_PER_HOUR, OTP_MAX_TRIES = 10, 3, 5


def otp_required() -> bool:
    return (get_settings().otp_mode or "off").strip().lower() == "demo"


def _otp_hash(phone: str, code: str) -> str:
    import hashlib
    return hashlib.sha256(f"{phone}:{code}:{get_settings().jwt_secret}".encode()).hexdigest()


def otp_issue(db: Session, phone: str) -> str:
    """Make a new 6-digit code for this number (3 per hour). Only the hash is stored."""
    import secrets
    now = datetime.now(timezone.utc)
    recent = len(db.scalars(select(m.OtpCode.id).where(m.OtpCode.phone == phone, m.OtpCode.created_at >= now - timedelta(hours=1))).all())
    if recent >= OTP_MAX_PER_HOUR:
        raise HTTPException(429, "Too many codes asked for this number. Please try again in an hour.")
    code = f"{secrets.randbelow(10**6):06d}"
    db.add(m.OtpCode(phone=phone, code_hash=_otp_hash(phone, code), expires_at=now + timedelta(minutes=OTP_MINUTES)))
    db.commit()
    return code


def otp_check(db: Session, phone: str, code: str | None) -> None:
    """Raises unless the newest unexpired code for this number matches (5 tries). A used code is deleted."""
    if not code or not code.strip().isdigit():
        raise HTTPException(422, "Enter the 6-digit code.")
    row = db.scalars(select(m.OtpCode).where(m.OtpCode.phone == phone).order_by(m.OtpCode.id.desc())).first()
    exp = row.expires_at if row else None
    if exp is not None and exp.tzinfo is None:
        exp = exp.replace(tzinfo=timezone.utc)
    if row is None or exp < datetime.now(timezone.utc) or row.tries >= OTP_MAX_TRIES:
        raise HTTPException(422, "That code has expired. Ask for a new one.")
    if row.code_hash != _otp_hash(phone, code.strip()):
        row.tries += 1
        db.commit()
        raise HTTPException(422, "That code is not right.")
    for r in db.scalars(select(m.OtpCode).where(m.OtpCode.phone == phone)).all():
        db.delete(r)
