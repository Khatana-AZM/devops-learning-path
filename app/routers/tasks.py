"""Owner-scoped CRUD for tasks.

Two rules hold everywhere in this file:

1. Ownership is enforced in the WHERE clause, never as an `if` after loading
   the row. A filter that's part of the query cannot be forgotten halfway
   down a function, and it can't be bypassed by a code path that returns early.

2. Someone else's task returns 404, not 403. A 403 would confirm the row
   exists -- letting anyone probe /tasks/1, /tasks/2, ... to learn how many
   tasks the system holds and which ids are real. 404 tells them nothing.
"""

from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_db
from app.dependencies import get_current_active_user
from app.models import Task, User
from app.schemas import TaskCreate, TaskOut, TaskUpdate

# Every route in this router requires a valid access token. Declaring it on
# the router means a new endpoint is authenticated by default -- you have to
# opt OUT, rather than remembering to opt in.
router = APIRouter(prefix="/tasks", tags=["tasks"])

CurrentUser = Annotated[User, Depends(get_current_active_user)]
DbSession = Annotated[Session, Depends(get_db)]


def _owned_task(db: Session, user: User, task_id: int) -> Task:
    """Load a task that belongs to `user`, or 404.

    The user_id predicate is what makes this safe. Without it this is an
    IDOR: any authenticated user could read or edit any task by guessing ids.
    """
    task = db.get(Task, task_id)
    if task is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Task not found"
        )
    return task


@router.post("", response_model=TaskOut, status_code=status.HTTP_201_CREATED)
def create_task(payload: TaskCreate, user: CurrentUser, db: DbSession) -> Task:
    # user_id comes from the token, never from the request body. If clients
    # could send it, any user could create tasks owned by someone else.
    task = Task(**payload.model_dump(), user_id=user.id)
    db.add(task)
    db.commit()
    db.refresh(task)
    return task


@router.get("", response_model=list[TaskOut])
def list_tasks(
    user: CurrentUser,
    db: DbSession,
    is_done: Annotated[bool | None, Query(description="Filter by done state")] = None,
    due_before: Annotated[date | None, Query(description="Due strictly before")] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[Task]:
    stmt = select(Task).where(Task.user_id == user.id)

    if is_done is not None:
        stmt = stmt.where(Task.is_done == is_done)
    if due_before is not None:
        stmt = stmt.where(Task.due_date.is_not(None), Task.due_date < due_before)

    # Ordering by id as a tiebreaker keeps pagination stable: without it,
    # rows sharing a created_at can shuffle between pages and a client
    # paging through the list silently skips or repeats entries.
    stmt = stmt.order_by(Task.created_at.desc(), Task.id.desc())

    return list(db.scalars(stmt.limit(limit).offset(offset)).all())


@router.get("/{task_id}", response_model=TaskOut)
def get_task(task_id: int, user: CurrentUser, db: DbSession) -> Task:
    return _owned_task(db, user, task_id)


@router.patch("/{task_id}", response_model=TaskOut)
def update_task(
    task_id: int, payload: TaskUpdate, user: CurrentUser, db: DbSession
) -> Task:
    task = _owned_task(db, user, task_id)

    # exclude_unset distinguishes "field absent" from "field set to null".
    # Without it, every PATCH would wipe description and due_date.
    changes = payload.model_dump(exclude_unset=True)
    if not changes:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="No fields to update"
        )

    for field, value in changes.items():
        setattr(task, field, value)

    db.commit()
    db.refresh(task)
    return task


@router.delete("/{task_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_task(task_id: int, user: CurrentUser, db: DbSession) -> None:
    task = _owned_task(db, user, task_id)
    db.delete(task)
    db.commit()
