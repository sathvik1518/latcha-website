import os, pathlib, tempfile
_db = pathlib.Path(tempfile.mkdtemp()) / "t.db"
os.environ.update(DATABASE_URL=os.environ.get("TEST_DATABASE_URL", f"sqlite:///{_db}"), JWT_SECRET="x" * 40, ADMIN_USERNAME="owner",
                  CORS_ORIGINS="https://site.example")
from app.security import hash_password
os.environ["ADMIN_PASSWORD_HASH"] = hash_password("correct horse battery")

import pytest
from fastapi.testclient import TestClient
from app.main import app
from app.db import Base, SessionLocal, engine
from scripts.init_db import seed


@pytest.fixture(scope="module")
def c():
    Base.metadata.create_all(engine)
    with SessionLocal() as db:
        assert seed(db) == 33
        assert seed(db) == 0  # second run does nothing
    return TestClient(app)


def tok(c):
    r = c.post("/api/admin/login", json={"username": "owner", "password": "correct horse battery"})
    assert r.status_code == 200
    return {"Authorization": "Bearer " + r.json()["access_token"]}


ORDER = dict(customer_name="Asha", phone="+91 98765 43210", address="12 MG Road, Indiranagar",
             city="Bengaluru", lines=[dict(item_id=1, quantity=2)])


def test_health(c):
    assert c.get("/api/health").json() == {"ok": True}


def test_menu_counts_and_filter(c):
    assert len(c.get("/api/menu").json()) == 33
    assert len(c.get("/api/menu?category=cloud").json()) == 6
    assert c.get("/api/menu?category=nope").status_code == 422
    first = c.get("/api/menu?category=classic").json()[0]
    assert first["name"] == "Pure Iced Matcha Latte" and first["calories"] == 107 and first["position"] == 1


def test_order_flow(c):
    r = c.post("/api/orders", json=ORDER)
    assert r.status_code == 201 and r.json()["status"] == "new"
    h = tok(c)
    o = c.get("/api/admin/orders", headers=h).json()[0]
    assert o["phone"] == "9876543210" and o["lines"][0]["item_name"] == "Pure Iced Matcha Latte"
    r = c.patch(f"/api/admin/orders/{o['id']}", json={"status": "confirmed"}, headers=h)
    assert r.json()["status"] == "confirmed"


@pytest.mark.parametrize("bad", [
    dict(phone="12345"), dict(city="Mumbai"), dict(lines=[]),
    dict(lines=[dict(item_id=1, quantity=0)]), dict(lines=[dict(item_id=1, quantity=21)]),
    dict(lines=[dict(item_id=9999, quantity=1)]), dict(customer_name="A"),
])
def test_order_rejects_bad_input(c, bad):
    assert c.post("/api/orders", json={**ORDER, **bad}).status_code == 422


def test_order_rate_limit(c):
    body = {**ORDER, "phone": "9000000001"}
    codes = [c.post("/api/orders", json=body).status_code for _ in range(6)]
    assert codes == [201] * 5 + [429]


def _place(c, phone, name="Track Test"):
    r = c.post("/api/orders", json={**ORDER, "customer_name": name, "phone": phone})
    assert r.status_code == 201
    return r.json()["id"]


def test_track_shows_status_and_items_but_no_private_details(c):
    oid = _place(c, "9100000001")
    r = c.post("/api/orders/track", json={"order_id": oid, "phone": "+91 91000 00001"})
    assert r.status_code == 200
    body = r.json()
    assert body["id"] == oid and body["status"] == "new"
    assert body["lines"] == [{"item_name": "Pure Iced Matcha Latte", "quantity": 2}]
    assert set(body) == {"id", "created_at", "status", "lines", "payment_method", "payment_status", "amount", "fam_discount"}  # no phone, address or name
    h = tok(c)
    c.patch(f"/api/admin/orders/{oid}", json={"status": "delivered"}, headers=h)
    assert c.post("/api/orders/track", json={"order_id": oid, "phone": "9100000001"}).json()["status"] == "delivered"


def test_track_wrong_phone_and_missing_order_look_the_same(c):
    oid = _place(c, "9100000002")
    wrong = c.post("/api/orders/track", json={"order_id": oid, "phone": "9100000003"})
    missing = c.post("/api/orders/track", json={"order_id": 99999999, "phone": "9100000002"})
    assert wrong.status_code == missing.status_code == 404
    assert wrong.json() == missing.json()


@pytest.mark.parametrize("bad", [
    dict(order_id=0, phone="9000000002"), dict(order_id=-1, phone="9000000002"),
    dict(order_id=1, phone="12345"), dict(order_id="abc", phone="9000000002"), dict(phone="9000000002"),
])
def test_track_rejects_bad_input(c, bad):
    assert c.post("/api/orders/track", json=bad).status_code == 422


