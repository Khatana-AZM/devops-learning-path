"""Reusable FastAPI dependencies."""

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import User
from app.security import decode_access_token

# tokenUrl is what Swagger's "Authorize" button posts to. It does not create
# the route -- app/routers/auth.py does.
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="auth/login")

_CREDENTIALS_ERROR = HTTPException(
    status_code=status.HTTP_401_UNAUTHORIZED,
    detail="Could not validate credentials",
    # RFC 6750: tells the client how to authenticate. Swagger relies on it.
    headers={"WWW-Authenticate": "Bearer"},
)


def get_current_user(
    token: str = Depends(oauth2_scheme),
    db: Session = Depends(get_db),
) -> User:
    """Resolve the bearer token to a User, or 401."""
    try:
        claims = decode_access_token(token)
    except jwt.ExpiredSignatureError:
        # Expiry is the one case worth distinguishing: the client needs to
        # know to hit /auth/refresh rather than give up. It leaks nothing --
        # `exp` is readable without any key, since JWTs are signed, not
        # encrypted. RFC 6750 defines this exact header.
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Access token expired",
            headers={
                "WWW-Authenticate": (
                    'Bearer error="invalid_token", '
                    'error_description="The access token expired"'
                )
            },
        ) from None
    except jwt.InvalidTokenError:
        # One generic error for forged/malformed/wrong-audience.
        # Saying which would tell an attacker how close they are.
        raise _CREDENTIALS_ERROR from None

    try:
        user_id = int(claims["sub"])
    except (KeyError, ValueError):
        raise _CREDENTIALS_ERROR from None

    user = db.scalar(select(User).where(User.id == user_id))
    if user is None:
        # Valid signature but the account is gone -- deleted users must not
        # keep access until their token expires.
        raise _CREDENTIALS_ERROR
    return user


def get_current_active_user(
    user: User = Depends(get_current_user),
) -> User:
    """get_current_user, plus a check that the account isn't disabled."""
    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Inactive user"
        )
    return user
