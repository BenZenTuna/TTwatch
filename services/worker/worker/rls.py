import functools
import uuid

from celery import Task
from sqlalchemy import text

from worker.db import db_session


def with_rls_context(func):
    """Decorator that sets PostgreSQL RLS context for the task's user_id.

    Handles both bound tasks (@app.task(bind=True)) where `self` is the
    first argument, and unbound tasks where `user_id` is the first argument.

    Uses f-string formatting, safe because UUID is validated via round-trip.
    PostgreSQL SET does not accept bind parameters ($1).
    """
    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        # Handle bind=True tasks where first arg is the Task instance
        if args and isinstance(args[0], Task):
            task_self = args[0]
            user_id = args[1] if len(args) > 1 else kwargs.pop("user_id", None)
            remaining_args = args[2:]
        else:
            task_self = None
            user_id = args[0] if args else kwargs.pop("user_id", None)
            remaining_args = args[1:]

        if not user_id:
            raise ValueError("with_rls_context: user_id is required")

        validated_id = str(uuid.UUID(user_id))
        with db_session() as session:
            session.execute(text(
                f"SET LOCAL ttwatch.current_user_id = '{validated_id}'"
            ))
            if task_self is not None:
                return func(task_self, user_id, *remaining_args, session=session, **kwargs)
            else:
                return func(user_id, *remaining_args, session=session, **kwargs)
    return wrapper
