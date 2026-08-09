"""The vertical slice, end to end, against the fake Tally.

Slice 1: typed entry -> draft -> memory -> decision -> post -> read back -> reverse
Slice 2: unknown vendor -> question -> answer -> re-evaluate -> post
Slice 3: operation ID, idempotency, exact reversal
Slice 4: vendor_switch detector blocks the post
Slice 5: stub extractor drives the same flow

EVERY TEST HERE BOOTSTRAPS COMPANY-SCOPED MEMORY FIRST
------------------------------------------------------
The pipeline no longer builds an index of its own, and no longer accepts an
unscoped `MemoryIndex`. It takes a `CompanyMemory` that has already been read
out of THIS company's Tally, and refuses one belonging to anybody else.

So vendor history is seeded into the FakeTally company and picked up by
`bootstrap`, rather than poked into an index by hand. That is the difference
between a test that exercises the real path and a test that exercises a fixture
which happens to resemble it.

WHAT THIS FILE DOES NOT PROVE
-----------------------------
Every test here runs against FakeTally. None of it proves real TallyPrime
behaves the same way. In particular the read-back failure below is SIMULATED by
a wrapper that returns None on demand; it is not evidence that real Tally ever
loses a write, only that we handle it if it does.

`test_posting_a_not_valid_draft_is_refused` is named for an outcome it does not
produce. Measured 2026-08-09: the draft it builds evaluates to UNCLEAR, not
NOT_VALID. It still proves the gate refuses a non-Valid draft, which is what it
asserts, but it does not prove anything specific to NOT_VALID. The NOT_VALID
path is covered by `test_a_handed_over_entry_is_never_posted` in
`tests/test_questions.py`. The name is left alone here because renaming it is
outside this change.
"""

from __future__ import annotations

import datetime

import pytest

from accountant import pipeline
from accountant.extract.adapter import (
    StubExtractor,
    TypedTextExtractor,
    UnavailableExtractor,
)
from accountant.memory.bootstrap import bootstrap
from accountant.memory.company import CompanyMemory
from accountant.memory.store import BootstrapStatus, MemoryStore
from accountant.schema import Outcome, Voucher
from accountant.tallyio.client import WriteResult
from accountant.tallyio.fake import FakeTally

COMPANY = "Demo Co"
ACCOUNTS = ("Purchases", "Sundry Expenses", "Repairs & Maintenance", "Cash")
TODAY = datetime.date(2026, 8, 7)


def past(party: str, account: str, amount: int = 100000, n: int = 1) -> list[Voucher]:
    return [
        Voucher(
            id=f"hist-{party}-{account}-{i}",
            date=datetime.date(2026, 1, 1),
            party=party,
            narration=f"{party} purchase",
            debit_account=account,
            credit_account="Cash",
            amount_paise=amount,
        )
        for i in range(n)
    ]


def tally(history: list[Voucher] | None = None) -> FakeTally:
    t = FakeTally()
    t.add_company(
        COMPANY, accounts=ACCOUNTS, vouchers=tuple(history or []), backed_up=True
    )
    return t


def memory_for(t: FakeTally, company: str = COMPANY) -> CompanyMemory:
    """This company's own memory, read out of this company's own Tally.

    A fresh store per call. A test that shares a store with another test is a
    test that can pass for the wrong reason.
    """
    return bootstrap(t, company, MemoryStore(":memory:"))


def typed(text: str) -> bytes:
    return text.encode()


# ---- Slice 1: typed happy path ---------------------------------------------


def test_known_vendor_posts_without_asking():
    t = tally(past("Sharma Traders", "Purchases", n=40))
    d = pipeline.run(
        COMPANY,
        typed("paid Sharma Traders 4200 for cement"),
        "text/plain",
        TypedTextExtractor(),
        t,
        memory_for(t),
        today=TODAY,
    )
    assert d.outcome is Outcome.VALID
    assert d.posted_tally_id is not None
    assert d.voucher.debit_account == "Purchases"


def test_posted_voucher_is_readable_back_from_tally():
    t = tally(past("Sharma Traders", "Purchases", n=5))
    d = pipeline.run(
        COMPANY,
        typed("paid Sharma Traders 4200 cement"),
        "text/plain",
        TypedTextExtractor(),
        t,
        memory_for(t),
        today=TODAY,
    )
    assert t.read_by_operation_id(COMPANY, d.operation_id) is not None


