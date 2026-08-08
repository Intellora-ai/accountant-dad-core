"""Child 2 — company-scoped memory, with a mandatory bootstrap.

THE MEASURED FACT
-----------------
`accountant/ingest/crossorg.py`, real UK central-government spend, 16,011 rows,
30 ordered department pairs:

    within the same department   53.08% at best
    across departments            0.00% on 29 of the 30 pairs

Vendor -> account mappings do not transfer between organisations. Every
customer is a permanent cold start, and a pooled or shared model is wasted
effort. Measured, not assumed.

THE OPPOSITE MISTAKE, WHICH IS JUST AS BAD
------------------------------------------
Connecting to an EXISTING person's Tally company and behaving like a fresh
start. Their history is right there. It has to be read, counted and used before
the first automatic proposal — which is what `bootstrap` is for, and why
nothing here answers a question until it has run.

THE FOUR ANSWERS
----------------
    MATCH             one consistent company-local mapping -> may propose
    CONFLICTED        contradictory company-local history  -> ask, never pick
    NO_MATCH          no company-local history             -> ask
    MEMORY_NOT_READY  no successful bootstrap              -> do nothing

MEMORY_NOT_READY is never treated as NO_MATCH. There is no global fallback, no
pooled prior and no default account anywhere in this package.

MODULES
-------
    identity.py   the company key every record and every lookup carries
    index.py      the unscoped in-memory index, and the normalisers
    store.py      SQLite: our derived context, never the customer's books
    bootstrap.py  the mandatory read, the failure report, and the one-company
                  session
    company.py    the lookup surface, corrections, and the only proposal path

No model call and no network call happens in this package. A test asserts it
by reading the source rather than trusting the sentence.
"""

from __future__ import annotations

from accountant.memory.bootstrap import STEPS, MemorySession, bootstrap, resume
from accountant.memory.company import (
    FROM_HUMAN_ANSWER,
    FROM_OUR_POSTING,
    FROM_TALLY_HISTORY,
    CompanyMatch,
    CompanyMatchStatus,
    CompanyMemory,
    MemoryNotReady,
    propose_account,
)
from accountant.memory.identity import CompanyIdentity, normalise_company
from accountant.memory.index import MemoryIndex, normalise_phrase, normalise_vendor
from accountant.memory.store import (
    IN_MEMORY,
    SCHEMA,
    BootstrapCounts,
    BootstrapReport,
    BootstrapStatus,
    MemoryStore,
    Observation,
)

__all__ = [
    "FROM_HUMAN_ANSWER",
    "FROM_OUR_POSTING",
    "FROM_TALLY_HISTORY",
    "IN_MEMORY",
    "SCHEMA",
    "STEPS",
    "BootstrapCounts",
    "BootstrapReport",
    "BootstrapStatus",
    "CompanyIdentity",
    "CompanyMatch",
    "CompanyMatchStatus",
    "CompanyMemory",
    "MemoryIndex",
    "MemoryNotReady",
    "MemorySession",
    "MemoryStore",
    "Observation",
    "bootstrap",
    "normalise_company",
    "normalise_phrase",
    "normalise_vendor",
    "propose_account",
    "resume",
]
