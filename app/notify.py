"""Instant order alerts to the owner's Telegram. Does nothing unless both settings are filled in.

It never breaks ordering: every failure is caught and logged, and the call gives up after 4 seconds.
"""
import html
import json
import logging
import urllib.request
from zoneinfo import ZoneInfo

from .config import get_settings

log = logging.getLogger("latcha")
IST = ZoneInfo("Asia/Kolkata")


def configured() -> bool:
    st = get_settings()
    return bool(st.telegram_bot_token.strip() and st.telegram_chat_id.strip())


def send(text: str, site_url: str = "") -> tuple[bool, str]:
    """Send one message. Returns (ok, short reason)."""
    st = get_settings()
    if not configured():
        return False, "Telegram is not set up yet."
    body = {"chat_id": st.telegram_chat_id.strip(), "text": text, "parse_mode": "HTML", "disable_web_page_preview": True}
    if site_url.startswith("http"):
        body["reply_markup"] = {"inline_keyboard": [[{"text": "Open owner desk", "url": site_url.rstrip("/") + "/admin.html"}]]}
    req = urllib.request.Request(f"https://api.telegram.org/bot{st.telegram_bot_token.strip()}/sendMessage",
                                 data=json.dumps(body).encode(), headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=4) as r:
            return (r.status == 200), "Sent"
    except urllib.error.HTTPError as e:
        why = "The bot token looks wrong." if e.code == 401 else "Telegram refused it. Message your bot first, then check the chat id." if e.code in (400, 403) else f"Telegram error {e.code}."
        log.warning("Telegram alert failed: %s", e.code)
        return False, why
    except Exception as e:  # noqa: BLE001
        log.warning("Telegram alert failed: %s", type(e).__name__)
        return False, "Could not reach Telegram."


def order_text(o) -> str:
    e = html.escape
    lines = "\n".join(f"• {l.quantity} × {e(l.item_name)}" for l in o.lines)
    pay = {"upi": "Paid by UPI ✅" if o.payment_status == "paid" else "UPI (waiting)", "cod": "Pay at delivery"}.get(o.payment_method, o.payment_method)
    total = f"₹{o.amount}" if o.amount is not None else "price not set"
    when = o.created_at.astimezone(IST).strftime("%I:%M %p") if o.created_at else ""
    extra = []
    if o.fam_discount:
        extra.append(f"Fam free drink −₹{o.fam_discount}")
    if o.points_discount:
        extra.append(f"Club points −₹{o.points_discount}")
    parts = [f"🍵 <b>New LATCHA order #{o.id}</b> · {when}",
             f"📍 {e(o.outlet_name or o.city)}",
             f"👤 {e(o.customer_name)} · +91 {e(o.phone)}",
             f"🏠 {e(o.address)}", "", lines, "",
             f"💰 <b>{total}</b> · {pay}"]
    if extra:
        parts.append("🎁 " + ", ".join(extra))
    if o.notes:
        parts.append(f"📝 {e(o.notes)}")
    parts.append("\nCall to confirm, then tap Confirm in the owner desk.")
    return "\n".join(parts)


def order_placed(o, site_url: str = "") -> None:
    """Call after the order is saved. For UPI orders call it again only once the payment is confirmed."""
    if not configured():
        return
    try:
        send(order_text(o), site_url)
    except Exception:  # noqa: BLE001
        log.exception("Order alert failed")
