"""Owner dashboard maths, on a throw-away in-memory database with orders at exact times."""
import os, pathlib, tempfile
# same set-up as test_api.py, so the two files work in any order
_db = pathlib.Path(tempfile.mkdtemp()) / "t.db"
os.environ.update(DATABASE_URL=os.environ.get("TEST_DATABASE_URL", f"sqlite:///{_db}"), JWT_SECRET="x" * 40, ADMIN_USERNAME="owner",
                  CORS_ORIGINS="https://site.example")
from app.security import hash_password
os.environ["ADMIN_PASSWORD_HASH"] = hash_password("correct horse battery")

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import dashboard, models as m
from app.db import Base
from app.outlets import IST

NOW = datetime(2026, 11, 21, 12, 0, tzinfo=IST)


@pytest.fixture()
def db():
    eng = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(eng)
    with sessionmaker(eng, expire_on_commit=False)() as s:
        s.add_all([m.MenuItem(id=1, category="classic", position=1, name="Matcha"), m.MenuItem(id=2, category="cloud", position=1, name="Cloud")])
        s.add_all([m.Outlet(id=1, slug="a", name="Outlet A", city="X", address="a", position=1)])
        s.commit()
        yield s


def order(db, when_ist, phone, amount, lines, status="new", pay="cod", pstatus="unpaid", fam=0, outlet=None):
    o = m.Order(created_at=when_ist.astimezone(timezone.utc), customer_name="T", phone=phone, address="a" * 10, city="X", status=status,
                payment_method=pay, payment_status=pstatus, amount=amount, fam_discount=fam, outlet_id=outlet)
    o.lines = [m.OrderLine(item_id=i, item_name=n, quantity=q, price=p) for i, n, q, p in lines]
    db.add(o)
    db.commit()
    return o


def test_empty_shop_gives_zeros_and_every_day(db):
    d = dashboard.build(db, 7, NOW)
    assert d["head"]["orders"] == 0 and d["head"]["sales"] == 0 and d["head"]["orders_change"] is None and d["head"]["average_order"] == 0
    assert [x["date"] for x in d["daily"]][0] == "2026-11-15" and d["daily"][-1]["date"] == "2026-11-21" and len(d["daily"]) == 7
    assert len(d["hours"]) == 24 and d["dishes"] == [] and d["outlets"] == []


def test_numbers_days_comparison_and_customers(db):
    today = NOW.replace(hour=0, minute=0)
    order(db, NOW - timedelta(hours=2), "1111111111", 500, [(1, "Matcha", 2, 250)], pay="upi", pstatus="paid", outlet=1, status="delivered")   # 10:00 today
    order(db, NOW + timedelta(hours=7, minutes=30), "1111111111", 300, [(2, "Cloud", 1, 300)], outlet=1)                                      # 19:30 today
    order(db, today - timedelta(minutes=30), "2222222222", 200, [(1, "Matcha", 1, 200)], status="cancelled")                                  # 23:30 yesterday, cancelled
    order(db, today - timedelta(days=3) + timedelta(hours=14), "3333333333", None, [(1, "Matcha", 1, None)])                                 # no price
    order(db, today + timedelta(minutes=10), "4444444444", 100, [(2, "Cloud", 1, 100)], fam=100)                                              # 00:10 today (18:40 UTC yesterday)
    order(db, today - timedelta(days=9) + timedelta(hours=12), "1111111111", 400, [(1, "Matcha", 2, 200)])                                    # previous period

    d = dashboard.build(db, 7, NOW)
    h = d["head"]
    assert (h["orders"], h["sales"], h["collected"], h["average_order"]) == (4, 900, 500, 300)
    assert (h["delivered"], h["cancelled"], h["unpriced_orders"]) == (1, 1, 1)
    assert (h["orders_change"], h["sales_change"]) == (300, 125)               # 1 order and Rs 400 before
    assert (h["fam_free_drinks"], h["fam_discount"]) == (1, 100)

    by_day = {x["date"]: x for x in d["daily"]}
    assert by_day["2026-11-21"] == {"date": "2026-11-21", "orders": 3, "sales": 900}   # the 00:10 order is TODAY in India, not yesterday
    assert by_day["2026-11-20"]["orders"] == 0 and by_day["2026-11-20"]["sales"] == 0   # cancelled order is not a sale
    assert by_day["2026-11-18"]["orders"] == 1

    hours = {x["hour"]: x["orders"] for x in d["hours"]}
    assert hours[10] == 1 and hours[19] == 1 and hours[0] == 1 and hours[14] == 1 and sum(hours.values()) == 4

    assert d["dishes"][0] == {"name": "Matcha", "quantity": 3, "sales": 500} or d["dishes"][0]["name"] in ("Matcha", "Cloud")
    dishes = {x["name"]: x for x in d["dishes"]}
    assert dishes["Matcha"]["quantity"] == 3 and dishes["Matcha"]["sales"] == 500 and dishes["Cloud"]["quantity"] == 2 and dishes["Cloud"]["sales"] == 400

    outs = {x["name"]: x for x in d["outlets"]}
    assert outs["Outlet A"] == {"name": "Outlet A", "orders": 2, "sales": 800} and outs["No outlet chosen"]["orders"] == 2

    p = d["payments"]
    assert p["upi_paid"] == {"orders": 1, "sales": 500} and p["cash_on_delivery"] == {"orders": 3, "sales": 400} and p["upi_waiting"]["orders"] == 0

    c = d["customers"]
    assert c == {"total": 3, "new": 2, "returning": 1, "ordered_twice_or_more": 1}     # 1111111111 also ordered in the earlier period


def test_range_changes_what_is_counted(db):
    order(db, NOW - timedelta(days=20), "5555555555", 250, [(1, "Matcha", 1, 250)])
    assert dashboard.build(db, 7, NOW)["head"]["orders"] == 0
    d30 = dashboard.build(db, 30, NOW)
    assert d30["head"]["orders"] == 1 and len(d30["daily"]) == 30
    assert d30["customers"]["new"] == 1
