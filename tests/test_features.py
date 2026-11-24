"""Order alerts (Telegram), birthday and comeback points, and the sign-up phone code."""
import os, pathlib, tempfile
_db = pathlib.Path(tempfile.mkdtemp()) / "t.db"   # same set-up as the other test files, so they work in any order
os.environ.update(DATABASE_URL=os.environ.get("TEST_DATABASE_URL", f"sqlite:///{_db}"), JWT_SECRET="x" * 40, ADMIN_USERNAME="owner", CORS_ORIGINS="https://site.example")
from app.security import hash_password
os.environ["ADMIN_PASSWORD_HASH"] = hash_password("correct horse battery")

from datetime import datetime, timedelta
import pytest
from fastapi.testclient import TestClient
from app.main import app
from app.config import get_settings
from app.db import Base, SessionLocal, engine
from app import loyalty, models as m, notify
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


def signup(c, phone, **kw):
    r = c.post("/api/customers/register", json=dict(name="Test User", phone=phone, password="secret1", **kw))
    return r


# ---------- Telegram alerts ----------
@pytest.fixture
def telegram(monkeypatch):
    st = get_settings()
    monkeypatch.setattr(st, "telegram_bot_token", "123:abc")
    monkeypatch.setattr(st, "telegram_chat_id", "999")
    sent = []
    monkeypatch.setattr(notify, "send", lambda text, site="": (sent.append(text) or (True, "Sent")))
    return sent


def order_body(phone="9822222222", **kw):
    return dict(customer_name="Meera <b>", phone=phone, address="12 MG Road, Indiranagar", city="Bengaluru",
                lines=[dict(item_id=1, quantity=2)], **kw)


def test_alert_not_configured_is_silent(c):
    assert notify.configured() is False
    r = c.post("/api/orders", json=order_body("9822222201"))
    assert r.status_code == 201
    assert c.get("/api/admin/alerts", headers=admin(c)).json() == {"configured": False}
    assert c.post("/api/admin/alerts/test", headers=admin(c)).status_code == 422


def test_cod_order_sends_alert_with_escaped_text(c, telegram):
    r = c.post("/api/orders", json=order_body("9822222202"))
    assert r.status_code == 201
    assert len(telegram) == 1
    t = telegram[0]
    assert f"#{r.json()['id']}" in t and "2 ×" in t and "Pay at delivery" in t
    assert "Meera &lt;b&gt;" in t and "<b>Meera" not in t          # customer text can't inject markup


def test_alert_failure_never_breaks_an_order(c, monkeypatch):
    st = get_settings()
    monkeypatch.setattr(st, "telegram_bot_token", "123:abc")
    monkeypatch.setattr(st, "telegram_chat_id", "999")
    def boom(*a, **k):
        raise RuntimeError("telegram down")
    monkeypatch.setattr(notify, "send", boom)
    assert c.post("/api/orders", json=order_body("9822222203")).status_code == 201


def test_upi_order_alerts_only_after_payment(c, telegram):
    r = c.post("/api/orders", json=order_body("9822222204", pay="upi"))
    assert r.status_code == 201, r.text
    oid = r.json()["id"]
    assert telegram == []                                           # not paid yet: no ping
    p = c.post(f"/api/orders/{oid}/pay/confirm", json=dict(phone="9822222204"))
    assert p.status_code == 200 and p.json()["payment_status"] == "paid"
    assert len(telegram) == 1 and "Paid by UPI" in telegram[0]
    c.post(f"/api/orders/{oid}/pay/confirm", json=dict(phone="9822222204"))
    assert len(telegram) == 1                                       # a repeat confirm does not ping again


def test_alert_test_button(c, telegram):
    assert c.get("/api/admin/alerts", headers=admin(c)).json() == {"configured": True}
    assert c.post("/api/admin/alerts/test", headers=admin(c)).json()["ok"] is True
    assert c.post("/api/admin/alerts/test").status_code in (401, 403)


# ---------- birthday + comeback ----------
def customer(phone):
    with SessionLocal() as db:
        return db.query(m.Customer).filter_by(phone=phone).one().id


def test_rules_include_new_numbers_and_owner_can_edit(c):
    r = c.get("/api/loyalty").json()
    assert (r["birthday_points"], r["comeback_days"], r["comeback_points"], r["otp"]) == (50, 14, 25, False)
    p = c.patch("/api/admin/loyalty", headers=admin(c), json=dict(birthday_points=70, comeback_days=10, comeback_points=15))
    assert p.status_code == 200 and (p.json()["birthday_points"], p.json()["comeback_days"], p.json()["comeback_points"]) == (70, 10, 15)
    assert c.patch("/api/admin/loyalty", headers=admin(c), json=dict(comeback_days=1)).status_code == 422
    c.patch("/api/admin/loyalty", headers=admin(c), json=dict(birthday_points=50, comeback_days=14, comeback_points=25))


