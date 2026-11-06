"""Password hashing and RS256 JWT issuing/verification.

RS256 = RSASSA-PKCS1-v1_5 with SHA-256. Asymmetric, so:
  - the PRIVATE key signs tokens (only this service should hold it)
  - the PUBLIC key verifies them (safe to hand to gateways/other services)
"""

import hashlib
import secrets
import uuid
from datetime import UTC, datetime, timedelta
from functools import lru_cache

import bcrypt
import jwt

from app.config import get_settings

settings = get_settings()

# bcrypt only looks at the first 72 BYTES of input and silently ignores the
# rest. Schemas cap password length so two different long passwords can never
# collide into the same hash.
BCRYPT_MAX_BYTES = 72


# --------------------------------------------------------------- passwords --


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode(), password_hash.encode())
    except ValueError:
        # Malformed hash in the DB -- treat as a failed login, never a 500.
        return False


# A hash of a throwaway value. Compared against when the email doesn't exist,
# so a login attempt costs the same whether or not the account is real --
# otherwise response timing leaks which emails are registered.
_DUMMY_HASH = hash_password(uuid.uuid4().hex)


def fake_verify() -> None:
    """Burn the same CPU as a real password check."""
    bcrypt.checkpw(b"invalid", _DUMMY_HASH.encode())


# -------------------------------------------------------------------- keys --


@lru_cache
def _private_key() -> str:
    path = settings.jwt_private_key_path
    if not path.exists():
        raise RuntimeError(
            f"JWT private key not found at {path}. Run `make keys` to generate it."
        )
    return path.read_text()


@lru_cache
def _public_key() -> str:
    path = settings.jwt_public_key_path
    if not path.exists():
        raise RuntimeError(
            f"JWT public key not found at {path}. Run `make keys` to generate it."
        )
    return path.read_text()


# ------------------------------------------------------------------ tokens --


def create_access_token(subject: str | int) -> tuple[str, int]:
    """Sign an access token for `subject` (a user id).

    Returns (token, seconds_until_expiry).
    """
    now = datetime.now(UTC)
    expires_delta = timedelta(minutes=settings.access_token_expire_minutes)
    expire = now + expires_delta

    claims = {
        "sub": str(subject),   # RFC 7519: `sub` MUST be a string
        "iat": now,
        "nbf": now,
        "exp": expire,
        "iss": settings.jwt_issuer,
        "aud": settings.jwt_audience,
        "jti": uuid.uuid4().hex,  # unique id, so tokens can be revoked later
        "typ": "access",
    }
    token = jwt.encode(claims, _private_key(), algorithm=settings.jwt_algorithm)
    return token, int(expires_delta.total_seconds())


def decode_access_token(token: str) -> dict:
    """Verify a token and return its claims.

    Raises jwt.InvalidTokenError (or a subclass) on anything suspicious.
    """
    claims = jwt.decode(
        token,
        _public_key(),
        # Pinning the algorithm list is the single most important line here.
        # Without it an attacker can hand us a token signed with "none", or
        # one signed with HS256 using our PUBLIC key as the shared secret.
        algorithms=[settings.jwt_algorithm],
        issuer=settings.jwt_issuer,
        audience=settings.jwt_audience,
        options={
            "require": ["exp", "iat", "nbf", "sub", "iss", "aud"],
            "verify_exp": True,
            "verify_iat": True,
            "verify_nbf": True,
            "verify_iss": True,
            "verify_aud": True,
            "verify_signature": True,
        },
    )
    if claims.get("typ") != "access":
        raise jwt.InvalidTokenError("wrong token type")
    return claims


# ---------------------------------------------------------- refresh tokens --


def new_refresh_token() -> tuple[str, str]:
    """Mint an opaque refresh token.

    Returns (plaintext, sha256_hex). The plaintext is shown to the client
    exactly once; only the hash is stored, so a dump of the refresh_tokens
    table doesn't let anyone log in as somebody else.
    """
    plaintext = secrets.token_urlsafe(settings.refresh_token_bytes)
    return plaintext, hash_refresh_token(plaintext)


def hash_refresh_token(token: str) -> str:
    """SHA-256, deliberately NOT bcrypt.

    bcrypt is slow on purpose, which is right for low-entropy human passwords
    that must resist offline brute force. A refresh token is 256 random bits --
    unguessable regardless of hash speed -- and we need to look it up by hash
    on every refresh, which a salted hash makes impossible without scanning
    the whole table.
    """
    return hashlib.sha256(token.encode()).hexdigest()
