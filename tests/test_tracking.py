"""Live order tracking (private link, "on the way" step) and the extra owner dashboard numbers + CSV export."""
import os, pathlib, tempfile
_db = pathlib.Path(tempfile.mkdtemp()) / "t.db"   # same set-up as the other test files, so they work in any order
os.environ.update(DATABASE_URL=os.environ.get("TEST_DATABASE_URL", f"sqlite:///{_db}"), JWT_SECRET="x" * 40, ADMIN_USERNAME="owner", CORS_ORIGINS="https://site.example")
from app.security import hash_password
os.environ["ADMIN_PASSWORD_HASH"] = hash_password("correct horse battery")

from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import dashboard, models as m
from app.db import Base, SessionLocal, engine
from app.main import app
from app.outlets import IST
from scripts.init_db import seed


@pytest.fixture(scope="module")
def c():
    Base.metadata.create_all(engine)
    with SessionLocal() as db:
        seed(db)
    return TestClient(app)


def admin(c):
    r = c.post("/api/admin/login", json={"username": "owner", "password": "correct horse battery"})
    return {"Authorization": "Bearer " + r.json()["access_token"]}


def place(c, phone="9200000001", name="Track Person"):
    item = c.get("/api/menu").json()[0]["id"]
    r = c.post("/api/orders", json={"customer_name": name, "phone": phone, "address": "12 Park Road, Hyderabad", "city": "Hyderabad",
                                    "lines": [{"item_id": item, "quantity": 1}]})
    assert r.status_code == 201, r.text
    return r.json()


# ---------- tracking link ----------
def test_receipt_has_a_private_code_and_the_link_shows_the_order(c):
    r = place(c)
    code = r["track"]
    assert code.startswith(f"{r['id']}.") and len(code.split(".")[1]) == 10
    t = c.get(f"/api/orders/track/{code}")
    assert t.status_code == 200
    b = t.json()
    assert b["id"] == r["id"] and b["status"] == "new" and b["code"] == code
    assert "phone" not in b and "address" not in b and "customer_name" not in b   # no private details


def test_wrong_or_made_up_codes_all_look_the_same(c):
    r = place(c, "9200000002")
    num, sig = r["track"].split(".")
    for bad in (f"{num}.{'0' * 10}", f"{num}.", num, f"{int(num) + 999}.{sig}", "abc.def", "9999999999999.aa", f"{num}.é", "٣.٤", "1.2.3", f"{num}.{sig}x"):
        x = c.get(f"/api/orders/track/{bad}")
        assert x.status_code == 404, bad
        assert x.json()["detail"] == "We could not find that order. Check the link and try again."


def test_code_of_one_order_does_not_open_another(c):
    a, b = place(c, "9200000003"), place(c, "9200000004")
    assert c.get(f"/api/orders/track/{b['id']}.{a['track'].split('.')[1]}").status_code == 404


def test_phone_tracking_also_returns_the_code(c):
    r = place(c, "9200000005")
    x = c.post("/api/orders/track", json={"order_id": r["id"], "phone": "9200000005"}).json()
    assert x["code"] == r["track"]


# ---------- on the way ----------
def test_owner_can_send_an_order_out_and_the_customer_sees_it(c):
    h = admin(c)
    r = place(c, "9200000006")
    assert c.get(f"/api/orders/track/{r['track']}").json()["status_at"] is None
    for st in ("confirmed", "out"):
        assert c.patch(f"/api/admin/orders/{r['id']}", json={"status": st}, headers=h).status_code == 200
    t = c.get(f"/api/orders/track/{r['track']}").json()
    assert t["status"] == "out" and t["status_at"]
    assert c.get("/api/admin/stats", headers=h).json()["on_the_way"] >= 1
    assert c.patch(f"/api/admin/orders/{r['id']}", json={"status": "delivered"}, headers=h).json()["status"] == "delivered"


def test_same_status_does_not_move_the_time(c):
    h = admin(c)
    r = place(c, "9200000007")
    c.patch(f"/api/admin/orders/{r['id']}", json={"status": "confirmed"}, headers=h)
    first = c.get(f"/api/orders/track/{r['track']}").json()["status_at"]
    c.patch(f"/api/admin/orders/{r['id']}", json={"status": "confirmed"}, headers=h)
    assert c.get(f"/api/orders/track/{r['track']}").json()["status_at"] == first


