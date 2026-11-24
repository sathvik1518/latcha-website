import time
from datetime import datetime, timedelta, timezone
import csv, hashlib, hmac, io, json, logging
from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from . import dashboard, fam, loyalty, models as m, notify, outlets as ot, payments as pay, schemas as s
from .config import get_settings
from .db import get_db
from .security import hash_password, make_token, require_admin, verify_password

public = APIRouter(prefix="/api")
admin = APIRouter(prefix="/api/admin", dependencies=[Depends(require_admin)])
auth = APIRouter(prefix="/api/admin")

MAX_ORDERS_PER_PHONE_HOUR = 5
MAX_PENDING_REVIEWS = 200
log = logging.getLogger("latcha.pay")
NOT_FOUND = "We could not find that order. Check the order number and phone number."


def track_code(order_id: int) -> str:
    """A private link code for one order: "<number>.<10 secret letters>". Nobody can guess it without the server secret."""
    key = (get_settings().jwt_secret or "latcha-dev").encode()
    return f"{order_id}.{hmac.new(key, f'track:{order_id}'.encode(), hashlib.sha256).hexdigest()[:10]}"


# ---------- public ----------
@public.get("/menu", response_model=list[s.MenuItemOut])
def menu(category: s.Category | None = None, db: Session = Depends(get_db)):
    q = select(m.MenuItem).where(m.MenuItem.available.is_(True))
    if category:
        q = q.where(m.MenuItem.category == category)
    return db.scalars(q.order_by(m.MenuItem.category, m.MenuItem.position)).all()


def _sold_out_map(db: Session) -> dict[int, list[int]]:
    out: dict[int, list[int]] = {}
    for oid, iid in db.execute(select(m.OutletSoldOut.outlet_id, m.OutletSoldOut.item_id)):
        out.setdefault(oid, []).append(iid)
    return out


def _outlet_view(o: m.Outlet, sold: dict[int, list[int]], at: datetime) -> dict:
    open_now, text = ot.status_text(o, at)
    can = open_now or (not get_settings().enforce_outlet_hours and o.accepting_orders and not o.coming_soon)
    return dict(id=o.id, slug=o.slug, name=o.name, city=o.city, area=o.area, address=o.address, phone=o.phone,
                maps_url=o.maps_url, opens=o.opens, closes=o.closes, coming_soon=o.coming_soon,
                accepting_orders=o.accepting_orders, open_now=open_now, can_order=can, status=text,
                sold_out=sorted(sold.get(o.id, [])))


@public.get("/outlets", response_model=list[s.OutletOut])
def list_outlets(db: Session = Depends(get_db)):
    at, sold = ot.now_ist(), _sold_out_map(db)
    return [_outlet_view(o, sold, at) for o in db.scalars(select(m.Outlet).order_by(m.Outlet.position, m.Outlet.id))]