def test_amount_lands_in_tally_as_integer_paise():
    t = tally(past("Sharma Traders", "Purchases", n=5))
    d = pipeline.run(
        COMPANY,
        typed("paid Sharma Traders 4200 cement"),
        "text/plain",
        TypedTextExtractor(),
        t,
        memory_for(t),
        today=TODAY,
    )
    assert d.voucher.amount_paise == 420000
    assert isinstance(d.voucher.amount_paise, int)


def test_every_field_carries_provenance():
    t = tally(past("Sharma Traders", "Purchases", n=5))
    d = pipeline.run(
        COMPANY,
        typed("paid Sharma Traders 4200 cement"),
        "text/plain",
        TypedTextExtractor(),
        t,
        memory_for(t),
        today=TODAY,
    )
    assert set(d.provenance) >= {"date", "party", "total_paise", "tax_paise"}


def test_reverse_restores_the_exact_trial_balance():
    t = tally(past("Sharma Traders", "Purchases", n=5))
    before = t.trial_balance(COMPANY)
    d = pipeline.run(
        COMPANY,
        typed("paid Sharma Traders 4200 cement"),
        "text/plain",
        TypedTextExtractor(),
        t,
        memory_for(t),
        today=TODAY,
    )
    assert t.trial_balance(COMPANY) != before
    assert pipeline.reverse(d, t) is True
    assert t.trial_balance(COMPANY) == before


# ---- Slice 2: unknown vendor asks, never guesses ---------------------------


def test_unknown_vendor_is_unclear_and_does_not_post():
    t = tally(past("Sharma Traders", "Purchases", n=5))
    d = pipeline.run(
        COMPANY,
        typed("paid Verma Cement 900 for bags"),
        "text/plain",
        TypedTextExtractor(),
        t,
        memory_for(t),
        today=TODAY,
    )
    assert d.outcome is Outcome.UNCLEAR
    assert d.posted_tally_id is None
    assert len(t.list_our_vouchers(COMPANY)) == 0


def test_conflicted_vendor_asks_and_offers_only_accounts_seen_before():
    hist = past("Verma Cement", "Purchases", n=3) + past(
        "Verma Cement", "Repairs & Maintenance", n=2
    )
    t = tally(hist)
    d = pipeline.run(
        COMPANY,
        typed("paid Verma Cement 900 bags"),
        "text/plain",
        TypedTextExtractor(),
        t,
        memory_for(t),
        today=TODAY,
    )
    assert d.outcome is Outcome.UNCLEAR
    # plus a "something else" escape so nobody gets stuck
    options = set(d.decision.question_options if d.decision else ())
    assert options >= {"Purchases", "Repairs & Maintenance"}


def test_answering_then_re_evaluating_posts():
    t = tally(past("Sharma Traders", "Purchases", n=5))
    accounts = t.read_accounts(COMPANY)
    history = t.read_vouchers(COMPANY)
    memory = memory_for(t)

    d = pipeline.build_draft(
        COMPANY,
        typed("paid Verma Cement 900 bags"),
        "text/plain",
        TypedTextExtractor(),
        memory,
        today=TODAY,
    )
    d = pipeline.evaluate(d, accounts, history, memory)
    assert d.outcome is Outcome.UNCLEAR

    d = pipeline.answer(d, "Purchases")
    memory.record_correction("Verma Cement", "Purchases")
    d = pipeline.evaluate(d, accounts, history, memory)

    # Verma Cement is new to this company, so its funding leg is unknown too.
    # Until 2026-08-09 it was filled in by `_default_credit` with the string
    # "Cash" and no provenance, which is why one answer used to be enough.
    assert d.outcome is Outcome.UNCLEAR
    q = pipeline.next_question(d)
    assert q is not None and q.problem_id == pipeline.FUNDING_PROBLEM

    d = pipeline.answer(d, "Cash", problem_id=q.problem_id)
    d = pipeline.evaluate(d, accounts, history, memory)

    assert d.outcome is Outcome.VALID
    assert (d.voucher.debit_account, d.voucher.credit_account) == ("Purchases", "Cash")
    d = pipeline.post(d, t)
    assert d.posted_tally_id is not None


