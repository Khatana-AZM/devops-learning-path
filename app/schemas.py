"""Pydantic request/response schemas."""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field

from app.security import BCRYPT_MAX_BYTES


class UserCreate(BaseModel):
    email: EmailStr
    # max_length is a real security control, not cosmetic: bcrypt ignores
    # everything past 72 bytes, so without a cap two different long passwords
    # would hash identically.
    password: str = Field(min_length=8, max_length=BCRYPT_MAX_BYTES)


class UserOut(BaseModel):
    """What we send back. Deliberately has no password field of any kind."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    email: EmailStr
    is_active: bool
    created_at: datetime


class Token(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int  # seconds until the ACCESS token expires
    refresh_token: str


class RefreshRequest(BaseModel):
    refresh_token: str
