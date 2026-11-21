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
    assert set(body) == {"id", "created_at", "status", "lines"}  # no phone, address or name
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
