"""SQLite — our index, never their books.

WHAT IS STORED
--------------
    company identity          the scope key every other row carries
    normalised vendor keys    collapsed spellings, not the name as typed
    vendor -> account history  with the observed count and the voucher ids it
                               came from
    phrase -> account history  the same, keyed on a normalised narration
    observed counts            how many TIMES, never how much
    conflicts                  recorded as a fact, never resolved by a pick
    source voucher ids         so every row traces back into Tally
    bootstrap status, the attempt time, and the last SUCCESSFUL time
    provenance                 where each row came from

WHAT IS NOT STORED
------------------
Amounts, tax, dates, parties as typed, narrations as written — anything that
would make this file a second copy of the customer's books. Tally is the book
of record. Delete this file and the customer loses our learning and nothing
else. There is no money column in this schema and a test asserts it, which is
also why the "integer paise, never float" rule shows up here as an absence
rather than a type.

COMPANY SCOPING IS STRUCTURAL
-----------------------------
Every table carries `company_key`, and it is the FIRST column of every table's
primary key. Every statement in this module is a literal string with the key
bound as a parameter — there is no string-built SQL here, and no query that can
be issued without a company. `save_bootstrap` additionally refuses any row
whose `company_key` is not the company being saved, so one company's history
cannot be written into another's scope even by mistake.

A test reads the live schema back through `table_names`, `columns_of` and
`primary_key_of` and asserts all of it. Enforced, not promised.

`action_log` IS EXEMPT FROM LOG REDACTION, AND THAT IS AN OWNER DECISION
------------------------------------------------------------------------
Recorded 2026-08-11, when redaction arrived for the APPLICATION log
(`accountant/redact.py`). That work took vendor names, amounts, credentials and
session material out of diagnostic logs. **It deliberately stopped at this
table, which keeps the vendor and keeps the amount.**

`action_log` is the record of what this software did to a real business's
statutory books. `docs/DATA_POLICY.md` Table B row 10 says of it *"it **is** the
log"*, and row 8 says amounts *"appear in the local action log by design"*. An
audit row that cannot say which party and how much is not an audit row, it is a
timestamp.

So this module imports nothing from `accountant.redact`, and
`tests/test_redaction.py::test_nothing_in_the_memory_package_imports_the_redactor`
reads the import graph and fails the day somebody helpfully adds it. If you are
here to "finish the redaction work", this paragraph is the answer: it is
finished, and this is where it stops. `docs/REDACTION.md` has the long version.
"""

from __future__ import annotations

import datetime
import json
import sqlite3
from collections.abc import Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import cast

from accountant.auth.identity import Session, Tenant, User
from accountant.memory.identity import (
    CompanyIdentity,
    IdentityEvidence,
    normalise_company,
)
from accountant.schema import NOT_RECORDED, ActionLog

IN_MEMORY = ":memory:"

