from functools import lru_cache
from pathlib import Path
from urllib.parse import quote_plus

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


def _force_psycopg_driver(dsn: str) -> str:
    """Rewrite a provider DSN to the driver we actually ship.

    Hosted Postgres hands out `postgres://` or `postgresql://`. SQLAlchemy
    needs an explicit driver, and the only one installed here is psycopg 3 --
    so `postgresql+psycopg2://` from an old copy-paste would fail at import
    with a confusing error rather than a clear one.
    """
    scheme, separator, rest = dsn.partition("://")
    if not separator:
        raise ValueError(f"DATABASE_URL is not a URL: {dsn[:30]!r}...")

    base = scheme.split("+", 1)[0].lower()
    if base not in {"postgres", "postgresql"}:
        raise ValueError(f"DATABASE_URL must be a Postgres URL, got {base!r}")

    return f"postgresql+psycopg://{rest}"


class Settings(BaseSettings):
    """App configuration, read from environment variables (or a local .env)."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_name: str = "devops-learning-api"
    debug: bool = False

    # Postgres connection pieces -- compose passes these in.
    #
    # postgres_password has NO default on purpose. A default here fails open:
    # forget the env var in production and the app boots happily against a
    # password an attacker guesses first. With no default, pydantic refuses
    # to start and you find out immediately.
    postgres_user: str = "app"
    postgres_password: str | None = None
    postgres_db: str = "appdb"
    postgres_host: str = "db"
    postgres_port: int = 5432

    # A complete DSN, as hosted Postgres providers issue it. When set it
    # replaces the five fields above entirely.
    database_url: str | None = None

    # "require" for any hosted database. Left unset locally, where the
    # connection never leaves the Docker network.
    db_sslmode: str | None = None

    # --- JWT / RS256 ------------------------------------------------------
    # RS256 is asymmetric: the private key signs, the public key verifies.
    # Only this service needs the private key; anything that merely validates
    # tokens (a gateway, another service) needs the public key alone.
    jwt_algorithm: str = "RS256"

    # Two ways to supply the keypair, checked in this order:
    #
    #   1. JWT_PRIVATE_KEY / JWT_PUBLIC_KEY -- the PEM itself, either raw or
    #      base64-encoded. This is how hosted platforms and CI pass secrets,
    #      where there's no filesystem to mount keys onto.
    #   2. the *_path files below -- local dev, written by `make keys`.
    #
    # Inline wins when both are set, so deploying never silently picks up a
    # stale key file baked into an image.
    jwt_private_key: str | None = None
    jwt_public_key: str | None = None
    jwt_private_key_path: Path = Path("keys/jwt-private.pem")
    jwt_public_key_path: Path = Path("keys/jwt-public.pem")
    jwt_issuer: str = "devops-learning-api"
    jwt_audience: str = "devops-learning-api"
    access_token_expire_minutes: int = 30

    # Opaque refresh tokens: random strings stored (hashed) in the DB, so
    # unlike the access token they can actually be revoked.
    refresh_token_expire_days: int = 30
    refresh_token_bytes: int = 32  # 256 bits of entropy

    @model_validator(mode="after")
    def _require_a_database_credential(self) -> "Settings":
        """Fail closed: neither credential route has a usable default.

        A default password lets the app boot in production against a
        password an attacker guesses first. Refusing to start is louder.
        """
        if not self.database_url and not self.postgres_password:
            raise ValueError(
                "No database credentials. Set DATABASE_URL, or set "
                "POSTGRES_PASSWORD to assemble the connection from parts."
            )
        return self

    @property
    def sqlalchemy_url(self) -> str:
        """The DSN SQLAlchemy actually connects with.

        DATABASE_URL wins when present -- hosted Postgres (Neon, Render,
        Northflank) hands you one string rather than five separate pieces.
        Otherwise the URL is assembled from the parts, which is how compose
        and CI supply it.
        """
        if self.database_url:
            url = _force_psycopg_driver(self.database_url)
        else:
            # The password is percent-encoded: generated passwords routinely
            # contain @ : / ? # and any of those would silently corrupt the
            # URL's structure if pasted in raw.
            url = (
                f"postgresql+psycopg://{quote_plus(self.postgres_user)}"
                f":{quote_plus(self.postgres_password or '')}"
                f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
            )

        # Hosted Postgres refuses plaintext connections. Only appended when
        # the DSN doesn't already carry it -- provider strings usually do.
        if self.db_sslmode and "sslmode=" not in url:
            url += ("&" if "?" in url else "?") + f"sslmode={self.db_sslmode}"

        return url


@lru_cache
def get_settings() -> Settings:
    return Settings()
