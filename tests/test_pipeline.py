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
from accountant.cage.decision import AUTO_POST_ALLOWED_TIERS
from accountant.extract.adapter import (
    LineItem,
    StubExtractor,
    TypedTextExtractor,
    UnavailableExtractor,
)
from accountant.memory.bootstrap import bootstrap
from accountant.memory.company import CompanyMatchStatus, CompanyMemory
from accountant.memory.index import normalise_vendor
from accountant.memory.store import BootstrapStatus, MemoryStore
from accountant.schema import Outcome, Voucher
from accountant.tallyio.client import WriteResult
from accountant.tallyio.fake import FakeTally
from tests.test_period_handoff import open_books_for

COMPANY = "Demo Co"
ACCOUNTS = ("Purchases", "Sundry Expenses", "Repairs & Maintenance", "Cash")
TODAY = datetime.date(2026, 8, 7)

#: Outcomes that mean "nothing was posted and the person was told something".
#: Spelt the same way as `tests/test_gst_safety_sweep.py:74`, because it is the
#: same claim: a refusal is safe only when it also wrote nothing.
SAFE = frozenset({Outcome.UNCLEAR, Outcome.NOT_VALID})


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


def tally(
    history: list[Voucher] | None = None, *, also_ledgers: tuple[str, ...] = ()
) -> FakeTally:
    """This company's books. `also_ledgers` adds names to the CHART OF ACCOUNTS.

    A supplier is a ledger head in Tally, and whether that ledger already exists
    is a fact about the customer's books quite separate from whether we have
    ever seen it used. `cage/decision.py::_world_blocks` needs it: a party in
    NEITHER the chart nor the history is unknown, and an unknown party blocks,
    because "I will never add a new name to your books on my own". Tests written
    before that check existed never stated the fact either way, so this is where
    one states it.

    Default empty, so nothing gains a ledger it did not ask for. In particular
    `test_unknown_vendor_is_unclear_and_does_not_post` needs Verma Cement absent
    from the chart, or its refusal would prove nothing.
    """
    t = FakeTally()
    t.add_company(
        COMPANY,
        accounts=ACCOUNTS + also_ledgers,
        vouchers=tuple(history or []),
        backed_up=True,
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


#: A bill from a vendor this company has two accounts for. The tests below are
#: about WHICH ACCOUNT, so the sentence has to get past the amount to be about
#: anything at all.
#:
#: CORRECTED 2026-08-13, and it is the sentence that was wrong rather than the
#: assertions. It read `paid Verma Cement 900 bags`, which states a QUANTITY and
#: no price - and `adapter._not_an_amount` now says so, because a number
#: followed by a unit word counts things rather than rupees. Nine hundred bags
#: of cement is not nine hundred rupees, and reading it as rupees is the same
#: defect these tests were never about. The account question they ask is
#: unchanged; the fixture now says the thing it always meant, in the wording
#: `test_unknown_vendor_is_unclear_and_does_not_post` was already using.
A_BILL_FROM_A_TWO_ACCOUNT_VENDOR = "paid Verma Cement 900 for bags"


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
        period_reader=open_books_for(COMPANY),
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
        period_reader=open_books_for(COMPANY),
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
        period_reader=open_books_for(COMPANY),
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
        period_reader=open_books_for(COMPANY),
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
        period_reader=open_books_for(COMPANY),
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
        period_reader=open_books_for(COMPANY),
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
        typed(A_BILL_FROM_A_TWO_ACCOUNT_VENDOR),
        "text/plain",
        TypedTextExtractor(),
        t,
        memory_for(t),
        today=TODAY,
        period_reader=open_books_for(COMPANY),
    )
    assert d.outcome is Outcome.UNCLEAR
    # plus a "something else" escape so nobody gets stuck
    options = set(d.decision.question_options if d.decision else ())
    assert options >= {"Purchases", "Repairs & Maintenance"}