SCHEMA: tuple[str, ...] = (
    """
    CREATE TABLE IF NOT EXISTS company (
        company_key     TEXT    NOT NULL PRIMARY KEY,
        display_name    TEXT    NOT NULL,
        status          TEXT    NOT NULL,
        detail          TEXT    NOT NULL,
        attempted_at    TEXT    NOT NULL,
        bootstrapped_at TEXT    NOT NULL,
        steps           TEXT    NOT NULL,
        vouchers        INTEGER NOT NULL,
        vendors         INTEGER NOT NULL,
        accounts        INTEGER NOT NULL,
        mappings        INTEGER NOT NULL,
        conflicts       INTEGER NOT NULL,
        unusable        INTEGER NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS vendor_account (
        company_key        TEXT    NOT NULL,
        subject            TEXT    NOT NULL,
        account            TEXT    NOT NULL,
        times              INTEGER NOT NULL,
        source_voucher_ids TEXT    NOT NULL,
        provenance         TEXT    NOT NULL,
        -- D-05, 2026-08-10. The name the SOURCE actually gave, kept beside the
        -- key the strip produced. NULL means INCOMPLETE: a row written before
        -- this column existed, whose legal form is gone and must never be
        -- guessed back. `subject` finds candidates; this decides identity.
        raw_subject        TEXT,
        PRIMARY KEY (company_key, subject, account)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS phrase_account (
        company_key        TEXT    NOT NULL,
        subject            TEXT    NOT NULL,
        account            TEXT    NOT NULL,
        times              INTEGER NOT NULL,
        source_voucher_ids TEXT    NOT NULL,
        provenance         TEXT    NOT NULL,
        -- D-05, 2026-08-10. The name the SOURCE actually gave, kept beside the
        -- key the strip produced. NULL means INCOMPLETE: a row written before
        -- this column existed, whose legal form is gone and must never be
        -- guessed back. `subject` finds candidates; this decides identity.
        raw_subject        TEXT,
        PRIMARY KEY (company_key, subject, account)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS chart_account (
        company_key TEXT NOT NULL,
        account     TEXT NOT NULL,
        PRIMARY KEY (company_key, account)
    )
    """,
    # The action log. Company-keyed like every other table here, and APPEND
    # ONLY — there is deliberately no update or delete path, because a record
    # that can be edited after the fact is not a record of what happened.
    #
    # `rowid` orders it rather than the timestamp: two decisions inside the
    # same clock tick would otherwise be unorderable, and "which came first"
    # is exactly what an audit trail is asked.
    """
    CREATE TABLE IF NOT EXISTS action_log (
        company_key  TEXT NOT NULL,
        ts           TEXT NOT NULL,
        action       TEXT NOT NULL,
        outcome      TEXT NOT NULL,
        reason       TEXT NOT NULL,
        run_id       TEXT NOT NULL,
        backend      TEXT NOT NULL,
        operation_id TEXT NOT NULL,
        voucher_id   TEXT NOT NULL,
        vendor_id    TEXT NOT NULL,
        detail       TEXT NOT NULL,
        -- Phase 8 PR-5, owner decision Q8 = A, 2026-08-10. The two fields of
        -- the seven that this table did not have, plus the batch a reversal
        -- event belongs to.
        --
        -- NULLABLE ON PURPOSE, and the NULL carries meaning. A database
        -- written before these columns existed has them added by `_migrate`
        -- with every existing row left NULL, and NULL reads back as
        -- NOT_RECORDED. Back-filling `accountant_dad` onto those rows would
        -- invent provenance for actions nobody recorded the actor of. Same
        -- rule, and the same reason, as `raw_subject` above.
        actor          TEXT,
        previous_state TEXT,
        batch_id       TEXT,
        -- Tenancy, 2026-08-10. Same shape and same reason as the three above:
        -- nullable, and NULL reads back as NOT_RECORDED rather than being
        -- back-filled with a tenant nobody measured.
        tenant_id      TEXT,
        user_id        TEXT
    )
    """,
    # The other four tables key on `company_key` FIRST in a composite primary
    # key, which both scopes them and makes each row unique. A log cannot use
    # that shape: two identical decisions are not a mistake to be deduplicated,
    # they are two things that happened, and a primary key would silently drop
    # the second. So scoping is carried by NOT NULL plus this index, and
    # uniqueness by SQLite's rowid.
    """
    CREATE INDEX IF NOT EXISTS action_log_by_company
        ON action_log (company_key)
    """,
    # ----------------------------------------------------------------------
    # Tenancy. Added 2026-08-10 for the cloud product.
    #
    # `company_key` already scopes every table above, and it stays. Tenancy
    # sits ABOVE it rather than replacing it: a tenant owns companies, and a
    # company still owns its vendors and its log. Replacing the existing key
    # would have meant rewriting five tables and every query that reads them,
    # to gain nothing the extra level does not already give.
    # ----------------------------------------------------------------------
    # Every operation id this company has ever written, and whether it was
    # later reversed. Defect I1, fixed 2026-08-10.
    #
    # WHY A TABLE AND NOT A LOOK IN TALLY. Reversing a voucher DELETES it, so
    # after a reversal Tally's answer to "does operation X exist" is no, and the
    # id looked free. It was not free: docs/ARCHITECTURE.md section 7 and
    # accountant/tallyio/client.py both say the operation id IS the identity of
    # a write. An identity that can be reused after a delete is not an
    # identity, it is a slot, and two `posted` rows naming one operation id and
    # two different Tally ids cannot afterwards be reconciled by the one thing
    # they have in common.
    #
    # The PRIMARY KEY is the guard, not a SELECT before the INSERT. A read
    # followed by a write has a window between them; a constraint does not, and
    # SQLite refusing the second INSERT is the same answer whatever else is
    # happening in the process.
    #
    # `reversed_at` is NULLABLE and records WHEN, not WHETHER: a row that is
    # here at all already means the id is spent. The column exists so the trail
    # can say "used, then undone" rather than only "used".
    """
    CREATE TABLE IF NOT EXISTS operation (
        company_key   TEXT NOT NULL,
        operation_id  TEXT NOT NULL,
        first_used_at TEXT NOT NULL,
        reversed_at   TEXT,
        PRIMARY KEY (company_key, operation_id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS tenant (
        tenant_id  TEXT NOT NULL PRIMARY KEY,
        name       TEXT NOT NULL,
        created_at TEXT NOT NULL,
        deleted_at TEXT
    )
    """,
    # `email` is UNIQUE across the whole table, not per tenant. Two tenants
    # sharing one email address would make "which tenant am I logging in to"
    # ambiguous at exactly the moment a password is being checked, and the
    # only ways out are asking the person to disambiguate or picking one.
    """
    CREATE TABLE IF NOT EXISTS app_user (
        user_id       TEXT NOT NULL PRIMARY KEY,
        tenant_id     TEXT NOT NULL,
        email         TEXT NOT NULL UNIQUE,
        password_hash TEXT NOT NULL,
        salt          TEXT NOT NULL,
        created_at    TEXT NOT NULL,
        deleted_at    TEXT
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS app_user_by_tenant ON app_user (tenant_id)
    """,
    # The PRIMARY KEY is the token FINGERPRINT, never the token. A database
    # that leaks yields nothing replayable.
    #
    # `expires_at` is a column rather than a timer because the process
    # restarts: a session that expired while the server was down must still be
    # expired when it comes back.
    """
    CREATE TABLE IF NOT EXISTS session (
        token_fingerprint TEXT NOT NULL PRIMARY KEY,
        user_id           TEXT NOT NULL,
        tenant_id         TEXT NOT NULL,
        created_at        TEXT NOT NULL,
        expires_at        TEXT NOT NULL,
        revoked_at        TEXT
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS session_by_user ON session (user_id)
    """,
)


