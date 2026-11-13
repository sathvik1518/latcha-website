"""End-to-end tests: a real browser uses the site the way a customer and the owner do."""
import re
import uuid

from playwright.sync_api import Page, expect


def unique(prefix: str) -> str:
    return f"{prefix} {uuid.uuid4().hex[:6]}"


def admin_login(page: Page, password: str):
    page.goto("/admin.html")
    page.fill("#u", "owner")
    page.fill("#p", password)
    page.click("#login button")
    expect(page.locator("#app")).to_be_visible()


def set_status(page: Page, customer: str, status: str):
    """Pick a status and wait until the server has saved it (the page saves in the background)."""
    select = page.locator("#list .card", has_text=customer).get_by_label("Status")
    with page.expect_response(lambda r: r.request.method == "PATCH" and "/api/admin/orders/" in r.url) as saved:
        select.select_option(status)
    assert saved.value.ok


def test_menu_loads_from_the_database(page: Page):
    page.goto("/")
    adds = page.locator(".item .add")
    expect(adds.first).to_be_attached()
    assert adds.count() >= 30


def test_plus_and_minus_buttons(page: Page):
    page.goto("/")
    card = page.locator(".item:has(.add)").nth(1)
    card.scroll_into_view_if_needed()
    add, minus = card.locator(".add"), card.locator(".minus")
    expect(add).to_have_text("+ Add")
    expect(minus).to_be_disabled()

    add.click()
    add.click()
    expect(add).to_have_text("Added ✓ ×2")
    expect(page.locator("#orderbtn")).to_have_text("Order (2)")

    minus.click()
    expect(add).to_have_text("Added ✓ ×1")
    minus.click()
    expect(add).to_have_text("+ Add")
    expect(minus).to_be_disabled()
    expect(page.locator("#orderbtn")).to_have_text("Order")


def test_customer_order_and_review_then_owner_approves(page: Page, context, admin_password):
    customer = unique("E2E Customer")
    reviewer = unique("E2E Reviewer")

    # 1. A customer orders two drinks.
    page.goto("/")
    add = page.locator(".item .add").first
    add.scroll_into_view_if_needed()
    add.click()
    add.click()
    page.click("#cartbar")
    page.fill("#oform [name=customer_name]", customer)
    page.fill("#oform [name=phone]", "98765 43210")
    page.fill("#oform [name=address]", "12 MG Road, Indiranagar")
    page.click("#osub")
    msg = page.locator("#omsg")
    expect(msg).to_contain_text("received")
    order_no = re.search(r"#(\d+)", msg.inner_text()).group(1)

    # 2. The same customer leaves a review. It must NOT be public yet.
    page.keyboard.press("Escape")
    page.fill("#rform [name=author]", reviewer)
    page.fill("#rform [name=text]", "Lovely matcha, the cloud latte was perfect.")
    page.click("#rform button[type=submit]")
    expect(page.locator("#rmsg")).to_contain_text("Thank you")
    page.reload()
    page.wait_for_load_state("networkidle")
    expect(page.locator("#rgrid")).not_to_contain_text(reviewer)

    # 3. The owner logs in, sees the order and approves the review.
    owner = context.new_page()
    admin_login(owner, admin_password)
    expect(owner.locator("#list")).to_contain_text(f"#{order_no} · {customer}")
    owner.click("#tabs button[data-t=reviews]")
    card = owner.locator("#list .card", has_text=reviewer)
    expect(card).to_contain_text("Waiting")
    card.get_by_role("button", name="Approve").click()
    expect(card).to_contain_text("Approved")

    # 4. Now the review shows on the public page.
    page.goto("/")
    expect(page.locator("#rgrid")).to_contain_text(reviewer)


def test_owner_can_change_an_order_status(page: Page, admin_password):
    customer = unique("E2E Status")
    page.goto("/")
    add = page.locator(".item .add").first
    add.scroll_into_view_if_needed()
    add.click()
    page.click("#cartbar")
    page.fill("#oform [name=customer_name]", customer)
    page.fill("#oform [name=phone]", "98765 43210")
    page.fill("#oform [name=address]", "12 MG Road, Indiranagar")
    page.click("#osub")
    expect(page.locator("#omsg")).to_contain_text("received")

    admin_login(page, admin_password)
    set_status(page, customer, "confirmed")
    page.reload()
    expect(page.locator("#list .card", has_text=customer).get_by_label("Status")).to_have_value("confirmed")


def test_wrong_admin_password_is_refused(page: Page):
    page.goto("/admin.html")
    page.fill("#u", "owner")
    page.fill("#p", "not-the-password")
    page.click("#login button")
    expect(page.locator("#lerr")).to_contain_text("Wrong username or password")
    expect(page.locator("#app")).to_be_hidden()