@public.post("/orders", response_model=s.OrderReceipt, status_code=201)
def place_order(body: s.OrderIn, request: Request, db: Session = Depends(get_db), cust: m.Customer | None = Depends(loyalty.optional_customer)):
    since = datetime.now(timezone.utc) - timedelta(hours=1)
    recent = db.scalar(select(func.count(m.Order.id)).where(
        m.Order.phone == body.phone, m.Order.created_at >= since))
    if recent >= MAX_ORDERS_PER_PHONE_HOUR:
        raise HTTPException(429, "Too many orders from this number. Please try again later.")
    ids = {l.item_id for l in body.lines}
    items = {i.id: i for i in db.scalars(select(m.MenuItem).where(m.MenuItem.id.in_(ids)))}
    for l in body.lines:
        it = items.get(l.item_id)
        if it is None or not it.available:
            raise HTTPException(422, f"Item {l.item_id} is not available")
    outlet = None
    if body.outlet:
        outlet = db.scalar(select(m.Outlet).where(m.Outlet.slug == body.outlet))
        if outlet is None:
            raise HTTPException(422, "We could not find that outlet. Please choose another.")
        view = _outlet_view(outlet, _sold_out_map(db), ot.now_ist())
        if outlet.coming_soon:
            raise HTTPException(422, f"{outlet.name} is opening soon. Please choose another outlet.")
        if not view["can_order"]:
            raise HTTPException(422, f"{outlet.name}: {view['status']}. Please try again later or choose another outlet.")
        for l in body.lines:
            if l.item_id in view["sold_out"]:
                raise HTTPException(422, f"{items[l.item_id].name} is sold out at {outlet.name}. Please remove it or choose another outlet.")
    total = pay.order_total([(items[l.item_id], l.quantity) for l in body.lines])
    discount = 0
    if body.use_reward:   # a Latcha Fam free drink: the cheapest drink in the cart is free
        if fam.card(db, body.phone)["rewards_available"] < 1:
            raise HTTPException(422, "No free drink is waiting on this number yet.")
        discount = fam.reward_discount(items, body.lines)
        if discount is None:
            raise HTTPException(422, "A free drink cannot be used on these items yet. Please order without it.")
    pts_used = pts_disc = 0
    if body.use_points:   # Latcha points: whole blocks (500 points = Rs 50 by default), never more than the bill
        if cust is None:
            raise HTTPException(401, "Please sign in to use your Latcha points.")
        if total is None:
            raise HTTPException(422, "Points cannot be used on these items yet. Please order without them.")
        pts_used, pts_disc = loyalty.redeem(db, cust, total - discount)
        if not pts_disc:
            r = loyalty.rules(db)
            raise HTTPException(422, f"You need at least {r['redeem_points']} Latcha points (and a bill of Rs {r['redeem_rupees']} or more) to use them.")
    charge = None if total is None else total - discount - pts_disc
    free = charge == 0            # the whole order was the free drink: nothing to pay
    method, pstatus = body.pay, ("pending" if body.pay == "upi" else "unpaid")
    if free:
        method, pstatus = "cod", "paid"
    elif body.pay == "upi":
        if pay.mode() == "off":
            raise HTTPException(422, "Online payment is not available right now. Please choose pay at delivery.")
        if charge is None:
            raise HTTPException(422, "Online payment is not available for these items yet. Please choose pay at delivery.")
    order = m.Order(customer_name=body.customer_name, phone=body.phone, address=body.address,
                    city=outlet.city if outlet else body.city, notes=body.notes, outlet_id=outlet.id if outlet else None,
                    amount=charge, payment_method=method, payment_status=pstatus, fam_discount=discount,
                    customer_id=cust.id if cust else None, points_used=pts_used, points_discount=pts_disc)
    if free:
        order.payment_id, order.paid_at = "reward", datetime.now(timezone.utc)
    for l in body.lines:
        order.lines.append(m.OrderLine(item_id=l.item_id, item_name=items[l.item_id].name, quantity=l.quantity,
                                       price=items[l.item_id].price))
    db.add(order)
    if pts_used:
        db.flush()
        loyalty.add_tx(db, cust, -pts_used, "redeem", f"Order #{order.id}")
    start = None
    if method == "upi":
        db.flush()  # we need the order number for the provider
        try:
            made = pay.create_provider_order(order.id, charge)
        except Exception:  # noqa: BLE001  (nothing is saved if the payment service is down)
            db.rollback()
            raise HTTPException(502, "The payment service is busy. Please try again, or choose pay at delivery.")
        order.payment_provider, order.payment_ref = made["provider"], made["provider_order_id"]
        start = s.PaymentStart(**pay.public_payment(order))
    db.commit()
    if not start:   # pay-at-delivery (or free) orders ping the owner now; UPI orders ping once the payment is confirmed
        notify.order_placed(order, str(request.base_url))
    msg = "Order saved. Complete the UPI payment to confirm it." if start else "Order request received. LATCHA will confirm it by phone."
    return s.OrderReceipt(id=order.id, status="new", outlet=outlet.name if outlet else None, message=msg,
                          amount=charge, fam_discount=discount, points_used=pts_used, points_discount=pts_disc,
                          points=cust.points if cust else None, payment=start, track=track_code(order.id))


def _own_order(db: Session, order_id: int, phone: str) -> m.Order:
    o = db.get(m.Order, order_id)
    if o is None or o.phone != phone:   # same answer for "no such order" and "wrong phone"
        raise HTTPException(404, NOT_FOUND)
    return o


@public.post("/fam/card", response_model=s.FamCard)
def fam_card(body: s.FamIn, db: Session = Depends(get_db)):
    """Sips and free drinks for one mobile number. Shows counts only: no names, addresses or orders."""
    return fam.card(db, body.phone)


@public.get("/payments/config", response_model=s.PayConfig)
def payments_config():
    return s.PayConfig(mode=pay.mode())