def test_unavailable_item_blocked(c):
    h = tok(c)
    c.patch("/api/admin/menu/2", json={"available": False}, headers=h)
    assert c.post("/api/orders", json={**ORDER, "phone": "9000000002",
                  "lines": [dict(item_id=2, quantity=1)]}).status_code == 422
    assert all(i["id"] != 2 for i in c.get("/api/menu").json())
    c.patch("/api/admin/menu/2", json={"available": True}, headers=h)


def test_reviews_need_approval(c):
    body = dict(author="Meera", rating=5, text="The ube cloud latte is dreamy.")
    assert c.post("/api/reviews", json=body).status_code == 202
    assert c.get("/api/reviews").json() == []
    h = tok(c)
    pend = c.get("/api/admin/reviews?approved=false", headers=h).json()
    assert len(pend) == 1
    c.post(f"/api/admin/reviews/{pend[0]['id']}/approve", headers=h)
    pub = c.get("/api/reviews").json()
    assert len(pub) == 1 and "approved" not in pub[0]


def test_honeypot_and_bad_review(c):
    n = len(c.get("/api/admin/reviews", headers=tok(c)).json())
    r = c.post("/api/reviews", json=dict(author="Bot", rating=5, text="Buy cheap things now!!", website="http://spam"))
    assert r.status_code == 202
    assert len(c.get("/api/admin/reviews", headers=tok(c)).json()) == n  # nothing stored
    assert c.post("/api/reviews", json=dict(author="Bo", rating=6, text="long enough text")).status_code == 422


def test_admin_requires_login(c):
    for method, url in [("get", "/api/admin/orders"), ("get", "/api/admin/menu"),
                        ("get", "/api/admin/reviews"), ("delete", "/api/admin/reviews/1")]:
        assert getattr(c, method)(url).status_code == 401
    assert c.get("/api/admin/orders", headers={"Authorization": "Bearer junk"}).status_code == 401
    assert c.post("/api/admin/login", json={"username": "owner", "password": "wrong"}).status_code == 401


def test_menu_edit_validates(c):
    h = tok(c)
    assert c.patch("/api/admin/menu/1", json={"sweetness": 9}, headers=h).status_code == 422
    assert c.patch("/api/admin/menu/99999", json={"name": "x"}, headers=h).status_code == 404
    r = c.post("/api/admin/menu", headers=h, json=dict(category="soft", position=9, name="Test Scoop"))
    assert r.status_code == 201


def test_cors(c):
    ok = c.options("/api/menu", headers={"Origin": "https://site.example", "Access-Control-Request-Method": "GET"})
    assert ok.headers.get("access-control-allow-origin") == "https://site.example"
    bad = c.options("/api/menu", headers={"Origin": "https://evil.example", "Access-Control-Request-Method": "GET"})
    assert "access-control-allow-origin" not in bad.headers


def test_site_is_served_with_the_api(c):
    home = c.get("/")
    assert home.status_code == 200 and "LATCHA" in home.text and "/api" in home.text
    assert c.get("/admin.html").status_code == 200
    assert c.get("/assets/logo.png").headers["content-type"] == "image/png"
    assert c.get("/does-not-exist").status_code == 404
    assert c.get("/api/does-not-exist").status_code == 404


def test_cache_headers(c):
    assert "s-maxage" in c.get("/api/menu").headers["cache-control"]
    assert tok(c) and c.get("/api/admin/orders", headers=tok(c)).headers["cache-control"] == "no-store"


# ---------- outlets ----------
from datetime import datetime  # noqa: E402
from app import outlets as ot  # noqa: E402


def _at(monkeypatch, h, mi=0):
    monkeypatch.setattr(ot, "now_ist", lambda: datetime(2026, 11, 21, h, mi, tzinfo=ot.IST))


def _o(c, slug):
    return next(o for o in c.get("/api/outlets").json() if o["slug"] == slug)


def test_open_hours_logic_including_after_midnight():
    assert ot.is_open("11:00", "01:00", datetime(2026, 1, 1, 0, 30)) and ot.is_open("11:00", "01:00", datetime(2026, 1, 1, 23, 0))
    assert not ot.is_open("11:00", "01:00", datetime(2026, 1, 1, 1, 30)) and not ot.is_open("11:00", "01:00", datetime(2026, 1, 1, 10, 59))
    assert not ot.is_open("11:00", "00:00", datetime(2026, 1, 1, 0, 30)) and ot.is_open("11:00", "00:00", datetime(2026, 1, 1, 23, 59))
    assert ot.pretty("00:00") == "12:00 AM" and ot.pretty("13:05") == "1:05 PM"