def test_an_answer_is_not_permission_to_post():
    """Answering is new information, not consent. A bad answer still does not
    post - it asks again."""
    t = tally(past("Sharma Traders", "Purchases", n=5))
    accounts = t.read_accounts(COMPANY)
    history = t.read_vouchers(COMPANY)
    memory = memory_for(t)

    d = pipeline.build_draft(
        COMPANY,
        typed("paid Verma Cement 900 bags"),
        "text/plain",
        TypedTextExtractor(),
        memory,
        today=TODAY,
    )
    d = pipeline.answer(d, "Not A Real Ledger")
    memory.record_correction("Verma Cement", "Not A Real Ledger")
    d = pipeline.evaluate(d, accounts, history, memory)

    assert d.outcome is not Outcome.VALID
    assert "Not A Real Ledger" in d.reason


def test_answer_is_recorded_as_provenance():
    t = tally([])
    memory = memory_for(t)
    d = pipeline.build_draft(
        COMPANY,
        typed("paid Verma Cement 900 bags"),
        "text/plain",
        TypedTextExtractor(),
        memory,
        today=TODAY,
    )
    d = pipeline.answer(d, "Purchases")
    assert (d.voucher.provenance or {})["debit_account"] == "human_answer"


# ---- the post gate lives server side ---------------------------------------


def test_posting_a_not_valid_draft_is_refused():
    t = tally([])
    accounts = t.read_accounts(COMPANY)
    memory = memory_for(t)
    d = pipeline.build_draft(
        COMPANY,
        typed("paid Nobody 0 for nothing"),
        "text/plain",
        TypedTextExtractor(),
        memory,
        today=TODAY,
    )
    d = pipeline.evaluate(d, accounts, (), memory)
    assert d.outcome is not Outcome.VALID
    with pytest.raises(ValueError):
        pipeline.post(d, t)


def test_an_unclear_draft_is_refused_and_leaves_the_books_byte_identical():
    """The gate refuses UNCLEAR, not just NOT_VALID, and refusing changes nothing.

    Both halves are needed. `pytest.raises` on its own would pass if `post` threw
    after writing, so the trial balance and our own voucher list are compared
    across the call. The balance is asserted non-empty first: comparing two empty
    dicts would be a test that cannot fail.
    """
    t = tally(past("Sharma Traders", "Purchases", n=40))
    accounts = t.read_accounts(COMPANY)
    history = t.read_vouchers(COMPANY)
    memory = memory_for(t)

    # Gupta Hardware appears nowhere in this company's books, so it is unseen.
    d = pipeline.build_draft(
        COMPANY,
        typed("paid Gupta Hardware 1500 for tools"),
        "text/plain",
        TypedTextExtractor(),
        memory,
        today=TODAY,
    )
    d = pipeline.evaluate(d, accounts, history, memory)
    assert d.outcome is Outcome.UNCLEAR

    before_ours = t.list_our_vouchers(COMPANY)
    before_balance = t.trial_balance(COMPANY)
    assert before_balance, "no balance to preserve would make the comparison vacuous"

    with pytest.raises(ValueError):
        pipeline.post(d, t)

    assert t.list_our_vouchers(COMPANY) == before_ours == ()
    assert t.trial_balance(COMPANY) == before_balance
    assert d.posted_tally_id is None


def test_posting_an_unevaluated_draft_is_refused():
    t = tally([])
    memory = memory_for(t)
    d = pipeline.build_draft(
        COMPANY,
        typed("paid Sharma Traders 4200 cement"),
        "text/plain",
        TypedTextExtractor(),
        memory,
        today=TODAY,
    )
    with pytest.raises(ValueError):
        pipeline.post(d, t)


# ---- C6: a write we cannot read back is not a success ----------------------


