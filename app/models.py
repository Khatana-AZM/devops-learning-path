"""SQLAlchemy ORM models.

Every model must inherit from Base and be importable from this module, or
Alembic's autogenerate won't see it. After adding one:

    make migration m="add <thing> table"   # writes migrations/versions/*.py
    make migrate                           # applies it
"""

from datetime import date, datetime

from sqlalchemy import Date, DateTime, ForeignKey, Index, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(320), unique=True, index=True)

    # The bcrypt hash -- never the password itself. Named so that an
    # accidental `print(user.__dict__)` is obviously not a plaintext leak.
    hashed_password: Mapped[str] = mapped_column(String(60))

    is_active: Mapped[bool] = mapped_column(default=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    refresh_tokens: Mapped[list["RefreshToken"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )
    tasks: Mapped[list["Task"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )


class Task(Base):
    """A to-do item. Belongs to exactly one user."""

    __tablename__ = "tasks"

    id: Mapped[int] = mapped_column(primary_key=True)

    # The owner. ondelete=CASCADE means deleting a user takes their tasks
    # with it rather than leaving orphaned rows pointing at nothing.
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )

    title: Mapped[str] = mapped_column(String(200))
    description: Mapped[str | None] = mapped_column(String(2000), default=None)
    is_done: Mapped[bool] = mapped_column(default=False)
    due_date: Mapped[date | None] = mapped_column(Date, default=None)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    user: Mapped["User"] = relationship(back_populates="tasks")

    __table_args__ = (
        # Every single query filters by user_id and usually by is_done,
        # so this composite index covers the common path.
        Index("ix_tasks_user_done", "user_id", "is_done"),
    )


class RefreshToken(Base):
    """An opaque refresh token.

    Rows are never deleted on rotation, only marked revoked -- replaying an
    already-used token is the signal that it was stolen, and that only works
    if the used rows stick around.
    """

    __tablename__ = "refresh_tokens"

    id: Mapped[int] = mapped_column(primary_key=True)

    # SHA-256 hex of the token. The plaintext is never stored.
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)

    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )

    # All tokens descended from one login share a family id. Detecting reuse
    # anywhere in the chain lets us revoke the whole lineage at once.
    family_id: Mapped[str] = mapped_column(String(32), index=True)

    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    # NULL = still usable. Set on rotation, logout, or reuse detection.
    revoked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), default=None
    )
    revoked_reason: Mapped[str | None] = mapped_column(String(32), default=None)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    user: Mapped["User"] = relationship(back_populates="refresh_tokens")

    __table_args__ = (
        # Revoking a family is the hot path during reuse detection.
        Index("ix_refresh_tokens_family_active", "family_id", "revoked_at"),
    )

    def is_usable(self, now: datetime) -> bool:
        return self.revoked_at is None and self.expires_at > now
