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
    page.click("#otabs [data-t=details]")
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
    expect(owner.locator("#list .card", has_text=customer).filter(has_text=f"#{order_no}")).to_have_count(1)
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
    page.click("#otabs [data-t=details]")
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
    page.click("#otabs [data-t=details]")
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
    page.click("#otabs [data-t=details]")
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
    page.click("#otabs [data-t=details]")
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
    card = owner.locator("#list .card", has_text=name).filter(has_text=f"#{order_no}")
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
                                                 "outlet": "jp-nagar", "lines": [{"item_id": 1, "quantity": 1}]})
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


# ---------- Latcha Fam ----------

def owner_token(page: Page, password: str) -> dict:
    r = page.request.post("/api/admin/login", data={"username": "owner", "password": password})
    assert r.ok
    return {"Authorization": "Bearer " + r.json()["access_token"]}


def deliver_sips(page: Page, password: str, phone: str, drinks: int):
    """Order `drinks` drinks on this number and mark the order Delivered, like the owner would."""
    hdr = owner_token(page, password)
    r = page.request.post("/api/orders", data={"customer_name": "Fam E2E", "phone": phone, "address": "12 MG Road, Indiranagar",
                                                 "outlet": "jp-nagar", "lines": [{"item_id": 1, "quantity": drinks}]})
    assert r.ok, r.text()
    oid = r.json()["id"]
    ok = page.request.patch(f"/api/admin/orders/{oid}", data={"status": "delivered"}, headers=hdr)
    assert ok.ok, ok.text()
    return oid


def test_fam_card_from_the_footer_shows_sips(page: Page, admin_password):
    phone = "98765 4" + str(uuid.uuid4().int)[:4]
    deliver_sips(page, admin_password, phone, 3)
    page.goto("/")
    page.click("#fambtn")
    expect(page.locator("#famwin")).to_be_visible()
    page.fill("#fform [name=phone]", phone)
    page.click("#famsub")
    expect(page.locator("#fres")).to_be_visible()
    expect(page.locator("#fres")).to_contain_text("3")
    expect(page.locator("#fres .pearl.on")).to_have_count(3)


def test_free_drink_is_used_in_the_order_window_and_order_costs_nothing(page: Page, admin_password):
    phone = "98765 5" + str(uuid.uuid4().int)[:4]
    deliver_sips(page, admin_password, phone, 8)                     # 8 sips = 1 free drink
    fill_order(page, unique("E2E Fam"), phone)
    expect(page.locator("#famstrip")).to_be_visible(timeout=8000)
    expect(page.locator("#famuselab")).to_be_visible()
    page.locator("#famuselab").click()
    expect(page.locator("#osub")).to_contain_text("free order")      # one cheap drink in the cart is fully covered
    page.click("#osub")
    expect(page.locator("#omsg")).to_contain_text("received", timeout=8000)
    card = page.request.post("/api/fam/card", data={"phone": phone}).json()
    assert card["rewards_available"] == 0 and card["rewards_used"] == 1


def test_owner_sees_fam_members_and_the_free_drink_badge(page: Page, context, admin_password):
    phone = "98765 6" + str(uuid.uuid4().int)[:4]
    deliver_sips(page, admin_password, phone, 8)
    owner = context.new_page()
    admin_login(owner, admin_password)
    owner.click("#tabs button[data-t=fam]")
    row = owner.locator("#list .card", has_text=phone.replace(" ", ""))
    expect(row).to_be_visible()
    expect(row).to_contain_text("1 free drink ready")


# ---------- Find my matcha quiz ----------

def take_quiz(page: Page, *answers: str):
    page.goto("/")
    page.locator("#menu").scroll_into_view_if_needed()
    page.click("#quizbtn")
    expect(page.locator("#quizwin")).to_be_visible()
    for a in answers:
        page.locator("#quizbody .qopt", has_text=a).first.click()


def test_quiz_recommends_a_fruity_light_iced_latte_and_adds_it(page: Page):
    take_quiz(page, "Iced latte", "Fruity", "Barely sweet", "Keep it light")
    top = page.locator("#quizbody .qwin")
    expect(top).to_be_visible()
    expect(top.locator(".qname")).to_have_text("Strawberry")          # fruity, low sweetness, 146 kcal, best seller
    expect(top).to_contain_text("Iced latte")
    expect(top).to_contain_text("kcal")
    expect(page.locator("#quizbody .qrun")).to_have_count(2)         # two more ideas
    top.get_by_role("button", name="+ Add").click()
    expect(top.get_by_role("button", name="Added ✓ ×1")).to_be_visible()
    expect(page.locator("#orderbtn")).to_have_text("Order (1)")
    top.get_by_role("button", name="Open my order").click()
    expect(page.locator("#quizwin")).to_be_hidden()
    expect(page.locator("#ord")).to_be_visible()
    expect(page.locator("#ordlist")).to_contain_text("Strawberry")