class LosesTheWriteTally:
    """FakeTally whose write succeeds but whose read-back finds nothing.

    Wraps a real FakeTally and delegates everything except
    `read_by_operation_id`, so the only behavioural difference from the fake is
    the one under test. Reimplementing the client instead would let the test
    pass for reasons unrelated to the read-back branch.

    This is the C6 failure in the form that actually costs money: Tally accepts
    the write and hands back an ID, and the voucher is not there afterwards. An
    HTTP 200 is not proof a statutory entry exists.

    Note the inner fake still sees the write, so `t.list_our_vouchers` grows.
    That is correct - the write really did happen. What must not happen is the
    draft recording it as posted.
    """

    def __init__(self, inner: FakeTally) -> None:
        self.inner = inner
        self.write_calls = 0
        self.readback_asked_for: list[tuple[str, str]] = []

    def list_companies(self) -> tuple[str, ...]:
        return self.inner.list_companies()

    def read_accounts(self, company: str) -> tuple[str, ...]:
        return self.inner.read_accounts(company)

    def read_vouchers(self, company: str) -> tuple[Voucher, ...]:
        return self.inner.read_vouchers(company)

    def trial_balance(self, company: str) -> dict[str, int]:
        return self.inner.trial_balance(company)

    def write_voucher(
        self, company: str, voucher: Voucher, operation_id: str
    ) -> WriteResult:
        self.write_calls += 1
        return self.inner.write_voucher(company, voucher, operation_id)

    def read_by_operation_id(self, company: str, operation_id: str) -> Voucher | None:
        self.readback_asked_for.append((company, operation_id))
        return None

    def reverse_by_operation_id(self, company: str, operation_id: str) -> bool:
        return self.inner.reverse_by_operation_id(company, operation_id)

    def list_our_vouchers(self, company: str) -> tuple[Voucher, ...]:
        return self.inner.list_our_vouchers(company)

    def backed_up(self, company: str) -> bool:
        # The ninth TallyClient method, added 2026-08-09 for G5.2. Delegated
        # like the rest: this double wraps a real FakeTally and has no opinion
        # about backups.
        return self.inner.backed_up(company)


def test_a_write_that_cannot_be_read_back_raises_and_never_records_a_tally_id():
    """C6. The draft must not claim a posted ID it could not confirm.

    `write_calls == 1` is load-bearing: without it this test would also pass if
    `post` raised BEFORE writing, which is a different branch entirely and would
    leave the read-back check unproven.
    """
    t = tally(past("Sharma Traders", "Purchases", n=40))
    accounts = t.read_accounts(COMPANY)
    history = t.read_vouchers(COMPANY)
    memory = memory_for(t)

    d = pipeline.build_draft(
        COMPANY,
        typed("paid Sharma Traders 4200 for cement"),
        "text/plain",
        TypedTextExtractor(),
        memory,
        today=TODAY,
    )
    d = pipeline.evaluate(d, accounts, history, memory)
    assert d.outcome is Outcome.VALID  # the Valid gate is not what stops this one

    blind = LosesTheWriteTally(t)
    with pytest.raises(RuntimeError) as raised:
        pipeline.post(d, blind)

    assert blind.write_calls == 1
    assert blind.readback_asked_for == [(COMPANY, d.operation_id)]
    assert d.operation_id in str(raised.value)
    assert d.posted_tally_id is None


# ---- Slice 4: the detector blocks a post -----------------------------------


def test_vendor_switch_asks_instead_of_posting_and_names_the_evidence():
    hist = past("Sharma Traders", "Purchases", n=40)
    t = tally(hist)
    accounts = t.read_accounts(COMPANY)
    memory = memory_for(t)

    d = pipeline.build_draft(
        COMPANY,
        typed("paid Sharma Traders 4200 cement"),
        "text/plain",
        TypedTextExtractor(),
        memory,
        today=TODAY,
    )
    d = pipeline.answer(d, "Sundry Expenses")  # the accountant slipped
    d = pipeline.evaluate(d, accounts, tuple(hist), memory)

    assert d.outcome is Outcome.UNCLEAR  # asks, never refuses
    assert d.posted_tally_id is None
    assert "40 times" in d.reason
    q = pipeline.next_question(d)
    assert q is not None and q.problem_id == "vendor_switch"


# ---- Slice 5: stub extractor, same flow ------------------------------------


