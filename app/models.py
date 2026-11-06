"""SQLAlchemy ORM models.

Empty for now -- the CRUD resource goes here once we know what it is.

Every model must inherit from Base and be importable from this module, or
Alembic's autogenerate won't see it. After adding one:

    make migration m="add <thing> table"   # writes migrations/versions/*.py
    make migrate                           # applies it
"""

from app.database import Base  # noqa: F401
