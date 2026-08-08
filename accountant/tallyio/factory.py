"""The one place a runtime TallyClient is constructed.

WHY THIS MODULE EXISTS
----------------------
Owner decision, 2026-08-09: `RealTally` is the only runtime backend. `FakeTally`
stays for isolated unit tests, but it must never be reachable from the live web
app and its results must never be presented as Phase 3 evidence.

Before this module, `accountant/web/app.py` imported `FakeTally` directly and
built its own client. Backend identity was therefore a property of whichever
module happened to construct a client, which means it could differ between call
sites and could not be logged, gated or asserted in one place. Here it is one
function, so "which Tally are we talking to" has exactly one answer.

FAIL CLOSED, ALWAYS
-------------------
If Tally is unavailable, unlicensed, disconnected, misidentified, or the company
identity is uncertain, this raises `RealTallyRequired`. It NEVER falls back to a
fake. A fallback would turn "your books are unreachable" into "your books are
empty", and those are opposite facts: the first must stop the system, the second
is a legitimate state a new customer is in.

The check here is READ-ONLY. It lists companies and confirms the intended one is
open. Nothing is written, so a misconfigured startup cannot touch anyone's
books.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from accountant.tallyio.client import TallyClient
from accountant.tallyio.real import (
    RealTally,
    RecordedBackups,
    TallyConfig,
    TallyError,
)


class RealTallyRequired(RuntimeError):
    """Tally could not be reached or identified, so nothing may happen.

    Deliberately not a subclass of `TallyError`: that hierarchy describes
    things Tally told us, and this describes our refusal to proceed without it.
    """


@dataclass(frozen=True)
class BackendIdentity:
    """What we are actually connected to, measured rather than assumed.

    Every field is read off the live connection at startup. `backend` is a
    literal string rather than a computed class name so that a report carrying
    it cannot silently start saying "FakeTally" if wiring changes.
    """

    backend: str
    endpoint: str
    company: str
    company_exists: bool
    companies_visible: int
    run_id: str

    def as_metrics(self) -> dict[str, object]:
        """The measurement-contract rows this identity is responsible for."""
        return {
            "backend": self.backend,
            "endpoint": self.endpoint,
            "company_identifier": self.company,
            "company_exists": self.company_exists,
            "companies_visible": self.companies_visible,
            "run_id": self.run_id,
        }


def new_run_id() -> str:
    """One id per process start, stamped on every metric and action-log row."""
    return f"run_{uuid.uuid4().hex}"


def real_tally(
    config: TallyConfig,
    company: str,
    *,
    backups: RecordedBackups | None = None,
    run_id: str | None = None,
) -> tuple[TallyClient, BackendIdentity]:
    """Connect to the real Tally, or refuse. There is no third outcome.

    Returns the client as a `TallyClient`, not as a `RealTally`, so callers
    depend on the interface and cannot reach past it into transport details.

    `backups` defaults to `RecordedBackups()` — an EMPTY set, which refuses
    every write. That is deliberate: a caller that has not recorded a backup for
    a company has not earned the right to write to it, and the safe default is
    the one that does nothing.
    """
    identifier = run_id or new_run_id()
    client = RealTally(config, backups=backups or RecordedBackups())

    try:
        companies = client.list_companies()
    except TallyError as exc:
        raise RealTallyRequired(
            f"REAL TALLY REQUIRED: no operation performed. "
            f"{config.url} did not answer: {type(exc).__name__}: {exc}"
        ) from exc
    except OSError as exc:  # socket-level: refused, unreachable, timed out
        raise RealTallyRequired(
            f"REAL TALLY REQUIRED: no operation performed. "
            f"could not reach {config.url}: {type(exc).__name__}: {exc}"
        ) from exc

    if company not in companies:
        raise RealTallyRequired(
            f"REAL TALLY REQUIRED: no operation performed. "
            f"{company!r} is not open in Tally at {config.url}; "
            f"{len(companies)} company/companies are: {list(companies)}. "
            "Company identity is uncertain, so nothing is read or written."
        )

    return client, BackendIdentity(
        backend="RealTally",
        endpoint=config.url,
        company=company,
        company_exists=True,
        companies_visible=len(companies),
        run_id=identifier,
    )