@public.post("/orders/{order_id}/pay", response_model=s.PaymentStart)
def restart_payment(order_id: int, body: s.PayIn, db: Session = Depends(get_db)):
    """Used when the customer closed the payment window and wants to pay again. Reuses the same provider order."""
    o = _own_order(db, order_id, body.phone)
    if o.payment_method != "upi" or o.status == "cancelled" or pay.mode() == "off":
        raise HTTPException(422, "This order cannot be paid online.")
    if o.payment_status == "paid":
        raise HTTPException(409, "This order is already paid.")
    if not o.payment_ref or (o.payment_provider or "demo") != ("razorpay" if pay.mode() == "razorpay" else "demo"):
        try:
            made = pay.create_provider_order(o.id, o.amount)
        except Exception:  # noqa: BLE001
            raise HTTPException(502, "The payment service is busy. Please try again.")
        o.payment_provider, o.payment_ref = made["provider"], made["provider_order_id"]
    o.payment_status = "pending"
    db.commit()
    return s.PaymentStart(**pay.public_payment(o))


@public.post("/orders/{order_id}/pay/confirm", response_model=s.PayResult)
def confirm_payment(order_id: int, body: s.PayIn, request: Request, db: Session = Depends(get_db)):
    o = _own_order(db, order_id, body.phone)
    if o.payment_method != "upi" or o.status == "cancelled":
        raise HTTPException(422, "This order cannot be paid online.")
    if o.payment_status == "paid":   # double click or retry: same answer, nothing changes
        return s.PayResult(id=o.id, payment_status="paid", amount=o.amount)
    if o.payment_provider == "demo":
        if pay.mode() != "demo":
            raise HTTPException(404, "Not found")   # the pretend screen is switched off once real payments are on
        if pay.mark_paid(db, o, f"demo_pay_{o.id}"):
            notify.order_placed(o, str(request.base_url))
    else:
        if not (body.razorpay_payment_id and body.razorpay_order_id and body.razorpay_signature):
            raise HTTPException(422, "Payment details are missing.")
        if pay.mode() != "razorpay" or body.razorpay_order_id != o.payment_ref or not pay.verify_checkout_signature(
                body.razorpay_order_id, body.razorpay_payment_id, body.razorpay_signature):
            raise HTTPException(400, "We could not verify that payment. If money was taken, it will be matched shortly.")
        if pay.mark_paid(db, o, body.razorpay_payment_id):
            notify.order_placed(o, str(request.base_url))
    return s.PayResult(id=o.id, payment_status=o.payment_status, amount=o.amount)


@public.post("/payments/razorpay/webhook")
async def razorpay_webhook(request: Request, db: Session = Depends(get_db)):
    """Razorpay tells us a payment succeeded or failed. Signature checked; safe to receive twice."""
    raw = await request.body()
    if not pay.verify_webhook_signature(raw, request.headers.get("x-razorpay-signature", "")):
        raise HTTPException(400, "Bad signature")
    try:
        ev = json.loads(raw)
        kind = ev.get("event", "")
        ent = (ev.get("payload", {}).get("payment") or {}).get("entity") or {}
        ref = ent.get("order_id")
    except (ValueError, AttributeError):
        raise HTTPException(400, "Bad payload")
    o = db.scalar(select(m.Order).where(m.Order.payment_ref == ref)) if ref else None
    if o is None:
        log.warning("Webhook %s for unknown order %s", kind, ref)
        return {"ok": True}
    if kind in ("payment.captured", "order.paid"):
        if ent.get("amount") != (o.amount or 0) * 100:
            log.error("Webhook amount mismatch on order %s: got %s", o.id, ent.get("amount"))
            return {"ok": True}
        if pay.mark_paid(db, o, ent.get("id")):
            notify.order_placed(o, str(request.base_url))
    elif kind == "payment.failed" and o.payment_status == "pending":
        o.payment_status = "failed"
        db.commit()
    return {"ok": True}


@public.post("/orders/track", response_model=s.TrackOut)
def track_order(body: s.TrackIn, db: Session = Depends(get_db)):
    o = db.get(m.Order, body.order_id)
    # Same answer for "no such order" and "wrong phone", so nobody can probe which numbers exist.
    if o is None or o.phone != body.phone:
        raise HTTPException(404, "We could not find that order. Check the order number and phone number.")
    return _track_out(o)


