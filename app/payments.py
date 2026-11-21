"""UPI payments. Two modes, one flow:
  demo     - a pretend pay screen, no money moves (for showing the café owner)
  razorpay - real Razorpay Checkout (test keys = fake money, live keys = real money)
The amount is ALWAYS worked out here from the database prices. The browser is never trusted for money."""
import base64
import hashlib
import hmac
import json
import logging
import urllib.error
import urllib.request
from datetime import datetime, timezone
from sqlalchemy import inspect, select, text, update

from .config import get_settings

log = logging.getLogger("latcha.pay")

# Placeholder prices (rupees) used ONCE when the price column is first created. Owner edits them in Admin > Menu.
DEMO_PRICE = {"classic": 249, "cloud": 279, "soft": 199, "float": 299}


def upgrade_payment_schema(engine) -> None:
    """Safe to run many times. Adds the payment columns and, once, placeholder prices."""
    insp = inspect(engine)
    ocols = {c["name"] for c in insp.get_columns("orders")}
    adds = [
        ("payment_method", "VARCHAR(10) NOT NULL DEFAULT 'cod'"),
        ("payment_status", "VARCHAR(10) NOT NULL DEFAULT 'unpaid'"),
        ("amount", "INTEGER"), ("payment_provider", "VARCHAR(12)"), ("payment_ref", "VARCHAR(64)"),
        ("payment_id", "VARCHAR(64)"), ("paid_at", "TIMESTAMP"),
    ]
    with engine.begin() as c:
        for name, ddl in adds:
            if name not in ocols:
                c.execute(text(f"ALTER TABLE orders ADD COLUMN {name} {ddl}"))
        if "payment_ref" not in ocols:
            c.execute(text("CREATE INDEX IF NOT EXISTS ix_orders_payment_ref ON orders (payment_ref)"))
        if "price" not in {x["name"] for x in insp.get_columns("order_lines")}:
            c.execute(text("ALTER TABLE order_lines ADD COLUMN price INTEGER"))
        if "price" not in {x["name"] for x in insp.get_columns("menu_items")}:
            c.execute(text("ALTER TABLE menu_items ADD COLUMN price INTEGER"))
            for cat, price in DEMO_PRICE.items():
                c.execute(text("UPDATE menu_items SET price = :p WHERE category = :c AND price IS NULL"), {"p": price, "c": cat})


def mode() -> str:
    return get_settings().pay_mode


def order_total(lines) -> int | None:
    """Total in rupees, or None when any dish has no price (then only pay-on-delivery is offered)."""
    total = 0
    for it, qty in lines:
        if not it.price or it.price <= 0:
            return None
        total += it.price * qty
    return total


# ---------- Razorpay ----------
def _rzp_post(path: str, body: dict) -> dict:
    st = get_settings()
    tok = base64.b64encode(f"{st.razorpay_key_id}:{st.razorpay_key_secret}".encode()).decode()
    req = urllib.request.Request("https://api.razorpay.com/v1" + path, data=json.dumps(body).encode(), method="POST",
                                 headers={"Content-Type": "application/json", "Authorization": "Basic " + tok})
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return json.loads(r.read())
    except (urllib.error.URLError, TimeoutError, ValueError) as e:  # noqa: BLE001
        log.error("Razorpay create-order failed: %s", e)
        raise


def create_provider_order(order_id: int, amount_rupees: int) -> dict:
    """Returns {provider, provider_order_id}. Raises if the provider is unreachable."""
    if mode() == "razorpay":
        o = _rzp_post("/orders", {"amount": amount_rupees * 100, "currency": "INR", "receipt": f"latcha-{order_id}",
                                  "notes": {"latcha_order": str(order_id)}})
        return {"provider": "razorpay", "provider_order_id": o["id"]}
    return {"provider": "demo", "provider_order_id": f"demo_{order_id}"}


def _sig(secret: str, msg: bytes) -> str:
    return hmac.new(secret.encode(), msg, hashlib.sha256).hexdigest()


def verify_checkout_signature(provider_order_id: str, payment_id: str, signature: str) -> bool:
    return hmac.compare_digest(_sig(get_settings().razorpay_key_secret, f"{provider_order_id}|{payment_id}".encode()), signature or "")


def verify_webhook_signature(raw: bytes, signature: str) -> bool:
    secret = get_settings().razorpay_webhook_secret
    return bool(secret) and hmac.compare_digest(_sig(secret, raw), signature or "")


def mark_paid(db, order, payment_id: str | None) -> bool:
    """Idempotent: a repeat call (double click, webhook retry) changes nothing. Returns True the first time only."""
    res = db.execute(update(type(order)).where(type(order).id == order.id, type(order).payment_status != "paid")
                     .values(payment_status="paid", payment_id=payment_id, paid_at=datetime.now(timezone.utc)))
    db.commit()
    db.refresh(order)
    return res.rowcount == 1


def public_payment(order) -> dict:
    """What the browser needs to start paying."""
    st = get_settings()
    d = {"provider": order.payment_provider, "amount": order.amount, "provider_order_id": order.payment_ref}
    if order.payment_provider == "razorpay":
        d["key_id"] = st.razorpay_key_id
    return d
