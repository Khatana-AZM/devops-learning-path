"""Pydantic request/response schemas."""

from datetime import date, datetime

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
    token_type: str = "bearer"  # noqa: S105 -- a token *type*, not a secret
    expires_in: int  # seconds until the ACCESS token expires
    refresh_token: str


class RefreshRequest(BaseModel):
    refresh_token: str


# ------------------------------------------------------------------ tasks --


class TaskBase(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=2000)
    due_date: date | None = None


class TaskCreate(TaskBase):
    is_done: bool = False


class TaskUpdate(BaseModel):
    """Every field optional -- this is a PATCH.

    `None` is a meaningful value here (it clears the field), so the router
    uses `exclude_unset=True` to tell "set this to null" apart from
    "don't touch this".
    """

    title: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=2000)
    due_date: date | None = None
    is_done: bool | None = None


class TaskOut(TaskBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
    is_done: bool
    created_at: datetime
    updated_at: datetime
    # user_id is deliberately NOT exposed. The caller can only ever see their
    # own tasks, so it carries no information and only invites clients to
    # start passing it around.
