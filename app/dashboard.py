"""Numbers for the owner dashboard. Everything is counted from real orders, nothing is stored extra.

Rules the owner can rely on:
- Cancelled orders are never counted as sales or orders.
- "Sales" is the rupee value of orders placed (cancelled excluded). "Collected" is what is marked Paid.
- Orders without a price (a dish had no price when it was ordered) count as orders but add no rupees.
- Days and hours are Indian time.
"""
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from . import models as m
from .outlets import IST


def _ist(dt: datetime) -> datetime:
    """SQLite hands back times without a zone. They are stored in UTC, so say so before converting."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(IST)


def _pct(now: float, before: float) -> int | None:
    """Percent change, or None when there is nothing to compare with."""
    if not before:
        return None
    return round((now - before) * 100 / before)


def _money(orders) -> int:
    return sum(o.amount or 0 for o in orders)


def build(db: Session, days: int, now: datetime | None = None) -> dict:
    now = (now or datetime.now(IST)).astimezone(IST)
    today = now.replace(hour=0, minute=0, second=0, microsecond=0)
    start = today - timedelta(days=days - 1)
    prev_start = start - timedelta(days=days)

    rows = db.scalars(
        select(m.Order).options(selectinload(m.Order.lines), selectinload(m.Order.outlet))
        .where(m.Order.created_at >= prev_start.astimezone(timezone.utc))
        .order_by(m.Order.id)
    ).all()
    stamped = [(o, _ist(o.created_at)) for o in rows]
    cur = [(o, t) for o, t in stamped if t >= start]
    prev = [o for o, t in stamped if prev_start <= t < start and o.status != "cancelled"]
    live = [(o, t) for o, t in cur if o.status != "cancelled"]
    live_orders = [o for o, _ in live]

    # ---- headline numbers
    sales, before_sales = _money(live_orders), _money(prev)
    priced = [o for o in live_orders if o.amount]
    collected = _money([o for o in live_orders if o.payment_status == "paid"])
    head = dict(
        orders=len(live), orders_change=_pct(len(live), len(prev)),
        sales=sales, sales_change=_pct(sales, before_sales),
        collected=collected,
        average_order=round(sales / len(priced)) if priced else 0,
        delivered=sum(1 for o in live_orders if o.status == "delivered"),
        cancelled=sum(1 for o, _ in cur if o.status == "cancelled"),
        unpriced_orders=len(live) - len(priced),
        fam_free_drinks=sum(1 for o in live_orders if o.fam_discount),
        fam_discount=sum(o.fam_discount or 0 for o in live_orders),
    )

    # ---- day by day (every day shown, even with no orders)
    per_day: dict[str, list[int]] = {}
    for i in range(days):
        per_day[(start + timedelta(days=i)).strftime("%Y-%m-%d")] = [0, 0]
    for o, t in live:
        d = per_day[t.strftime("%Y-%m-%d")]
        d[0] += 1
        d[1] += o.amount or 0
    daily = [dict(date=k, orders=v[0], sales=v[1]) for k, v in per_day.items()]

    # ---- dishes (by name, so a dish edited later is still one row)
    dish: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    for o in live_orders:
        for ln in o.lines:
            row = dish[ln.item_name]
            row[0] += ln.quantity
            row[1] += (ln.price or 0) * ln.quantity
    top = sorted(dish.items(), key=lambda kv: (-kv[1][0], kv[0]))[:8]
    dishes = [dict(name=k, quantity=v[0], sales=v[1]) for k, v in top]

    # ---- outlets
    out: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    for o in live_orders:
        row = out[o.outlet_name or "No outlet chosen"]
        row[0] += 1
        row[1] += o.amount or 0
    outlets = [dict(name=k, orders=v[0], sales=v[1]) for k, v in sorted(out.items(), key=lambda kv: (-kv[1][0], kv[0]))]

    # ---- busiest hours
    hours = Counter(t.hour for _, t in live)
    by_hour = [dict(hour=h, orders=hours.get(h, 0)) for h in range(24)]

    # ---- how people pay
    def bucket(pred):
        sel = [o for o in live_orders if pred(o)]
        return dict(orders=len(sel), sales=_money(sel))
    payments = dict(
        upi_paid=bucket(lambda o: o.payment_method == "upi" and o.payment_status == "paid"),
        upi_waiting=bucket(lambda o: o.payment_method == "upi" and o.payment_status in ("pending", "failed")),
        cash_paid=bucket(lambda o: o.payment_method != "upi" and o.payment_status == "paid"),
        cash_on_delivery=bucket(lambda o: o.payment_method != "upi" and o.payment_status != "paid"),
    )

    # ---- customers (a customer is a mobile number)
    seen_before = {o.phone for o, t in stamped if t < start and o.status != "cancelled"}
    older = db.scalars(
        select(m.Order.phone).where(m.Order.created_at < prev_start.astimezone(timezone.utc), m.Order.status != "cancelled").distinct()
    ).all()
    seen_before |= set(older)
    counts = Counter(o.phone for o in live_orders)
    customers = dict(
        total=len(counts),
        new=sum(1 for p in counts if p not in seen_before),
        returning=sum(1 for p in counts if p in seen_before),
        ordered_twice_or_more=sum(1 for c in counts.values() if c >= 2),
    )

    return dict(
        days=days, from_date=start.strftime("%Y-%m-%d"), to_date=today.strftime("%Y-%m-%d"),
        head=head, daily=daily, dishes=dishes, outlets=outlets, hours=by_hour, payments=payments, customers=customers,
        pending_reviews=db.scalar(select(func.count(m.Review.id)).where(m.Review.approved.is_(False))) or 0,
    )