def test_quiz_soft_serve_and_pure_matcha(page: Page):
    take_quiz(page, "Soft serve", "Pure matcha", "Balanced", "No preference")
    expect(page.locator("#quizbody .qname")).to_have_text("Uji Matcha")
    expect(page.locator("#quizbody .qfacts")).to_contain_text("Soft serve")


def test_quiz_back_retake_and_escape(page: Page):
    page.goto("/")
    page.locator("#menu").scroll_into_view_if_needed()
    page.click("#quizbtn")
    page.locator("#quizbody .qopt", has_text="Float").click()
    expect(page.locator("#quizbody .qq")).to_have_text("Which flavour sounds good?")
    page.click("#quizbody .qback")
    expect(page.locator("#quizbody .qq")).to_have_text("What are you in the mood for?")
    for a in ("Float", "Fruity", "Sweet tooth", "No preference"):
        page.locator("#quizbody .qopt", has_text=a).first.click()
    expect(page.locator("#quizbody .qname")).to_contain_text("Strawberry Crush Float")
    page.get_by_role("button", name="Take the quiz again").click()
    expect(page.locator("#quizbody .qq")).to_have_text("What are you in the mood for?")
    page.keyboard.press("Escape")
    expect(page.locator("#quizwin")).to_be_hidden()
    expect(page.locator("body")).not_to_have_class(re.compile("ordopen"))


def test_quiz_skips_a_dish_that_is_sold_out_at_the_chosen_outlet(page: Page, admin_password):
    hdr = owner_token(page, admin_password)
    outlets = page.request.get("/api/admin/outlets", headers=hdr).json()
    open_one = next(o for o in outlets if not o["coming_soon"])
    menu = page.request.get("/api/menu").json()
    straw = next(i for i in menu if i["name"] == "Strawberry" and i["category"] == "classic")
    set_it = lambda flag: page.request.put(f"/api/admin/outlets/{open_one['id']}/sold-out/{straw['id']}", data={"sold_out": flag}, headers=hdr)
    assert set_it(True).ok
    try:
        page.goto("/")
        page.evaluate("localStorage.setItem('latcha_outlet', %r)" % open_one["slug"])
        take_quiz(page, "Iced latte", "Fruity", "Barely sweet", "Keep it light")
        expect(page.locator("#quizbody .qname")).not_to_have_text("Strawberry")
        expect(page.locator("#quizbody .qname")).to_have_text("Raspberry")
    finally:
        set_it(False)


# ---------- Owner dashboard ----------

def dash_api(page: Page, hdr: dict, days: int = 7) -> dict:
    r = page.request.get(f"/api/admin/dashboard?days={days}", headers=hdr)
    assert r.ok, r.text()
    return r.json()


def test_dashboard_counts_a_new_order_and_the_owner_can_switch_period(page: Page, context, admin_password):
    hdr = owner_token(page, admin_password)
    before = dash_api(page, hdr)["head"]
    phone = "98765 7" + str(uuid.uuid4().int)[:4]
    r = page.request.post("/api/orders", data={"customer_name": unique("E2E Dash"), "phone": phone, "address": "12 MG Road, Indiranagar",
                                                 "outlet": "jp-nagar", "lines": [{"item_id": 1, "quantity": 2}]})
    assert r.ok, r.text()
    after = dash_api(page, hdr)["head"]
    assert after["orders"] == before["orders"] + 1
    assert after["sales"] == before["sales"] + 2 * 249                       # 2 cups at the placeholder price

    owner = context.new_page()
    admin_login(owner, admin_password)
    owner.click("#tabs button[data-t=dash]")
    expect(owner.locator(".stat", has_text="Sales")).to_be_visible()
    expect(owner.locator(".stat", has_text="Orders")).to_contain_text(str(after["orders"]))
    expect(owner.locator(".dbars").first.locator("i")).to_have_count(7)
    expect(owner.locator("#list .card", has_text="Top dishes")).to_be_visible()
    owner.locator("[data-d='30']").click()
    expect(owner.locator(".dbars").first.locator("i")).to_have_count(30)
    owner.locator("[data-m='orders']").click()
    expect(owner.locator("[data-m='orders']")).to_have_class(re.compile("on"))
    expect(owner.locator("#list .card", has_text="How people pay")).to_be_visible()


