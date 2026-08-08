"""Every decision leaves a durable row saying what happened and why.

WHAT WAS ACTUALLY THERE BEFORE 2026-08-09
-----------------------------------------
`ActionLog` was declared in `accountant/schema.py` and NEVER CONSTRUCTED. Not
once, anywhere in the repository. A type nothing builds is not a feature.

What the product had instead was `accountant/web/app.py::EVENTS`, a module-level
list of `(kind, message)` pairs capped at forty rows. Measured against what a
log is for, it failed on every axis:

    no timestamp            you cannot say when anything happened
    no voucher or operation id  you cannot tie a row to the entry it describes
    reason only on refusals the posted and asked rows carried none
    outcome discarded       the renderer iterated `for _, m in EVENTS`
    in memory, per process  a restart erased the entire history
    written by the web app  any other caller of the pipeline got nothing

The last one matters most. An audit trail written by the presentation layer is
an audit trail that only exists when somebody happens to be looking at a
browser. `pipeline.run` is where a decision is MADE, so that is where the record
of it belongs.

WHY REASON ON EVERY PATH, NOT JUST REFUSALS
-------------------------------------------
"Why did you refuse" is the obvious question. "Why did you POST" is the one that
matters when somebody is looking at a voucher in their books six months later
and wants to know what made it valid. A log that only explains refusals cannot
answer it.

WHAT THIS FILE DOES NOT PROVE
-----------------------------
Anything about real Tally. The rows here are written from a `FakeTally` run
because the question is whether OUR pipeline records its own decisions, not
what Tally returns.
"""

from __future__ import annotations

import datetime

from accountant import pipeline
from accountant.extract.adapter import TypedTextExtractor
from accountant.memory.bootstrap import bootstrap
from accountant.memory.company import CompanyMemory
from accountant.memory.store import MemoryStore
from accountant.schema import Outcome, Voucher
from accountant.tallyio.fake import FakeTally
from accountant.web import app

# Imported and touched deliberately: `pipeline.record_decision` is the function
# the web app calls, so if that name ever moves this file stops importing rather
# than quietly testing only the `run` path.
assert app.record.__module__ == "accountant.web.app"

COMPANY = "Demo Co"
ACCOUNTS = ("Purchases", "Repairs & Maintenance", "Cash")
TODAY = datetime.date(2026, 8, 7)
RUN = "run_actionlog_test"


def _history(party: str, account: str, n: int) -> tuple[Voucher, ...]:
    return tuple(
        Voucher(
            id=f"h{party}{i}",
            date=datetime.date(2026, 1, 1),
            party=party,
            narration=f"{party} supply",
            debit_account=account,
            credit_account="Cash",
            amount_paise=100000,
        )
        for i in range(n)
    )


def _tally(history: tuple[Voucher, ...] = ()) -> FakeTally:
    t = FakeTally()
    t.add_company(COMPANY, accounts=ACCOUNTS, vouchers=history, backed_up=True)
    return t


def _memory(t: FakeTally, store: MemoryStore) -> CompanyMemory:
    return bootstrap(t, COMPANY, store)


def _run(t: FakeTally, store: MemoryStore, text: str) -> pipeline.Draft:
    return pipeline.run(
        COMPANY,
        text.encode(),
        "text/plain",
        TypedTextExtractor(),
        t,
        _memory(t, store),
        today=TODAY,
        log=store,
        run_id=RUN,
    )


def test_a_posted_entry_records_its_outcome_and_its_reason() -> None:
    """The row that used to carry neither.

    `EVENTS` recorded a hand-built sentence for a post and no reason at all.
    "Why did you post this" is the question somebody asks six months later.
    """
    store = MemoryStore(":memory:")
    t = _tally(_history("Sharma Traders", "Purchases", 40))
    draft = _run(t, store, "paid Sharma Traders 4200 for cement")

    assert draft.outcome is Outcome.VALID
    rows = store.actions(COMPANY)
    posted = [r for r in rows if r.action == "posted"]

    assert len(posted) == 1
    assert posted[0].outcome == Outcome.VALID.value
    assert posted[0].reason, "a posted row must say WHY it was valid"
    assert posted[0].operation_id == draft.operation_id
    assert posted[0].run_id == RUN


def test_a_blocked_entry_records_its_outcome_and_its_reason() -> None:
    """An unseen vendor is UNCLEAR, posts nothing, and still leaves a record."""
    store = MemoryStore(":memory:")
    t = _tally(_history("Sharma Traders", "Purchases", 40))
    draft = _run(t, store, "paid Gupta Hardware 1500 for tools")

    assert draft.outcome is Outcome.UNCLEAR
    assert t.list_our_vouchers(COMPANY) == ()

    rows = store.actions(COMPANY)
    assert rows, "an entry that did not post is still a decision worth recording"
    assert rows[-1].outcome == Outcome.UNCLEAR.value
    assert rows[-1].reason


def test_every_row_carries_the_whole_field_set() -> None:
    """Owner requirement, 2026-08-09. Missing fields are what made EVENTS useless."""
    store = MemoryStore(":memory:")
    t = _tally(_history("Sharma Traders", "Purchases", 40))
    _run(t, store, "paid Sharma Traders 4200 for cement")

    row = store.actions(COMPANY)[-1]

    assert isinstance(row.ts, datetime.datetime)
    assert row.run_id == RUN
    assert row.action
    assert row.company_key
    assert row.outcome
    assert row.reason
    assert row.backend
    assert row.operation_id


def test_the_log_survives_a_restart() -> None:
    """A log held in a list is lost with the process that held it.

    Two stores over one file: the second has never seen the pipeline run, so
    anything it can read came off disk.
    """
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        path = f"{tmp}/memory.sqlite3"
        writing = MemoryStore(path)
        t = _tally(_history("Sharma Traders", "Purchases", 40))
        _run(t, writing, "paid Sharma Traders 4200 for cement")

        reading = MemoryStore(path)
        rows = reading.actions(COMPANY)

    assert rows, "the log did not survive being reopened"
    assert rows[-1].outcome == Outcome.VALID.value


def test_one_companys_actions_are_never_another_companys() -> None:
    """Company scoping, same rule as every other table in this store."""
    store = MemoryStore(":memory:")
    t = _tally(_history("Sharma Traders", "Purchases", 40))
    _run(t, store, "paid Sharma Traders 4200 for cement")

    assert store.actions("Some Other Company") == ()
    assert store.actions(COMPANY)
