"""Latcha points: sign up, daily visit reward, spend 500 points for Rs 50 off, refund on cancel, owner edits."""
import os, pathlib, tempfile
_db = pathlib.Path(tempfile.mkdtemp()) / "t.db"   # same set-up as test_api.py, so the files work in any order
os.environ.update(DATABASE_URL=os.environ.get("TEST_DATABASE_URL", f"sqlite:///{_db}"), JWT_SECRET="x" * 40, ADMIN_USERNAME="owner", CORS_ORIGINS="https://site.example")
from app.security import hash_password
os.environ["ADMIN_PASSWORD_HASH"] = hash_password("correct horse battery")

from datetime import datetime, timedelta
import pytest
from fastapi.testclient import TestClient
from app.main import app
from app.db import Base, SessionLocal, engine
from app import loyalty, models as m
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


def auth(tok):
    return {"Authorization": "Bearer " + tok}


def give(phone, n):
    with SessionLocal() as db:
        cu = db.query(m.Customer).filter_by(phone=phone).one()
        loyalty.add_tx(db, cu, n, "adjust", "test")
        db.commit()


def order(items=2, **kw):
    return dict(customer_name="Asha", phone="9811111111", address="12 MG Road, Indiranagar", outlet="jp-nagar",
                lines=[dict(item_id=1, quantity=items)], **kw)


def test_register_gives_welcome_and_first_visit_points(c):
    r = c.post("/api/customers/register", json=dict(name="Asha K", phone="98111 11111", password="secret1"))
    assert r.status_code == 201
    me = r.json()["me"]
    assert me["phone"] == "9811111111" and me["points"] == 60 and me["visit_awarded"] == 60   # 50 welcome + 10 visit
    assert [h["reason"] for h in me["history"]] == ["visit", "welcome"]
    assert c.post("/api/customers/register", json=dict(name="Other", phone="9811111111", password="secret1")).status_code == 409


def test_login_and_visit_reward_once_a_day(c):
    r = c.post("/api/customers/login", json=dict(phone="9811111111", password="secret1"))
    assert r.status_code == 200
    h = auth(r.json()["access_token"])
    assert r.json()["me"]["visit_awarded"] == 0       # already rewarded today by registering
    assert c.get("/api/me", headers=h).json()["points"] == 60
    with SessionLocal() as db:                         # pretend the last visit was yesterday
        db.query(m.Customer).filter_by(phone="9811111111").one().last_visit = (datetime.now(loyalty.IST) - timedelta(days=1)).strftime("%Y-%m-%d")
        db.commit()
    assert c.get("/api/me", headers=h).json()["visit_awarded"] == 10
    assert c.get("/api/me", headers=h).json()["points"] == 70
    assert c.get("/api/me", headers=h).json()["visit_awarded"] == 0


def test_wrong_password_and_no_token(c):
    assert c.post("/api/customers/login", json=dict(phone="9811111111", password="nope123")).status_code == 401
    assert c.get("/api/me").status_code == 401
    assert c.get("/api/me", headers=auth("junk")).status_code == 401


def test_customer_token_is_not_an_admin_token(c):
    tok = c.post("/api/customers/login", json=dict(phone="9811111111", password="secret1")).json()["access_token"]
    assert c.get("/api/admin/orders", headers=auth(tok)).status_code == 401


def test_redeem_500_points_for_50_rupees_and_refund_on_cancel(c):
    h = admin(c)
    assert c.patch("/api/admin/menu/1", json={"price": 300}, headers=h).status_code == 200
    tok = c.post("/api/customers/login", json=dict(phone="9811111111", password="secret1")).json()["access_token"]
    # not enough points yet
    assert c.post("/api/orders", json=order(use_points=True), headers=auth(tok)).status_code == 422
    assert c.post("/api/orders", json=order(use_points=True)).status_code == 401      # not signed in
    give("9811111111", 430)                                                            # 70 + 430 = exactly 500
    r = c.post("/api/orders", json=order(use_points=True), headers=auth(tok))
    assert r.status_code == 201, r.text
    b = r.json()
    assert b["points_used"] == 500 and b["points_discount"] == 50 and b["amount"] == 550 and b["points"] == 0
    me = c.get("/api/me", headers=auth(tok)).json()
    assert me["points"] == 0 and me["orders"][0]["points_discount"] == 50 and me["history"][0]["reason"] == "redeem"
    # cancelling gives the points back, once
    assert c.patch(f"/api/admin/orders/{b['id']}", json={"status": "cancelled"}, headers=h).status_code == 200
    assert c.patch(f"/api/admin/orders/{b['id']}", json={"status": "cancelled"}, headers=h).status_code == 200
    assert c.get("/api/me", headers=auth(tok)).json()["points"] == 500
    # un-cancelling takes them again
    c.patch(f"/api/admin/orders/{b['id']}", json={"status": "confirmed"}, headers=h)
    assert c.get("/api/me", headers=auth(tok)).json()["points"] == 0


def test_discount_never_exceeds_the_bill_and_uses_whole_blocks(c):
    tok = c.post("/api/customers/login", json=dict(phone="9811111111", password="secret1")).json()["access_token"]
    give("9811111111", 5000)                          # plenty of points
    r = c.post("/api/orders", json=order(items=1, use_points=True), headers=auth(tok))   # bill Rs 300 -> 6 blocks of Rs 50 max
    b = r.json()
    assert b["points_discount"] == 300 and b["points_used"] == 3000 and b["amount"] == 0
    assert c.get("/api/me", headers=auth(tok)).json()["points"] == 5000 - 3000