def test_answering_then_re_evaluating_posts():
    # Verma Cement's LEDGER exists and its HABIT does not: nothing in this
    # company's history says which expense account their bills belong in, which
    # is the question this test is about. The two facts were conflated while
    # only one of them was checked. Since the cage refuses a party it cannot
    # find in the chart or the history, a fixture that stays silent about the
    # ledger is asking the pipeline to invent a supplier - which it will not do,
    # and which this test never meant to ask for. The refusal itself is proved
    # by `test_unknown_vendor_is_unclear_and_does_not_post`, where the same
    # supplier is absent from both and nothing posts.
    t = tally(past("Sharma Traders", "Purchases", n=5), also_ledgers=("Verma Cement",))
    accounts = t.read_accounts(COMPANY)
    history = t.read_vouchers(COMPANY)
    memory = memory_for(t)

    d = pipeline.build_draft(
        COMPANY,
        typed(A_BILL_FROM_A_TWO_ACCOUNT_VENDOR),
        "text/plain",
        TypedTextExtractor(),
        memory,
        today=TODAY,
    )
    d = pipeline.evaluate(
        d, accounts, history, memory, period_open=True, pdf_repaired=None
    )
    assert d.outcome is Outcome.UNCLEAR

    d = pipeline.answer(d, "Purchases")
    memory.record_correction("Verma Cement", "Purchases")
    d = pipeline.evaluate(
        d, accounts, history, memory, period_open=True, pdf_repaired=None
    )

    # Verma Cement is new to this company, so its funding leg is unknown too.
    # Until 2026-08-09 it was filled in by `_default_credit` with the string
    # "Cash" and no provenance, which is why one answer used to be enough.
    assert d.outcome is Outcome.UNCLEAR
    q = pipeline.next_question(d)
    assert q is not None and q.problem_id == pipeline.FUNDING_PROBLEM

    d = pipeline.answer(d, "Cash", problem_id=q.problem_id)
    d = pipeline.evaluate(
        d, accounts, history, memory, period_open=True, pdf_repaired=None
    )

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
        typed(A_BILL_FROM_A_TWO_ACCOUNT_VENDOR),
        "text/plain",
        TypedTextExtractor(),
        memory,
        today=TODAY,
    )
    d = pipeline.answer(d, "Not A Real Ledger")
    memory.record_correction("Verma Cement", "Not A Real Ledger")
    d = pipeline.evaluate(
        d, accounts, history, memory, period_open=None, pdf_repaired=None
    )

    assert d.outcome is not Outcome.VALID
    assert "Not A Real Ledger" in d.reason


