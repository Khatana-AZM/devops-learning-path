from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """App configuration, read from environment variables (or a local .env)."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_name: str = "devops-learning-api"
    debug: bool = False

    # Postgres connection pieces -- compose passes these in.
    postgres_user: str = "app"
    postgres_password: str = "app"
    postgres_db: str = "appdb"
    postgres_host: str = "db"
    postgres_port: int = 5432

    # --- JWT / RS256 ------------------------------------------------------
    # RS256 is asymmetric: the private key signs, the public key verifies.
    # Only this service needs the private key; anything that merely validates
    # tokens (a gateway, another service) needs the public key alone.
    jwt_algorithm: str = "RS256"
    jwt_private_key_path: Path = Path("keys/jwt-private.pem")
    jwt_public_key_path: Path = Path("keys/jwt-public.pem")
    jwt_issuer: str = "devops-learning-api"
    jwt_audience: str = "devops-learning-api"
    access_token_expire_minutes: int = 30

    # Opaque refresh tokens: random strings stored (hashed) in the DB, so
    # unlike the access token they can actually be revoked.
    refresh_token_expire_days: int = 30
    refresh_token_bytes: int = 32  # 256 bits of entropy

    @property
    def database_url(self) -> str:
        return (
            f"postgresql+psycopg://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )


@lru_cache
def get_settings() -> Settings:
    return Settings()