class BootstrapStatus(StrEnum):
    """What we know about this company's history.

    Owner decision, 2026-08-09. Reading the books successfully is NOT the same
    as understanding them, and an empty customer is NOT the same as a failed
    read. Collapsing any of these into READY is how a system ends up guessing
    on behalf of a company it knows nothing about.

        READY               history read AND mappings derived. May propose.
        EMPTY_SOURCE        history read, there was none. May ASK, never
                            proposes, never writes, until onboarding creates a
                            measurable mapping. A legitimate new customer.
        EMPTY_VENDOR_INDEX  history read and NOTHING usable came out. Something
                            is wrong with the read, the data or the derivation.
                            Does nothing at all.
        INCOMPLETE          a step failed outright. Does nothing at all.
        NEVER_RUN           no bootstrap has ever been attempted.
        COMPANY_KEY_COLLISION
                            another company open in Tally right now normalises
                            to this same key. Refused BEFORE anything is read
                            or erased, because the alternative is answering for
                            the wrong company's books. Does nothing at all.

    Until 2026-08-09 the first three were one state. A company with forty
    vouchers whose every row was unusable was READY with zero mappings —
    recorded in `docs/PROJECT_STATE.md` as "PRODUCT INVARIANT - NOT YET
    ENFORCED".
    """

    READY = "ready"
    EMPTY_SOURCE = "empty_source"
    EMPTY_VENDOR_INDEX = "empty_vendor_index"
    INCOMPLETE = "incomplete"
    NEVER_RUN = "never_run"
    COMPANY_KEY_COLLISION = "company_key_collision"


@dataclass(frozen=True)
class BootstrapCounts:
    """What the bootstrap actually loaded. Reported, never assumed."""

    vouchers: int = 0
    vendors: int = 0
    accounts: int = 0
    mappings: int = 0
    conflicts: int = 0
    unusable: int = 0


@dataclass(frozen=True)
class BootstrapReport:
    """The record of one bootstrap attempt for one company.

    `bootstrapped_at` is the last time a bootstrap SUCCEEDED, and stays put
    across a later failure — "when did we last really read this company" is a
    different question from "when did we last try".
    """

    identity: CompanyIdentity
    status: BootstrapStatus
    detail: str
    attempted_at: str
    bootstrapped_at: str = ""
    counts: BootstrapCounts = field(default_factory=BootstrapCounts)
    steps: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.detail.strip():
            raise ValueError(
                f"bootstrap {self.status.value} for {self.identity.name!r} "
                f"must state what happened"
            )
        if self.status is BootstrapStatus.READY and not self.bootstrapped_at:
            raise ValueError(
                f"a ready bootstrap for {self.identity.name!r} must carry the "
                f"time it succeeded"
            )

    @property
    def ready(self) -> bool:
        """The single gate. Nothing proposes an account while this is False."""
        return self.status is BootstrapStatus.READY

    @property
    def askable(self) -> bool:
        """Whether we may put a question to the person about this company.

        Wider than `ready` by exactly one state. EMPTY_SOURCE means we read
        their books and there was nothing in them — a fact about the customer,
        not about us — so asking is honest and a proposal is still impossible.

        EMPTY_VENDOR_INDEX, INCOMPLETE and NEVER_RUN mean we cannot vouch for
        what we read, so we have no standing to ask anything either. That is
        the difference the two properties exist to keep apart.
        """
        return self.status in (BootstrapStatus.READY, BootstrapStatus.EMPTY_SOURCE)


