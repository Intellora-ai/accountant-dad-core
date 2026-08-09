"""READY means we learned something. It does not mean the reads succeeded.

THE DISTINCTION THIS FILE EXISTS FOR
------------------------------------
Three situations look similar from outside and must never share a status:

    we read their books and learned mappings   -> READY, may propose
    we read their books, there was nothing     -> EMPTY_SOURCE, may ask
    we read their books and learned nothing    -> EMPTY_VENDOR_INDEX, do nothing

The middle one is a legitimate new customer. The last one means an existing
company's history went in and no usable mapping came out, which is a signal
that something is wrong with the read, the data or the derivation — and the one
thing we must not do is behave as though we understand their books.

Before 2026-08-09 `bootstrap` returned READY whenever identity, accounts and
vouchers were read, with no reference to `counts`. A company with forty
vouchers whose every row was unusable was therefore READY with zero mappings.
`docs/PROJECT_STATE.md:126` already recorded this as
"PRODUCT INVARIANT - NOT YET ENFORCED", and `ARCHITECTURE.md:1073`'s checkbox
was unticked.

Owner decision, 2026-08-09: EMPTY_SOURCE "may ask onboarding questions, but it
must not propose or write a voucher until the required vendor/account mapping
exists and is measured. It must not be silently converted to READY."

WHAT THIS FILE DOES NOT PROVE
-----------------------------
Anything about a real Tally. It uses `FakeTally` deliberately, because the
question here is what our own state machine does with counts it has already
read — not what Tally returns. Live evidence lives in `docs/PROJECT_STATE.md`
§21 and may never come from this file.
"""

from __future__ import annotations

import datetime

import pytest

from accountant.memory.bootstrap import bootstrap
from accountant.memory.company import (
    CompanyMatchStatus,
    MemoryNotReady,
    propose_account,
)
from accountant.memory.store import BootstrapStatus, MemoryStore
from accountant.schema import Voucher
from accountant.tallyio.fake import FakeTally

ACCOUNTS = ("Purchases", "Cash")
COMPANY = "Nagpur Hardware Stores"


def _voucher(n: int, party: str, debit: str) -> Voucher:
    return Voucher(
        id=f"h{n}",
        date=datetime.date(2026, 4, 1),
        party=party,
        narration="cement supply",
        debit_account=debit,
        credit_account="Cash",
        amount_paise=118000,
    )


def _tally(vouchers: tuple[Voucher, ...]) -> FakeTally:
    t = FakeTally()
    t.add_company(COMPANY, accounts=ACCOUNTS, vouchers=vouchers, backed_up=True)
    return t


def test_an_existing_company_that_yields_no_mappings_is_a_bootstrap_failure() -> None:
    """Forty vouchers in, zero mappings out. Something is wrong; do nothing.

    Every row here has a blank party, which `_derive` counts as unusable
    (`bootstrap.py`). The reads all succeeded, so the pre-2026-08-09 code called
    this READY — a company we understand nothing about, advertised as safe to
    serve.
    """
    history = tuple(_voucher(n, "", "Purchases") for n in range(40))
    memory = bootstrap(_tally(history), COMPANY, MemoryStore(":memory:"))

    assert memory.report.counts.vouchers == 40
    assert memory.report.counts.mappings == 0
    assert memory.report.status is BootstrapStatus.EMPTY_VENDOR_INDEX
    assert not memory.ready


def test_a_bootstrap_failure_proposes_nothing_at_all() -> None:
    """Not a quieter answer — no answer. `propose_account` must refuse."""
    history = tuple(_voucher(n, "", "Purchases") for n in range(40))
    memory = bootstrap(_tally(history), COMPANY, MemoryStore(":memory:"))

    with pytest.raises(MemoryNotReady):
        propose_account(memory, "Sharma Traders")

    assert memory.lookup("Sharma Traders").status is (
        CompanyMatchStatus.MEMORY_NOT_READY
    )


def test_a_genuinely_empty_company_is_empty_source_and_never_ready() -> None:
    """No history is not a failure. It is a new customer, and it is honest.

    Owner decision 2026-08-09: this state may ask onboarding questions but must
    never be converted to READY.
    """
    memory = bootstrap(_tally(()), COMPANY, MemoryStore(":memory:"))

    assert memory.report.counts.vouchers == 0
    assert memory.report.status is BootstrapStatus.EMPTY_SOURCE
    assert not memory.ready


def test_an_empty_source_company_asks_but_never_proposes() -> None:
    """The difference from a failure: a question is allowed, a guess is not.

    A failure means we cannot trust what we read. EMPTY_SOURCE means there was
    nothing to read, which is a fact about the customer and not about us — so
    the person can still be asked, and the answer still cannot be invented.
    """
    memory = bootstrap(_tally(()), COMPANY, MemoryStore(":memory:"))

    assert propose_account(memory, "Sharma Traders") is None
    assert memory.lookup("Sharma Traders").status is CompanyMatchStatus.NO_MATCH


def test_a_company_whose_history_yields_mappings_is_ready() -> None:
    """The counter-test. Without it, returning failure always would also pass."""
    history = tuple(_voucher(n, "Sharma Traders", "Purchases") for n in range(4))
    memory = bootstrap(_tally(history), COMPANY, MemoryStore(":memory:"))

    assert memory.report.counts.mappings > 0
    assert memory.report.status is BootstrapStatus.READY
    assert memory.ready
    assert propose_account(memory, "Sharma Traders") == "Purchases"
