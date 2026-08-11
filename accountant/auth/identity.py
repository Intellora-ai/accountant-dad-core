"""Tenants, users, sessions, and the one function that turns a request into a
principal or a refusal.

THE ONE RULE THIS MODULE EXISTS TO ENFORCE
------------------------------------------
**A tenant id is derived from the credential, never read from the request.**

That is the whole defence. A request that could name its own tenant would let
any customer read any other customer's books by editing a form field, and no
amount of checking downstream repairs it. `Principal.tenant_id` therefore comes
only from a stored session row, and there is no code path in this module that
builds a `Principal` from caller-supplied data.

WHY SCRYPT, AND WHY THESE NUMBERS
---------------------------------
`hashlib.scrypt` is stdlib, so no dependency is added to hash a password. The
parameters n=16384, r=8, p=1 are the interactive-login figures from RFC 7914
section 2 and are written here rather than left to a default so somebody can
read what the cost actually is.

A password is never stored, and a token is never stored either — only their
hashes. A database that leaks then yields neither a password to reuse elsewhere
nor a session to replay.

WHY EXPIRY IS A COLUMN AND NOT A TIMER
--------------------------------------
The process restarts. A session that expired while the server was down must
still be expired when it comes back, so the fact lives in the row.
"""

from __future__ import annotations

import datetime
import hashlib
import hmac
import os
import secrets
from dataclasses import dataclass

#: Set to "1" to run without authentication. Anything else, including unset,
#: means production. The default is the safe one on purpose: a flag that must
#: be set to become UNSAFE fails closed when somebody forgets it.
ENV_LOCAL_DEV_MODE = "LOCAL_DEV_MODE"

#: The single tenant and user that local development runs as. Real values, so
#: every audit row still names an actor and a tenant, and so a row written in
#: local mode is visibly a local-mode row rather than a blank.
LOCAL_DEV_TENANT = "local-dev"
LOCAL_DEV_USER = "local-dev-user"

#: RFC 7914 section 2, interactive login. Stated rather than defaulted.
_SCRYPT_N = 16384
_SCRYPT_R = 8
_SCRYPT_P = 1
_SCRYPT_DKLEN = 64

#: How long a new session lasts. Owner-facing knob; the value is written here
#: so "how long until I am logged out" is a fact somebody can read.
SESSION_HOURS = 12


class AuthRefusal(Exception):
    """Why a request may not proceed. `status` is the HTTP code to answer.

    401 means "I do not know who you are". 403 means "I know, and no". Keeping
    them apart matters: a 403 tells the caller the resource exists, and a 401
    does not, so answering 403 to an unauthenticated request leaks existence.
    """

    def __init__(self, status: int, reason: str) -> None:
        super().__init__(reason)
        self.status = status
        self.reason = reason


@dataclass(frozen=True)
class Tenant:
    tenant_id: str
    name: str
    created_at: str
    deleted_at: str = ""

    @property
    def live(self) -> bool:
        return not self.deleted_at


@dataclass(frozen=True)
class User:
    user_id: str
    tenant_id: str
    email: str
    password_hash: str
    salt: str
    created_at: str
    deleted_at: str = ""

    @property
    def live(self) -> bool:
        return not self.deleted_at


@dataclass(frozen=True)
class Session:
    """A live credential. The token itself is NOT here — only its fingerprint.

    Storing the token would mean a database read yields something replayable.
    The fingerprint is enough to recognise a token presented later and useless
    to anybody who steals the row.
    """

    token_fingerprint: str
    user_id: str
    tenant_id: str
    created_at: str
    expires_at: str
    revoked_at: str = ""

    def live_at(self, when: datetime.datetime) -> bool:
        if self.revoked_at:
            return False
        return when < datetime.datetime.fromisoformat(self.expires_at)


@dataclass(frozen=True)
class Principal:
    """Who is acting. Built only from a stored session, or from local dev mode.

    There is deliberately no constructor here that takes a tenant id from a
    request. `tenant_id` is a conclusion, never an input.
    """

    user_id: str
    tenant_id: str
    local_dev: bool = False

    def owns(self, tenant_id: str) -> bool:
        return self.tenant_id == tenant_id

    def require(self, tenant_id: str) -> None:
        """Raise 403 unless this principal owns that tenant."""
        if not self.owns(tenant_id):
            raise AuthRefusal(
                403,
                f"this session belongs to tenant {self.tenant_id!r} and the "
                f"request names {tenant_id!r}",
            )


# ---------------------------------------------------------------------------
# passwords and tokens
# ---------------------------------------------------------------------------


def hash_password(password: str, salt: str | None = None) -> tuple[str, str]:
    """Return `(hash, salt)`, both hex. A new salt per password."""
    if not password:
        raise ValueError("a password is required")
    salt_bytes = bytes.fromhex(salt) if salt else secrets.token_bytes(16)
    digest = hashlib.scrypt(
        password.encode("utf-8"),
        salt=salt_bytes,
        n=_SCRYPT_N,
        r=_SCRYPT_R,
        p=_SCRYPT_P,
        dklen=_SCRYPT_DKLEN,
    )
    return digest.hex(), salt_bytes.hex()


def verify_password(password: str, stored_hash: str, salt: str) -> bool:
    """Constant-time comparison, so a wrong password cannot be found by timing."""
    if not password or not stored_hash or not salt:
        return False
    candidate, _ = hash_password(password, salt)
    return hmac.compare_digest(candidate, stored_hash)


def new_token() -> str:
    """A session token. 32 bytes of OS randomness, URL-safe."""
    return secrets.token_urlsafe(32)


def token_fingerprint(token: str) -> str:
    """What goes in the database. The token itself never does."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# mode
# ---------------------------------------------------------------------------


def local_dev_mode(environ: dict[str, str] | None = None) -> bool:
    """True only for exactly "1". Not "true", not "yes", not "0 ".

    Strict on purpose. A loose reading turns a typo into an unauthenticated
    production server, and the failure is silent.
    """
    source = environ if environ is not None else dict(os.environ)
    return source.get(ENV_LOCAL_DEV_MODE, "") == "1"


# ---------------------------------------------------------------------------
# the seam
# ---------------------------------------------------------------------------


def authenticate(
    token: str,
    lookup: object,
    *,
    dev_mode: bool = False,
    now: datetime.datetime | None = None,
) -> Principal:
    """Turn a presented token into a principal, or raise `AuthRefusal`.

    `lookup` is anything with `session_by_fingerprint(str) -> Session | None`;
    the store satisfies it. Injected rather than imported so this module holds
    no database and every refusal is testable without one.

    In dev mode the token is not read at all. That is the point: local mode
    exists so a developer does not have to hold a credential, and quietly
    honouring a token that happened to be present would make the two modes
    differ by whether the caller guessed right.
    """
    if dev_mode:
        return Principal(LOCAL_DEV_USER, LOCAL_DEV_TENANT, local_dev=True)

    if not token:
        raise AuthRefusal(401, "no session token was presented")

    finder = getattr(lookup, "session_by_fingerprint", None)
    if finder is None:
        raise AuthRefusal(401, "no session store is configured")

    session = finder(token_fingerprint(token))
    if session is None:
        raise AuthRefusal(401, "that session token is not recognised")

    when = now or datetime.datetime.now(datetime.UTC)
    if session.revoked_at:
        raise AuthRefusal(401, "that session has been revoked")
    if not session.live_at(when):
        raise AuthRefusal(401, "that session has expired")

    return Principal(session.user_id, session.tenant_id)