def test_outlets_list(c, monkeypatch):
    _at(monkeypatch, 15)
    r = c.get("/api/outlets")
    assert r.status_code == 200 and "s-maxage" in r.headers["cache-control"]
    rows = r.json()
    assert [o["slug"] for o in rows] == ["jp-nagar", "sattva-knowledge-park", "indiranagar"]
    jp = rows[0]
    assert jp["city"] == "Bengaluru" and jp["open_now"] and jp["can_order"] and jp["status"].startswith("Open now")
    assert "phone" in jp and "sold_out" in jp
    soon = rows[2]
    assert soon["coming_soon"] and not soon["can_order"] and soon["status"] == "Opening soon"


def test_order_to_outlet_sets_city_and_shows_in_admin(c, monkeypatch):
    _at(monkeypatch, 15)
    body = {k: v for k, v in ORDER.items() if k != "city"}
    r = c.post("/api/orders", json={**body, "phone": "9200000001", "outlet": "sattva-knowledge-park"})
    assert r.status_code == 201 and r.json()["outlet"] == "LATCHA Sattva Knowledge Park"
    o = c.get("/api/admin/orders", headers=tok(c)).json()[0]
    assert o["city"] == "Hyderabad" and o["outlet_name"] == "LATCHA Sattva Knowledge Park"
    mine = c.get(f"/api/admin/orders?outlet_id={o['outlet_id']}", headers=tok(c)).json()
    assert mine and all(x["outlet_id"] == o["outlet_id"] for x in mine)


def test_old_style_order_with_only_a_city_still_works(c, monkeypatch):
    _at(monkeypatch, 15)
    r = c.post("/api/orders", json={**ORDER, "phone": "9200000002"})
    assert r.status_code == 201 and r.json()["outlet"] is None


def test_closed_outlet_refuses_orders(c, monkeypatch):
    _at(monkeypatch, 0, 30)  # 12:30 AM: JP Nagar (till 1 AM) open, Sattva (till midnight) closed
    ok = c.post("/api/orders", json={**ORDER, "phone": "9200000003", "outlet": "jp-nagar"})
    assert ok.status_code == 201
    no = c.post("/api/orders", json={**ORDER, "phone": "9200000004", "outlet": "sattva-knowledge-park"})
    assert no.status_code == 422 and "Closed now" in no.json()["detail"] and "11:00 AM" in no.json()["detail"]
    assert _o(c, "sattva-knowledge-park")["can_order"] is False


@pytest.mark.parametrize("extra,why", [
    (dict(outlet="indiranagar"), "opening soon"), (dict(outlet="nowhere"), "could not find"),
])
def test_bad_outlets_rejected(c, monkeypatch, extra, why):
    _at(monkeypatch, 15)
    r = c.post("/api/orders", json={**ORDER, "phone": "9200000005", **extra})
    assert r.status_code == 422 and why in r.json()["detail"]


def test_order_needs_outlet_or_city(c):
    body = {k: v for k, v in ORDER.items() if k != "city"}
    assert c.post("/api/orders", json={**body, "phone": "9200000006"}).status_code == 422


def test_owner_can_pause_and_change_an_outlet(c, monkeypatch):
    _at(monkeypatch, 15)
    h = tok(c)
    jp = next(o for o in c.get("/api/admin/outlets", headers=h).json() if o["slug"] == "jp-nagar")
    assert c.patch(f"/api/admin/outlets/{jp['id']}", json={"accepting_orders": False}, headers=h).status_code == 200
    r = c.post("/api/orders", json={**ORDER, "phone": "9200000007", "outlet": "jp-nagar"})
    assert r.status_code == 422 and "Not taking orders" in r.json()["detail"]
    assert _o(c, "jp-nagar")["status"] == "Not taking orders right now"
    c.patch(f"/api/admin/outlets/{jp['id']}", json={"accepting_orders": True, "phone": "+91 98765 43210"}, headers=h)
    assert _o(c, "jp-nagar")["phone"] == "9876543210" and _o(c, "jp-nagar")["can_order"]
    assert c.patch(f"/api/admin/outlets/{jp['id']}", json={"opens": "25:00"}, headers=h).status_code == 422
    assert c.patch(f"/api/admin/outlets/{jp['id']}", json={"phone": "123"}, headers=h).status_code == 422
    assert c.patch("/api/admin/outlets/9999", json={"accepting_orders": True}, headers=h).status_code == 404