def test_unknown_status_is_refused(c):
    r = place(c, "9200000008")
    assert c.patch(f"/api/admin/orders/{r['id']}", json={"status": "teleported"}, headers=admin(c)).status_code == 422


# ---------- CSV export ----------
def test_export_needs_the_owner_login(c):
    assert c.get("/api/admin/export.csv").status_code in (401, 403)


def test_export_has_the_order_and_blocks_spreadsheet_formulas(c):
    place(c, "9200000009", name="=HYPERLINK(\"http://x\")")
    x = c.get("/api/admin/export.csv?days=7", headers=admin(c))
    assert x.status_code == 200 and x.headers["content-type"].startswith("text/csv")
    assert "attachment" in x.headers["content-disposition"] and ".csv" in x.headers["content-disposition"]
    txt = x.content.decode("utf-8")
    assert txt.startswith("﻿") and "Order,Placed (IST),Status,Customer" in txt
    assert "'=HYPERLINK" in txt and ",=HYPERLINK" not in txt
    assert "9200000009" in txt
    assert c.get("/api/admin/export.csv?days=0", headers=admin(c)).status_code == 422


# ---------- dashboard numbers ----------
NOW = datetime(2026, 11, 21, 12, 0, tzinfo=IST)


@pytest.fixture()
def db():
    eng = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(eng)
    with sessionmaker(eng, expire_on_commit=False)() as s:
        s.add_all([m.MenuItem(id=1, category="classic", position=1, name="Matcha")])
        s.commit()
        yield s


def mk(db, when, phone, status="new", took=None, amount=100):
    o = m.Order(created_at=when.astimezone(timezone.utc), customer_name="T", phone=phone, address="a" * 10, city="X", status=status, amount=amount)
    if took is not None:
        o.status_at = (when + timedelta(minutes=took)).astimezone(timezone.utc)
    o.lines = [m.OrderLine(item_id=1, item_name="Matcha", quantity=1, price=amount)]
    db.add(o)
    db.commit()


def test_average_delivery_time_cancel_and_repeat_rates(db):
    mk(db, NOW - timedelta(hours=3), "1", "delivered", took=20)
    mk(db, NOW - timedelta(hours=2), "2", "delivered", took=40)
    mk(db, NOW - timedelta(hours=1), "3", "delivered")                   # no time recorded: not averaged
    mk(db, NOW - timedelta(minutes=30), "4", "cancelled")
    mk(db, NOW - timedelta(days=20), "1")                                 # phone 1 ordered before this period
    h = dashboard.build(db, 7, NOW)["head"]
    assert h["avg_delivery_minutes"] == 30 and h["delivered_timed"] == 2
    assert h["cancel_rate"] == 25                                         # 1 of 4 placed in the period
    assert h["repeat_rate"] == 33                                         # 1 of 3 customers had ordered before


def test_no_delivery_times_gives_none_and_zero_rates(db):
    h = dashboard.build(db, 7, NOW)["head"]
    assert h["avg_delivery_minutes"] is None and h["cancel_rate"] == 0 and h["repeat_rate"] == 0


def test_weekdays_use_indian_days(db):
    mk(db, datetime(2026, 11, 21, 0, 10, tzinfo=IST), "1", amount=200)    # Saturday 00:10 IST (Friday in UTC)
    mk(db, datetime(2026, 11, 20, 9, 0, tzinfo=IST), "2", amount=100)     # Friday
    wk = {x["day"]: x for x in dashboard.build(db, 7, NOW)["weekdays"]}
    assert len(wk) == 7 and wk["Sat"] == {"day": "Sat", "orders": 1, "sales": 200} and wk["Fri"]["orders"] == 1 and wk["Mon"]["orders"] == 0


def test_owner_order_list_includes_when_the_status_changed(c):
    h = admin(c)
    r = place(c, "9200000010")
    row = next(o for o in c.get("/api/admin/orders", headers=h).json() if o["id"] == r["id"])
    assert row["status_at"] is None
    c.patch(f"/api/admin/orders/{r['id']}", json={"status": "confirmed"}, headers=h)
    row = next(o for o in c.get("/api/admin/orders", headers=h).json() if o["id"] == r["id"])
    assert row["status_at"]
