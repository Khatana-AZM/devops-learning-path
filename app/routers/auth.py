from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.database import get_db
from app.dependencies import get_current_active_user
from app.models import User
from app.schemas import RefreshRequest, Token, UserCreate, UserOut
from app import tokens
from app.security import (
    create_access_token,
    fake_verify,
    hash_password,
    verify_password,
)

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/register", response_model=UserOut, status_code=status.HTTP_201_CREATED)
def register(payload: UserCreate, db: Session = Depends(get_db)) -> User:
    user = User(
        email=payload.email.lower(),
        hashed_password=hash_password(payload.password),
    )
    db.add(user)
    try:
        db.commit()
    except IntegrityError:
        # Let the UNIQUE constraint decide, rather than checking first --
        # a SELECT-then-INSERT races two concurrent signups.
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Email already registered",
        ) from None
    db.refresh(user)
    return user


@router.post("/login", response_model=Token)
def login(
    form: OAuth2PasswordRequestForm = Depends(),
    db: Session = Depends(get_db),
) -> Token:
    """Exchange email + password for an access token.

    The form field is named `username` because that's what the OAuth2 password
    flow specifies; we put the email in it.
    """
    user = db.scalar(select(User).where(User.email == form.username.lower()))

    if user is None:
        # Spend the same time as a real bcrypt check so response latency
        # doesn't reveal whether the email exists.
        fake_verify()
        raise _bad_credentials()

    if not verify_password(form.password, user.hashed_password):
        raise _bad_credentials()

    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Inactive user"
        )

    # New login = new token family.
    refresh = tokens.issue(db, user)
    db.commit()
    access, expires_in = create_access_token(user.id)
    return Token(
        access_token=access, expires_in=expires_in, refresh_token=refresh
    )


@router.post("/refresh", response_model=Token)
def refresh(payload: RefreshRequest, db: Session = Depends(get_db)) -> Token:
    """Exchange a refresh token for a new access token AND a new refresh token.

    The old refresh token is invalidated on use (rotation). Presenting it a
    second time is treated as theft and revokes the whole family.
    """
    try:
        user, new_refresh = tokens.rotate(db, payload.refresh_token)
    except tokens.RefreshError:
        # Deliberately uniform: unknown / expired / revoked / reused all look
        # identical from outside.
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid refresh token",
            headers={"WWW-Authenticate": "Bearer"},
        ) from None

    access, expires_in = create_access_token(user.id)
    return Token(
        access_token=access, expires_in=expires_in, refresh_token=new_refresh
    )


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(payload: RefreshRequest, db: Session = Depends(get_db)) -> None:
    """Revoke the token family this refresh token belongs to (one device).

    Always 204, even for an unknown token -- otherwise logout doubles as an
    oracle for checking whether a token is live. Note the already-issued
    access token stays valid until it expires; that's the tradeoff stateless
    access tokens make.
    """
    tokens.revoke_family_of(db, payload.refresh_token)


@router.post("/logout-all", status_code=status.HTTP_204_NO_CONTENT)
def logout_all(
    user: User = Depends(get_current_active_user), db: Session = Depends(get_db)
) -> None:
    """Revoke every refresh token for the current user -- sign out everywhere."""
    tokens.revoke_all_for_user(db, user)


@router.get("/me", response_model=UserOut)
def read_me(user: User = Depends(get_current_active_user)) -> User:
    """Example protected route -- add `Depends(get_current_active_user)` to any
    endpoint to require a valid token."""
    return user


def _bad_credentials() -> HTTPException:
    # Identical message for "no such user" and "wrong password", so the
    # endpoint can't be used to enumerate registered emails.
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Incorrect email or password",
        headers={"WWW-Authenticate": "Bearer"},
    )
