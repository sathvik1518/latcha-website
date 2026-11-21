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


def choose_cash(page: Page):
    """The order window now offers UPI first. These older tests place a pay-at-delivery order, as before."""
    expect(page.locator("#paychoice")).to_be_visible()
    page.locator(".pc", has_text="Pay at delivery").click()
    expect(page.locator("#osub")).to_have_text("Send order request")


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
    choose_cash(page)
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
    choose_cash(page)
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
    choose_cash(page)
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


def fill_order(page: Page, name: str, phone: str):
    page.goto("/")
    add = page.locator(".item .add").first
    add.scroll_into_view_if_needed()
    add.click()
    page.click("#cartbar")
    page.fill("#oform [name=customer_name]", name)
    page.fill("#oform [name=phone]", phone)
    page.fill("#oform [name=address]", "12 MG Road, Indiranagar")
    expect(page.locator("#paychoice")).to_be_visible()


def test_customer_pays_by_upi_in_demo_mode_and_owner_sees_paid(page: Page, context, admin_password):
    name = unique("E2E Payer")
    fill_order(page, name, "98765 43211")
    expect(page.locator("#osub")).to_contain_text("with UPI")       # UPI is the default choice
    page.click("#osub")
    box = page.locator("#paybox")
    expect(box).to_be_visible()
    expect(page.locator("#pbtag")).to_contain_text("Demo payment")
    amount = page.locator("#pbamt").inner_text()
    assert amount.startswith("₹") and int(amount[1:]) > 0
    page.click("#pbpay")
    expect(page.locator(".pbtick")).to_be_visible(timeout=8000)
    expect(page.locator("#pbsub")).to_contain_text("is paid")
    order_no = re.search(r"#(\d+)", page.locator("#pbsub").inner_text()).group(1)

    owner = context.new_page()
    admin_login(owner, admin_password)
    card = owner.locator("#list .card", has_text=f"#{order_no} · {name}")
    expect(card).to_contain_text(f"{amount} · UPI · Paid")


def test_unpaid_upi_order_can_be_paid_later_from_tracking(page: Page):
    name = unique("E2E Later")
    fill_order(page, name, "98765 43212")
    page.click("#osub")
    expect(page.locator("#paybox")).to_be_visible()
    order_no = re.search(r"#(\d+)", page.locator("#pbsub").inner_text()).group(1)
    page.click("#pbcancel")                                          # "Not now"
    expect(page.locator("#ord")).to_be_hidden()

    page.click("#trackbtn")
    page.fill("#tform [name=order_id]", order_no)
    page.fill("#tform [name=phone]", "98765 43212")
    page.click("#tsub")
    expect(page.locator("#tres")).to_contain_text("Payment pending")
    page.click("#paynow")
    expect(page.locator("#paybox")).to_be_visible()
    page.click("#pbpay")
    expect(page.locator(".pbtick")).to_be_visible(timeout=8000)

    page.click("#pbmsg .chip")                                       # Track this order
    page.click("#tsub")
    expect(page.locator("#tres")).to_contain_text("Paid")


def test_owner_can_mark_a_cash_order_as_paid(page: Page, context, admin_password):
    name = unique("E2E Cash")
    fill_order(page, name, "98765 43213")
    choose_cash(page)
    page.click("#osub")
    expect(page.locator("#omsg")).to_contain_text("received")
    owner = context.new_page()
    admin_login(owner, admin_password)
    card = owner.locator("#list .card", has_text=name)
    expect(card).to_contain_text("Pay at delivery")
    card.get_by_role("button", name="Mark as paid").click()
    expect(card).to_contain_text("Paid")


def api_order(page: Page, name: str, phone: str):
    """Place an order straight through the API (like a customer on another phone)."""
    r = page.request.post("/api/orders", data={"customer_name": name, "phone": phone, "address": "12 MG Road, Indiranagar",
                                                 "city": "Bengaluru", "lines": [{"item_id": 1, "quantity": 1}]})
    assert r.ok, r.text()
    return r.json()["id"]


def test_owner_screen_shows_a_new_order_by_itself_and_moves_it_along(page: Page, context, admin_password):
    owner = context.new_page()
    admin_login(owner, admin_password)
    name = unique("E2E Live")
    order_no = api_order(page, name, "98765 43214")
    owner.locator("#flash").wait_for(state="visible", timeout=25000)       # arrives without pressing anything
    expect(owner.locator("#flash")).to_contain_text(f"#{order_no}")
    card = owner.locator("#list .card", has_text=name)
    expect(card).to_be_visible()
    expect(owner).to_have_title(re.compile(r"^\(\d+\) LATCHA Owner"))

    card.get_by_role("button", name="Confirm order").click()
    card = owner.locator("#list .card", has_text=name)
    expect(card.get_by_role("button", name="Mark delivered")).to_be_visible()
    card.get_by_role("button", name="Mark delivered").click()
    expect(owner.locator("#list .card", has_text=name)).to_have_count(0)    # finished orders leave the Active list
    owner.locator("#chips button", has_text="Delivered").click()
    expect(owner.locator("#list .card", has_text=name)).to_be_visible()


def test_owner_can_cancel_an_order_after_confirming_the_question(page: Page, context, admin_password):
    owner = context.new_page()
    admin_login(owner, admin_password)
    name = unique("E2E Cancel")
    api_order(page, name, "98765 43215")
    owner.reload()
    owner.once("dialog", lambda d: d.accept())
    owner.locator("#list .card", has_text=name).get_by_role("button", name="Cancel order").click()
    expect(owner.locator("#list .card", has_text=name)).to_have_count(0)
    owner.locator("#chips button", has_text="Cancelled").click()
    expect(owner.locator("#list .card", has_text=name)).to_be_visible()


def test_owner_edits_a_dish_and_customers_see_the_change(page: Page, context, admin_password):
    owner = context.new_page()
    admin_login(owner, admin_password)
    owner.click("#tabs button[data-t=menu]")
    card = owner.locator("#list .card", has_text="Honey Iced Matcha Latte")
    expect(card).to_be_visible()                      # the menu has loaded
    owner.fill("#msearch", "Honey Iced")
    expect(owner.locator("#list .card:visible")).to_have_count(1)   # the search box hides the other dishes
    card.locator("summary").click()
    card.get_by_label("Calories").fill("180")
    card.get_by_label("Description").fill("Sweetened with local honey, edited by the owner")
    with owner.expect_response(lambda r: r.request.method == "PATCH" and "/api/admin/menu/" in r.url) as saved:
        card.get_by_role("button", name="Save details").click()
    assert saved.value.ok
    expect(card).to_contain_text("180 kcal")
    items = page.request.get("/api/menu").json()
    honey = [i for i in items if i["name"] == "Honey Iced Matcha Latte"][0]
    assert honey["calories"] == 180 and "edited by the owner" in honey["description"], (honey, saved.value.request.post_data, saved.value.json())