def test_sold_out_is_per_outlet(c, monkeypatch):
    _at(monkeypatch, 15)
    h = tok(c)
    outs = {o["slug"]: o["id"] for o in c.get("/api/admin/outlets", headers=h).json()}
    assert c.put(f"/api/admin/outlets/{outs['jp-nagar']}/sold-out/1", json={"sold_out": True}, headers=h).json()["sold_out"] == [1]
    assert _o(c, "jp-nagar")["sold_out"] == [1] and _o(c, "sattva-knowledge-park")["sold_out"] == []
    here = c.post("/api/orders", json={**ORDER, "phone": "9200000008", "outlet": "jp-nagar"})
    assert here.status_code == 422 and "sold out at LATCHA JP Nagar" in here.json()["detail"]
    there = c.post("/api/orders", json={**ORDER, "phone": "9200000009", "outlet": "sattva-knowledge-park"})
    assert there.status_code == 201
    c.put(f"/api/admin/outlets/{outs['jp-nagar']}/sold-out/1", json={"sold_out": True}, headers=h)  # twice is fine
    assert c.put(f"/api/admin/outlets/{outs['jp-nagar']}/sold-out/1", json={"sold_out": False}, headers=h).json()["sold_out"] == []
    assert c.put(f"/api/admin/outlets/{outs['jp-nagar']}/sold-out/99999", json={"sold_out": True}, headers=h).status_code == 404


def test_outlet_admin_needs_login(c):
    assert c.get("/api/admin/outlets").status_code == 401
    assert c.patch("/api/admin/outlets/1", json={"accepting_orders": False}).status_code == 401
    assert c.put("/api/admin/outlets/1/sold-out/1", json={"sold_out": True}).status_code == 401


# ---------------- UPI payments ----------------
import hashlib, hmac, json as _json
from app import payments as _pay
from app.config import get_settings as _gs

UPI = dict(ORDER, pay="upi", phone="9200000001", city="Hyderabad", lines=[dict(item_id=1, quantity=2), dict(item_id=2, quantity=1)])


@pytest.fixture
def paymode(monkeypatch):
    def set_mode(mode, **kw):
        st = _gs()
        monkeypatch.setattr(st, "payments_mode", mode)
        for k, v in kw.items():
            monkeypatch.setattr(st, k, v)
    return set_mode


def _price(c, i):
    return next(x["price"] for x in c.get("/api/menu").json() if x["id"] == i)


def test_menu_has_prices_and_owner_can_change_one(c):
    assert [x["id"] for x in c.get("/api/menu").json() if not x["price"] and x["id"] <= 33] == []
    h = tok(c)
    assert c.patch("/api/admin/menu/1", json={"price": 260}, headers=h).json()["price"] == 260
    assert c.patch("/api/admin/menu/1", json={"price": 0}, headers=h).status_code == 422
    assert c.patch("/api/admin/menu/1", json={"price": 249}, headers=h).json()["price"] == 249


def test_payments_config(c, paymode):
    paymode("demo"); assert c.get("/api/payments/config").json() == {"mode": "demo"}
    paymode("off"); assert c.get("/api/payments/config").json() == {"mode": "off"}
    paymode("razorpay", razorpay_key_id="", razorpay_key_secret="")   # keys missing: never a silent fake
    assert c.get("/api/payments/config").json() == {"mode": "off"}


def test_cash_order_is_unchanged_and_has_amount(c, paymode):
    paymode("demo")
    r = c.post("/api/orders", json=dict(ORDER, phone="9200000002")).json()
    assert r["payment"] is None and r["amount"] == 2 * _price(c, 1)
    o = c.post("/api/orders/track", json={"order_id": r["id"], "phone": "9200000002"}).json()
    assert o["payment_method"] == "cod" and o["payment_status"] == "unpaid"


def test_demo_upi_order_pays_once(c, paymode):
    paymode("demo")
    r = c.post("/api/orders", json=UPI)
    assert r.status_code == 201
    b = r.json()
    assert b["amount"] == 2 * _price(c, 1) + _price(c, 2)            # worked out by the server
    assert b["payment"]["provider"] == "demo" and b["payment"]["amount"] == b["amount"]
    tr = lambda: c.post("/api/orders/track", json={"order_id": b["id"], "phone": "9200000001"}).json()
    assert tr()["payment_status"] == "pending"
    ok = c.post(f"/api/orders/{b['id']}/pay/confirm", json={"phone": "9200000001"})
    assert ok.status_code == 200 and ok.json()["payment_status"] == "paid"
    again = c.post(f"/api/orders/{b['id']}/pay/confirm", json={"phone": "9200000001"})   # double click
    assert again.status_code == 200 and again.json()["payment_status"] == "paid"
    assert tr()["payment_status"] == "paid"
    adm = [o for o in c.get("/api/admin/orders", headers=tok(c)).json() if o["id"] == b["id"]][0]
    assert adm["payment_status"] == "paid" and adm["amount"] == b["amount"]


