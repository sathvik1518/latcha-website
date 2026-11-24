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
    use_reward: bool = False                                  # spend a Latcha Fam free drink on this order
    use_points: bool = False                                  # spend Latcha points (needs a signed-in customer)
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
    fam_discount: int = 0
    points_discount: int = 0
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
    fam_discount: int = 0
    points_used: int = 0
    points_discount: int = 0
    points: int | None = None
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


class FamIn(BaseModel):
    phone: str

    @field_validator("phone")
    @classmethod
    def phone_ok(cls, v: str) -> str:
        return clean_phone(v)


class FamCard(BaseModel):
    sips: int                # drinks delivered so far
    per_reward: int          # sips needed for a free drink
    into_card: int           # sips on the current card (0 to per_reward - 1)
    to_next: int             # sips left to the next free drink
    rewards_available: int   # free drinks ready to use
    rewards_used: int
    delivered_orders: int


class FamMember(FamCard):
    phone: str


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
    fam_discount: int = 0
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


# ---------- owner dashboard ----------
class DashHead(BaseModel):
    orders: int
    orders_change: int | None
    sales: int
    sales_change: int | None
    collected: int
    average_order: int
    delivered: int
    cancelled: int
    unpriced_orders: int
    fam_free_drinks: int
    fam_discount: int


class DashDay(BaseModel):
    date: str
    orders: int
    sales: int


class DashDish(BaseModel):
    name: str
    quantity: int
    sales: int


class DashOutlet(BaseModel):
    name: str
    orders: int
    sales: int


class DashHour(BaseModel):
    hour: int
    orders: int


class DashBucket(BaseModel):
    orders: int
    sales: int


class DashPayments(BaseModel):
    upi_paid: DashBucket
    upi_waiting: DashBucket
    cash_paid: DashBucket
    cash_on_delivery: DashBucket


class DashCustomers(BaseModel):
    total: int
    new: int
    returning: int
    ordered_twice_or_more: int


class DashOut(BaseModel):
    days: int
    from_date: str
    to_date: str
    head: DashHead
    daily: list[DashDay]
    dishes: list[DashDish]
    outlets: list[DashOutlet]
    hours: list[DashHour]
    payments: DashPayments
    customers: DashCustomers
    pending_reviews: int


# ---------- customers (Latcha points) ----------
class RegisterIn(BaseModel):
    name: str = Field(min_length=2, max_length=80)
    phone: str
    password: str = Field(min_length=6, max_length=72)
    otp: str | None = Field(None, max_length=8)

    @field_validator("name")
    @classmethod
    def strip(cls, v: str) -> str:
        return v.strip()

    @field_validator("phone")
    @classmethod
    def phone_ok(cls, v: str) -> str:
        return clean_phone(v)


class CustLoginIn(BaseModel):
    phone: str
    password: str = Field(max_length=72)

    @field_validator("phone")
    @classmethod
    def phone_ok(cls, v: str) -> str:
        return clean_phone(v)


class MeEdit(BaseModel):
    name: str | None = Field(None, min_length=2, max_length=80)
    current_password: str | None = Field(None, max_length=72)
    new_password: str | None = Field(None, min_length=6, max_length=72)
    birthday: str | None = Field(None, pattern=r"^\d{2}-\d{2}$")   # "MM-DD", can be set once

    @field_validator("birthday")
    @classmethod
    def real_day(cls, v: str | None) -> str | None:
        if v is not None:
            try:
                datetime.strptime("2024-" + v, "%Y-%m-%d")   # 2024 is a leap year, so 02-29 is allowed
            except ValueError:
                raise ValueError("That is not a real date.")
        return v


class OtpIn(BaseModel):
    phone: str

    @field_validator("phone")
    @classmethod
    def phone_ok(cls, v: str) -> str:
        return clean_phone(v)


class PointRules(BaseModel):
    enabled: bool
    signup_bonus: int
    visit_points: int
    redeem_points: int
    redeem_rupees: int
    birthday_points: int = 0
    comeback_days: int = 14
    comeback_points: int = 0
    otp: bool = False          # the sign-up form asks for a phone code


class PointRulesPatch(BaseModel):
    enabled: bool | None = None
    signup_bonus: int | None = Field(None, ge=0, le=5000)
    visit_points: int | None = Field(None, ge=0, le=1000)
    redeem_points: int | None = Field(None, ge=1, le=100000)
    redeem_rupees: int | None = Field(None, ge=1, le=5000)
    birthday_points: int | None = Field(None, ge=0, le=5000)
    comeback_days: int | None = Field(None, ge=2, le=365)
    comeback_points: int | None = Field(None, ge=0, le=5000)


class MyOrder(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    created_at: datetime
    status: Status
    outlet_name: str | None = None
    payment_method: str = "cod"
    payment_status: str = "unpaid"
    amount: int | None = None
    fam_discount: int = 0
    points_discount: int = 0
    lines: list[TrackLine]


class MyTx(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    created_at: datetime
    delta: int
    reason: str
    note: str


class MyReview(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    created_at: datetime
    rating: int
    text: str
    approved: bool


class MeOut(BaseModel):
    id: int
    name: str
    phone: str
    points: int
    visits: int
    member_since: datetime
    rules: PointRules
    birthday: str | None = None
    worth_rupees: int          # what the current points are worth right now
    visit_awarded: int = 0     # points given by this very call (a "welcome back" toast)
    fam: FamCard
    orders: list[MyOrder]
    history: list[MyTx]
    reviews: list[MyReview]


class SignedIn(BaseModel):
    access_token: str
    token_type: str = "bearer"
    me: MeOut


class AdminCustomer(BaseModel):
    id: int
    name: str
    phone: str
    points: int
    visits: int
    orders: int
    last_visit: str | None = None
    created_at: datetime


class AdjustIn(BaseModel):
    delta: int = Field(ge=-100000, le=100000)
    note: str = Field("", max_length=100)
