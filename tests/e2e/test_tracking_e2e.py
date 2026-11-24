"""Browser tests for live tracking (updates by itself, share link), the "on the way" step, and the dashboard export."""
from playwright.sync_api import Page, expect

from .test_shop import admin_login, place_order, unique


def owner_token(page: Page, password: str) -> dict:
    r = page.request.post("/api/admin/login", data={"username": "owner", "password": password})
    assert r.ok
    return {"Authorization": "Bearer " + r.json()["access_token"]}


def test_tracker_updates_by_itself_and_shows_on_the_way(page: Page, admin_password):
    order_no = place_order(page, unique("E2E Live"), "98222 22222")
    page.click("#trackthis")
    page.click("#tsub")
    expect(page.locator("#tstat")).to_contain_text("LATCHA will call you to confirm")
    expect(page.locator(".tstep li")).to_have_count(4)
    expect(page.locator("#tupd")).to_contain_text("refreshes by itself")

    # The owner moves the order along; the open tracker follows without any click.
    h = owner_token(page, admin_password)
    for st in ("confirmed", "out"):
        assert page.request.patch(f"/api/admin/orders/{order_no}", data={"status": st}, headers=h).ok
    expect(page.locator("#tstat")).to_contain_text("On the way", timeout=25000)
    expect(page.locator(".tstep li.on")).to_have_count(3)
    expect(page.locator("#tshare")).to_be_visible()


def test_shared_link_opens_the_tracker_without_a_phone_number(page: Page):
    order_no = place_order(page, unique("E2E Share"), "98333 33333")
    code = page.request.post("/api/orders/track", data={"order_id": int(order_no), "phone": "98333 33333"}).json()["code"]
    page.goto(f"/?track={code}")
    expect(page.locator("#trk")).to_be_visible()
    expect(page.locator("#tstat")).to_contain_text(f"Order #{order_no}")
    assert "track=" not in page.url                       # the secret is not left in the address bar
    expect(page.locator("#paynow")).to_have_count(0)      # paying still needs the phone number

    page.goto("/?track=1.0000000000")
    expect(page.locator("#tmsg")).to_contain_text("could not find that order")
    expect(page.locator("#tres")).to_be_hidden()


def test_owner_sends_an_order_out_for_delivery(page: Page, admin_password):
    name = unique("E2E Out")
    place_order(page, name, "98444 44444")
    admin_login(page, admin_password)
    card = page.locator("#list .card", has_text=name)
    card.get_by_role("button", name="Confirm order").click()
    card = page.locator("#list .card", has_text=name)
    card.get_by_role("button", name="Send out for delivery").click()
    card = page.locator("#list .card", has_text=name)
    expect(card.get_by_label("Status")).to_have_value("out")
    expect(card.get_by_role("button", name="Mark delivered")).to_be_visible()
    expect(page.locator("#chips button", has_text="Active")).to_be_visible()   # an order on the way still counts as active


def test_dashboard_shows_new_numbers_and_downloads_orders(page: Page, admin_password):
    place_order(page, unique("E2E Dash"), "98555 55555")
    admin_login(page, admin_password)
    page.click("#tabs button[data-t=dash]")
    expect(page.get_by_text("Avg time to deliver")).to_be_visible()
    expect(page.get_by_text("Repeat customers")).to_be_visible()
    expect(page.get_by_text("Best weekday")).to_be_visible()
    with page.expect_download() as dl:
        page.click("#dcsv")
    d = dl.value
    assert d.suggested_filename.endswith(".csv")
    text = open(d.path(), encoding="utf-8").read()
    assert "Order,Placed (IST),Status,Customer" in text and "98555 55555".replace(" ", "") in text