def test_client_cannot_choose_the_amount(c, paymode):
    paymode("demo")
    r = c.post("/api/orders", json=dict(UPI, phone="9200000003", amount=1))   # extra field is ignored
    assert r.json()["amount"] == 2 * _price(c, 1) + _price(c, 2)


def test_wrong_phone_cannot_pay_or_restart(c, paymode):
    paymode("demo")
    b = c.post("/api/orders", json=dict(UPI, phone="9200000004")).json()
    assert c.post(f"/api/orders/{b['id']}/pay/confirm", json={"phone": "9200000009"}).status_code == 404
    assert c.post(f"/api/orders/{b['id']}/pay", json={"phone": "9200000009"}).status_code == 404


def test_closed_window_can_pay_again_with_same_provider_order(c, paymode):
    paymode("demo")
    b = c.post("/api/orders", json=dict(UPI, phone="9200000005")).json()
    again = c.post(f"/api/orders/{b['id']}/pay", json={"phone": "9200000005"})
    assert again.status_code == 200 and again.json()["provider_order_id"] == b["payment"]["provider_order_id"]
    c.post(f"/api/orders/{b['id']}/pay/confirm", json={"phone": "9200000005"})
    assert c.post(f"/api/orders/{b['id']}/pay", json={"phone": "9200000005"}).status_code == 409


def test_upi_refused_when_payments_off_or_item_has_no_price(c, paymode):
    paymode("off")
    assert c.post("/api/orders", json=dict(UPI, phone="9200000006")).status_code == 422
    paymode("demo")
    h = tok(c)
    c.patch("/api/admin/menu/2", json={"price": None}, headers=h)
    r = c.post("/api/orders", json=dict(UPI, phone="9200000006"))
    assert r.status_code == 422 and "pay at delivery" in r.json()["detail"]
    assert c.post("/api/orders", json=dict(UPI, pay="cod", phone="9200000006")).status_code == 201   # cash still fine
    c.patch("/api/admin/menu/2", json={"price": 249}, headers=h)


def test_demo_button_is_dead_once_real_payments_are_on(c, paymode):
    paymode("demo")
    b = c.post("/api/orders", json=dict(UPI, phone="9200000007")).json()
    paymode("razorpay", razorpay_key_id="rzp_test_x", razorpay_key_secret="secret1")
    assert c.post(f"/api/orders/{b['id']}/pay/confirm", json={"phone": "9200000007"}).status_code == 404


class _FakeRzp:
    n = 0
    def __call__(self, path, body):
        _FakeRzp.n += 1
        return {"id": f"order_T{_FakeRzp.n}", "amount": body["amount"]}


def _rzp(paymode, monkeypatch):
    paymode("razorpay", razorpay_key_id="rzp_test_abc", razorpay_key_secret="topsecret", razorpay_webhook_secret="whsec")
    monkeypatch.setattr(_pay, "_rzp_post", _FakeRzp())


def _sign(secret, msg):
    return hmac.new(secret.encode(), msg, hashlib.sha256).hexdigest()


def test_razorpay_checkout_signature_flow(c, paymode, monkeypatch):
    _rzp(paymode, monkeypatch)
    b = c.post("/api/orders", json=dict(UPI, phone="9200000008")).json()
    p = b["payment"]
    assert p["provider"] == "razorpay" and p["key_id"] == "rzp_test_abc" and p["provider_order_id"].startswith("order_T")
    pid = "pay_123"
    bad = c.post(f"/api/orders/{b['id']}/pay/confirm", json={"phone": "9200000008", "razorpay_payment_id": pid,
                 "razorpay_order_id": p["provider_order_id"], "razorpay_signature": "0" * 64})
    assert bad.status_code == 400
    assert c.post("/api/orders/track", json={"order_id": b["id"], "phone": "9200000008"}).json()["payment_status"] == "pending"
    sig = _sign("topsecret", f"{p['provider_order_id']}|{pid}".encode())
    good = c.post(f"/api/orders/{b['id']}/pay/confirm", json={"phone": "9200000008", "razorpay_payment_id": pid,
                  "razorpay_order_id": p["provider_order_id"], "razorpay_signature": sig})
    assert good.status_code == 200 and good.json()["payment_status"] == "paid"
    wrong_order = c.post(f"/api/orders/{b['id']}/pay/confirm", json={"phone": "9200000008", "razorpay_payment_id": pid,
                         "razorpay_order_id": "order_OTHER", "razorpay_signature": sig})
    assert wrong_order.json()["payment_status"] == "paid"   # already paid: nothing changes


