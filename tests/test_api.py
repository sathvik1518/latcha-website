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