@dataclass(frozen=True)
class Observation:
    """One thing this company was seen to do, and where it was seen.

    `times` counts observations. `source_voucher_ids` holds the ones that came
    from a voucher — a human correction has no voucher, so the two are allowed
    to differ, and the difference is itself provenance.
    """

    company_key: str
    subject: str
    account: str
    times: int
    source_voucher_ids: tuple[str, ...] = ()
    provenance: str = ""
    raw_subject: str | None = None

    @property
    def identity_evidence(self) -> IdentityEvidence:
        """Is there enough here to decide WHO this is, or only WHERE to look?

        Derived from `raw_subject` rather than stored beside it. A second
        column could disagree with the first, and a stored flag claiming
        COMPLETE over a NULL name is exactly the false confidence D-05 exists
        to stop.
        """
        return (
            IdentityEvidence.INCOMPLETE
            if self.raw_subject is None
            else IdentityEvidence.COMPLETE
        )

    def __post_init__(self) -> None:
        if self.times < 1:
            raise ValueError(
                f"observation {self.subject!r} -> {self.account!r} was never observed"
            )


@dataclass(frozen=True)
class _Table:
    """The four literal statements one observation table needs."""

    select_one: str
    select_all: str
    upsert: str
    delete: str


_VENDOR = _Table(
    select_one=(
        "SELECT subject, account, times, source_voucher_ids, provenance, raw_subject "
        "FROM vendor_account WHERE company_key = ? AND subject = ? "
        "ORDER BY account"
    ),
    select_all=(
        "SELECT subject, account, times, source_voucher_ids, provenance, raw_subject "
        "FROM vendor_account WHERE company_key = ? ORDER BY subject, account"
    ),
    upsert=(
        "INSERT OR REPLACE INTO vendor_account "
        "(company_key, subject, account, times, source_voucher_ids, provenance, "
        "raw_subject) VALUES (?, ?, ?, ?, ?, ?, ?)"
    ),
    delete="DELETE FROM vendor_account WHERE company_key = ?",
)

_PHRASE = _Table(
    select_one=(
        "SELECT subject, account, times, source_voucher_ids, provenance, raw_subject "
        "FROM phrase_account WHERE company_key = ? AND subject = ? "
        "ORDER BY account"
    ),
    select_all=(
        "SELECT subject, account, times, source_voucher_ids, provenance, raw_subject "
        "FROM phrase_account WHERE company_key = ? ORDER BY subject, account"
    ),
    upsert=(
        "INSERT OR REPLACE INTO phrase_account "
        "(company_key, subject, account, times, source_voucher_ids, provenance, "
        "raw_subject) VALUES (?, ?, ?, ?, ?, ?, ?)"
    ),
    delete="DELETE FROM phrase_account WHERE company_key = ?",
)

_CHART_SELECT = (
    "SELECT account FROM chart_account WHERE company_key = ? ORDER BY account"
)
_CHART_INSERT = (
    "INSERT OR REPLACE INTO chart_account (company_key, account) VALUES (?, ?)"
)
_CHART_DELETE = "DELETE FROM chart_account WHERE company_key = ?"
_COMPANY_DELETE = "DELETE FROM company WHERE company_key = ?"

_COMPANY_SELECT = (
    "SELECT display_name, status, detail, attempted_at, bootstrapped_at, steps, "
    "vouchers, vendors, accounts, mappings, conflicts, unusable "
    "FROM company WHERE company_key = ?"
)
_COMPANY_UPSERT = (
    "INSERT OR REPLACE INTO company "
    "(company_key, display_name, status, detail, attempted_at, bootstrapped_at, "
    "steps, vouchers, vendors, accounts, mappings, conflicts, unusable) "
    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
)

_ACTION_INSERT = """
    INSERT INTO action_log (
        company_key, ts, action, outcome, reason, run_id,
        backend, operation_id, voucher_id, vendor_id, detail,
        actor, previous_state, batch_id, tenant_id, user_id
    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
"""

_ACTION_SELECT = """
    SELECT company_key, ts, action, outcome, reason, run_id,
           backend, operation_id, voucher_id, vendor_id, detail,
           actor, previous_state, batch_id, tenant_id, user_id
      FROM action_log
     WHERE company_key = ?
     ORDER BY rowid
"""

# `action_log` is NOT here, and that is the point. `forget()` runs on every
# rebuild; the index is a statement about our memory and may be rebuilt, but
# what we already did to somebody's books is a different fact and survives.
_DELETES: tuple[str, ...] = (
    _VENDOR.delete,
    _PHRASE.delete,
    _CHART_DELETE,
    _COMPANY_DELETE,
)

_TABLE_NAMES = "SELECT name FROM sqlite_master WHERE type = 'table' ORDER BY name"
_COLUMNS = "SELECT name FROM pragma_table_info(?) ORDER BY cid"
_PRIMARY_KEY = "SELECT name FROM pragma_table_info(?) WHERE pk > 0 ORDER BY pk"


def _unrecorded_as_null(value: str) -> str | None:
    """`NOT_RECORDED` goes into the file as SQL NULL, and so does an empty
    string. One representation of "nobody wrote this down", shared with every
    row that predates the column."""
    return None if value == NOT_RECORDED or not value.strip() else value