def test_razorpay_signature_for_another_order_is_rejected(c, paymode, monkeypatch):
    _rzp(paymode, monkeypatch)
    b = c.post("/api/orders", json=dict(UPI, phone="9200000010")).json()
    sig = _sign("topsecret", b"order_OTHER|pay_1")
    r = c.post(f"/api/orders/{b['id']}/pay/confirm", json={"phone": "9200000010", "razorpay_payment_id": "pay_1",
               "razorpay_order_id": "order_OTHER", "razorpay_signature": sig})
    assert r.status_code == 400


def _hook(c, ev, secret="whsec", sig=None):
    raw = _json.dumps(ev).encode()
    return c.post("/api/payments/razorpay/webhook", content=raw,
                  headers={"X-Razorpay-Signature": sig or _sign(secret, raw), "Content-Type": "application/json"})


def test_webhook_marks_paid_once_and_checks_signature_and_amount(c, paymode, monkeypatch):
    _rzp(paymode, monkeypatch)
    b = c.post("/api/orders", json=dict(UPI, phone="9200000011")).json()
    ref, amt = b["payment"]["provider_order_id"], b["amount"] * 100
    ev = lambda a=amt, k="payment.captured": {"event": k, "payload": {"payment": {"entity": {"id": "pay_w1", "order_id": ref, "amount": a}}}}
    st = lambda: c.post("/api/orders/track", json={"order_id": b["id"], "phone": "9200000011"}).json()["payment_status"]
    assert _hook(c, ev(), sig="0" * 64).status_code == 400 and st() == "pending"      # fake caller
    assert _hook(c, ev(a=100)).status_code == 200 and st() == "pending"               # wrong amount
    assert _hook(c, ev()).status_code == 200 and st() == "paid"
    assert _hook(c, ev()).status_code == 200 and st() == "paid"                       # Razorpay retried: still fine
    assert _hook(c, ev(k="payment.failed")).status_code == 200 and st() == "paid"      # late failure never un-pays


def test_webhook_failed_then_retry_paid(c, paymode, monkeypatch):
    _rzp(paymode, monkeypatch)
    b = c.post("/api/orders", json=dict(UPI, phone="9200000012")).json()
    ref = b["payment"]["provider_order_id"]
    st = lambda: c.post("/api/orders/track", json={"order_id": b["id"], "phone": "9200000012"}).json()["payment_status"]
    _hook(c, {"event": "payment.failed", "payload": {"payment": {"entity": {"id": "pay_f", "order_id": ref}}}})
    assert st() == "failed"
    assert c.post(f"/api/orders/{b['id']}/pay", json={"phone": "9200000012"}).status_code == 200 and st() == "pending"
    _hook(c, {"event": "order.paid", "payload": {"payment": {"entity": {"id": "pay_ok", "order_id": ref, "amount": b["amount"] * 100}}}})
    assert st() == "paid"


def test_webhook_without_secret_configured_is_refused(c, paymode):
    paymode("demo", razorpay_webhook_secret="")
    assert _hook(c, {"event": "payment.captured"}, secret="").status_code == 400


def test_payment_service_down_saves_no_order(c, paymode, monkeypatch):
    paymode("razorpay", razorpay_key_id="rzp_test_abc", razorpay_key_secret="topsecret")
    def boom(path, body): raise OSError("down")
    monkeypatch.setattr(_pay, "_rzp_post", boom)
    before = len(c.get("/api/admin/orders?limit=200", headers=tok(c)).json())
    assert c.post("/api/orders", json=dict(UPI, phone="9200000013")).status_code == 502
    assert len(c.get("/api/admin/orders?limit=200", headers=tok(c)).json()) == before


def test_owner_can_mark_cash_order_paid(c, paymode):
    paymode("demo")
    b = c.post("/api/orders", json=dict(ORDER, phone="9200000014")).json()
    h = tok(c)
    assert c.post(f"/api/admin/orders/{b['id']}/mark-paid").status_code == 401
    r = c.post(f"/api/admin/orders/{b['id']}/mark-paid", headers=h)
    assert r.status_code == 200 and r.json()["payment_status"] == "paid"


