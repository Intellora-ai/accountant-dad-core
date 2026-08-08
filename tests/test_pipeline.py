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
from accountant.memory.store import MemoryStore
from accountant.schema import Outcome, Voucher
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
        accounts,
        memory,
        today=TODAY,
    )
    d = pipeline.evaluate(d, accounts, history, memory)
    assert d.outcome is Outcome.UNCLEAR

    d = pipeline.answer(d, "Purchases")
    memory.record_correction("Verma Cement", "Purchases")
    d = pipeline.evaluate(d, accounts, history, memory)

    assert d.outcome is Outcome.VALID
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
        accounts,
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
    accounts = t.read_accounts(COMPANY)
    memory = memory_for(t)
    d = pipeline.build_draft(
        COMPANY,
        typed("paid Verma Cement 900 bags"),
        "text/plain",
        TypedTextExtractor(),
        accounts,
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
        accounts,
        memory,
        today=TODAY,
    )
    d = pipeline.evaluate(d, accounts, (), memory)
    assert d.outcome is not Outcome.VALID
    with pytest.raises(ValueError):
        pipeline.post(d, t)


def test_posting_an_unevaluated_draft_is_refused():
    t = tally([])
    memory = memory_for(t)
    d = pipeline.build_draft(
        COMPANY,
        typed("paid Sharma Traders 4200 cement"),
        "text/plain",
        TypedTextExtractor(),
        t.read_accounts(COMPANY),
        memory,
        today=TODAY,
    )
    with pytest.raises(ValueError):
        pipeline.post(d, t)


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
        accounts,
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
    """
    t = tally([])
    memory = memory_for(t)
    assert memory.ready is True
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
