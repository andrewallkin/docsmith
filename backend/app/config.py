from functools import lru_cache
from urllib.parse import quote_plus

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # ---- Application ----
    app_name: str = "Docsmith"
    environment: str = "development"
    backend_port: int = 8000
    frontend_port: int = 3000

    # ---- Database ----
    postgres_user: str = "docsmith"
    postgres_password: str = "docsmith"
    postgres_db: str = "docsmith"
    postgres_host: str = "localhost"
    postgres_port: int = 5432
    # The VPS control panel writes DATABASE_URL directly; it wins over the
    # individual POSTGRES_* fields when present.
    database_url_override: str = ""

    # ---- Auth ----
    jwt_secret_key: str
    jwt_access_token_expire_minutes: int = 15
    jwt_refresh_token_expire_days: int = 7
    cookie_secure: bool = False
    cors_origins: str = ""
    authorized_users: str = ""

    # ---- Encryption ----
    encryption_key: str

    # ---- Google OAuth ----
    google_client_id: str
    google_client_secret: str
    google_redirect_uri: str
    # Base URL of the dashboard, used for OAuth callback redirects.
    # Dev: http://localhost:3000 ; prod: https://docsmith.andrewallkin.cloud
    frontend_url: str = "http://localhost:3000"

    @property
    def database_url(self) -> str:
        url = self.database_url_override.strip()
        if url:
            if url.startswith("postgres://"):
                url = url.replace("postgres://", "postgresql://", 1)
            return url
        user = quote_plus(self.postgres_user)
        password = quote_plus(self.postgres_password)
        return (
            f"postgresql+psycopg2://{user}:{password}@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def authorized_user_set(self) -> frozenset[str]:
        return frozenset(
            u.strip().lower() for u in self.authorized_users.split(",") if u.strip()
        )


@lru_cache
def get_settings() -> Settings:
    return Settings()
