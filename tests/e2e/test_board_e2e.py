"""Browser tests for the owner board view: lanes, wait-time colours and one-tap contact buttons."""
from playwright.sync_api import Page, expect

from .test_shop import admin_login, place_order, unique
from .test_tracking_e2e import owner_token


def lane(page: Page, name: str):
    return page.locator(f".lane[aria-label='{name}']")


def test_board_lanes_follow_the_order_and_can_switch_to_a_list(page: Page, admin_password):
    page.set_viewport_size({"width": 1280, "height": 900})
    who = unique("E2E Board")
    place_order(page, who, "98777 70001")
    admin_login(page, admin_password)
    expect(page.locator(".board")).to_be_visible()
    expect(lane(page, "New").locator(".card", has_text=who)).to_have_count(1)
    expect(lane(page, "New").locator(".none")).to_have_count(0)

    lane(page, "New").locator(".card", has_text=who).get_by_role("button", name="Confirm order").click()
    expect(lane(page, "Making").locator(".card", has_text=who)).to_have_count(1)
    expect(lane(page, "New").locator(".card", has_text=who)).to_have_count(0)

    lane(page, "Making").locator(".card", has_text=who).get_by_role("button", name="Send out for delivery").click()
    expect(lane(page, "On the way").locator(".card", has_text=who)).to_have_count(1)

    page.click("#vw")                                           # list view: same card, no lanes
    expect(page.locator(".board")).to_have_count(0)
    expect(page.locator("#list .card", has_text=who).locator(".steps")).to_have_count(1)
    page.click("#vw")
    expect(page.locator(".board")).to_be_visible()

    page.click("#chips button:has-text('Delivered')")           # other filters are a plain list
    expect(page.locator(".board")).to_have_count(0)
    expect(page.locator("#vw")).to_be_hidden()


def test_wait_time_turns_amber_then_red(page: Page, admin_password):
    page.set_viewport_size({"width": 1280, "height": 900})
    who = unique("E2E Wait")
    place_order(page, who, "98777 70002")
    admin_login(page, admin_password)
    chip = page.locator(".card", has_text=who).locator(".wait")
    expect(chip).to_have_count(1)
    assert "amber" not in (chip.get_attribute("class") or "") and "red" not in (chip.get_attribute("class") or "")
    for minutes, cls in ((12, "amber"), (25, "red")):
        page.evaluate("""(m)=>{var o=window.__utc||(window.__utc=window.utc);window.utc=function(t){return new Date(o(t).getTime()-m*60000)};renderOrders({})}""", minutes)
        expect(page.locator(".card", has_text=who).locator(f".wait.{cls}")).to_have_count(1)
    expect(page.locator(".card.w-red", has_text=who)).to_have_count(1)


def test_contact_buttons_call_whatsapp_and_map(page: Page, admin_password):
    page.set_viewport_size({"width": 1280, "height": 900})
    who = unique("E2E Contact")
    place_order(page, who, "98777 70003")
    admin_login(page, admin_password)
    card = page.locator(".card", has_text=who)
    assert card.get_by_role("link", name="Call").get_attribute("href") == "tel:9877770003"
    wa = card.get_by_role("link", name="WhatsApp").get_attribute("href")
    assert wa.startswith("https://wa.me/919877770003?text=") and "LATCHA" in wa and "order" in wa
    maps = card.get_by_role("link", name="Map").get_attribute("href")
    assert maps.startswith("https://www.google.com/maps/search/?api=1&query=") and "MG%20Road" in maps
    for a in ("WhatsApp", "Map"):
        assert card.get_by_role("link", name=a).get_attribute("rel") == "noopener"


def test_phone_always_gets_the_list(page: Page, admin_password):
    page.set_viewport_size({"width": 390, "height": 800})
    who = unique("E2E Phone")
    place_order(page, who, "98777 70004")
    admin_login(page, admin_password)
    expect(page.locator("#list .card", has_text=who)).to_have_count(1)
    expect(page.locator(".board")).to_have_count(0)
    expect(page.locator("#vw")).to_be_hidden()
