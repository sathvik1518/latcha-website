from datetime import datetime, timezone
from sqlalchemy import (Boolean, CheckConstraint, DateTime, ForeignKey, Integer,
                        String, Text, JSON)
from sqlalchemy.orm import Mapped, mapped_column, relationship
from .db import Base


def now():
    return datetime.now(timezone.utc)


class MenuItem(Base):
    __tablename__ = "menu_items"
    id: Mapped[int] = mapped_column(primary_key=True)
    category: Mapped[str] = mapped_column(String(20), index=True)  # classic|cloud|soft|float
    position: Mapped[int] = mapped_column(Integer, default=0)      # order inside category (image = category-position)
    name: Mapped[str] = mapped_column(String(80))
    description: Mapped[str] = mapped_column(String(200), default="")
    calories: Mapped[int | None] = mapped_column(Integer, nullable=True)
    sweetness: Mapped[int | None] = mapped_column(Integer, nullable=True)
    best_seller: Mapped[bool] = mapped_column(Boolean, default=False)
    available: Mapped[bool] = mapped_column(Boolean, default=True)
    price: Mapped[int | None] = mapped_column(Integer, nullable=True)   # rupees; empty = no online payment for this dish
    __table_args__ = (
        CheckConstraint("sweetness IS NULL OR (sweetness BETWEEN 0 AND 5)", name="ck_sweet"),
        CheckConstraint("calories IS NULL OR calories >= 0", name="ck_cal"),
    )


class Outlet(Base):
    """One LATCHA shop. Hours are Indian time as "HH:MM"; closing before opening means "after midnight"."""
    __tablename__ = "outlets"
    id: Mapped[int] = mapped_column(primary_key=True)
    slug: Mapped[str] = mapped_column(String(40), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(80))
    city: Mapped[str] = mapped_column(String(20))            # Bengaluru | Hyderabad
    area: Mapped[str] = mapped_column(String(60), default="")
    address: Mapped[str] = mapped_column(String(200), default="")
    phone: Mapped[str] = mapped_column(String(20), default="")
    maps_url: Mapped[str] = mapped_column(String(300), default="")
    opens: Mapped[str] = mapped_column(String(5), default="11:00")
    closes: Mapped[str] = mapped_column(String(5), default="23:00")
    accepting_orders: Mapped[bool] = mapped_column(Boolean, default=True)   # owner pause switch
    coming_soon: Mapped[bool] = mapped_column(Boolean, default=False)
    position: Mapped[int] = mapped_column(Integer, default=0)


class OutletSoldOut(Base):
    """A row here means: this dish is sold out at this outlet (other outlets are not affected)."""
    __tablename__ = "outlet_sold_out"
    outlet_id: Mapped[int] = mapped_column(ForeignKey("outlets.id", ondelete="CASCADE"), primary_key=True)
    item_id: Mapped[int] = mapped_column(ForeignKey("menu_items.id", ondelete="CASCADE"), primary_key=True)


class Order(Base):
    __tablename__ = "orders"
    id: Mapped[int] = mapped_column(primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, index=True)
    customer_name: Mapped[str] = mapped_column(String(80))
    phone: Mapped[str] = mapped_column(String(20))
    address: Mapped[str] = mapped_column(String(300))
    city: Mapped[str] = mapped_column(String(20))
    notes: Mapped[str] = mapped_column(String(300), default="")
    status: Mapped[str] = mapped_column(String(20), default="new", index=True)
    outlet_id: Mapped[int | None] = mapped_column(ForeignKey("outlets.id"), nullable=True, index=True)
    outlet: Mapped["Outlet | None"] = relationship()
    payment_method: Mapped[str] = mapped_column(String(10), default="cod", server_default="cod")      # cod | upi
    payment_status: Mapped[str] = mapped_column(String(10), default="unpaid", server_default="unpaid")  # unpaid | pending | paid | failed
    amount: Mapped[int | None] = mapped_column(Integer, nullable=True)       # rupees, worked out on the server
    payment_provider: Mapped[str | None] = mapped_column(String(12), nullable=True)   # demo | razorpay
    payment_ref: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)   # provider order id
    payment_id: Mapped[str | None] = mapped_column(String(64), nullable=True)        # provider payment id once paid
    paid_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    fam_discount: Mapped[int] = mapped_column(Integer, default=0, server_default="0")   # rupees off from a Latcha Fam free drink
    customer_id: Mapped[int | None] = mapped_column(ForeignKey("customers.id"), nullable=True, index=True)   # set when placed while signed in
    points_used: Mapped[int] = mapped_column(Integer, default=0, server_default="0")        # Latcha points spent on this order
    points_discount: Mapped[int] = mapped_column(Integer, default=0, server_default="0")    # rupees off from those points
    points_refunded: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0")   # points given back after a cancel
    lines: Mapped[list["OrderLine"]] = relationship(back_populates="order", cascade="all, delete-orphan")

    @property
    def outlet_name(self) -> str | None:
        return self.outlet.name if self.outlet else None


class OrderLine(Base):
    __tablename__ = "order_lines"
    id: Mapped[int] = mapped_column(primary_key=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("orders.id", ondelete="CASCADE"), index=True)
    item_id: Mapped[int] = mapped_column(ForeignKey("menu_items.id"))
    item_name: Mapped[str] = mapped_column(String(80))  # snapshot, survives menu edits
    quantity: Mapped[int] = mapped_column(Integer)
    price: Mapped[int | None] = mapped_column(Integer, nullable=True)  # price per piece when ordered (snapshot)
    order: Mapped[Order] = relationship(back_populates="lines")
    __table_args__ = (CheckConstraint("quantity BETWEEN 1 AND 20", name="ck_qty"),)


class Review(Base):
    __tablename__ = "reviews"
    id: Mapped[int] = mapped_column(primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, index=True)
    author: Mapped[str] = mapped_column(String(60))
    rating: Mapped[int] = mapped_column(Integer)
    text: Mapped[str] = mapped_column(Text)
    approved: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    customer_id: Mapped[int | None] = mapped_column(ForeignKey("customers.id"), nullable=True, index=True)
    __table_args__ = (CheckConstraint("rating BETWEEN 1 AND 5", name="ck_rating"),)


class Customer(Base):
    """A signed-up customer. Points live here; every change is also written to point_tx so the history always adds up."""
    __tablename__ = "customers"
    id: Mapped[int] = mapped_column(primary_key=True)
    phone: Mapped[str] = mapped_column(String(20), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(80))
    pw_hash: Mapped[str] = mapped_column(String(200))
    points: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    visits: Mapped[int] = mapped_column(Integer, default=0, server_default="0")        # days with a visit reward
    last_visit: Mapped[str | None] = mapped_column(String(10), nullable=True)           # "YYYY-MM-DD" Indian date
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class PointTx(Base):
    __tablename__ = "point_tx"
    id: Mapped[int] = mapped_column(primary_key=True)
    customer_id: Mapped[int] = mapped_column(ForeignKey("customers.id", ondelete="CASCADE"), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, index=True)
    delta: Mapped[int] = mapped_column(Integer)
    reason: Mapped[str] = mapped_column(String(12))      # welcome | visit | redeem | refund | adjust
    note: Mapped[str] = mapped_column(String(120), default="")


class Setting(Base):
    """Owner-editable numbers (loyalty rules). One row per value."""
    __tablename__ = "settings"
    key: Mapped[str] = mapped_column(String(40), primary_key=True)
    value: Mapped[str] = mapped_column(String(40))