def test_admin_api_needs_a_login(page: Page):
    for path in ("/api/admin/orders", "/api/admin/reviews", "/api/admin/menu"):
        assert page.request.get(path).status == 401


def test_bad_phone_gets_a_friendly_message(page: Page):
    page.goto("/")
    add = page.locator(".item .add").first
    add.scroll_into_view_if_needed()
    add.click()
    page.click("#cartbar")
    page.fill("#oform [name=customer_name]", "E2E Bad Phone")
    page.fill("#oform [name=phone]", "123")
    page.fill("#oform [name=address]", "12 MG Road, Indiranagar")
    page.click("#osub")
    expect(page.locator("#omsg")).to_contain_text("valid 10 digit mobile number")


def test_bot_review_with_filled_trap_field_is_not_stored(page: Page, admin_password):
    spammer = unique("E2E Bot")
    r = page.request.post("/api/reviews", data={
        "author": spammer, "rating": 5, "text": "Buy cheap watches now", "website": "http://spam.example"})
    assert r.status == 202  # the bot is told "ok"
    token = page.request.post("/api/admin/login", data={"username": "owner", "password": admin_password}).json()["access_token"]
    reviews = page.request.get("/api/admin/reviews", headers={"Authorization": f"Bearer {token}"}).json()
    assert spammer not in [x["author"] for x in reviews]


def place_order(page: Page, name: str, phone: str) -> str:
    """Order one drink through the page and return the order number shown to the customer."""
    page.goto("/")
    add = page.locator(".item .add").first
    add.scroll_into_view_if_needed()
    add.click()
    page.click("#cartbar")
    page.fill("#oform [name=customer_name]", name)
    page.fill("#oform [name=phone]", phone)
    page.fill("#oform [name=address]", "12 MG Road, Indiranagar")
    page.click("#osub")
    msg = page.locator("#omsg")
    expect(msg).to_contain_text("received")
    return re.search(r"#(\d+)", msg.inner_text()).group(1)


def test_customer_tracks_an_order_and_sees_the_owner_change_it(page: Page, context, admin_password):
    customer = unique("E2E Tracker")
    order_no = place_order(page, customer, "98111 11111")

    # The order message offers a shortcut with the details already filled in.
    page.click("#trackthis")
    expect(page.locator("#trk")).to_be_visible()
    expect(page.locator("#tform [name=order_id]")).to_have_value(order_no)
    page.click("#tsub")
    expect(page.locator("#tstat")).to_contain_text(f"Order #{order_no}")
    expect(page.locator("#tstat")).to_contain_text("LATCHA will call you to confirm")
    expect(page.locator(".tstep li.on")).to_have_count(1)  # only "Received" so far
    assert customer not in page.locator("#trk").inner_text()  # name is private
    assert "MG Road" not in page.locator("#trk").inner_text()  # address is private

    # The owner confirms the order.
    owner = context.new_page()
    admin_login(owner, admin_password)
    set_status(owner, customer, "confirmed")
    owner.reload()
    expect(owner.locator("#list .card", has_text=customer).get_by_label("Status")).to_have_value("confirmed")

    # The customer checks again and sees the new status.
    page.click("#tsub")
    expect(page.locator("#tstat")).to_contain_text("Confirmed")
    expect(page.locator(".tstep li.on")).to_have_count(2)

    set_status(owner, customer, "cancelled")
    owner.reload()
    page.click("#tsub")
    expect(page.locator("#tstat")).to_contain_text("cancelled")
    expect(page.locator(".tstep")).to_have_count(0)


def test_tracking_from_the_footer_needs_the_right_phone(page: Page):
    order_no = place_order(page, unique("E2E Private"), "98222 22222")
    page.keyboard.press("Escape")

    page.click("#trackbtn")
    page.fill("#tform [name=order_id]", f"#{order_no}")  # a leading # is fine
    page.fill("#tform [name=phone]", "98333 33333")      # someone else's number
    page.click("#tsub")
    expect(page.locator("#tmsg")).to_contain_text("could not find that order")
    expect(page.locator("#tres")).to_be_hidden()

    page.fill("#tform [name=phone]", "+91 98222 22222")
    page.click("#tsub")
    expect(page.locator("#tstat")).to_contain_text(f"Order #{order_no}")
    expect(page.locator("#tmsg")).to_have_text("")

    page.keyboard.press("Escape")
    expect(page.locator("#trk")).to_be_hidden()


def test_tracking_rejects_a_bad_order_number(page: Page):
    page.goto("/")
    page.click("#trackbtn")
    page.fill("#tform [name=order_id]", "abc")
    page.fill("#tform [name=phone]", "98222 22222")
    page.click("#tsub")
    expect(page.locator("#tmsg")).to_contain_text("Enter your order number")