def test_old_database_is_upgraded_in_place(tmp_path):
    from sqlalchemy import create_engine, text, inspect
    e = create_engine(f"sqlite:///{tmp_path/'old.db'}")
    with e.begin() as cx:
        cx.execute(text("CREATE TABLE menu_items (id INTEGER PRIMARY KEY, category VARCHAR(20), name VARCHAR(80))"))
        cx.execute(text("INSERT INTO menu_items VALUES (1,'classic','A'),(2,'float','B')"))
        cx.execute(text("CREATE TABLE orders (id INTEGER PRIMARY KEY, phone VARCHAR(20))"))
        cx.execute(text("INSERT INTO orders VALUES (1,'9999999999')"))
        cx.execute(text("CREATE TABLE order_lines (id INTEGER PRIMARY KEY, order_id INTEGER)"))
    _pay.upgrade_payment_schema(e); _pay.upgrade_payment_schema(e)   # twice: must be safe
    with e.connect() as cx:
        assert [r[0] for r in cx.execute(text("SELECT price FROM menu_items ORDER BY id"))] == [249, 299]
        assert cx.execute(text("SELECT payment_method, payment_status FROM orders")).one() == ("cod", "unpaid")
    assert {"amount", "payment_ref", "paid_at", "fam_discount"} <= {x["name"] for x in inspect(e).get_columns("orders")}
    with e.connect() as cx:
        assert cx.execute(text("SELECT fam_discount FROM orders")).scalar() == 0


# ---------------- live order screen numbers ----------------
def test_stats_need_login(c):
    assert c.get("/api/admin/stats").status_code == 401


def test_stats_count_new_being_made_unpaid_and_todays_money(c, paymode):
    paymode("demo")
    h = tok(c)
    s0 = c.get("/api/admin/stats", headers=h).json()
    a = c.post("/api/orders", json=dict(ORDER, phone="9300000001")).json()                  # cash order
    b = c.post("/api/orders", json=dict(UPI, phone="9300000002")).json()                    # UPI, unpaid
    s1 = c.get("/api/admin/stats", headers=h).json()
    assert s1["new_orders"] == s0["new_orders"] + 2 and s1["today_orders"] == s0["today_orders"] + 2
    assert s1["awaiting_payment"] == s0["awaiting_payment"] + 1 and s1["latest_order_id"] == b["id"]
    c.post(f"/api/orders/{b['id']}/pay/confirm", json={"phone": "9300000002"})              # pays
    c.patch(f"/api/admin/orders/{a['id']}", json={"status": "confirmed"}, headers=h)
    s2 = c.get("/api/admin/stats", headers=h).json()
    assert s2["today_paid"] == s0["today_paid"] + b["amount"]
    assert s2["awaiting_payment"] == s0["awaiting_payment"] and s2["being_made"] == s0["being_made"] + 1
    c.patch(f"/api/admin/orders/{b['id']}", json={"status": "cancelled"}, headers=h)         # cancelled money is not counted
    s3 = c.get("/api/admin/stats", headers=h).json()
    assert s3["today_paid"] == s0["today_paid"] and s3["today_orders"] == s0["today_orders"] + 1


def test_menu_editor_can_change_text_fields_but_not_break_rules(c):
    h = tok(c)
    r = c.patch("/api/admin/menu/3", json={"name": "Vanilla Silk", "description": "Vanilla + matcha", "calories": 130, "sweetness": 2}, headers=h)
    assert r.status_code == 200 and r.json()["calories"] == 130 and r.json()["sweetness"] == 2
    assert c.patch("/api/admin/menu/3", json={"calories": None, "sweetness": None}, headers=h).json()["calories"] is None
    assert c.patch("/api/admin/menu/3", json={"name": ""}, headers=h).status_code == 422
    assert c.patch("/api/admin/menu/3", json={"calories": -5}, headers=h).status_code == 422
    c.patch("/api/admin/menu/3", json={"calories": 128, "sweetness": 2}, headers=h)


# ---------------- Latcha Fam loyalty ----------------
def _fam(c, phone):
    r = c.post("/api/fam/card", json={"phone": phone})
    assert r.status_code == 200
    return r.json()


def _order(c, phone, lines, **kw):
    return c.post("/api/orders", json=dict(ORDER, phone=phone, city="Hyderabad", lines=lines, **kw))


def _deliver(c, oid, to="delivered"):
    return c.patch(f"/api/admin/orders/{oid}", json={"status": to}, headers=tok(c))


def _earn(c, phone, sips):
    """Place and deliver orders until the number has this many sips."""
    while sips > 0:
        q = min(sips, 20)
        _deliver(c, _order(c, phone, [dict(item_id=1, quantity=q)]).json()["id"])
        sips -= q


