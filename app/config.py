from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")
    database_url: str = "sqlite:///./dev.db"
    cors_origins: str = "http://localhost:8000"
    admin_username: str = "owner"
    admin_password_hash: str = ""
    jwt_secret: str = ""
    token_minutes: int = 120
    enforce_outlet_hours: bool = True   # refuse delivery requests while an outlet is closed
    max_orders_per_ip_hour: int = 20    # orders per hour from one network (shared office or college Wi-Fi counts as one)
    fam_sips_per_reward: int = 8        # Latcha Fam: this many sips (delivered drinks) = 1 free drink
    # UPI payments: "off" (default), "demo" (fake pay screen, no money; must be switched on deliberately),
    # "razorpay" (real checkout, test or live keys)
    payments_mode: str = "off"
    razorpay_key_id: str = ""
    razorpay_key_secret: str = ""
    razorpay_webhook_secret: str = ""
    # Instant order alerts on Telegram (optional): make a bot with @BotFather, then fill both in
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""
    # Phone check for Latcha Club sign-up: "off" (default) or "demo" (the code is shown on screen, no SMS is sent)
    otp_mode: str = "off"

    @property
    def pay_mode(self) -> str:
        """The mode actually used. "razorpay" without keys becomes "off", never a silent fake."""
        m = (self.payments_mode or "off").strip().lower()
        if m == "razorpay":
            return "razorpay" if (self.razorpay_key_id and self.razorpay_key_secret) else "off"
        return m if m in ("demo", "off") else "off"

    @property
    def db_url(self) -> str:
        u = self.database_url
        # SQLAlchemy needs the driver name; Neon gives plain postgresql://
        if u.startswith("postgres://"):
            u = "postgresql://" + u[len("postgres://"):]
        if u.startswith("postgresql://"):
            u = "postgresql+psycopg://" + u[len("postgresql://"):]
        return u

    @property
    def origins(self) -> list[str]:
        return [o.strip().rstrip("/") for o in self.cors_origins.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
