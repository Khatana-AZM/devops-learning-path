"""Shared test fixtures.

Tests run against a real Postgres, not SQLite. The app relies on Postgres
behaviour that SQLite doesn't share -- ON DELETE CASCADE, timezone-aware
timestamps, server-side now(), UNIQUE partial semantics -- so testing on
SQLite would pass while production broke.

Point them at a database with POSTGRES_DB (default: appdb_test).
"""

import os

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from app import security
from app.config import get_settings
from app.database import Base, get_db
from app.main import app

# Tables are listed explicitly so TRUNCATE order is deterministic.
ALL_TABLES = "tasks, refresh_tokens, users"


@pytest.fixture(scope="session", autouse=True)
def signing_keys() -> None:
    """Give the suite its own throwaway RSA keypair.

    Generated in-process, so tests need neither `make keys` nor any secret.
    This also exercises the inline-PEM code path that CI and the deployed
    app use, rather than the local file path.
    """
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private_pem = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode()
    public_pem = (
        key.public_key()
        .public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        .decode()
    )

    settings = get_settings()
    settings.jwt_private_key = private_pem
    settings.jwt_public_key = public_pem

    # The loaders memoise, so clear anything cached from a key file.
    security._private_key.cache_clear()
    security._public_key.cache_clear()


@pytest.fixture(scope="session")
def engine():
    settings = get_settings()
    url = settings.database_url

    if "test" not in (os.getenv("POSTGRES_DB") or settings.postgres_db):
        pytest.exit(
            f"Refusing to run: POSTGRES_DB is '{settings.postgres_db}', which "
            "doesn't look like a test database. The suite TRUNCATEs tables.",
            returncode=1,
        )

    eng = create_engine(url, pool_pre_ping=True)
    Base.metadata.create_all(eng)
    yield eng
    eng.dispose()


@pytest.fixture(autouse=True)
def clean_db(engine):
    """Empty every table between tests, so order never matters."""
    yield
    with engine.begin() as conn:
        conn.execute(text(f"TRUNCATE {ALL_TABLES} RESTART IDENTITY CASCADE"))


@pytest.fixture
def db(engine):
    with Session(engine) as session:
        yield session


@pytest.fixture
def client(engine):
    """TestClient wired to the test database."""

    def override_get_db():
        with Session(engine) as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


# ----------------------------------------------------------------- helpers --


def register_and_login(client: TestClient, email: str, password: str) -> dict:
    """Create a user and return their token pair."""
    response = client.post(
        "/auth/register", json={"email": email, "password": password}
    )
    assert response.status_code == 201, response.text
    response = client.post(
        "/auth/login", data={"username": email, "password": password}
    )
    assert response.status_code == 200, response.text
    return response.json()


def auth_header(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def alice(client) -> dict:
    tokens = register_and_login(client, "alice@example.com", "alice-password-123")
    return {
        "tokens": tokens,
        "headers": auth_header(tokens["access_token"]),
    }


@pytest.fixture
def bob(client) -> dict:
    tokens = register_and_login(client, "bob@example.com", "bob-password-123")
    return {
        "tokens": tokens,
        "headers": auth_header(tokens["access_token"]),
    }
