"""BetterAuth bridge for FastAPI.

Sessions are owned by Next.js (the `better-auth` lib writes to the `session`
table via its own pg pool); FastAPI reads the same table via SQLAlchemy. The
shared source of truth is a single Postgres database — there is no JWT, no
auth proxy, no second copy of session state.
"""

from app.auth.session import (
    optional_user_id,
    require_user_id,
    resolve_user_id,
)

__all__ = ["optional_user_id", "require_user_id", "resolve_user_id"]
