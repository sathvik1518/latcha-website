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
    __table_args__ = (
        CheckConstraint("sweetness IS NULL OR (sweetness BETWEEN 0 AND 5)", name="ck_sweet"),
        CheckConstraint("calories IS NULL OR calories >= 0", name="ck_cal"),
    )


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
    lines: Mapped[list["OrderLine"]] = relationship(back_populates="order", cascade="all, delete-orphan")


class OrderLine(Base):
    __tablename__ = "order_lines"
    id: Mapped[int] = mapped_column(primary_key=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("orders.id", ondelete="CASCADE"), index=True)
    item_id: Mapped[int] = mapped_column(ForeignKey("menu_items.id"))
    item_name: Mapped[str] = mapped_column(String(80))  # snapshot, survives menu edits
    quantity: Mapped[int] = mapped_column(Integer)
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
    __table_args__ = (CheckConstraint("rating BETWEEN 1 AND 5", name="ck_rating"),)
