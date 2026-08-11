"""Who is asking, and whose books they may touch.

WHY THIS EXISTS
---------------
Until now every route in `accountant/web/app.py` answered anyone who could
reach the socket, and that was defensible while the socket was
`127.0.0.1:8000` on the user's own laptop. The moment the same code serves two
customers, "whoever can reach the port" stops being an identity and becomes a
hole: `POST /reverse` deletes a voucher on a caller-supplied operation id, and
`POST /reverse-all` deletes every voucher we ever wrote.

TWO MODES, AND THE DEFAULT IS THE SAFE ONE
------------------------------------------
    LOCAL_DEV_MODE=1     no login. One tenant, "local-dev". A warning is
                         logged on every start so nobody can run it this way
                         by accident and not know.
    anything else        every request needs a live session, and every query
                         is scoped to the tenant that session belongs to.

The default is production, not development. A flag that must be set to become
unsafe is a different object from a flag that must be set to become safe: the
first fails closed when somebody forgets, and forgetting is the normal case.

WHAT IS DELIBERATELY NOT HERE
-----------------------------
No JWT. A signed token that the server does not store cannot be revoked, and
revocation is a stated requirement — so it would need a blocklist, which is a
session table wearing a hat. Sessions are stored, and revoking one is deleting
a row.

No password reset by email: sending mail needs a provider, an account and a
domain, none of which exist. `docs/OWNER_WORK.md` records it.
"""

from __future__ import annotations

from accountant.auth.identity import (
    ENV_LOCAL_DEV_MODE,
    LOCAL_DEV_TENANT,
    LOCAL_DEV_USER,
    SESSION_HOURS,
    AuthRefusal,
    Principal,
    Session,
    Tenant,
    User,
    authenticate,
    hash_password,
    local_dev_mode,
    new_token,
    token_fingerprint,
    verify_password,
)

__all__ = [
    "ENV_LOCAL_DEV_MODE",
    "LOCAL_DEV_TENANT",
    "LOCAL_DEV_USER",
    "SESSION_HOURS",
    "AuthRefusal",
    "Principal",
    "Session",
    "Tenant",
    "User",
    "authenticate",
    "hash_password",
    "local_dev_mode",
    "new_token",
    "token_fingerprint",
    "verify_password",
]
