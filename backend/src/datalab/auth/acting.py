"""Who the current request acts for, where code deep inside it (the
knowledge and pipelines services' GitHub calls) needs to know without every
call passing it down.

ApiProtection (web.py) sets it for each signed-in request. Work on a worker
thread from that request (asyncio.to_thread) and tasks it starts see it too.
Anything else (a background sync, a scheduled run) acts for no one: on the
lab's server that means no one's GitHub sign-in, never someone else's.
"""

from __future__ import annotations

from contextvars import ContextVar

from datalab.auth.users import User

_acting: ContextVar[User | None] = ContextVar("datalab_acting_user", default=None)


def acting_user() -> User | None:
    return _acting.get()


def act_as(user: User | None):
    """Set for the rest of this context; returns the token to reset it."""
    return _acting.set(user)


def stop_acting(token) -> None:
    _acting.reset(token)