def _track_out(o: m.Order) -> s.TrackOut:
    out = s.TrackOut.model_validate(o)
    out.code = track_code(o.id)
    return out


@public.get("/orders/track/{code}", response_model=s.TrackOut)
def track_by_code(code: str, db: Session = Depends(get_db)):
    """The share link: the secret code stands in for the phone number. Same answer for every kind of miss."""
    num, _, _sig = code.partition(".")
    o = db.get(m.Order, int(num)) if num.isdigit() and len(num) <= 10 and int(num) < 2_000_000_000 else None
    if o is None or not hmac.compare_digest(code.encode(), track_code(o.id).encode()):
        raise HTTPException(404, "We could not find that order. Check the link and try again.")
    return _track_out(o)


@public.get("/reviews", response_model=list[s.ReviewOut])
def reviews(limit: int = Query(20, ge=1, le=50), db: Session = Depends(get_db)):
    return db.scalars(select(m.Review).where(m.Review.approved.is_(True))
                      .order_by(m.Review.created_at.desc()).limit(limit)).all()


@public.post("/reviews", status_code=202)
def submit_review(body: s.ReviewIn, db: Session = Depends(get_db), cust: m.Customer | None = Depends(loyalty.optional_customer)):
    ok = {"message": "Thanks! Your review will appear after the owner approves it."}
    if body.website:  # bot filled the honeypot: pretend success, store nothing
        return ok
    pending = db.scalar(select(func.count(m.Review.id)).where(m.Review.approved.is_(False)))
    if pending >= MAX_PENDING_REVIEWS:
        raise HTTPException(429, "Review queue is full. Please try again later.")
    db.add(m.Review(author=body.author, rating=body.rating, text=body.text, customer_id=cust.id if cust else None))
    db.commit()
    return ok


# ---------- customers: accounts and Latcha points ----------
def _rules_out(db: Session) -> dict:
    r = loyalty.rules(db)
    return {**r, "enabled": bool(r["enabled"]), "otp": loyalty.otp_required()}