def test_answer_is_recorded_as_provenance():
    t = tally([])
    memory = memory_for(t)
    d = pipeline.build_draft(
        COMPANY,
        typed(A_BILL_FROM_A_TWO_ACCOUNT_VENDOR),
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
    d = pipeline.evaluate(d, accounts, (), memory, period_open=None, pdf_repaired=None)
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
    d = pipeline.evaluate(
        d, accounts, history, memory, period_open=None, pdf_repaired=None
    )
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
    d = pipeline.evaluate(
        d, accounts, history, memory, period_open=True, pdf_repaired=None
    )
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
    d = pipeline.evaluate(
        d, accounts, tuple(hist), memory, period_open=None, pdf_repaired=None
    )

    assert d.outcome is Outcome.UNCLEAR  # asks, never refuses
    assert d.posted_tally_id is None
    assert "40 times" in d.reason
    q = pipeline.next_question(d)
    assert q is not None and q.problem_id == "vendor_switch"


# ---- Slice 5: stub extractor, same flow ------------------------------------


def test_stub_extractor_drives_the_same_pipeline():
    """Slice 5's claim: a stubbed backend reaches every stage the typed one does.

    MEASURED AT THE DRAFT, NOT AT THE POST, since the owner's ruling of
    2026-08-17. The claim in the name is that the pipeline is BACKEND-AGNOSTIC -
    that swapping the reader changes no pipeline code - and that claim is about
    the work the pipeline did: the amounts it carried through, the accounts it
    proposed, the party it recognised, the provenance it stamped and the eight
    cage checks it ran. Every one of those is asserted below.

    IT IS NOT A CLAIM THAT A STUB MAY POST, and asserting `Outcome.VALID` here
    made it one by accident. `decision.AUTO_POST_ALLOWED_TIERS` is
    `{pdf_text_layer, typed_text}` and `stub` is deliberately absent, so a test
    double cannot buy auto-post by handing in tidy numbers. Adding `stub` to
    that allowlist to make this line green would have been the change marking
    its own homework, and it is forbidden.

    `tax_paise=0`, `net_paise` AND `line_items` ARE ALL HANDED IN, and each one
    is the fact a conservation law needs before it can evaluate at all. Without
    them the draft blocks on "there is something on this bill I could not check
    at all", which would leave this test measuring an unread fixture rather
    than the tier rule. Nothing is derived from `total_paise`: `gate.py:119`
    refuses a net worked out from the total because both are already inputs to
    the same law, and a number checked against itself passes for ever.

    THE REFUSAL IS ASSERTED FOUR WAYS at the end, not one. "It refused" without
    "and wrote nothing" is exactly the assertion that lets a silent write past.
    """
    t = tally(past("Sharma Traders", "Purchases", n=5))
    before = t.trial_balance(COMPANY)
    # `tax_paise` READ AND ZERO, changed 2026-08-17, and the distinction is the
    # point. It passed 64068 paise of GST until 2026-08-10 and posted it; that
    # only worked because `FakeTally` was softer than `RealTally`, which builds
    # no tax lines and REFUSES a voucher carrying GST. The double mirrors that
    # refusal now (defect D1). It then said `None` until today, and `None` is
    # "nobody read the tax field" rather than "there is no tax on this bill" -
    # an unread field must block, so the test was measuring a missing fact.
    # Zero is the fact this fixture is actually about, so it states it.
    stub = StubExtractor(
        date=TODAY,
        party="Sharma Traders",
        total_paise=420000,
        tax_paise=0,
        net_paise=420000,
        line_items=(LineItem(description="one line", amount_paise=420000),),
    )
    d = pipeline.run(
        COMPANY,
        b"<pretend pdf>",
        "application/pdf",
        stub,
        t,
        memory_for(t),
        period_reader=open_books_for(COMPANY),
    )

    # THE PIPELINE DID THE WHOLE JOB. Every stage a typed bill reaches, reached
    # from a stubbed one: the amount, the tax figure as read, the funding leg,
    # the account proposed from this company's own history, the party matched
    # against its memory, and a stated source for every field.
    assert d.voucher.amount_paise == 420000
    assert d.voucher.gst_paise == 0
    assert d.voucher.party == "Sharma Traders"
    assert d.voucher.debit_account == "Purchases"
    assert d.voucher.credit_account == "Cash"
    assert d.record.backend == "stub"
    assert d.provenance["total_paise"] == "stub"
    assert all(source.strip() for source in d.provenance.values())
    # All eight cage checks RAN and all eight PASSED. This is the line that
    # would go red if a stubbed backend stopped reaching the cage at all, which
    # is the failure mode "it refused" alone could never tell apart from this.
    assert [c.name for c in d.checks if c.passed] == [c.name for c in d.checks]
    assert len(d.checks) == 8

    # AND IT STILL MAY NOT POST. Clean arithmetic from an untrusted tier buys
    # nothing: `stub` is not on the allowlist, and the reason says so in words
    # a person can act on rather than naming a broken law.
    assert "stub" not in AUTO_POST_ALLOWED_TIERS
    assert d.outcome in SAFE, f"a stub reached {d.outcome.value}"
    assert d.posted_tally_id is None
    assert len(t.list_our_vouchers(COMPANY)) == 0
    assert t.trial_balance(COMPANY) == before
    assert "not posted on their own" in d.reason


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
        period_reader=open_books_for(COMPANY),
    )
    assert d.outcome is not Outcome.VALID
    assert d.posted_tally_id is None
    assert all("not_found" in v for v in d.provenance.values())


