"""Refresh-token lifecycle: issue, rotate, revoke, detect reuse."""

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import RefreshToken, User
from app.security import hash_refresh_token, new_refresh_token

settings = get_settings()


class RefreshError(Exception):
    """Refresh token was missing, expired, already used, or revoked."""


def issue(db: Session, user: User, family_id: str | None = None) -> str:
    """Create a refresh token for `user` and return the plaintext.

    A new login starts a new family; a rotation stays in the existing one.
    """
    plaintext, token_hash = new_refresh_token()
    db.add(
        RefreshToken(
            token_hash=token_hash,
            user_id=user.id,
            family_id=family_id or uuid.uuid4().hex,
            expires_at=datetime.now(UTC)
            + timedelta(days=settings.refresh_token_expire_days),
        )
    )
    return plaintext


def rotate(db: Session, presented: str) -> tuple[User, str]:
    """Exchange a refresh token for a fresh one.

    Returns (user, new_plaintext_refresh_token). Raises RefreshError on
    anything that isn't a clean, single use of a live token.
    """
    now = datetime.now(UTC)
    row = db.scalar(
        select(RefreshToken).where(
            RefreshToken.token_hash == hash_refresh_token(presented)
        )
    )

    if row is None:
        # Unknown token. Nothing to revoke -- we can't tell which family it
        # would have belonged to, and 256 random bits aren't brute-forced.
        raise RefreshError("unknown refresh token")

    if row.revoked_at is not None:
        # REUSE DETECTED. This token was already rotated away, so either an
        # attacker is replaying a stolen copy or the legitimate client is
        # replaying an old one. We can't tell which, and the safe assumption
        # is theft: kill the entire family so both parties must log in again.
        _revoke_family(db, row.family_id, "reuse_detected")
        db.commit()
        raise RefreshError("refresh token reuse detected")

    if row.expires_at <= now:
        raise RefreshError("refresh token expired")

    # Claim the token atomically. Two concurrent refreshes with the same
    # token race here; the conditional UPDATE means exactly one wins, and
    # the loser falls through to reuse detection on its next attempt.
    claimed = db.execute(
        update(RefreshToken)
        .where(RefreshToken.id == row.id, RefreshToken.revoked_at.is_(None))
        .values(revoked_at=now, revoked_reason="rotated")
        .returning(RefreshToken.id)
    ).scalar_one_or_none()

    if claimed is None:
        _revoke_family(db, row.family_id, "reuse_detected")
        db.commit()
        raise RefreshError("refresh token reuse detected")

    user = db.get(User, row.user_id)
    if user is None or not user.is_active:
        _revoke_family(db, row.family_id, "user_inactive")
        db.commit()
        raise RefreshError("user inactive")

    new_plaintext = issue(db, user, family_id=row.family_id)
    db.commit()
    return user, new_plaintext


def revoke_family_of(db: Session, presented: str, reason: str = "logout") -> bool:
    """Revoke the whole chain a token belongs to. Used by logout."""
    row = db.scalar(
        select(RefreshToken).where(
            RefreshToken.token_hash == hash_refresh_token(presented)
        )
    )
    if row is None:
        return False
    _revoke_family(db, row.family_id, reason)
    db.commit()
    return True


def revoke_all_for_user(db: Session, user: User, reason: str = "logout_all") -> int:
    """Revoke every live token for a user -- 'sign out everywhere'."""
    result = db.execute(
        update(RefreshToken)
        .where(
            RefreshToken.user_id == user.id,
            RefreshToken.revoked_at.is_(None),
        )
        .values(revoked_at=datetime.now(UTC), revoked_reason=reason)
    )
    db.commit()
    return result.rowcount


def _revoke_family(db: Session, family_id: str, reason: str) -> None:
    db.execute(
        update(RefreshToken)
        .where(
            RefreshToken.family_id == family_id,
            RefreshToken.revoked_at.is_(None),
        )
        .values(revoked_at=datetime.now(UTC), revoked_reason=reason)
    )