def test_fam_card_starts_empty_and_needs_a_valid_phone(c):
    card = _fam(c, "9400000001")
    assert card == dict(sips=0, per_reward=8, into_card=0, to_next=8, rewards_available=0, rewards_used=0, delivered_orders=0)
    assert c.post("/api/fam/card", json={"phone": "123"}).status_code == 422


def test_sips_are_added_only_when_the_owner_marks_the_order_delivered(c):
    o = _order(c, "9400000002", [dict(item_id=1, quantity=3)]).json()
    assert _fam(c, "9400000002")["sips"] == 0
    _deliver(c, o["id"], "confirmed"); assert _fam(c, "9400000002")["sips"] == 0
    _deliver(c, o["id"]); k = _fam(c, "9400000002")
    assert k["sips"] == 3 and k["into_card"] == 3 and k["to_next"] == 5 and k["delivered_orders"] == 1
    _deliver(c, o["id"], "cancelled"); assert _fam(c, "9400000002")["sips"] == 0
    assert _fam(c, "9400000009")["sips"] == 0   # other numbers are separate


def test_eight_sips_unlock_a_free_drink_and_the_cheapest_drink_is_free(c, paymode):
    paymode("demo")
    _earn(c, "9400000003", 8)
    assert _fam(c, "9400000003")["rewards_available"] == 1
    lines = [dict(item_id=1, quantity=2), dict(item_id=25, quantity=1)]
    cheap = min(_price(c, 1), _price(c, 25))
    r = _order(c, "9400000003", lines, use_reward=True)
    assert r.status_code == 201
    b = r.json()
    assert b["fam_discount"] == cheap and b["amount"] == 2 * _price(c, 1) + _price(c, 25) - cheap
    assert _fam(c, "9400000003")["rewards_available"] == 0                              # reserved straight away
    again = _order(c, "9400000003", lines, use_reward=True)
    assert again.status_code == 422 and "free drink" in again.json()["detail"]
    _deliver(c, b["id"], "cancelled")                                                   # cancelled: the free drink comes back
    assert _fam(c, "9400000003")["rewards_available"] == 1


def test_reward_cannot_be_used_without_enough_sips(c):
    _earn(c, "9400000004", 7)
    r = _order(c, "9400000004", [dict(item_id=1, quantity=1)], use_reward=True)
    assert r.status_code == 422
    assert _order(c, "9400000004", [dict(item_id=1, quantity=1)]).status_code == 201      # a normal order is fine


def test_free_drink_alone_makes_a_free_order_that_needs_no_payment(c, paymode):
    paymode("demo")
    _earn(c, "9400000005", 8)
    r = _order(c, "9400000005", [dict(item_id=1, quantity=1)], use_reward=True, pay="upi")
    assert r.status_code == 201
    b = r.json()
    assert b["amount"] == 0 and b["payment"] is None and b["fam_discount"] == _price(c, 1)
    o = c.post("/api/orders/track", json={"order_id": b["id"], "phone": "9400000005"}).json()
    assert o["payment_status"] == "paid" and o["amount"] == 0 and o["fam_discount"] == _price(c, 1)


def test_free_drink_with_upi_charges_only_the_rest(c, paymode):
    paymode("demo")
    _earn(c, "9400000006", 8)
    b = _order(c, "9400000006", [dict(item_id=1, quantity=2)], use_reward=True, pay="upi").json()
    assert b["payment"]["amount"] == _price(c, 1) == b["amount"]


def test_reward_refused_when_an_item_has_no_price(c):
    _earn(c, "9400000007", 8)
    h = tok(c)
    c.patch("/api/admin/menu/2", json={"price": None}, headers=h)
    r = _order(c, "9400000007", [dict(item_id=2, quantity=1)], use_reward=True)
    c.patch("/api/admin/menu/2", json={"price": 249}, headers=h)
    assert r.status_code == 422 and _fam(c, "9400000007")["rewards_available"] == 1


def test_second_card_starts_after_a_reward(c):
    _earn(c, "9400000008", 16)
    k = _fam(c, "9400000008")
    assert k["rewards_available"] == 2 and k["into_card"] == 0
    assert _order(c, "9400000008", [dict(item_id=1, quantity=1)], use_reward=True).status_code == 201
    assert _fam(c, "9400000008")["rewards_available"] == 1


def test_owner_sees_fam_members(c):
    assert c.get("/api/admin/fam").status_code == 401
    _earn(c, "9400000010", 9)
    rows = c.get("/api/admin/fam", headers=tok(c)).json()
    me = [r for r in rows if r["phone"] == "9400000010"][0]
    assert me["sips"] == 9 and me["rewards_available"] == 1
    sips = [r["sips"] for r in rows]
    assert sips == sorted(sips, reverse=True)
