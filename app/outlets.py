"""Outlet helpers: opening hours, status text, and a safe one-time schema upgrade.

Hours are Indian time. An outlet that closes "before" it opens (11:00 -> 01:00) is open past midnight.
"""
import re
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, inspect, select, text

from . import models as m

IST = timezone(timedelta(hours=5, minutes=30))
HHMM = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")


def now_ist() -> datetime:
    return datetime.now(IST)


def _mins(hhmm: str) -> int:
    h, mi = hhmm.split(":")
    return int(h) * 60 + int(mi)


def pretty(hhmm: str) -> str:
    """'13:05' -> '1:05 PM', '00:00' -> '12:00 AM'."""
    h, mi = divmod(_mins(hhmm), 60)
    return f"{h % 12 or 12}:{mi:02d} {'AM' if h < 12 else 'PM'}"


def is_open(opens: str, closes: str, at: datetime) -> bool:
    t, o, c = at.hour * 60 + at.minute, _mins(opens), _mins(closes)
    if o == c:
        return True  # same time twice = open all day
    return o <= t < c if o < c else (t >= o or t < c)


def status_text(o: "m.Outlet", at: datetime) -> tuple[bool, str]:
    """(open_now, short sentence for the customer)."""
    if o.coming_soon:
        return False, "Opening soon"
    if not o.accepting_orders:
        return False, "Not taking orders right now"
    if is_open(o.opens, o.closes, at):
        return True, f"Open now · until {pretty(o.closes)}"
    return False, f"Closed now · opens {pretty(o.opens)}"


SEED = [
    # slug, name, city, area, address, opens, closes, coming_soon, position   (hours from the café's Instagram bio: confirm with the owner)
    ("jp-nagar", "LATCHA JP Nagar", "Bengaluru", "JP Nagar", "JP Nagar, Bengaluru", "11:00", "01:00", False, 1),
    ("sattva-knowledge-park", "LATCHA Sattva Knowledge Park", "Hyderabad", "Sattva Knowledge Park",
     "Sattva Knowledge Park, Hyderabad (opposite Underdogs)", "11:00", "00:00", False, 2),
    ("indiranagar", "LATCHA Indiranagar", "Bengaluru", "Indiranagar", "Indiranagar, Bengaluru", "11:00", "01:00", True, 3),
]


def seed_outlets(db) -> int:
    if db.scalar(select(func.count(m.Outlet.id))):
        return 0
    for slug, name, city, area, addr, o, c, soon, pos in SEED:
        db.add(m.Outlet(slug=slug, name=name, city=city, area=area, address=addr, opens=o, closes=c,
                        coming_soon=soon, accepting_orders=not soon, position=pos))
    db.commit()
    return len(SEED)


def ensure_schema(engine, SessionLocal, Base) -> None:
    """Safe to run many times: creates new tables, adds orders.outlet_id if missing, seeds the outlets."""
    Base.metadata.create_all(engine)  # creates outlets + outlet_sold_out; leaves existing tables alone
    cols = {c["name"] for c in inspect(engine).get_columns("orders")}
    if "outlet_id" not in cols:
        with engine.begin() as c:
            c.execute(text("ALTER TABLE orders ADD COLUMN outlet_id INTEGER REFERENCES outlets(id)"))
            c.execute(text("CREATE INDEX IF NOT EXISTS ix_orders_outlet_id ON orders (outlet_id)"))
    from .payments import upgrade_payment_schema
    upgrade_payment_schema(engine)
    from .loyalty import upgrade_schema
    upgrade_schema(engine)
    with SessionLocal() as db:
        seed_outlets(db)
