"""Registration, login, token verification, refresh rotation."""

import base64
import json

import pytest

from tests.conftest import auth_header, register_and_login

ALICE = ("alice@example.com", "alice-password-123")


# ------------------------------------------------------------- registration --


def test_register_returns_the_user_without_any_password_field(client):
    response = client.post(
        "/auth/register", json={"email": ALICE[0], "password": ALICE[1]}
    )
    assert response.status_code == 201
    body = response.json()
    assert body["email"] == ALICE[0]
    assert not any("password" in key for key in body), body


def test_email_is_normalised_to_lowercase(client):
    client.post(
        "/auth/register", json={"email": "MiXeD@Example.COM", "password": ALICE[1]}
    )
    # Login with a different casing must still find the account.
    response = client.post(
        "/auth/login", data={"username": "mixed@example.com", "password": ALICE[1]}
    )
    assert response.status_code == 200


def test_duplicate_email_conflicts(client):
    client.post("/auth/register", json={"email": ALICE[0], "password": ALICE[1]})
    response = client.post(
        "/auth/register", json={"email": ALICE[0], "password": ALICE[1]}
    )
    assert response.status_code == 409


@pytest.mark.parametrize("password", ["short", "x" * 73])
def test_password_length_bounds(client, password):
    """Max length matters: bcrypt silently ignores bytes past 72."""
    response = client.post(
        "/auth/register", json={"email": ALICE[0], "password": password}
    )
    assert response.status_code == 422


def test_invalid_email_rejected(client):
    response = client.post(
        "/auth/register", json={"email": "not-an-email", "password": ALICE[1]}
    )
    assert response.status_code == 422


def test_password_is_stored_hashed(client, db):
    from sqlalchemy import select

    from app.models import User

    client.post("/auth/register", json={"email": ALICE[0], "password": ALICE[1]})
    user = db.scalar(select(User).where(User.email == ALICE[0]))
    assert user.hashed_password != ALICE[1]
    assert user.hashed_password.startswith("$2b$")


# -------------------------------------------------------------------- login --


def test_login_returns_both_tokens(client):
    tokens = register_and_login(client, *ALICE)
    assert tokens["token_type"] == "bearer"
    assert tokens["expires_in"] > 0
    # The refresh token is opaque -- a JWT would have two dots.
    assert "." not in tokens["refresh_token"]


def test_wrong_password_and_unknown_email_are_indistinguishable(client):
    client.post("/auth/register", json={"email": ALICE[0], "password": ALICE[1]})
    wrong = client.post(
        "/auth/login", data={"username": ALICE[0], "password": "nope-nope-nope"}
    )
    unknown = client.post(
        "/auth/login", data={"username": "ghost@example.com", "password": "nope"}
    )
    assert wrong.status_code == unknown.status_code == 401
    # Identical bodies, so login can't be used to enumerate accounts.
    assert wrong.json() == unknown.json()


# ------------------------------------------------------------ token checks --


def test_me_requires_a_token(client):
    assert client.get("/auth/me").status_code == 401


def test_me_returns_the_caller(client):
    tokens = register_and_login(client, *ALICE)
    response = client.get("/auth/me", headers=auth_header(tokens["access_token"]))
    assert response.status_code == 200
    assert response.json()["email"] == ALICE[0]


def test_garbage_token_rejected(client):
    assert client.get("/auth/me", headers=auth_header("nonsense")).status_code == 401


def test_alg_none_token_rejected(client):
    """The classic JWT bypass: an unsigned token claiming alg 'none'."""

    def b64(raw: bytes) -> str:
        return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()

    header = b64(json.dumps({"alg": "none", "typ": "JWT"}).encode())
    claims = b64(
        json.dumps(
            {
                "sub": "1",
                "iss": "devops-learning-api",
                "aud": "devops-learning-api",
            }
        ).encode()
    )
    forged = f"{header}.{claims}."
    assert client.get("/auth/me", headers=auth_header(forged)).status_code == 401


def test_hs256_confusion_rejected(client):
    """Signing with the PUBLIC key as an HMAC secret must not verify.

    The public key isn't secret, so a server that doesn't pin RS256 would let
    anyone forge any identity this way.
    """
    import hashlib
    import hmac

    from app.security import _public_key

    def b64(raw: bytes) -> str:
        return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()

    header = b64(json.dumps({"alg": "HS256", "typ": "JWT"}).encode())
    claims = b64(
        json.dumps(
            {
                "sub": "1",
                "iss": "devops-learning-api",
                "aud": "devops-learning-api",
                "exp": 4102444800,
            }
        ).encode()
    )
    signing_input = f"{header}.{claims}"
    signature = b64(
        hmac.new(
            _public_key().encode(), signing_input.encode(), hashlib.sha256
        ).digest()
    )
    forged = f"{signing_input}.{signature}"
    assert client.get("/auth/me", headers=auth_header(forged)).status_code == 401