def test_stub_extractor_drives_the_same_pipeline():
    t = tally(past("Sharma Traders", "Purchases", n=5))
    stub = StubExtractor(
        date=TODAY, party="Sharma Traders", total_paise=420000, tax_paise=64068
    )
    d = pipeline.run(
        COMPANY, b"<pretend pdf>", "application/pdf", stub, t, memory_for(t)
    )
    assert d.outcome is Outcome.VALID
    assert d.voucher.amount_paise == 420000
    assert d.record.backend == "stub"


def test_backend_outage_asks_the_person_to_type_instead():
    """#15.7 - the system continues, the person types instead."""
    t = tally(past("Sharma Traders", "Purchases", n=5))
    d = pipeline.run(
        COMPANY,
        b"<pdf>",
        "application/pdf",
        UnavailableExtractor("provider timed out"),
        t,
        memory_for(t),
    )
    assert d.outcome is not Outcome.VALID
    assert d.posted_tally_id is None
    assert all("not_found" in v for v in d.provenance.values())


def test_swapping_the_backend_changes_no_pipeline_code():
    """#15.5 - two backends, identical call path."""
    t1 = tally(past("Sharma Traders", "Purchases", n=5))
    t2 = tally(past("Sharma Traders", "Purchases", n=5))
    a = pipeline.run(
        COMPANY,
        typed("paid Sharma Traders 4200 cement"),
        "text/plain",
        TypedTextExtractor(),
        t1,
        memory_for(t1),
        today=TODAY,
    )
    b = pipeline.run(
        COMPANY,
        b"x",
        "application/pdf",
        StubExtractor(
            date=TODAY, party="Sharma Traders", total_paise=420000, tax_paise=None
        ),
        t2,
        memory_for(t2),
    )
    assert a.outcome is b.outcome is Outcome.VALID


# ---- cold start is safe by construction ------------------------------------


def test_a_brand_new_company_never_posts_silently():
    """A company whose books we HAVE read, and which says nothing about any of
    these vendors. Every vendor is unseen, so every entry asks.

    Note this is NOT the same as memory that was never bootstrapped: this
    company's history was read and found empty, which is a fact about their
    books. `tests/test_pipeline_isolation.py` covers the other one, where the
    fact is about us.

    That distinction was prose here before it was a state. Since 2026-08-09 it
    has a name: EMPTY_SOURCE — read honestly, askable, never proposes. This
    line asserted `ready is True` until then, which claimed we had learned
    something from books that were empty.
    """
    t = tally([])
    memory = memory_for(t)
    assert memory.report.status is BootstrapStatus.EMPTY_SOURCE
    assert memory.ready is False
    assert memory.report.askable is True
    for text in (
        "paid Sharma Traders 4200 cement",
        "paid Verma Cement 900 bags",
        "paid Gupta Hardware 1500 tools",
    ):
        d = pipeline.run(
            COMPANY,
            typed(text),
            "text/plain",
            TypedTextExtractor(),
            t,
            memory,
            today=TODAY,
        )
        assert d.outcome is Outcome.UNCLEAR
    assert t.list_our_vouchers(COMPANY) == ()


# ---- vendor spelling variants ----------------------------------------------


def test_spelling_variants_of_one_vendor_are_the_same_vendor():
    hist = past("Sharma Traders", "Purchases", n=10)
    t = tally(hist)
    memory = memory_for(t)
    for spelling in ("M/s Sharma Traders Pvt Ltd", "SHARMA TRADERS", "Sharma  Traders"):
        d = pipeline.run(
            COMPANY,
            typed(f"paid {spelling} 4200 cement"),
            "text/plain",
            TypedTextExtractor(),
            t,
            memory,
            today=TODAY,
        )
        assert d.outcome is Outcome.VALID, spelling


# ---- checks report a count -------------------------------------------------


def test_every_draft_carries_a_check_count():
    t = tally(past("Sharma Traders", "Purchases", n=5))
    d = pipeline.run(
        COMPANY,
        typed("paid Sharma Traders 4200 cement"),
        "text/plain",
        TypedTextExtractor(),
        t,
        memory_for(t),
        today=TODAY,
    )
    assert len(d.checks) == len(
        __import__("accountant.checks", fromlist=["x"]).ALL_CHECKS
    )
