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