def _null_as_unrecorded(value: object) -> str:
    """NULL comes back as the explicit marker, NEVER as a default actor.

    This function is the whole of the migration's honesty. Returning
    `Actor.ACCOUNTANT_DAD` here instead would make every row a company ever
    wrote look like a system action, including the ones a person took, and
    nothing downstream could tell the difference.
    """
    return NOT_RECORDED if value is None or not str(value).strip() else str(value)


def _row_to_observation(company_key: str, row: Sequence[object]) -> Observation:
    return Observation(
        company_key=company_key,
        subject=str(row[0]),
        account=str(row[1]),
        times=int(str(row[2])),
        source_voucher_ids=_ids_from(str(row[3])),
        provenance=str(row[4]),
        raw_subject=None if row[5] is None else str(row[5]),
    )


def _ids_from(text: str) -> tuple[str, ...]:
    """JSON is the serialisation, so a voucher id containing anything at all
    survives the round trip. Decoded as `object` and forced to `str`, because
    what comes out of `json.loads` is whatever was in the column."""
    loaded = cast(list[object], json.loads(text))
    return tuple(str(item) for item in loaded)


def _ids_to(ids: Sequence[str]) -> str:
    return json.dumps(list(ids), sort_keys=True)


def _observation_params(
    o: Observation,
) -> tuple[str, str, str, int, str, str, str | None]:
    return (
        o.company_key,
        o.subject,
        o.account,
        o.times,
        _ids_to(o.source_voucher_ids),
        o.provenance,
        o.raw_subject,
    )