def test_order_without_points_is_unchanged_and_linked_to_customer(c):
    tok = c.post("/api/customers/login", json=dict(phone="9811111111", password="secret1")).json()["access_token"]
    before = c.get("/api/me", headers=auth(tok)).json()
    r = c.post("/api/orders", json=order(items=1), headers=auth(tok))
    assert r.status_code == 201 and r.json()["points_discount"] == 0 and r.json()["amount"] == 300
    after = c.get("/api/me", headers=auth(tok)).json()
    assert after["points"] == before["points"] and len(after["orders"]) == len(before["orders"]) + 1


def test_owner_edits_the_rules_and_the_shop_follows(c):
    h = admin(c)
    assert c.get("/api/loyalty").json() == dict(enabled=True, signup_bonus=50, visit_points=10, redeem_points=500, redeem_rupees=50,
                                                birthday_points=50, comeback_days=14, comeback_points=25, otp=False)
    r = c.patch("/api/admin/loyalty", json=dict(redeem_points=200, redeem_rupees=40, visit_points=25), headers=h)
    assert r.json()["redeem_points"] == 200 and r.json()["redeem_rupees"] == 40 and r.json()["visit_points"] == 25
    assert c.get("/api/loyalty").json()["redeem_points"] == 200
    assert c.patch("/api/admin/loyalty", json=dict(redeem_points=0), headers=h).status_code == 422
    assert c.patch("/api/admin/loyalty", json=dict(visit_points=5), headers={}).status_code == 401
    r = c.post("/api/customers/register", json=dict(name="Ravi", phone="9822222222", password="secret1"))
    assert r.json()["me"]["points"] == 75                      # 50 welcome + 25 visit
    c.patch("/api/admin/loyalty", json=dict(enabled=False), headers=h)
    r = c.post("/api/customers/register", json=dict(name="Mina", phone="9833333333", password="secret1"))
    assert r.json()["me"]["points"] == 0                       # switched off: no points are handed out
    tok = r.json()["access_token"]
    give("9833333333", 1000)
    assert c.post("/api/orders", json=order(use_points=True), headers=auth(tok)).status_code == 422
    c.patch("/api/admin/loyalty", json=dict(enabled=True, redeem_points=500, redeem_rupees=50, visit_points=10), headers=h)


def test_owner_sees_customers_and_adjusts_points(c):
    h = admin(c)
    rows = c.get("/api/admin/customers", headers=h).json()
    assert {r["phone"] for r in rows} >= {"9811111111", "9822222222"}
    assert c.get("/api/admin/customers?q=Ravi", headers=h).json()[0]["name"] == "Ravi"
    cid = next(r["id"] for r in rows if r["phone"] == "9822222222")
    before = next(r["points"] for r in rows if r["id"] == cid)
    r = c.post(f"/api/admin/customers/{cid}/adjust", json=dict(delta=100, note="Birthday"), headers=h)
    assert r.json()["points"] == before + 100
    r = c.post(f"/api/admin/customers/{cid}/adjust", json=dict(delta=-100000), headers=h)
    assert r.json()["points"] == 0                              # never below zero
    assert c.get("/api/admin/customers").status_code == 401


def test_change_name_and_password(c):
    tok = c.post("/api/customers/login", json=dict(phone="9822222222", password="secret1")).json()["access_token"]
    assert c.patch("/api/me", json=dict(name="Ravi Kumar"), headers=auth(tok)).json()["name"] == "Ravi Kumar"
    assert c.patch("/api/me", json=dict(new_password="newpass9", current_password="wrong"), headers=auth(tok)).status_code == 422
    assert c.patch("/api/me", json=dict(new_password="newpass9", current_password="secret1"), headers=auth(tok)).status_code == 200
    assert c.post("/api/customers/login", json=dict(phone="9822222222", password="newpass9")).status_code == 200


def test_review_while_signed_in_shows_in_my_account(c):
    tok = c.post("/api/customers/login", json=dict(phone="9822222222", password="newpass9")).json()["access_token"]
    assert c.post("/api/reviews", json=dict(author="Ravi", rating=5, text="Lovely matcha, truly."), headers=auth(tok)).status_code == 202
    me = c.get("/api/me", headers=auth(tok)).json()
    assert me["reviews"][0]["rating"] == 5 and me["reviews"][0]["approved"] is False


def test_points_cannot_be_spent_twice(c):
    """Two orders racing for one balance: the second spend must fail, never push the balance below zero."""
    with SessionLocal() as db:
        cu = db.query(m.Customer).filter_by(phone="9811111111").one()
        loyalty.add_tx(db, cu, 500 - cu.points, "adjust", "test")
        db.commit()
        stale = db.query(m.Customer).filter_by(phone="9811111111").one()   # both "requests" saw 500 points
        assert loyalty.spend(db, stale, 500, "first") is True
        assert loyalty.spend(db, stale, 500, "second") is False
        db.commit()
        assert stale.points == 0


def test_order_is_refused_and_not_saved_when_points_vanish(c, monkeypatch):
    tok = c.post("/api/customers/login", json=dict(phone="9811111111", password="secret1")).json()["access_token"]
    with SessionLocal() as db:
        before = db.query(m.Order).count()
    monkeypatch.setattr(loyalty, "redeem", lambda db, cu, charge: (500, 50))   # balance read before another order spent it
    r = c.post("/api/orders", json=order(use_points=True), headers=auth(tok))
    assert r.status_code == 409, r.text
    with SessionLocal() as db:
        assert db.query(m.Order).count() == before
    assert c.get("/api/me", headers=auth(tok)).json()["points"] >= 0
