import re
from datetime import datetime
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

Category = Literal["classic", "cloud", "soft", "float"]
Status = Literal["new", "confirmed", "delivered", "cancelled"]


def clean_phone(v: str) -> str:
    """One rule for every phone number: the same one orders use."""
    d = re.sub(r"[\s\-()]", "", v)
    if d.startswith("+91"):
        d = d[3:]
    elif d.startswith("91") and len(d) == 12:
        d = d[2:]
    if not re.fullmatch(r"[6-9]\d{9}", d):
        raise ValueError("Enter a valid 10 digit Indian mobile number")
    return d


class MenuItemOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    category: Category
    position: int
    name: str
    description: str
    calories: int | None
    sweetness: int | None
    best_seller: bool
    available: bool
    price: int | None = None


class MenuItemIn(BaseModel):
    category: Category
    position: int = Field(0, ge=0)
    name: str = Field(min_length=1, max_length=80)
    description: str = Field("", max_length=200)
    calories: int | None = Field(None, ge=0, le=3000)
    sweetness: int | None = Field(None, ge=0, le=5)
    best_seller: bool = False
    available: bool = True
    price: int | None = Field(None, ge=1, le=20000)


class MenuItemPatch(BaseModel):
    position: int | None = Field(None, ge=0)
    name: str | None = Field(None, min_length=1, max_length=80)
    description: str | None = Field(None, max_length=200)
    calories: int | None = Field(None, ge=0, le=3000)
    sweetness: int | None = Field(None, ge=0, le=5)
    best_seller: bool | None = None
    available: bool | None = None
    price: int | None = Field(None, ge=1, le=20000)


class LineIn(BaseModel):
    item_id: int
    quantity: int = Field(ge=1, le=20)


class OrderIn(BaseModel):
    customer_name: str = Field(min_length=2, max_length=80)
    phone: str
    address: str = Field(min_length=5, max_length=300)
    city: Literal["Bengaluru", "Hyderabad"] | None = None   # optional when an outlet is chosen (the outlet knows its city)
    outlet: str | None = Field(None, max_length=40)           # outlet slug, e.g. "jp-nagar"
    notes: str = Field("", max_length=300)
    pay: Literal["cod", "upi"] = "cod"                        # cod = pay at delivery (the old way)
    lines: list[LineIn] = Field(min_length=1, max_length=20)

    @model_validator(mode="after")
    def city_or_outlet(self):
        if not self.city and not self.outlet:
            raise ValueError("Choose an outlet or a city")
        return self

    @field_validator("customer_name", "address", "notes")
    @classmethod
    def strip(cls, v: str) -> str:
        return v.strip()

    @field_validator("phone")
    @classmethod
    def phone_ok(cls, v: str) -> str:
        return clean_phone(v)


class LineOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    item_id: int
    item_name: str
    quantity: int


class OrderOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    created_at: datetime
    customer_name: str
    phone: str
    address: str
    city: str
    notes: str
    status: Status
    outlet_id: int | None = None
    outlet_name: str | None = None
    payment_method: str = "cod"
    payment_status: str = "unpaid"
    amount: int | None = None
    lines: list[LineOut]


class PaymentStart(BaseModel):
    provider: Literal["demo", "razorpay"]
    amount: int
    provider_order_id: str
    key_id: str | None = None


class OrderReceipt(BaseModel):
    id: int
    status: Status
    message: str
    outlet: str | None = None
    amount: int | None = None
    payment: PaymentStart | None = None


class PayIn(BaseModel):
    phone: str
    razorpay_payment_id: str | None = Field(None, max_length=64)
    razorpay_order_id: str | None = Field(None, max_length=64)
    razorpay_signature: str | None = Field(None, max_length=128)

    @field_validator("phone")
    @classmethod
    def phone_ok(cls, v: str) -> str:
        return clean_phone(v)


class StatsOut(BaseModel):
    """Numbers for the owner's live screen. All counts are real orders; money is rupees actually paid."""
    new_orders: int          # waiting for the owner to confirm
    being_made: int          # confirmed, not yet delivered
    awaiting_payment: int    # UPI orders not paid yet (not cancelled)
    today_orders: int        # placed today (Indian time), not cancelled
    today_paid: int          # rupees paid today
    latest_order_id: int


class PayConfig(BaseModel):
    mode: Literal["demo", "razorpay", "off"]


class PayResult(BaseModel):
    id: int
    payment_status: str
    amount: int | None = None


class TrackIn(BaseModel):
    order_id: int = Field(ge=1, le=2_000_000_000)
    phone: str

    @field_validator("phone")
    @classmethod
    def phone_ok(cls, v: str) -> str:
        return clean_phone(v)


class TrackLine(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    item_name: str
    quantity: int


class TrackOut(BaseModel):
    """What a customer may see: no phone, no address."""
    model_config = ConfigDict(from_attributes=True)
    id: int
    created_at: datetime
    status: Status
    payment_method: str = "cod"
    payment_status: str = "unpaid"
    amount: int | None = None
    lines: list[TrackLine]


class StatusIn(BaseModel):
    status: Status


class ReviewIn(BaseModel):
    author: str = Field(min_length=2, max_length=60)
    rating: int = Field(ge=1, le=5)
    text: str = Field(min_length=10, max_length=600)
    website: str = ""  # honeypot: real people never fill this

    @field_validator("author", "text")
    @classmethod
    def strip(cls, v: str) -> str:
        return v.strip()


class ReviewOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    created_at: datetime
    author: str
    rating: int
    text: str


class ReviewAdminOut(ReviewOut):
    approved: bool


class LoginIn(BaseModel):
    username: str
    password: str


class TokenOut(BaseModel):
    access_token: str
    token_type: str = "bearer"


HHMM = r"^([01]\d|2[0-3]):[0-5]\d$"


class OutletOut(BaseModel):
    """What the website sees. `status` is a ready sentence; `can_order` says if the order form should accept it now."""
    slug: str
    name: str
    city: str
    area: str
    address: str
    phone: str
    maps_url: str
    opens: str
    closes: str
    coming_soon: bool
    open_now: bool
    can_order: bool
    status: str
    sold_out: list[int]


class OutletAdminOut(OutletOut):
    id: int
    accepting_orders: bool


class OutletPatch(BaseModel):
    accepting_orders: bool | None = None
    coming_soon: bool | None = None
    opens: str | None = Field(None, pattern=HHMM)
    closes: str | None = Field(None, pattern=HHMM)
    phone: str | None = Field(None, max_length=20)
    maps_url: str | None = Field(None, max_length=300)
    address: str | None = Field(None, max_length=200)

    @field_validator("phone")
    @classmethod
    def phone_ok(cls, v):
        return v if v in (None, "") else clean_phone(v)


class SoldOutIn(BaseModel):
    sold_out: bool
