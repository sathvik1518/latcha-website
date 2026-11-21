"""Latcha Fam: a phone-number loyalty card, worked out from real orders (no extra tables to drift out of sync).

1 sip = 1 drink in an order the owner marked DELIVERED.  Every N sips (default 8) = 1 free drink on a later order.
A free drink is "reserved" when an order using it is placed, comes back if that order is cancelled."""
from sqlalchemy import func, inspect, select, text

from . import models as m
from .config import get_settings


def upgrade_fam_schema(engine) -> None:
    if "fam_discount" not in {c["name"] for c in inspect(engine).get_columns("orders")}:
        with engine.begin() as c:
            c.execute(text("ALTER TABLE orders ADD COLUMN fam_discount INTEGER NOT NULL DEFAULT 0"))


def per_reward() -> int:
    return max(1, get_settings().fam_sips_per_reward)


def _shape(sips: int, used: int, orders: int) -> dict:
    n = per_reward()
    return dict(sips=sips, per_reward=n, into_card=sips % n, to_next=n - sips % n,
                rewards_available=max(0, sips // n - used), rewards_used=used, delivered_orders=orders)


def card(db, phone: str) -> dict:
    sips = db.scalar(select(func.coalesce(func.sum(m.OrderLine.quantity), 0)).join(m.Order, m.Order.id == m.OrderLine.order_id)
                     .where(m.Order.phone == phone, m.Order.status == "delivered")) or 0
    orders = db.scalar(select(func.count(m.Order.id)).where(m.Order.phone == phone, m.Order.status == "delivered")) or 0
    used = db.scalar(select(func.count(m.Order.id)).where(m.Order.phone == phone, m.Order.fam_discount > 0,
                                                           m.Order.status != "cancelled")) or 0
    return _shape(int(sips), int(used), int(orders))


def members(db, limit: int = 100) -> list[dict]:
    rows = db.execute(select(m.Order.phone, func.sum(m.OrderLine.quantity), func.count(func.distinct(m.Order.id)))
                      .join(m.OrderLine, m.OrderLine.order_id == m.Order.id).where(m.Order.status == "delivered")
                      .group_by(m.Order.phone).order_by(func.sum(m.OrderLine.quantity).desc(), m.Order.phone).limit(limit)).all()
    used = dict(db.execute(select(m.Order.phone, func.count(m.Order.id)).where(m.Order.fam_discount > 0, m.Order.status != "cancelled")
                           .group_by(m.Order.phone)).all())
    return [dict(phone=p, **_shape(int(s), int(used.get(p, 0)), int(o))) for p, s, o in rows]


def reward_discount(items_by_id: dict, lines) -> int | None:
    """Rupees off for one free drink: the cheapest drink in the cart. None when any dish has no price."""
    prices = [items_by_id[l.item_id].price for l in lines]
    if any(not p or p <= 0 for p in prices):
        return None
    return min(prices)