class MemoryStore:
    """Our SQLite file. One store can hold many companies; no query can reach
    more than one of them at a time."""

    def __init__(self, path: str | Path = IN_MEMORY) -> None:
        self._db = sqlite3.connect(str(path))
        for statement in SCHEMA:
            self._db.execute(statement)
        self._migrate()
        self._db.commit()

    def _migrate(self) -> None:
        """Add columns a file written by an older build does not have.

        `raw_subject` arrived with D-05 on 2026-08-10. A database created
        before it has the column added and every existing row left NULL, which
        is the truth: those rows were keyed by a strip that threw the legal
        form away, and nothing can recover it. They read back as INCOMPLETE and
        are never a confident match. Backfilling them from `subject` would
        manufacture the evidence.

        `actor`, `previous_state` and `batch_id` on `action_log` arrived with
        Phase 8 PR-5 on 2026-08-10 and follow the identical rule. Every
        pre-existing row keeps NULL in all three and reads back as
        `NOT_RECORDED`. THEY ARE NEVER BACK-FILLED WITH `accountant_dad`:
        that would say the system did something nobody recorded the actor of,
        and a plausible guess in an audit trail is worse than a gap, because
        the gap is visible.

        Additive only. No row is rewritten, so the migration cannot lose data
        and does not need to be undone. There is no UPDATE and no DELETE here
        for the same reason there is none anywhere else in this module.
        """
        added: tuple[tuple[str, tuple[str, ...]], ...] = (
            ("vendor_account", ("raw_subject",)),
            ("phrase_account", ("raw_subject",)),
            (
                "action_log",
                (
                    "actor",
                    "previous_state",
                    "batch_id",
                    "tenant_id",
                    "user_id",
                ),
            ),
        )
        for table, wanted in added:
            columns = {
                str(row[0]) for row in self._db.execute(_COLUMNS, (table,)).fetchall()
            }
            for column in wanted:
                if column not in columns:
                    self._db.execute(f"ALTER TABLE {table} ADD COLUMN {column} TEXT")

    def close(self) -> None:
        self._db.close()

    # ---- tenancy ----------------------------------------------------------
    #
    # Added 2026-08-10. Every method here either takes a tenant id from a
    # STORED row or writes one; none of them accepts a tenant id chosen by a
    # caller and returns data scoped to it without checking. That asymmetry is
    # the whole isolation guarantee, and `tests/test_auth.py` asserts it.

    def create_tenant(self, tenant_id: str, name: str, created_at: str) -> Tenant:
        with self._db:
            self._db.execute(
                "INSERT INTO tenant (tenant_id, name, created_at, deleted_at) "
                "VALUES (?, ?, ?, NULL)",
                (tenant_id, name, created_at),
            )
        return Tenant(tenant_id, name, created_at)

    def tenant(self, tenant_id: str) -> Tenant | None:
        row = self._db.execute(
            "SELECT tenant_id, name, created_at, deleted_at FROM tenant "
            "WHERE tenant_id = ?",
            (tenant_id,),
        ).fetchone()
        if row is None:
            return None
        return Tenant(str(row[0]), str(row[1]), str(row[2]), str(row[3] or ""))

    def create_user(
        self,
        user_id: str,
        tenant_id: str,
        email: str,
        password_hash: str,
        salt: str,
        created_at: str,
    ) -> User:
        with self._db:
            self._db.execute(
                "INSERT INTO app_user (user_id, tenant_id, email, password_hash, "
                "salt, created_at, deleted_at) VALUES (?, ?, ?, ?, ?, ?, NULL)",
                (user_id, tenant_id, email, password_hash, salt, created_at),
            )
        return User(user_id, tenant_id, email, password_hash, salt, created_at)

    def user_by_email(self, email: str) -> User | None:
        row = self._db.execute(
            "SELECT user_id, tenant_id, email, password_hash, salt, created_at, "
            "deleted_at FROM app_user WHERE email = ?",
            (email,),
        ).fetchone()
        if row is None:
            return None
        return User(
            str(row[0]),
            str(row[1]),
            str(row[2]),
            str(row[3]),
            str(row[4]),
            str(row[5]),
            str(row[6] or ""),
        )

    def open_session(
        self,
        token_fingerprint: str,
        user_id: str,
        tenant_id: str,
        created_at: str,
        expires_at: str,
    ) -> Session:
        """Store a session. The TOKEN is never passed here, only its hash.

        The caller keeps the token and hands it to the person; this row can
        recognise it later and is useless to anyone who steals the database.
        """
        with self._db:
            self._db.execute(
                "INSERT INTO session (token_fingerprint, user_id, tenant_id, "
                "created_at, expires_at, revoked_at) VALUES (?, ?, ?, ?, ?, NULL)",
                (token_fingerprint, user_id, tenant_id, created_at, expires_at),
            )
        return Session(token_fingerprint, user_id, tenant_id, created_at, expires_at)

    def session_by_fingerprint(self, token_fingerprint: str) -> Session | None:
        """The lookup `accountant.auth.authenticate` needs, and the only way a
        `Principal` is ever built from a credential."""
        row = self._db.execute(
            "SELECT token_fingerprint, user_id, tenant_id, created_at, "
            "expires_at, revoked_at FROM session WHERE token_fingerprint = ?",
            (token_fingerprint,),
        ).fetchone()
        if row is None:
            return None
        return Session(
            str(row[0]),
            str(row[1]),
            str(row[2]),
            str(row[3]),
            str(row[4]),
            str(row[5] or ""),
        )

    def revoke_session(self, token_fingerprint: str, revoked_at: str) -> bool:
        """Mark a session dead. Returns whether a row was actually changed.

        An UPDATE rather than a DELETE, so "this session was revoked at 14:02"
        stays answerable. A deleted row and a session that never existed are
        indistinguishable afterwards, and support needs to tell them apart.
        """
        with self._db:
            changed = self._db.execute(
                "UPDATE session SET revoked_at = ? "
                "WHERE token_fingerprint = ? AND revoked_at IS NULL",
                (revoked_at, token_fingerprint),
            ).rowcount
        return bool(changed)

    def revoke_all_sessions_for(self, user_id: str, revoked_at: str) -> int:
        """Every live session for one user. What a password change should call."""
        with self._db:
            changed = self._db.execute(
                "UPDATE session SET revoked_at = ? "
                "WHERE user_id = ? AND revoked_at IS NULL",
                (revoked_at, user_id),
            ).rowcount
        return int(changed)

    # ---- operation ids, so an identity is never handed out twice ------------

    def claim_operation(self, company_key: str, operation_id: str, at: str) -> bool:
        """Take this operation id for this company. False if it is already taken.

        The INSERT is the check. A `SELECT` first and an `INSERT` after has a
        window between them, and the whole point of this record is that an
        identity is handed out exactly once — including when two requests arrive
        together, which is the ordinary case behind a double-clicked button.

        Returns rather than raises, because the caller has a better sentence to
        write than this function does: `pipeline.post` knows the draft, the
        vendor and the amount.
        """
        try:
            with self._db:
                self._db.execute(
                    "INSERT INTO operation "
                    "(company_key, operation_id, first_used_at, reversed_at) "
                    "VALUES (?, ?, ?, NULL)",
                    (normalise_company(company_key), operation_id, at),
                )
        except sqlite3.IntegrityError:
            return False
        return True

    def operation_used(self, company_key: str, operation_id: str) -> bool:
        row = self._db.execute(
            "SELECT 1 FROM operation WHERE company_key = ? AND operation_id = ?",
            (normalise_company(company_key), operation_id),
        ).fetchone()
        return row is not None

    def operation_reversed_at(self, company_key: str, operation_id: str) -> str:
        """When it was reversed, or "". Never a bool: "not reversed" and "never
        used" are different facts and `operation_used` answers the second."""
        row = self._db.execute(
            "SELECT reversed_at FROM operation "
            "WHERE company_key = ? AND operation_id = ?",
            (normalise_company(company_key), operation_id),
        ).fetchone()
        return "" if row is None or row[0] is None else str(row[0])

    def mark_operation_reversed(
        self, company_key: str, operation_id: str, at: str
    ) -> bool:
        """Record that this id's voucher was undone. The id STAYS SPENT.

        Marking, never releasing. Releasing it would recreate defect I1 exactly:
        the reason a reversed id must not be written again is that the two
        vouchers would share one identity, and that is as true after the
        reversal is recorded as it was before.

        Returns whether a row changed, so a second reversal of the same id is
        distinguishable from the first rather than silently identical.
        """
        with self._db:
            changed = self._db.execute(
                "UPDATE operation SET reversed_at = ? "
                "WHERE company_key = ? AND operation_id = ? AND reversed_at IS NULL",
                (at, normalise_company(company_key), operation_id),
            ).rowcount
        return bool(changed)

    # ---- introspection, so the scoping rule is checked rather than trusted --

    def table_names(self) -> tuple[str, ...]:
        return tuple(str(row[0]) for row in self._db.execute(_TABLE_NAMES).fetchall())

    def columns_of(self, table: str) -> tuple[str, ...]:
        rows = self._db.execute(_COLUMNS, (table,)).fetchall()
        return tuple(str(row[0]) for row in rows)

    def primary_key_of(self, table: str) -> tuple[str, ...]:
        rows = self._db.execute(_PRIMARY_KEY, (table,)).fetchall()
        return tuple(str(row[0]) for row in rows)

    # ---- reads -------------------------------------------------------------

    def state(self, company_key: str) -> BootstrapReport | None:
        """The last recorded bootstrap for this company, or None if none ran."""
        row = self._db.execute(_COMPANY_SELECT, (company_key,)).fetchone()
        if row is None:
            return None
        display_name = str(row[0])
        return BootstrapReport(
            identity=CompanyIdentity(
                name=display_name, key=normalise_company(display_name)
            ),
            status=BootstrapStatus(str(row[1])),
            detail=str(row[2]),
            attempted_at=str(row[3]),
            bootstrapped_at=str(row[4]),
            steps=_ids_from(str(row[5])),
            counts=BootstrapCounts(
                vouchers=int(str(row[6])),
                vendors=int(str(row[7])),
                accounts=int(str(row[8])),
                mappings=int(str(row[9])),
                conflicts=int(str(row[10])),
                unusable=int(str(row[11])),
            ),
        )

    def chart(self, company_key: str) -> tuple[str, ...]:
        rows = self._db.execute(_CHART_SELECT, (company_key,)).fetchall()
        return tuple(str(row[0]) for row in rows)

    def record_action(self, entry: ActionLog) -> None:
        """Append one decision. There is no update and no delete.

        Deliberately absent from `_DELETES`, so `forget()` — which runs on
        every rebuild — cannot erase it. Re-reading a company's index is a
        statement about our memory; what we already did to their books is a
        different fact and stays true regardless.

        `INSERT`, never `INSERT OR REPLACE`. The four lookup tables upsert —
        `vendor_account` and `phrase_account` deliberately collapse a repeated
        observation into a higher count — and this one must not, because two
        identical decisions are two things that happened. The table has no
        primary key precisely so SQLite cannot collapse them either.

        `actor` and `previous_state` are stored as NULL when they say
        `NOT_RECORDED`, so "we did not record this" has exactly one
        representation in the file, the same one a row written before those
        columns existed already has.
        """
        with self._db:
            self._db.execute(
                _ACTION_INSERT,
                (
                    entry.company_key,
                    entry.ts.isoformat(),
                    entry.action,
                    entry.outcome,
                    entry.reason,
                    entry.run_id,
                    entry.backend,
                    entry.operation_id,
                    entry.voucher_id,
                    entry.vendor_id,
                    entry.detail,
                    _unrecorded_as_null(entry.actor),
                    _unrecorded_as_null(entry.previous_state),
                    entry.batch_id,
                    _unrecorded_as_null(entry.tenant_id),
                    _unrecorded_as_null(entry.user_id),
                ),
            )

    def actions(self, company: str) -> tuple[ActionLog, ...]:
        """This company's decisions, oldest first. Never anybody else's.

        Ordered by `rowid`, not by timestamp: two decisions inside one clock
        tick would otherwise be unorderable, and their order is precisely what
        a trail is asked for.
        """
        key = normalise_company(company)
        rows = self._db.execute(_ACTION_SELECT, (key,)).fetchall()
        return tuple(
            ActionLog(
                company_key=str(row[0]),
                ts=datetime.datetime.fromisoformat(str(row[1])),
                action=str(row[2]),
                outcome=str(row[3]),
                reason=str(row[4]),
                run_id=str(row[5]),
                backend=str(row[6]),
                operation_id=str(row[7]),
                voucher_id=str(row[8]),
                vendor_id=str(row[9]),
                detail=str(row[10]),
                actor=_null_as_unrecorded(row[11]),
                previous_state=_null_as_unrecorded(row[12]),
                batch_id="" if row[13] is None else str(row[13]),
                tenant_id=_null_as_unrecorded(row[14]),
                user_id=_null_as_unrecorded(row[15]),
            )
            for row in rows
        )

    def vendors(self, company_key: str) -> tuple[Observation, ...]:
        return self._all(_VENDOR, company_key)

    def phrases(self, company_key: str) -> tuple[Observation, ...]:
        return self._all(_PHRASE, company_key)

    def vendor(self, company_key: str, subject: str) -> tuple[Observation, ...]:
        return self._one(_VENDOR, company_key, subject)

    def phrase(self, company_key: str, subject: str) -> tuple[Observation, ...]:
        return self._one(_PHRASE, company_key, subject)

    def _all(self, table: _Table, company_key: str) -> tuple[Observation, ...]:
        rows = self._db.execute(table.select_all, (company_key,)).fetchall()
        return tuple(_row_to_observation(company_key, row) for row in rows)

    def _one(
        self, table: _Table, company_key: str, subject: str
    ) -> tuple[Observation, ...]:
        rows = self._db.execute(table.select_one, (company_key, subject)).fetchall()
        return tuple(_row_to_observation(company_key, row) for row in rows)

    # ---- writes ------------------------------------------------------------

    def forget(self, company_key: str) -> None:
        """Drop everything this company knows. A rebuild starts from nothing,
        so a half-loaded index can never be mistaken for a whole one."""
        with self._db:
            for statement in _DELETES:
                self._db.execute(statement, (company_key,))

    def save_bootstrap(
        self,
        report: BootstrapReport,
        *,
        chart: Sequence[str] = (),
        vendors: Sequence[Observation] = (),
        phrases: Sequence[Observation] = (),
    ) -> None:
        """Replace this company's memory with exactly what was just loaded.

        Refuses any observation carrying a different company's key. That is the
        structural half of "no pooled mappings" — the other half is that every
        primary key starts with `company_key`.
        """
        key = report.identity.key
        for o in (*vendors, *phrases):
            if o.company_key != key:
                raise ValueError(
                    f"refusing to store {o.subject!r} from company "
                    f"{o.company_key!r} under {key!r}"
                )
        with self._db:
            for statement in _DELETES:
                self._db.execute(statement, (key,))
            self._db.executemany(_CHART_INSERT, [(key, a) for a in chart])
            self._db.executemany(
                _VENDOR.upsert, [_observation_params(o) for o in vendors]
            )
            self._db.executemany(
                _PHRASE.upsert, [_observation_params(o) for o in phrases]
            )
            self._db.execute(_COMPANY_UPSERT, _company_params(report))

    def record_vendor(
        self,
        company_key: str,
        subject: str,
        account: str,
        *,
        source_voucher_id: str = "",
        provenance: str,
        raw_subject: str | None = None,
    ) -> Observation:
        return self._record(
            _VENDOR,
            company_key,
            subject,
            account,
            source_voucher_id,
            provenance,
            raw_subject,
        )

    def record_phrase(
        self,
        company_key: str,
        subject: str,
        account: str,
        *,
        source_voucher_id: str = "",
        provenance: str,
    ) -> Observation:
        return self._record(
            _PHRASE, company_key, subject, account, source_voucher_id, provenance
        )

    def _record(
        self,
        table: _Table,
        company_key: str,
        subject: str,
        account: str,
        source_voucher_id: str,
        provenance: str,
        raw_subject: str | None = None,
    ) -> Observation:
        existing = self._one(table, company_key, subject)
        seen = [o for o in existing if o.account == account]
        times = seen[0].times + 1 if seen else 1
        ids = list(seen[0].source_voucher_ids) if seen else []
        if source_voucher_id and source_voucher_id not in ids:
            ids.append(source_voucher_id)
        # Evidence is only ever GAINED. A row that already knows the name it
        # was written under does not lose it to a later call that does not.
        kept_raw = raw_subject if raw_subject is not None else None
        if kept_raw is None and seen:
            kept_raw = seen[0].raw_subject
        updated = Observation(
            company_key=company_key,
            subject=subject,
            account=account,
            times=times,
            source_voucher_ids=tuple(ids),
            provenance=provenance,
            raw_subject=kept_raw,
        )
        with self._db:
            self._db.execute(table.upsert, _observation_params(updated))
        return updated


def _company_params(
    report: BootstrapReport,
) -> tuple[str, str, str, str, str, str, str, int, int, int, int, int, int]:
    c = report.counts
    return (
        report.identity.key,
        report.identity.name,
        report.status.value,
        report.detail,
        report.attempted_at,
        report.bootstrapped_at,
        _ids_to(report.steps),
        c.vouchers,
        c.vendors,
        c.accounts,
        c.mappings,
        c.conflicts,
        c.unusable,
    )
