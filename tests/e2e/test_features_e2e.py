"""Browser tests for: install-on-phone, birthday, the sign-up phone code, and the owner's alert button and new rules."""
import json
import uuid

from playwright.sync_api import Page, expect

from .test_shop import admin_login


def new_phone() -> str:
    return "98765" + str(uuid.uuid4().int)[:5]


def sign_up(page: Page, phone: str):
    page.click("#acctbtn")
    page.click(".otabs.lp [data-t=up]")
    page.fill("#acform [name=name]", "Feature Tester")
    page.fill("#acform [name=phone]", phone)
    page.fill("#acform [name=password]", "secret1")


def test_site_is_installable_and_service_worker_runs(page: Page):
    page.goto("/")
    href = page.locator("link[rel=manifest]").get_attribute("href")
    man = page.request.get(href)
    assert man.ok
    data = man.json()
    assert data["display"] == "standalone" and data["short_name"] == "LATCHA"
    for icon in data["icons"]:
        assert page.request.get(icon["src"]).ok
    assert {i["sizes"] for i in data["icons"]} >= {"192x192", "512x512"}
    assert page.request.get("/sw.js").ok
    ready = page.evaluate("navigator.serviceWorker.ready.then(r => !!r.active)")
    assert ready is True


def test_birthday_can_be_saved_once(page: Page):
    page.goto("/")
    sign_up(page, new_phone())
    page.click("#acsub2")
    expect(page.locator("#acctbtn")).to_contain_text("60")
    page.click(".otabs.lp [data-t=me]")
    form = page.locator("#acbday")
    expect(form).to_be_visible()
    form.locator("[name=mm]").select_option("02")
    form.locator("[name=dd]").select_option("14")
    form.locator("button[type=submit]").click()
    expect(page.locator("#acbody")).to_contain_text("14 February is saved")
    expect(page.locator("#acbday")).to_have_count(0)                       # locked after saving
    page.click(".otabs.lp [data-t=pts]")
    expect(page.locator(".lp-extra")).to_contain_text("on your birthday")


def test_points_window_shows_birthday_and_comeback_offers_before_sign_in(page: Page):
    page.goto("/")
    page.click("#acctbtn")
    expect(page.locator("#acbody .lp-extra")).to_contain_text("+50 on your birthday")
    expect(page.locator("#acbody .lp-extra")).to_contain_text("after 14 days")


def test_sign_up_asks_for_the_phone_code_when_it_is_on(page: Page):
    sent = {}

    def rules(route):
        r = route.fetch()
        body = r.json()
        body["otp"] = True
        route.fulfill(response=r, json=body)

    def otp(route):
        sent["phone"] = json.loads(route.request.post_data)["phone"]
        route.fulfill(json={"required": True, "demo_code": "482913", "minutes": 10, "note": "demo"})

    def register(route):
        sent["otp"] = json.loads(route.request.post_data).get("otp")
        route.continue_()

    page.route("**/api/loyalty", rules)
    page.route("**/api/customers/otp", otp)
    page.route("**/api/customers/register", register)
    page.goto("/")
    phone = new_phone()
    sign_up(page, phone)
    expect(page.locator("#acform [name=otp]")).to_be_visible()
    page.click("#otpsend")
    expect(page.locator("#otpnote")).to_contain_text("482913")
    assert sent["phone"] == phone
    page.fill("#acform [name=otp]", "482913")
    page.click("#acsub2")
    expect(page.locator("#acctbtn")).to_contain_text("60")
    assert sent["otp"] == "482913"


def test_phone_code_button_needs_a_number_first(page: Page):
    def rules(route):
        r = route.fetch()
        body = r.json()
        body["otp"] = True
        route.fulfill(response=r, json=body)

    page.route("**/api/loyalty", rules)
    page.goto("/")
    page.click("#acctbtn")
    page.click(".otabs.lp [data-t=up]")
    page.click("#otpsend")
    expect(page.locator("#acmsg")).to_contain_text("10 digit mobile number")


def test_owner_sees_alert_button_and_new_loyalty_rules(page: Page, admin_password):
    admin_login(page, admin_password)
    btn = page.locator("#alertbtn")
    expect(btn).to_contain_text("Set up phone alerts")                      # no Telegram keys on the test server
    btn.click()
    expect(page.locator("#flash")).to_contain_text("TELEGRAM_BOT_TOKEN")
    page.click("#tabs [data-t=fam]")
    for label in ("Birthday points", "Comeback after (days)", "Comeback points"):
        expect(page.get_by_label(label)).to_be_visible()
    page.get_by_label("Birthday points").fill("80")
    page.get_by_role("button", name="Save rules").click()
    expect(page.locator(".lyr")).to_contain_text("Saved")
    rules = page.request.get("/api/loyalty").json()
    assert rules["birthday_points"] == 80
    page.get_by_label("Birthday points").fill("50")
    page.get_by_role("button", name="Save rules").click()
    expect(page.locator(".lyr")).to_contain_text("Saved")
