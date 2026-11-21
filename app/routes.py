import time
from datetime import datetime, timedelta, timezone
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from . import models as m, outlets as ot, schemas as s
from .config import get_settings
from .db import get_db
from .security import make_token, require_admin, verify_password

public = APIRouter(prefix="/api")
admin = APIRouter(prefix="/api/admin", dependencies=[Depends(require_admin)])
auth = APIRouter(prefix="/api/admin")

MAX_ORDERS_PER_PHONE_HOUR = 5
MAX_PENDING_REVIEWS = 200


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
def place_order(body: s.OrderIn, db: Session = Depends(get_db)):
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
    order = m.Order(customer_name=body.customer_name, phone=body.phone, address=body.address,
                    city=outlet.city if outlet else body.city, notes=body.notes, outlet_id=outlet.id if outlet else None)
    for l in body.lines:
        order.lines.append(m.OrderLine(item_id=l.item_id, item_name=items[l.item_id].name, quantity=l.quantity))
    db.add(order)
    db.commit()
    return s.OrderReceipt(id=order.id, status="new", outlet=outlet.name if outlet else None,
                          message="Order request received. LATCHA will confirm it by phone.")


@public.post("/orders/track", response_model=s.TrackOut)
def track_order(body: s.TrackIn, db: Session = Depends(get_db)):
    o = db.get(m.Order, body.order_id)
    # Same answer for "no such order" and "wrong phone", so nobody can probe which numbers exist.
    if o is None or o.phone != body.phone:
        raise HTTPException(404, "We could not find that order. Check the order number and phone number.")
    return o


@public.get("/reviews", response_model=list[s.ReviewOut])
def reviews(limit: int = Query(20, ge=1, le=50), db: Session = Depends(get_db)):
    return db.scalars(select(m.Review).where(m.Review.approved.is_(True))
                      .order_by(m.Review.created_at.desc()).limit(limit)).all()


@public.post("/reviews", status_code=202)
def submit_review(body: s.ReviewIn, db: Session = Depends(get_db)):
    ok = {"message": "Thanks! Your review will appear after the owner approves it."}
    if body.website:  # bot filled the honeypot: pretend success, store nothing
        return ok
    pending = db.scalar(select(func.count(m.Review.id)).where(m.Review.approved.is_(False)))
    if pending >= MAX_PENDING_REVIEWS:
        raise HTTPException(429, "Review queue is full. Please try again later.")
    db.add(m.Review(author=body.author, rating=body.rating, text=body.text))
    db.commit()
    return ok


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
    o.status = body.status
    db.commit()
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