def test_swapping_the_backend_changes_no_pipeline_code():
    """#15.5 - two backends, identical call path.

    THE CLAIM IS ABOUT THE CALL PATH AND THE WORK, NOT ABOUT THE OUTCOME WORD.
    The two calls below differ in exactly three arguments - the bytes, the media
    type and the reader - and in nothing else. Everything the PIPELINE then
    computes is asserted EQUAL between them: the party, the amount, the funding
    leg, the account proposed out of this company's history, the voucher date,
    and the full list of cage checks with their results. That is what "swapping
    the backend changes no pipeline code" means and it is measured field by
    field rather than inferred from two drafts happening to share a word.

    THE ONE THING THAT DIFFERS IS THE TIER, AND IT IS SUPPOSED TO. Owner ruling
    2026-08-17: `decision.AUTO_POST_ALLOWED_TIERS` is `{pdf_text_layer,
    typed_text}` and `stub` is not on it, so the typed bill posts and the
    stubbed one - with byte-identical arithmetic - is refused. This test asserted
    `a.outcome is b.outcome is Outcome.VALID` until today, which quietly said a
    test double may buy auto-post by stating tidy numbers. It may not, and the
    line below now says both halves of that out loud.
    """
    t1 = tally(past("Sharma Traders", "Purchases", n=5))
    t2 = tally(past("Sharma Traders", "Purchases", n=5))
    before = t2.trial_balance(COMPANY)
    a = pipeline.run(
        COMPANY,
        typed("paid Sharma Traders 4200 cement"),
        "text/plain",
        TypedTextExtractor(),
        t1,
        memory_for(t1),
        today=TODAY,
        period_reader=open_books_for(COMPANY),
    )
    b = pipeline.run(
        COMPANY,
        b"x",
        "application/pdf",
        # SAME FACTS, HANDED IN. `tax_paise=0` because this bill carries no tax
        # and somebody looked; `net_paise` and `line_items` because
        # `net_plus_tax_equals_gross` and `lines_sum_to_total` are INDETERMINATE
        # without them, and INDETERMINATE blocks. Neither is derived from
        # `total_paise` - `gate.py:119` refuses a figure checked against itself.
        # With them stated, the only difference left between these two runs is
        # the name of the backend, which is precisely what this test is for.
        StubExtractor(
            date=TODAY,
            party="Sharma Traders",
            total_paise=420000,
            tax_paise=0,
            net_paise=420000,
            line_items=(LineItem(description="one line", amount_paise=420000),),
        ),
        t2,
        memory_for(t2),
        period_reader=open_books_for(COMPANY),
    )

    # THE PIPELINE DID THE SAME WORK TWICE. Asserted per field, so a backend
    # that quietly stopped reaching one of these stages cannot hide behind an
    # outcome that happens to match.
    assert a.record.backend == "typed_text"
    assert b.record.backend == "stub"
    assert a.voucher.party == b.voucher.party == "Sharma Traders"
    assert a.voucher.amount_paise == b.voucher.amount_paise == 420000
    assert a.voucher.debit_account == b.voucher.debit_account == "Purchases"
    assert a.voucher.credit_account == b.voucher.credit_account == "Cash"
    assert a.voucher.date == b.voucher.date == TODAY
    assert [(c.name, c.passed) for c in a.checks] == [
        (c.name, c.passed) for c in b.checks
    ]

    # AND THE TIER, WHICH IS THE ONE THING IT IS ALLOWED TO DIFFER ON.
    assert a.record.backend in AUTO_POST_ALLOWED_TIERS
    assert b.record.backend not in AUTO_POST_ALLOWED_TIERS
    assert a.outcome is Outcome.VALID
    assert a.posted_tally_id is not None
    # NOTHING WRITTEN, THREE WAYS. A refusal that only shows up in an enum is
    # the refusal a silent write walks straight past.
    assert b.outcome in SAFE, f"the stub reached {b.outcome.value}"
    assert b.posted_tally_id is None
    assert len(t2.list_our_vouchers(COMPANY)) == 0
    assert t2.trial_balance(COMPANY) == before


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
        A_BILL_FROM_A_TWO_ACCOUNT_VENDOR,
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
            period_reader=open_books_for(COMPANY),
        )
        assert d.outcome is Outcome.UNCLEAR
    assert t.list_our_vouchers(COMPANY) == ()


# ---- vendor spelling variants ----------------------------------------------


def _draft_for(t: FakeTally, memory: CompanyMemory, spelling: str) -> pipeline.Draft:
    return pipeline.run(
        COMPANY,
        typed(f"paid {spelling} 4200 cement"),
        "text/plain",
        TypedTextExtractor(),
        t,
        memory,
        today=TODAY,
        period_reader=open_books_for(COMPANY),
    )


def test_spelling_variants_of_one_vendor_are_the_same_vendor():
    """A spelling is noise. Case, doubled spaces and the M/s prefix all collapse.

    REWRITTEN 2026-08-10, owner ruling D-05. This used to include
    "M/s Sharma Traders Pvt Ltd" in the list below and require it to post
    VALID off a bare "Sharma Traders" history. It no longer does, and the case
    has moved to its own test underneath, because it is not a spelling variant
    - see there.

    "M/s Sharma Traders" IS still here. The prefix is noise and always was; the
    ruling only removed the LEGAL FORM from this list.
    """
    hist = past("Sharma Traders", "Purchases", n=10)
    t = tally(hist)
    memory = memory_for(t)
    for spelling in (
        "M/s Sharma Traders",
        "M.S. Sharma Traders",
        "SHARMA TRADERS",
        "Sharma  Traders",
    ):
        assert _draft_for(t, memory, spelling).outcome is Outcome.VALID, spelling


