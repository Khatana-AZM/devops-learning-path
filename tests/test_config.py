"""Connection-string assembly: the part that only breaks in production."""

import os

import pytest

from app.config import Settings, _force_psycopg_driver


@pytest.fixture(autouse=True)
def isolated_env(monkeypatch):
    """Strip ambient DB config so these tests assert on inputs, not on
    whatever happens to be exported in the shell or sitting in .env."""
    for key in list(os.environ):
        if key.startswith(("POSTGRES_", "DB_")) or key == "DATABASE_URL":
            monkeypatch.delenv(key, raising=False)


def make(**kwargs) -> Settings:
    """Settings built in isolation.

    `_env_file=""` -- NOT None. None means "fall back to model_config",
    which still reads .env; the empty string is what actually disables it.
    """
    base = {"postgres_password": "localpw"}
    base.update(kwargs)
    return Settings(_env_file="", **base)


# ------------------------------------------------------ driver rewriting --


@pytest.mark.parametrize(
    "dsn",
    [
        "postgres://u:p@host/db",
        "postgresql://u:p@host/db",
        "postgresql+psycopg2://u:p@host/db",
        "POSTGRES://u:p@host/db",
    ],
)
def test_any_postgres_scheme_becomes_psycopg(dsn):
    """Providers hand out postgres:// -- SQLAlchemy needs an explicit driver,
    and psycopg 3 is the only one installed."""
    assert _force_psycopg_driver(dsn).startswith("postgresql+psycopg://")


def test_credentials_and_path_survive_the_rewrite():
    out = _force_psycopg_driver("postgres://u:p@host:5432/db?sslmode=require")
    assert out == "postgresql+psycopg://u:p@host:5432/db?sslmode=require"


def test_non_postgres_url_rejected():
    with pytest.raises(ValueError, match="must be a Postgres URL"):
        _force_psycopg_driver("mysql://u:p@host/db")


def test_garbage_rejected():
    with pytest.raises(ValueError, match="not a URL"):
        _force_psycopg_driver("just-a-string")


# ----------------------------------------------------------- precedence --


def test_database_url_wins_over_the_parts():
    s = make(database_url="postgres://neon:pw@ep-x.neon.tech/main", postgres_host="db")
    assert "neon.tech" in s.sqlalchemy_url
    assert "@db:" not in s.sqlalchemy_url


def test_parts_used_when_no_database_url():
    s = make(postgres_host="db", postgres_db="appdb", postgres_user="app")
    assert s.sqlalchemy_url == "postgresql+psycopg://app:localpw@db:5432/appdb"


# ----------------------------------------------------- password encoding --


def test_special_characters_in_password_are_encoded():
    """A raw @ or / in a password silently corrupts the URL's structure."""
    s = make(postgres_password="p@ss/w:rd?x")
    url = s.sqlalchemy_url
    # Exactly one @ -- the delimiter before the host.
    assert url.count("@") == 1
    assert url.endswith("@db:5432/appdb")
    assert "p%40ss%2Fw%3Ard%3Fx" in url


# ------------------------------------------------------------------ ssl --


def test_sslmode_appended_when_configured():
    s = make(db_sslmode="require")
    assert s.sqlalchemy_url.endswith("?sslmode=require")


def test_sslmode_not_duplicated_when_dsn_already_has_it():
    s = make(
        database_url="postgres://u:p@host/db?sslmode=require", db_sslmode="require"
    )
    assert s.sqlalchemy_url.count("sslmode") == 1


def test_sslmode_uses_ampersand_when_dsn_has_other_params():
    s = make(
        database_url="postgres://u:p@host/db?connect_timeout=10", db_sslmode="require"
    )
    assert s.sqlalchemy_url.endswith("?connect_timeout=10&sslmode=require")


def test_no_sslmode_by_default():
    assert "sslmode" not in make().sqlalchemy_url


# ---------------------------------------------------------- fail closed --


def test_refuses_to_start_without_any_credential():
    with pytest.raises(ValueError, match="No database credentials"):
        Settings(_env_file="")


def test_database_url_alone_is_enough():
    s = Settings(_env_file="", database_url="postgres://u:p@host/db")
    assert s.sqlalchemy_url.startswith("postgresql+psycopg://")