def _me(db: Session, c: m.Customer, awarded: int = 0) -> s.MeOut:
    r = loyalty.rules(db)
    orders = db.scalars(select(m.Order).where(m.Order.customer_id == c.id).order_by(m.Order.created_at.desc()).limit(50)).all()
    hist = db.scalars(select(m.PointTx).where(m.PointTx.customer_id == c.id).order_by(m.PointTx.id.desc()).limit(60)).all()
    revs = db.scalars(select(m.Review).where(m.Review.customer_id == c.id).order_by(m.Review.created_at.desc()).limit(30)).all()
    return s.MeOut(id=c.id, name=c.name, phone=c.phone, points=c.points, visits=c.visits, member_since=c.created_at, birthday=c.birthday,
                   rules=_rules_out(db), worth_rupees=(c.points // r["redeem_points"]) * r["redeem_rupees"], visit_awarded=awarded,
                   fam=fam.card(db, c.phone), orders=orders, history=hist, reviews=revs)


@public.post("/customers/otp")
def customer_otp(body: s.OtpIn, db: Session = Depends(get_db)):
    """Ask for a sign-up code. In demo mode the code comes back in the reply (no SMS is sent)."""
    if not loyalty.otp_required():
        return {"required": False}
    if db.scalar(select(m.Customer.id).where(m.Customer.phone == body.phone)):
        raise HTTPException(409, "This number already has an account. Please sign in.")
    code = loyalty.otp_issue(db, body.phone)
    return {"required": True, "demo_code": code, "minutes": loyalty.OTP_MINUTES,
            "note": "Demo mode: no SMS is sent, so the code is shown here."}


@public.post("/customers/register", response_model=s.SignedIn, status_code=201)
def customer_register(body: s.RegisterIn, db: Session = Depends(get_db)):
    if db.scalar(select(m.Customer.id).where(m.Customer.phone == body.phone)):
        raise HTTPException(409, "This number already has an account. Please sign in.")
    if loyalty.otp_required():
        loyalty.otp_check(db, body.phone, body.otp)
    c = m.Customer(phone=body.phone, name=body.name, pw_hash=hash_password(body.password))
    db.add(c)
    db.flush()
    r = loyalty.rules(db)
    got = 0
    if r["enabled"] and r["signup_bonus"]:
        got += loyalty.add_tx(db, c, r["signup_bonus"], "welcome", "Welcome bonus")
    got += loyalty.award_visit(db, c)
    tok = loyalty.make_token(c)   # raises 503 before saving anything if accounts are not configured
    db.commit()
    return s.SignedIn(access_token=tok, me=_me(db, c, got))


@public.post("/customers/login", response_model=s.SignedIn)
def customer_login(body: s.CustLoginIn, db: Session = Depends(get_db)):
    c = db.scalar(select(m.Customer).where(m.Customer.phone == body.phone))
    if c is None or not verify_password(body.password, c.pw_hash):
        time.sleep(1)   # slows password guessing; same answer for "no account" and "wrong password"
        raise HTTPException(401, "Wrong mobile number or password")
    got = loyalty.award_visit(db, c)
    tok = loyalty.make_token(c)
    db.commit()
    return s.SignedIn(access_token=tok, me=_me(db, c, got))


@public.get("/me", response_model=s.MeOut)
def my_account(db: Session = Depends(get_db), c: m.Customer = Depends(loyalty.require_customer)):
    """Opening the site while signed in counts as a visit (once per day), so this also hands out the daily points."""
    got = loyalty.award_visit(db, c)
    db.commit()
    return _me(db, c, got)


@public.patch("/me", response_model=s.MeOut)
def edit_me(body: s.MeEdit, db: Session = Depends(get_db), c: m.Customer = Depends(loyalty.require_customer)):
    if body.name:
        c.name = body.name.strip()
    if body.new_password:
        if not body.current_password or not verify_password(body.current_password, c.pw_hash):
            raise HTTPException(422, "Your current password is not right.")
        c.pw_hash = hash_password(body.new_password)
    got = 0
    if body.birthday:
        if c.birthday and c.birthday != body.birthday:
            raise HTTPException(422, "Your birthday is already saved and cannot be changed. Ask LATCHA if it is wrong.")
        c.birthday = body.birthday
        got = loyalty.award_birthday(db, c)
    db.commit()
    return _me(db, c, got)


@public.get("/loyalty", response_model=s.PointRules)
def public_rules(db: Session = Depends(get_db)):
    """The current points rules, so the sign-up page always shows the owner's real numbers."""
    return _rules_out(db)


# ---------- admin ----------
@auth.post("/login", response_model=s.TokenOut)
def login(body: s.LoginIn):
    st = get_settings()
    good = (st.admin_password_hash and st.jwt_secret and body.username == st.admin_username
            and verify_password(body.password, st.admin_password_hash))
    if not good:
        time.sleep(1)  # slows password guessing
        raise HTTPException(401, "Wrong username or password")
    return s.TokenOut(access_token=make_token(body.username))


@admin.get("/menu", response_model=list[s.MenuItemOut])
def admin_menu(db: Session = Depends(get_db)):
    return db.scalars(select(m.MenuItem).order_by(m.MenuItem.category, m.MenuItem.position)).all()


@admin.post("/menu", response_model=s.MenuItemOut, status_code=201)
def add_item(body: s.MenuItemIn, db: Session = Depends(get_db)):
    it = m.MenuItem(**body.model_dump())
    db.add(it)
    db.commit()
    return it


@admin.patch("/menu/{item_id}", response_model=s.MenuItemOut)
def edit_item(item_id: int, body: s.MenuItemPatch, db: Session = Depends(get_db)):
    it = db.get(m.MenuItem, item_id)
    if not it:
        raise HTTPException(404, "Not found")
    for k, v in body.model_dump(exclude_unset=True).items():
        setattr(it, k, v)
    db.commit()
    return it


@admin.get("/orders", response_model=list[s.OrderOut])
def list_orders(status: s.Status | None = None, outlet_id: int | None = None,
                limit: int = Query(50, ge=1, le=200),
                offset: int = Query(0, ge=0), db: Session = Depends(get_db)):
    q = select(m.Order)
    if status:
        q = q.where(m.Order.status == status)
    if outlet_id:
        q = q.where(m.Order.outlet_id == outlet_id)
    return db.scalars(q.order_by(m.Order.created_at.desc()).limit(limit).offset(offset)).all()


@admin.patch("/orders/{order_id}", response_model=s.OrderOut)
def set_order_status(order_id: int, body: s.StatusIn, db: Session = Depends(get_db)):
    o = db.get(m.Order, order_id)
    if not o:
        raise HTTPException(404, "Not found")
    if o.status != body.status:
        o.status_at = datetime.now(timezone.utc)
    o.status = body.status
    loyalty.refund_if_cancelled(db, o, body.status)
    db.commit()
    return o


@admin.get("/fam", response_model=list[s.FamMember])
def admin_fam(db: Session = Depends(get_db)):
    return fam.members(db)


@admin.get("/stats", response_model=s.StatsOut)
def admin_stats(db: Session = Depends(get_db)):
    day = ot.now_ist().replace(hour=0, minute=0, second=0, microsecond=0).astimezone(timezone.utc)   # start of today in India
    O = m.Order
    cnt = lambda *w: db.scalar(select(func.count(O.id)).where(*w)) or 0
    return s.StatsOut(
        new_orders=cnt(O.status == "new"),
        being_made=cnt(O.status == "confirmed"),
        on_the_way=cnt(O.status == "out"),
        awaiting_payment=cnt(O.payment_method == "upi", O.payment_status.in_(("pending", "failed")), O.status != "cancelled"),
        today_orders=cnt(O.created_at >= day, O.status != "cancelled"),
        today_paid=db.scalar(select(func.coalesce(func.sum(O.amount), 0)).where(O.payment_status == "paid", O.paid_at >= day, O.status != "cancelled")) or 0,
        latest_order_id=db.scalar(select(func.coalesce(func.max(O.id), 0))) or 0)


@admin.get("/dashboard", response_model=s.DashOut)
def admin_dashboard(days: int = Query(7, ge=1, le=90), db: Session = Depends(get_db)):
    return dashboard.build(db, days)


def _safe(v) -> str:
    """Stop a spreadsheet from running a name or address as a formula."""
    v = "" if v is None else str(v)
    return "'" + v if v[:1] in ("=", "+", "-", "@", "\t", "\r") else v


@admin.get("/export.csv")
def export_orders(days: int = Query(30, ge=1, le=365), db: Session = Depends(get_db)):
    """Orders as a spreadsheet file (Excel opens it). Times are Indian time."""
    start = ot.now_ist().replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=days - 1)
    rows = db.scalars(select(m.Order).where(m.Order.created_at >= start.astimezone(timezone.utc))
                      .order_by(m.Order.id)).all()
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["Order", "Placed (IST)", "Status", "Customer", "Phone", "Outlet", "Items", "Amount (Rs)", "Payment", "Paid?", "Free drink (Rs)", "Points (Rs)"])
    for o in rows:
        placed = dashboard._ist(o.created_at).strftime("%Y-%m-%d %H:%M")
        items = "; ".join(f"{l.quantity} x {l.item_name}" for l in o.lines)
        w.writerow([o.id, placed, o.status, _safe(o.customer_name), _safe(o.phone), _safe(o.outlet_name or o.city), _safe(items),
                    "" if o.amount is None else o.amount, o.payment_method, o.payment_status, o.fam_discount, o.points_discount])
    name = f"latcha-orders-{ot.now_ist():%Y-%m-%d}.csv"
    return Response("\ufeff" + buf.getvalue(), media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="{name}"'})


@admin.post("/orders/{order_id}/mark-paid", response_model=s.OrderOut)
def mark_order_paid(order_id: int, db: Session = Depends(get_db)):
    """For cash or a UPI payment the owner saw land in their own app."""
    o = db.get(m.Order, order_id)
    if not o:
        raise HTTPException(404, "Not found")
    if o.payment_status != "paid":
        pay.mark_paid(db, o, "manual")
    return o


@admin.get("/reviews", response_model=list[s.ReviewAdminOut])
def admin_reviews(approved: bool | None = None, db: Session = Depends(get_db)):
    q = select(m.Review)
    if approved is not None:
        q = q.where(m.Review.approved.is_(approved))
    return db.scalars(q.order_by(m.Review.created_at.desc()).limit(200)).all()


@admin.post("/reviews/{rid}/approve", response_model=s.ReviewAdminOut)
def approve(rid: int, db: Session = Depends(get_db)):
    r = db.get(m.Review, rid)
    if not r:
        raise HTTPException(404, "Not found")
    r.approved = True
    db.commit()
    return r


@admin.delete("/reviews/{rid}", status_code=204)
def delete_review(rid: int, db: Session = Depends(get_db)):
    r = db.get(m.Review, rid)
    if not r:
        raise HTTPException(404, "Not found")
    db.delete(r)
    db.commit()


# ---------- admin: outlets ----------
@admin.get("/outlets", response_model=list[s.OutletAdminOut])
def admin_outlets(db: Session = Depends(get_db)):
    at, sold = ot.now_ist(), _sold_out_map(db)
    return [_outlet_view(o, sold, at) for o in db.scalars(select(m.Outlet).order_by(m.Outlet.position, m.Outlet.id))]


@admin.patch("/outlets/{outlet_id}", response_model=s.OutletAdminOut)
def edit_outlet(outlet_id: int, body: s.OutletPatch, db: Session = Depends(get_db)):
    o = db.get(m.Outlet, outlet_id)
    if not o:
        raise HTTPException(404, "Not found")
    for k, v in body.model_dump(exclude_unset=True).items():
        if v is not None:
            setattr(o, k, v)
    db.commit()
    return _outlet_view(o, _sold_out_map(db), ot.now_ist())


@admin.put("/outlets/{outlet_id}/sold-out/{item_id}", response_model=s.OutletAdminOut)
def set_sold_out(outlet_id: int, item_id: int, body: s.SoldOutIn, db: Session = Depends(get_db)):
    o = db.get(m.Outlet, outlet_id)
    if not o or not db.get(m.MenuItem, item_id):
        raise HTTPException(404, "Not found")
    row = db.get(m.OutletSoldOut, (outlet_id, item_id))
    if body.sold_out and not row:
        db.add(m.OutletSoldOut(outlet_id=outlet_id, item_id=item_id))
    elif not body.sold_out and row:
        db.delete(row)
    db.commit()
    return _outlet_view(o, _sold_out_map(db), ot.now_ist())


# ---------- admin: Latcha points ----------
@admin.get("/loyalty", response_model=s.PointRules)
def admin_rules(db: Session = Depends(get_db)):
    return _rules_out(db)


@admin.patch("/loyalty", response_model=s.PointRules)
def edit_rules(body: s.PointRulesPatch, db: Session = Depends(get_db)):
    patch = {k: int(v) for k, v in body.model_dump(exclude_unset=True).items() if v is not None}
    loyalty.save_rules(db, patch)
    return _rules_out(db)


@admin.get("/customers", response_model=list[s.AdminCustomer])
def admin_customers(q: str = Query("", max_length=40), db: Session = Depends(get_db)):
    counts = dict(db.execute(select(m.Order.customer_id, func.count(m.Order.id)).where(m.Order.customer_id.is_not(None))
                             .group_by(m.Order.customer_id)).all())
    sel = select(m.Customer).order_by(m.Customer.points.desc(), m.Customer.id).limit(300)
    if q.strip():
        like = f"%{q.strip()}%"
        sel = select(m.Customer).where(m.Customer.name.ilike(like) | m.Customer.phone.like(like)).order_by(m.Customer.points.desc()).limit(300)
    return [s.AdminCustomer(id=c.id, name=c.name, phone=c.phone, points=c.points, visits=c.visits, orders=counts.get(c.id, 0),
                            last_visit=c.last_visit, created_at=c.created_at) for c in db.scalars(sel)]


@admin.post("/customers/{cid}/adjust", response_model=s.AdminCustomer)
def adjust_points(cid: int, body: s.AdjustIn, db: Session = Depends(get_db)):
    c = db.get(m.Customer, cid)
    if not c:
        raise HTTPException(404, "Not found")
    loyalty.add_tx(db, c, body.delta, "adjust", body.note.strip() or "Added by LATCHA")
    db.commit()
    n = db.scalar(select(func.count(m.Order.id)).where(m.Order.customer_id == c.id)) or 0
    return s.AdminCustomer(id=c.id, name=c.name, phone=c.phone, points=c.points, visits=c.visits, orders=n,
                           last_visit=c.last_visit, created_at=c.created_at)


# ---------- order alerts (Telegram) ----------
@admin.get("/alerts")
def alerts_status():
    return {"configured": notify.configured()}


@admin.post("/alerts/test")
def alerts_test(request: Request):
    if not notify.configured():
        raise HTTPException(422, "Alerts are off. Add TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID in Vercel, then redeploy.")
    ok, why = notify.send("✅ <b>LATCHA alerts are working.</b>\nNew orders will appear here the moment they come in.", str(request.base_url))
    if not ok:
        raise HTTPException(502, why)
    return {"ok": True, "message": "Test sent. Check Telegram."}