def test_a_legal_form_the_history_never_saw_asks_instead_of_posting():
    """Required regression: AMBIGUOUS cannot reach an automatic posting.

    OWNER RULING D-05, 2026-08-10. This assertion is the exact inverse of what
    `test_spelling_variants_of_one_vendor_are_the_same_vendor` used to require
    of this same input. That older test treated "M/s Sharma Traders Pvt Ltd" as
    a third spelling of "Sharma Traders" and demanded it post VALID; the owner
    ruled that a bare name and a Pvt Ltd of that name are AMBIGUOUS, not the
    same supplier, and that ambiguity must ask or hand over rather than post.

    So the older test was not merely superseded - it was asserting the defect.
    Ten vouchers of history for the sole proprietor say nothing about the
    private limited company, and posting against them would put one firm's
    payment into another firm's account, silently.

    The cost is one question. The old behaviour's cost was a wrong ledger.
    """
    hist = past("Sharma Traders", "Purchases", n=10)
    t = tally(hist)
    memory = memory_for(t)

    # "Sharma Traders & Co" is deliberately NOT exercised here: the typed-text
    # extractor stops the party at the "&", so the pipeline never sees the
    # trading style and the case cannot be tested through this surface. It is
    # asserted directly in tests/test_legal_identity.py instead.
    draft = _draft_for(t, memory, "M/s Sharma Traders Pvt Ltd")

    assert draft.outcome is Outcome.UNCLEAR
    assert draft.outcome is not Outcome.VALID
    assert draft.posted_tally_id is None
    assert draft.voucher.debit_account == ""
    assert draft.voucher.party == "M/s Sharma Traders Pvt Ltd"
    assert draft.problems


# ---- the three ways the cage can already know a vendor ---------------------
#
# `pipeline.evaluate` hands the cage `party_known`, and until 2026-08-17 it was
# raw string equality over the chart of accounts and the live history. It now
# asks the same question the rest of the product asks - `normalise_vendor` on
# both sides - and accepts a THIRD source, an explicit mapping the person
# recorded through `CompanyMemory`. The three tests below are one each for the
# owner's three cases: MAPPED counts, UNMATCHED still does not, and a name that
# normalises to something else is a different supplier however close it reads.


def test_a_vendor_mapped_in_memory_is_known_and_reaches_a_posting():
    """MAPPED. A supplier the person confirmed themselves is not a stranger.

    Gupta Hardware is in NEITHER the chart of accounts NOR the history, so the
    only thing that can make it known is the correction recorded below. Before
    2026-08-17 the cage refused it on those two sources alone and forced
    NOT_VALID, so answering the question changed nothing and the same question
    came back for ever - rule D2a said the opposite.

    Reaching VALID is what measures it. The cage may only narrow, so a draft
    that arrives VALID is a draft the cage did not block, and `party_known` is
    the only one of its facts this fixture moves. The posting is asserted too:
    a decision that says VALID and writes nothing is not the outcome D2a
    promises.
    """
    t = tally(past("Sharma Traders", "Purchases", n=5))
    accounts = t.read_accounts(COMPANY)
    history = t.read_vouchers(COMPANY)
    memory = memory_for(t)

    assert "Gupta Hardware" not in accounts
    assert all(v.party != "Gupta Hardware" for v in history)
    memory.record_correction("Gupta Hardware", "Purchases")
    assert memory.lookup("Gupta Hardware").status is CompanyMatchStatus.MATCH

    d = pipeline.build_draft(
        COMPANY,
        typed("paid Gupta Hardware 1500 for tools"),
        "text/plain",
        TypedTextExtractor(),
        memory,
        today=TODAY,
    )
    d = pipeline.evaluate(
        d, accounts, history, memory, period_open=True, pdf_repaired=None
    )

    # The mapping settles the expense leg. The funding leg is a separate fact
    # this company's books have never stated for this supplier, so it is still
    # asked - which is the same two-question shape as
    # `test_answering_then_re_evaluating_posts`, minus the invented ledger.
    assert d.outcome is Outcome.UNCLEAR
    q = pipeline.next_question(d)
    assert q is not None and q.problem_id == pipeline.FUNDING_PROBLEM

    d = pipeline.answer(d, "Cash", problem_id=q.problem_id)
    d = pipeline.evaluate(
        d, accounts, history, memory, period_open=True, pdf_repaired=None
    )

    assert d.outcome is Outcome.VALID
    assert (d.voucher.debit_account, d.voucher.credit_account) == ("Purchases", "Cash")
    d = pipeline.post(d, t)
    assert d.posted_tally_id is not None
    assert len(t.list_our_vouchers(COMPANY)) == 1