def test_expired_token_says_so(client):
    """Expiry is distinguishable so clients know to refresh; forgery isn't."""
    import uuid
    from datetime import UTC, datetime, timedelta

    import jwt

    from app.security import _private_key

    issued = datetime.now(UTC) - timedelta(hours=1)
    token = jwt.encode(
        {
            "sub": "1",
            "iat": issued,
            "nbf": issued,
            "exp": issued + timedelta(minutes=30),
            "iss": "devops-learning-api",
            "aud": "devops-learning-api",
            "jti": uuid.uuid4().hex,
            "typ": "access",
        },
        _private_key(),
        algorithm="RS256",
    )
    response = client.get("/auth/me", headers=auth_header(token))
    assert response.status_code == 401
    assert "expired" in response.headers["www-authenticate"].lower()


def test_token_for_deleted_user_rejected(client, db):
    """A valid signature must not outlive the account."""
    from sqlalchemy import delete

    from app.models import User

    tokens = register_and_login(client, *ALICE)
    db.execute(delete(User).where(User.email == ALICE[0]))
    db.commit()
    response = client.get("/auth/me", headers=auth_header(tokens["access_token"]))
    assert response.status_code == 401


# ------------------------------------------------------------------ refresh --


def test_refresh_rotates_both_tokens(client):
    tokens = register_and_login(client, *ALICE)
    response = client.post(
        "/auth/refresh", json={"refresh_token": tokens["refresh_token"]}
    )
    assert response.status_code == 200
    new = response.json()
    assert new["access_token"] != tokens["access_token"]
    assert new["refresh_token"] != tokens["refresh_token"]
    # The new access token works.
    assert (
        client.get("/auth/me", headers=auth_header(new["access_token"])).status_code
        == 200
    )


def test_reusing_a_rotated_token_kills_the_family(client):
    """Replay is treated as theft: the whole chain is revoked."""
    tokens = register_and_login(client, *ALICE)
    rotated = client.post(
        "/auth/refresh", json={"refresh_token": tokens["refresh_token"]}
    ).json()

    # Replay the original.
    replay = client.post(
        "/auth/refresh", json={"refresh_token": tokens["refresh_token"]}
    )
    assert replay.status_code == 401

    # The legitimate successor is now dead too.
    assert (
        client.post(
            "/auth/refresh", json={"refresh_token": rotated["refresh_token"]}
        ).status_code
        == 401
    )


def test_unknown_refresh_token_rejected(client):
    assert (
        client.post("/auth/refresh", json={"refresh_token": "nope"}).status_code == 401
    )


def test_logout_revokes_only_that_device(client):
    first = register_and_login(client, *ALICE)
    second = client.post(
        "/auth/login", data={"username": ALICE[0], "password": ALICE[1]}
    ).json()

    assert (
        client.post(
            "/auth/logout", json={"refresh_token": first["refresh_token"]}
        ).status_code
        == 204
    )
    assert (
        client.post(
            "/auth/refresh", json={"refresh_token": first["refresh_token"]}
        ).status_code
        == 401
    )
    # The other session survives.
    assert (
        client.post(
            "/auth/refresh", json={"refresh_token": second["refresh_token"]}
        ).status_code
        == 200
    )


def test_logout_of_unknown_token_is_still_204(client):
    """Otherwise logout becomes an oracle for whether a token is live."""
    assert (
        client.post("/auth/logout", json={"refresh_token": "nope"}).status_code == 204
    )


def test_logout_all_revokes_every_session(client):
    first = register_and_login(client, *ALICE)
    second = client.post(
        "/auth/login", data={"username": ALICE[0], "password": ALICE[1]}
    ).json()

    assert (
        client.post(
            "/auth/logout-all", headers=auth_header(first["access_token"])
        ).status_code
        == 204
    )
    for tokens in (first, second):
        assert (
            client.post(
                "/auth/refresh", json={"refresh_token": tokens["refresh_token"]}
            ).status_code
            == 401
        )