def test_dashboard_leaves_cancelled_orders_out(page: Page, admin_password):
    hdr = owner_token(page, admin_password)
    phone = "98765 8" + str(uuid.uuid4().int)[:4]
    r = page.request.post("/api/orders", data={"customer_name": "Dash Cancel", "phone": phone, "address": "12 MG Road, Indiranagar",
                                                 "outlet": "jp-nagar", "lines": [{"item_id": 1, "quantity": 1}]})
    oid = r.json()["id"]
    live = dash_api(page, hdr)["head"]
    assert page.request.patch(f"/api/admin/orders/{oid}", data={"status": "cancelled"}, headers=hdr).ok
    gone = dash_api(page, hdr)["head"]
    assert gone["orders"] == live["orders"] - 1 and gone["sales"] == live["sales"] - 249 and gone["cancelled"] == live["cancelled"] + 1


# ---------- Latcha Club: accounts and points ----------
def test_customer_signs_up_earns_points_and_spends_them_at_checkout(page: Page, admin_password):
    phone = "98765 6" + str(uuid.uuid4().int)[:4]
    name = unique("Club Member")
    page.goto("/")
    page.click("#acctbtn")
    page.click(".otabs.lp [data-t=up]")
    page.fill("#acform [name=name]", name)
    page.fill("#acform [name=phone]", phone)
    page.fill("#acform [name=password]", "secret1")
    page.click(".lp-eye")
    expect(page.locator("#acform [name=password]")).to_have_attribute("type", "text")   # the eye shows the password
    page.click("#acsub2")
    expect(page.locator("#acctbtn")).to_contain_text("60")                                # 50 welcome + 10 first visit
    expect(page.locator(".lp-big")).to_contain_text("60")
    expect(page.locator("#acbody")).to_contain_text("Daily visit")
    page.click("#acx")

    # the owner tops the account up to exactly 500 points (it is worth Rs 50)
    login = page.request.post("/api/admin/login", data={"username": "owner", "password": admin_password})
    head = {"Authorization": "Bearer " + login.json()["access_token"]}
    cid = next(c["id"] for c in page.request.get("/api/admin/customers", headers=head).json() if c["phone"] == phone.replace(" ", ""))
    assert page.request.post(f"/api/admin/customers/{cid}/adjust", headers=head, data={"delta": 440, "note": "test"}).ok
    page.reload()
    expect(page.locator("#acctbtn")).to_contain_text("500")                               # still signed in after a reload

    add = page.locator(".item .add").first
    add.scroll_into_view_if_needed()
    add.click()
    add.click()
    page.click("#cartbar")
    page.click("#otabs [data-t=details]")
    expect(page.locator("#oform [name=phone]")).to_have_value(phone.replace(" ", ""))    # filled from the account
    expect(page.locator("#ptsuselab")).to_be_visible()
    page.locator("#ptsuselab").click()
    expect(page.locator("#osum")).to_contain_text("points −₹50")
    page.fill("#oform [name=address]", "12 MG Road, Indiranagar")
    choose_cash(page)
    page.click("#osub")
    expect(page.locator("#omsg")).to_contain_text("You saved ₹50")
    expect(page.locator("#acctbtn")).to_contain_text("0")
    page.click("#ordx")
    page.click("#acctbtn")
    page.click(".otabs.lp [data-t=ord]")
    expect(page.locator("#acbody")).to_contain_text("points −₹50")                        # the order is in the account's activity


def test_signing_in_with_a_wrong_password_is_refused(page: Page):
    page.goto("/")
    page.click("#acctbtn")
    page.fill("#acform [name=phone]", "98765 43211")
    page.fill("#acform [name=password]", "not-a-real-one")
    page.click("#acsub2")
    expect(page.locator("#acmsg")).to_contain_text("Wrong mobile number or password")
    expect(page.locator("#acctbtn")).to_contain_text("Sign in")


def test_owner_edits_the_points_rules_and_the_website_follows(page: Page, context, admin_password):
    owner = context.new_page()
    admin_login(owner, admin_password)
    owner.click("#tabs button[data-t=fam]")
    box = owner.get_by_label("Points needed")
    expect(box).to_be_visible()
    box.fill("400")
    owner.get_by_label("Rupees off per reward").fill("60")
    with owner.expect_response(lambda r: r.request.method == "PATCH" and "/api/admin/loyalty" in r.url) as saved:
        owner.click("text=Save rules")
    assert saved.value.ok
    rules = page.request.get("/api/loyalty").json()
    assert rules["redeem_points"] == 400 and rules["redeem_rupees"] == 60
    page.goto("/")
    page.click("#acctbtn")
    expect(page.locator(".lp-perks")).to_contain_text("₹60")
    # put the defaults back so other tests are unaffected
    owner.get_by_label("Points needed").fill("500")
    owner.get_by_label("Rupees off per reward").fill("50")
    with owner.expect_response(lambda r: r.request.method == "PATCH" and "/api/admin/loyalty" in r.url):
        owner.click("text=Save rules")