def test_an_unmapped_vendor_is_refused_and_writes_nothing():
    """UNMATCHED. The third source is a door, not a hole in the wall.

    Byte for byte the fixture above with ONE difference: no correction was
    recorded, so `lookup` answers NO_MATCH and none of the three sources knows
    this supplier. Every question is answered anyway - the account by hand, the
    funding by hand - so nothing but the vendor identity is left to refuse on.

    Without this test the one above would pass just as happily if
    `party_known` had been hard-wired True, which is the failure mode a new
    source of permission actually has. The books are read after the refusal
    because "it raised" and "it wrote nothing" are two different claims, and it
    is the second one that costs money when it is false.
    """
    t = tally(past("Sharma Traders", "Purchases", n=5))
    accounts = t.read_accounts(COMPANY)
    history = t.read_vouchers(COMPANY)
    memory = memory_for(t)
    assert memory.lookup("Gupta Hardware").status is CompanyMatchStatus.NO_MATCH

    d = pipeline.build_draft(
        COMPANY,
        typed("paid Gupta Hardware 1500 for tools"),
        "text/plain",
        TypedTextExtractor(),
        memory,
        today=TODAY,
    )
    d = pipeline.answer(d, "Purchases")
    d = pipeline.evaluate(
        d, accounts, history, memory, period_open=True, pdf_repaired=None
    )
    q = pipeline.next_question(d)
    assert q is not None and q.problem_id == pipeline.FUNDING_PROBLEM
    d = pipeline.answer(d, "Cash", problem_id=q.problem_id)
    d = pipeline.evaluate(
        d, accounts, history, memory, period_open=True, pdf_repaired=None
    )

    assert d.outcome is not Outcome.VALID
    assert d.posted_tally_id is None
    with pytest.raises(ValueError):
        pipeline.post(d, t)
    assert t.list_our_vouchers(COMPANY) == ()


def test_a_materially_different_legal_form_is_not_the_vendor_the_books_hold():
    """MATERIALLY DIFFERENT. Owner ruling D-05, measured at the cage seam.

    Both halves run the SAME bill against the SAME ten vouchers of history for
    "Sharma Traders", and separate only on the spelling of the party:

        M/s Sharma Traders          the prefix is noise      -> known, posts
        M/s Sharma Traders Pvt Ltd  a different legal entity -> unknown, asks

    The keys are asserted first so the test states WHY rather than only WHAT: a
    name is the same vendor when `normalise_vendor` says so, and the private
    limited company keys away from the sole proprietor. Running only the second
    half would pass against a cage that recognised nobody at all, and running
    only the first would pass against one that recognised everybody, so neither
    half measures the rule on its own.

    Two FakeTally instances, because the first half posts. Sharing one would
    leave a voucher in the books and make the second half's "wrote nothing"
    assertion impossible to state.
    """
    assert normalise_vendor("M/s Sharma Traders") == normalise_vendor("Sharma Traders")
    assert normalise_vendor("M/s Sharma Traders Pvt Ltd") != normalise_vendor(
        "Sharma Traders"
    )

    same = tally(past("Sharma Traders", "Purchases", n=10))
    other = tally(past("Sharma Traders", "Purchases", n=10))

    spelling = _draft_for(same, memory_for(same), "M/s Sharma Traders")
    assert spelling.outcome is Outcome.VALID
    assert spelling.posted_tally_id is not None
    assert len(same.list_our_vouchers(COMPANY)) == 1

    entity = _draft_for(other, memory_for(other), "M/s Sharma Traders Pvt Ltd")
    assert entity.outcome is not Outcome.VALID
    assert entity.posted_tally_id is None
    assert other.list_our_vouchers(COMPANY) == ()


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
        period_reader=open_books_for(COMPANY),
    )
    assert len(d.checks) == len(
        __import__("accountant.checks", fromlist=["x"]).ALL_CHECKS
    )