def test_comeback_points_after_a_long_break(c):
    tok = signup(c, "9833333301").json()["access_token"]
    cid = customer("9833333301")
    with SessionLocal() as db:   # pretend the last visit was 20 days ago
        cu = db.get(m.Customer, cid)
        cu.last_visit = (datetime.now(loyalty.IST) - timedelta(days=20)).strftime("%Y-%m-%d")
        db.commit()
    me = c.get("/api/me", headers=auth(tok)).json()
    assert me["visit_awarded"] == 35                                 # 10 visit + 25 comeback
    assert [h["reason"] for h in me["history"]][:2] == ["comeback", "visit"]
    assert "20 days" in me["history"][0]["note"]
    again = c.get("/api/me", headers=auth(tok)).json()
    assert again["visit_awarded"] == 0                              # only once per day


def test_no_comeback_for_a_short_break(c):
    tok = signup(c, "9833333302").json()["access_token"]
    with SessionLocal() as db:
        cu = db.get(m.Customer, customer("9833333302"))
        cu.last_visit = (datetime.now(loyalty.IST) - timedelta(days=3)).strftime("%Y-%m-%d")
        db.commit()
    assert c.get("/api/me", headers=auth(tok)).json()["visit_awarded"] == 10


def test_birthday_is_set_once_and_paid_once_a_year(c):
    tok = signup(c, "9833333303").json()["access_token"]
    today = datetime.now(loyalty.IST).strftime("%m-%d")
    r = c.patch("/api/me", headers=auth(tok), json=dict(birthday=today))
    assert r.status_code == 200 and r.json()["birthday"] == today
    assert r.json()["points"] == 60                                  # account made today: no birthday points yet
    assert c.patch("/api/me", headers=auth(tok), json=dict(birthday="01-01")).status_code == 422   # locked
    with SessionLocal() as db:   # make the account a week old and forget today's visit
        cu = db.get(m.Customer, customer("9833333303"))
        cu.created_at = datetime.now(loyalty.IST) - timedelta(days=7)
        cu.last_visit = None
        db.commit()
    me = c.get("/api/me", headers=auth(tok)).json()
    assert "birthday" in [h["reason"] for h in me["history"]] and me["visit_awarded"] == 60   # 10 visit + 50 birthday
    with SessionLocal() as db:
        db.get(m.Customer, customer("9833333303")).last_visit = None
        db.commit()
    again = c.get("/api/me", headers=auth(tok)).json()
    assert [h["reason"] for h in again["history"]].count("birthday") == 1


def test_birthday_must_be_a_real_date(c):
    tok = signup(c, "9833333304").json()["access_token"]
    assert c.patch("/api/me", headers=auth(tok), json=dict(birthday="13-40")).status_code == 422
    assert c.patch("/api/me", headers=auth(tok), json=dict(birthday="02-29")).status_code == 200


# ---------- phone code ----------
@pytest.fixture
def otp_on(monkeypatch):
    monkeypatch.setattr(get_settings(), "otp_mode", "demo")


def test_otp_off_by_default_register_works_without_code(c):
    assert c.post("/api/customers/otp", json=dict(phone="9844444401")).json() == {"required": False}
    assert signup(c, "9844444401").status_code == 201


def test_otp_register_needs_the_right_code(c, otp_on):
    assert c.get("/api/loyalty").json()["otp"] is True
    assert signup(c, "9844444402").status_code == 422                         # no code
    code = c.post("/api/customers/otp", json=dict(phone="9844444402")).json()["demo_code"]
    assert len(code) == 6 and code.isdigit()
    wrong = "000000" if code != "000000" else "111111"
    assert signup(c, "9844444402", otp=wrong).status_code == 422
    r = signup(c, "9844444402", otp=code)
    assert r.status_code == 201 and r.json()["me"]["points"] == 60
    assert signup(c, "9844444402", otp=code).status_code == 409               # number now taken


def test_otp_code_is_for_that_number_only_and_is_single_use(c, otp_on):
    code = c.post("/api/customers/otp", json=dict(phone="9844444403")).json()["demo_code"]
    assert signup(c, "9844444404", otp=code).status_code == 422               # another number can't use it
    assert signup(c, "9844444403", otp=code).status_code == 201
    with SessionLocal() as db:
        assert db.query(m.OtpCode).filter_by(phone="9844444403").count() == 0   # used code is deleted


def test_otp_locks_after_five_wrong_tries_and_limits_requests(c, otp_on):
    phone = "9844444405"
    code = c.post("/api/customers/otp", json=dict(phone=phone)).json()["demo_code"]
    wrong = "000000" if code != "000000" else "111111"
    for _ in range(5):
        assert signup(c, phone, otp=wrong).status_code == 422
    assert signup(c, phone, otp=code).status_code == 422                      # locked, even with the right code
    c.post("/api/customers/otp", json=dict(phone=phone))
    c.post("/api/customers/otp", json=dict(phone=phone))
    assert c.post("/api/customers/otp", json=dict(phone=phone)).status_code == 429   # 4th ask in an hour


def test_otp_refuses_numbers_that_already_have_an_account(c, otp_on):
    assert c.post("/api/customers/otp", json=dict(phone="9833333301")).status_code == 409
