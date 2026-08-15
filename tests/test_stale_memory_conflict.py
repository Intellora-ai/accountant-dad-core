"""D-06: live Tally wins over stale memory, and a disagreement is a question.

THE DEFECT THESE TESTS PIN
--------------------------
The vendor index is built once, by `bootstrap`, when the company is opened, and
nothing ever re-reads it. So a company that has genuinely changed how it books a
supplier kept getting the old answer, on a path that posts without asking
anybody. In the owner's words, reproduced below line for line:

    memory learned Purchases 40 times
    the live ledger later contains Repairs 60 times
    the next entry still posts Purchases without a flag or question

`vendor_switch` cannot catch it. That detector compares the proposed leg against
the same snapshot index the leg was proposed from, so it agrees with itself by
construction and stays silent — `test_the_detector_that_looks_like_it_should_
catch_this_cannot` measures exactly that rather than asserting it.

Owner decision, 2026-08-10: the entry becomes UNCLEAR and asks. Not NOT_VALID —
an answer fixes it. Both sources and both counts are recorded, and an answer is
new information rather than authorisation.

WHAT THIS FILE DOES NOT PROVE
-----------------------------
Evidence class: FakeTally implementation. Every scenario here runs against
`accountant.tallyio.fake.FakeTally`, so it says our logic is right and says
NOTHING about TallyPrime. In particular it does not prove that a real connector
returning an empty voucher list is ever a genuine empty ledger rather than a
degraded read, that Tally groups two spellings of a supplier the way our vendor
key does, or that a real ledger goes stale in the shape simulated here.

The one measurement in this file that is not FakeTally is
`test_no_conflict_fires_on_a_clean_entry_of_the_committed_real_ledgers`, which
runs over UK central-government spend files published by the departments that
made the postings. That is real-ledger evidence about the FALSE ALARM rate and
nothing else; it is silent about whether the rule catches anything.
"""

from __future__ import annotations

import datetime

import pytest

from accountant import checks, pipeline
from accountant import questions as Q
from accountant.detect import detectors
from accountant.extract.adapter import TypedTextExtractor
from accountant.ingest import sources, spend
from accountant.memory.bootstrap import bootstrap
from accountant.memory.company import (
    CompanyMemory,
    LiveDisagreement,
    disagrees_with_live_history,
    live_vendor_accounts,
    propose_account,
)
from accountant.memory.store import MemoryStore
from accountant.schema import Outcome, Voucher
from accountant.tallyio.client import TallyClient, WriteResult
from accountant.tallyio.fake import FakeTally

COMPANY = "Demo Co"
ACCOUNTS = ("Purchases", "Sundry Expenses", "Repairs & Maintenance", "Cash")
TODAY = datetime.date(2026, 8, 10)

VENDOR = "Sharma Traders"
OTHER_VENDOR = "Verma Cement"
REMEMBERED = "Purchases"
NOW_IN_THE_LEDGER = "Repairs & Maintenance"

# The owner's numbers, not invented ones.
REMEMBERED_TIMES = 40
NOW_TIMES = 60


#: ₹4,200, which is also what the typed entries below extract to. Deliberate:
#: a history of a different size would make `magnitude` fire on the very entry
#: this file needs to be unremarkable in every way except the stale account.
USUAL_PAISE = 420_000


def past(
    party: str, account: str, *, n: int, amount: int = USUAL_PAISE, tag: str = ""
) -> list[Voucher]:
    """`n` posted vouchers for one vendor and one account. Ids never collide."""
    return [
        Voucher(
            id=f"hist-{tag or account}-{party}-{i}",
            date=datetime.date(2026, 1, 1),
            party=party,
            narration=f"{party} invoice",
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


def memory_for(t: FakeTally) -> CompanyMemory:
    """This company's memory, read out of this company's Tally, own store."""
    return bootstrap(t, COMPANY, MemoryStore(":memory:"))


def typed(text: str) -> bytes:
    return text.encode()


def the_books_moved_on() -> tuple[FakeTally, CompanyMemory]:
    """The reproduced case: bootstrap on 40, then 60 more arrive in Tally.

    Memory is built FIRST and the new vouchers are entered afterwards, through
    the connector, exactly as an accountant typing into Tally would. Building
    the disagreement any other way would test the fixture rather than staleness.
    """
    t = tally(past(VENDOR, REMEMBERED, n=REMEMBERED_TIMES))
    memory = memory_for(t)
    for voucher in past(VENDOR, NOW_IN_THE_LEDGER, n=NOW_TIMES):
        t.seed_voucher(COMPANY, voucher)
    return t, memory


def run_one(
    t: TallyClient, memory: CompanyMemory, text: str = f"paid {VENDOR} 4200 for cement"
) -> pipeline.Draft:
    return pipeline.run(
        COMPANY, typed(text), "text/plain", TypedTextExtractor(), t, memory, today=TODAY
    )


def nothing_was_written(t: FakeTally, before: tuple[Voucher, ...]) -> None:
    """The state assertion every refusal in this file is followed by.

    Two separate claims, because they fail separately: our own write path
    recorded nothing, and the company's register holds exactly the rows it held
    before we were asked.
    """
    assert t.list_our_vouchers(COMPANY) == ()
    assert t.read_vouchers(COMPANY) == before


# ---------------------------------------------------------------------------
# 1. memory says Purchases, live Tally says Repairs
# ---------------------------------------------------------------------------


def test_memory_says_purchases_the_live_ledger_says_repairs_and_the_entry_asks() -> (
    None
):
    """The reproduced defect, in the owner's own numbers. UNCLEAR, not posted."""
    t, memory = the_books_moved_on()
    before = t.read_vouchers(COMPANY)

    draft = run_one(t, memory)

    assert draft.outcome is Outcome.UNCLEAR
    question = pipeline.next_question(draft)
    assert question is not None
    assert question.problem_id == pipeline.LIVE_HISTORY_DISAGREES
    assert draft.posted_tally_id is None
    nothing_was_written(t, before)


def test_this_comparison_is_the_only_thing_standing_between_the_entry_and_a_post() -> (
    None
):
    """Every OTHER gate on the same entry is satisfied. Measured, one by one.

    This is why the defect was silent rather than merely quiet: memory proposes
    confidently, both legs come out of the company's own books, every check
    passes and no detector fires. Nothing else in the system was ever going to
    stop it, which is what makes "delete the comparison" a mutation the suite
    has to catch.
    """
    t, memory = the_books_moved_on()
    history = t.read_vouchers(COMPANY)

    assert propose_account(memory, VENDOR) == REMEMBERED
    assert pipeline.funding_from_history(VENDOR, history) == "Cash"

    proposed = Voucher(
        id="d1",
        date=TODAY,
        party=VENDOR,
        narration="cement",
        debit_account=REMEMBERED,
        credit_account="Cash",
        amount_paise=USUAL_PAISE,
    )
    failed = [c.name for c in checks.run(proposed, ACCOUNTS) if not c.passed]
    assert failed == []
    flags, _ = detectors.run(
        proposed, history, memory.index(), detectors.ACTIVE_DETECTORS
    )
    assert flags == [], "no detector fires on it either"


def test_the_detector_that_looks_like_it_should_catch_this_cannot() -> None:
    """`vendor_switch` compares the proposal against the snapshot it came from.

    Measured, not assumed: the memory index says Purchases 40 times, the voucher
    says Purchases, so the detector agrees with itself and is silent — while the
    live ledger, which it never reads, says Repairs 60 times.
    """
    t, memory = the_books_moved_on()
    history = t.read_vouchers(COMPANY)
    index = memory.index()

    proposed = Voucher(
        id="d1",
        date=TODAY,
        party=VENDOR,
        narration="cement",
        debit_account=REMEMBERED,
        credit_account="Cash",
        amount_paise=USUAL_PAISE,
    )
    assert detectors.vendor_switch(proposed, history, index) == []
    assert index.times_posted(VENDOR, REMEMBERED) == REMEMBERED_TIMES
    assert live_vendor_accounts(VENDOR, history) == (
        (NOW_IN_THE_LEDGER, REMEMBERED),
        (NOW_TIMES, REMEMBERED_TIMES),
    )


# ---------------------------------------------------------------------------
# 2. memory says one vendor, live Tally says another
# ---------------------------------------------------------------------------


def test_one_vendor_disagrees_and_the_company_s_other_vendor_is_untouched() -> None:
    """The conflict is per vendor. It is not a mood the company is in.

    `Verma Cement` is posted to one account and only ever that account in both
    sources, so it is proposed, decided and written exactly as before, in the
    same books and the same run in which `Sharma Traders` is refused.
    """
    consistent = past(OTHER_VENDOR, "Sundry Expenses", n=6)
    t = tally(past(VENDOR, REMEMBERED, n=REMEMBERED_TIMES) + consistent)
    memory = memory_for(t)
    # The ledger is RESTATED: this vendor's rows now name a different account
    # entirely, and the remembered one is not among them.
    for voucher in past(VENDOR, NOW_IN_THE_LEDGER, n=NOW_TIMES, tag="restated"):
        t.seed_voucher(COMPANY, voucher)
    before = t.read_vouchers(COMPANY)

    refused = run_one(t, memory)
    assert refused.outcome is Outcome.UNCLEAR
    assert refused.posted_tally_id is None
    nothing_was_written(t, before)

    accepted = run_one(t, memory, f"paid {OTHER_VENDOR} 900 for bags")
    assert accepted.outcome is Outcome.VALID
    assert accepted.voucher.debit_account == "Sundry Expenses"
    assert accepted.posted_tally_id is not None
    assert accepted.memory_conflict is None


def test_a_vendor_the_live_ledger_moved_off_the_remembered_account_entirely_asks() -> (
    None
):
    """Disjoint sources: memory holds an account the live ledger no longer names."""
    memory_only = past(VENDOR, REMEMBERED, n=REMEMBERED_TIMES)
    live_only = past(VENDOR, NOW_IN_THE_LEDGER, n=NOW_TIMES)

    t = tally(memory_only)
    memory = memory_for(t)
    conflict = disagrees_with_live_history(memory, VENDOR, tuple(live_only))

    assert conflict is not None
    assert conflict.remembered_account == REMEMBERED
    assert conflict.live_accounts == (NOW_IN_THE_LEDGER,)
    assert conflict.changed_to == NOW_IN_THE_LEDGER


# ---------------------------------------------------------------------------
# 3. memory is stale after new vouchers are entered
# ---------------------------------------------------------------------------


def test_memory_that_was_right_at_bootstrap_goes_stale_when_tally_is_typed_into() -> (
    None
):
    """Same company, same memory handle, same code. Only the ledger changed.

    The first entry posts without a question, which is correct and is what makes
    the second one evidence: nothing about the fixture, the vendor or the wording
    is different, so the only thing that can have moved the outcome is the rows
    an accountant added in Tally in between.
    """
    t = tally(past(VENDOR, REMEMBERED, n=REMEMBERED_TIMES))
    memory = memory_for(t)

    first = run_one(t, memory)
    assert first.outcome is Outcome.VALID
    assert first.posted_tally_id is not None
    assert first.memory_conflict is None

    for voucher in past(VENDOR, NOW_IN_THE_LEDGER, n=NOW_TIMES):
        t.seed_voucher(COMPANY, voucher)
    before = t.read_vouchers(COMPANY)

    second = run_one(t, memory)
    assert second.outcome is Outcome.UNCLEAR
    assert second.posted_tally_id is None
    # One write in these books, and it is the first entry's, not this one's.
    assert len(t.list_our_vouchers(COMPANY)) == 1
    assert t.read_vouchers(COMPANY) == before


# ---------------------------------------------------------------------------
# 4. live Tally is unavailable
# ---------------------------------------------------------------------------


class UnreadableHistory:
    """A connector that answers everything except the question that matters.

    Identity, chart and writes all work. Only `read_vouchers` fails, which is
    the shape that would let a fallback to memory look reasonable: we know who
    the company is, we know its chart, and we have a remembered answer sitting
    right there.
    """

    def __init__(self, inner: FakeTally) -> None:
        self._inner = inner
        self.write_attempts = 0

    def list_companies(self) -> tuple[str, ...]:
        return self._inner.list_companies()

    def read_accounts(self, company: str) -> tuple[str, ...]:
        return self._inner.read_accounts(company)

    def read_vouchers(self, company: str) -> tuple[Voucher, ...]:
        raise ConnectionError(f"the voucher register for {company!r} is unreachable")

    def trial_balance(self, company: str) -> dict[str, int]:
        return self._inner.trial_balance(company)

    def write_voucher(
        self, company: str, voucher: Voucher, operation_id: str
    ) -> WriteResult:
        self.write_attempts += 1
        return self._inner.write_voucher(company, voucher, operation_id)

    def read_by_operation_id(self, company: str, operation_id: str) -> Voucher | None:
        return self._inner.read_by_operation_id(company, operation_id)

    def reverse_by_operation_id(self, company: str, operation_id: str) -> bool:
        return self._inner.reverse_by_operation_id(company, operation_id)

    def backed_up(self, company: str) -> bool:
        return self._inner.backed_up(company)

    def list_our_vouchers(self, company: str) -> tuple[Voucher, ...]:
        return self._inner.list_our_vouchers(company)


def test_a_live_ledger_that_cannot_be_read_fails_closed_instead_of_using_memory() -> (
    None
):
    """No live evidence means no decision, not a decision made from the snapshot.

    Memory is READY and would answer instantly — asserted here so the fallback
    is proved to have been available and not taken, rather than merely absent.
    """
    inner = tally(past(VENDOR, REMEMBERED, n=REMEMBERED_TIMES))
    memory = memory_for(inner)
    assert propose_account(memory, VENDOR) == REMEMBERED

    blind = UnreadableHistory(inner)
    before = inner.read_vouchers(COMPANY)

    with pytest.raises(ConnectionError):
        run_one(blind, memory)

    assert blind.write_attempts == 0
    nothing_was_written(inner, before)


def test_an_empty_live_history_is_absence_of_evidence_and_not_a_disagreement() -> None:
    """The stated limit of the rule, pinned so it cannot drift silently.

    A connector that succeeds and returns nothing is indistinguishable, at this
    seam, from a supplier with no posted history. Treating the pair as a
    contradiction would refuse every entry for the second in order to catch the
    first, so the unreadable connector is handled where it CAN be told apart —
    by raising, which the test above measures.
    """
    t = tally(past(VENDOR, REMEMBERED, n=REMEMBERED_TIMES))
    memory = memory_for(t)
    assert disagrees_with_live_history(memory, VENDOR, ()) is None


# ---------------------------------------------------------------------------
# 5. the conflict is resolved by the user
# ---------------------------------------------------------------------------


def test_the_person_s_answer_resolves_the_conflict_and_the_entry_is_decided_again() -> (
    None
):
    """An answer retires THIS question and sends the entry back to step 1."""
    t, memory = the_books_moved_on()
    accounts = t.read_accounts(COMPANY)
    history = t.read_vouchers(COMPANY)

    draft = pipeline.build_draft(
        COMPANY,
        typed(f"paid {VENDOR} 4200 for cement"),
        "text/plain",
        TypedTextExtractor(),
        memory,
        today=TODAY,
    )
    draft = pipeline.evaluate(
        draft, accounts, history, memory, period_open=None, pdf_repaired=None
    )
    assert draft.outcome is Outcome.UNCLEAR

    draft = pipeline.answer(draft, REMEMBERED, pipeline.LIVE_HISTORY_DISAGREES)
    # Cleared, so re-entering the decision order is mandatory rather than polite.
    assert draft.decision is None

    draft = pipeline.evaluate(
        draft, accounts, history, memory, period_open=None, pdf_repaired=None
    )
    assert draft.outcome is Outcome.VALID
    assert pipeline.next_question(draft) is None
    assert (draft.voucher.provenance or {})["debit_account"] == "human_answer"
    # Resolved, still recorded.
    assert draft.memory_conflict is not None

    draft = pipeline.post(draft, t)
    assert draft.posted_tally_id is not None


def test_an_answer_to_a_conflict_is_information_and_never_authorisation() -> None:
    """The same answered conflict, answered badly, still does not post."""
    t, memory = the_books_moved_on()
    accounts = t.read_accounts(COMPANY)
    history = t.read_vouchers(COMPANY)
    before = t.read_vouchers(COMPANY)

    draft = pipeline.build_draft(
        COMPANY,
        typed(f"paid {VENDOR} 4200 for cement"),
        "text/plain",
        TypedTextExtractor(),
        memory,
        today=TODAY,
    )
    draft = pipeline.evaluate(
        draft, accounts, history, memory, period_open=None, pdf_repaired=None
    )
    draft = pipeline.answer(draft, "Not A Real Ledger", pipeline.LIVE_HISTORY_DISAGREES)
    draft = pipeline.evaluate(
        draft, accounts, history, memory, period_open=None, pdf_repaired=None
    )

    assert draft.outcome is not Outcome.VALID
    assert "Not A Real Ledger" in draft.reason
    with pytest.raises(ValueError, match="refusing to post"):
        pipeline.post(draft, t)
    nothing_was_written(t, before)


# ---------------------------------------------------------------------------
# the question, and the S7 rule it has to obey
# ---------------------------------------------------------------------------


def test_the_conflict_question_contains_no_ledger_account_name() -> None:
    """S7, checked with the same guard the phrasebook's own questions use."""
    t, memory = the_books_moved_on()
    conflict = disagrees_with_live_history(memory, VENDOR, t.read_vouchers(COMPANY))
    assert conflict is not None

    question = pipeline.which_is_it_now(conflict, VENDOR, ACCOUNTS)
    assert Q.is_jargon(REMEMBERED), "the remembered account IS jargon, so a leak counts"
    assert question.mentions_any(ACCOUNTS) == [], question.text


def test_the_conflict_question_shows_both_counts_so_the_change_is_visible() -> None:
    """40 and 60, in the words a person reads. Not a log line, the question."""
    t, memory = the_books_moved_on()
    conflict = disagrees_with_live_history(memory, VENDOR, t.read_vouchers(COMPANY))
    assert conflict is not None

    text = pipeline.which_is_it_now(conflict, VENDOR, ACCOUNTS).text
    assert str(REMEMBERED_TIMES) in text
    assert str(NOW_TIMES) in text
    assert VENDOR in text


def test_every_answer_offered_is_a_ledger_this_company_actually_has() -> None:
    """An option is a promise that the thing exists — `how_paid`'s lesson."""
    t, memory = the_books_moved_on()
    conflict = disagrees_with_live_history(memory, VENDOR, t.read_vouchers(COMPANY))
    assert conflict is not None

    question = pipeline.which_is_it_now(conflict, VENDOR, ACCOUNTS)
    values = [a.value for a in question.answers if a.value != Q.HANDOVER]
    # Live first, most-used first: D-06 is that the current ledger wins.
    assert values == [NOW_IN_THE_LEDGER, REMEMBERED]
    assert all(v in ACCOUNTS for v in values)
    assert any(a.value == Q.HANDOVER for a in question.answers)


def test_a_conflict_over_ledgers_the_chart_no_longer_holds_still_offers_a_way_out() -> (
    None
):
    """Nobody is ever stranded with a question and no answerable option."""
    t, memory = the_books_moved_on()
    conflict = disagrees_with_live_history(memory, VENDOR, t.read_vouchers(COMPANY))
    assert conflict is not None

    question = pipeline.which_is_it_now(conflict, VENDOR, ("Cash",))
    assert question.answers
    assert question.mentions_any(ACCOUNTS) == []


# ---------------------------------------------------------------------------
# both sources recorded, in the draft and in the durable row
# ---------------------------------------------------------------------------


def test_the_conflict_records_both_sources_and_both_counts() -> None:
    t, memory = the_books_moved_on()
    draft = run_one(t, memory)

    conflict = draft.memory_conflict
    assert conflict is not None
    assert conflict.company_key == memory.identity.key
    assert (conflict.remembered_account, conflict.remembered_times) == (
        REMEMBERED,
        REMEMBERED_TIMES,
    )
    assert conflict.live_accounts == (NOW_IN_THE_LEDGER, REMEMBERED)
    assert conflict.live_times == (NOW_TIMES, REMEMBERED_TIMES)
    assert (conflict.changed_to, conflict.changed_times) == (
        NOW_IN_THE_LEDGER,
        NOW_TIMES,
    )


def test_the_durable_row_carries_both_accounts_and_both_counts() -> None:
    """Recorded where somebody looking six months later would find it."""
    t, memory = the_books_moved_on()
    store = MemoryStore(":memory:")

    draft = pipeline.run(
        COMPANY,
        typed(f"paid {VENDOR} 4200 for cement"),
        "text/plain",
        TypedTextExtractor(),
        t,
        memory,
        today=TODAY,
        log=store,
        run_id="d06",
    )

    (row,) = store.actions(COMPANY)
    assert row.outcome == Outcome.UNCLEAR.value
    assert REMEMBERED in row.reason and NOW_IN_THE_LEDGER in row.reason
    assert f"{REMEMBERED_TIMES} time(s)" in row.reason
    assert f"{NOW_TIMES} time(s)" in row.reason
    assert draft.posted_tally_id is None


def test_a_disagreement_cannot_be_built_out_of_two_sources_that_agree() -> None:
    """The type refuses it, so no caller can report agreement as a conflict."""
    with pytest.raises(ValueError, match="that is agreement"):
        LiveDisagreement(
            company_key="demo_co",
            subject="sharma_traders",
            remembered_account=REMEMBERED,
            remembered_times=REMEMBERED_TIMES,
            live_accounts=(REMEMBERED,),
            live_times=(REMEMBERED_TIMES,),
        )
    with pytest.raises(ValueError, match="nothing for memory to disagree with"):
        LiveDisagreement(
            company_key="demo_co",
            subject="sharma_traders",
            remembered_account=REMEMBERED,
            remembered_times=REMEMBERED_TIMES,
            live_accounts=(),
            live_times=(),
        )
    with pytest.raises(ValueError, match="count"):
        LiveDisagreement(
            company_key="demo_co",
            subject="sharma_traders",
            remembered_account=REMEMBERED,
            remembered_times=REMEMBERED_TIMES,
            live_accounts=(NOW_IN_THE_LEDGER,),
            live_times=(),
        )


# ---------------------------------------------------------------------------
# the false alarm budget: what must NOT fire
# ---------------------------------------------------------------------------


def test_a_vendor_with_one_consistent_account_never_disagrees_with_itself() -> None:
    """The single-consistent-account guard, stated on its own.

    Memory holds one account and the live ledger holds the same one, however
    many rows arrive. This is the case N1 is spent on, and the rule is
    structurally incapable of firing on it: there is no threshold here to turn.
    """
    t = tally(past(VENDOR, REMEMBERED, n=REMEMBERED_TIMES))
    memory = memory_for(t)
    for voucher in past(VENDOR, REMEMBERED, n=NOW_TIMES, tag="more"):
        t.seed_voucher(COMPANY, voucher)

    history = t.read_vouchers(COMPANY)
    assert live_vendor_accounts(VENDOR, history) == (
        (REMEMBERED,),
        (REMEMBERED_TIMES + NOW_TIMES,),
    )
    assert disagrees_with_live_history(memory, VENDOR, history) is None

    draft = run_one(t, memory)
    assert draft.outcome is Outcome.VALID
    assert draft.memory_conflict is None


def test_a_freshly_bootstrapped_company_cannot_disagree_with_the_books_it_read() -> (
    None
):
    """Memory derived from THIS history, compared against THIS history."""
    history = (
        past(VENDOR, REMEMBERED, n=REMEMBERED_TIMES)
        + past(OTHER_VENDOR, "Sundry Expenses", n=3)
        + past("Iyer Repairs", NOW_IN_THE_LEDGER, n=7)
    )
    t = tally(history)
    memory = memory_for(t)
    live = t.read_vouchers(COMPANY)

    for vendor in (VENDOR, OTHER_VENDOR, "Iyer Repairs", "Nobody At All", "   "):
        assert disagrees_with_live_history(memory, vendor, live) is None


def test_a_vendor_memory_cannot_answer_for_produces_no_conflict_of_its_own() -> None:
    """CONFLICTED, NO_MATCH and not-ready already ask or refuse on their own.

    Adding a second question about the same leg would double-ask the person for
    one problem, which is the non-overlapping rule this project is built on.
    """
    t = tally(
        past(VENDOR, REMEMBERED, n=4) + past(VENDOR, NOW_IN_THE_LEDGER, n=4, tag="mix")
    )
    memory = memory_for(t)
    live = t.read_vouchers(COMPANY)

    # CONFLICTED in memory: two accounts, so nothing was proposed to contradict.
    assert propose_account(memory, VENDOR) is None
    assert disagrees_with_live_history(memory, VENDOR, live) is None
    # NO_MATCH: this company has never posted this supplier.
    assert disagrees_with_live_history(memory, "Somebody New", live) is None


def test_no_conflict_fires_on_a_clean_entry_of_the_committed_real_ledgers() -> None:
    """The false-alarm measurement, on real published ledgers. 0 of 143.

    N1 is false alarms per 100 clean entries against a target of 10, and the
    measured position before this change was 6.29 with 3.71 of headroom. These
    are the same seven UK central-government spend files N1 is measured on, and
    every entry in them is clean by definition, so anything this rule says about
    one of them is a false alarm.

    Memory is bootstrapped from each department's own history and compared
    against that same history, which is the ordinary case: a company whose books
    have not moved since it was opened.
    """
    fired = 0
    clean = 0
    for source in sources.ALL_SOURCES:
        book = spend.as_score_book(spend.load_source(source))
        t = FakeTally()
        t.add_company(
            book.company, accounts=tuple(book.accounts), vouchers=tuple(book.history)
        )
        memory = bootstrap(t, book.company, MemoryStore(":memory:"))
        for entry in book.entries:
            clean += 1
            if disagrees_with_live_history(memory, entry.party, book.history):
                fired += 1

    assert (fired, clean) == (0, 143)


def test_the_rule_still_fires_when_a_real_department_s_books_are_made_stale() -> None:
    """The disconfirming half: prove the 0 above is not a rule that never fires.

    Same real files, but memory is bootstrapped from the FIRST HALF of each
    department's history and then compared against all of it — a company opened
    once and typed into afterwards. A rule that reported 0 on both halves of
    this pair would be measuring nothing.
    """
    stale_fired = 0
    for source in sources.ALL_SOURCES:
        book = spend.as_score_book(spend.load_source(source))
        earlier = tuple(book.history[: len(book.history) // 2])
        t = FakeTally()
        t.add_company(book.company, accounts=tuple(book.accounts), vouchers=earlier)
        memory = bootstrap(t, book.company, MemoryStore(":memory:"))
        for entry in book.entries:
            if disagrees_with_live_history(memory, entry.party, book.history):
                stale_fired += 1

    assert stale_fired > 0, "a rule that cannot fire on stale real books is not a rule"


@pytest.mark.parametrize(
    ("history", "expected"),
    [
        ((), ((), ())),
        (past("A", "Purchases", n=2), (("Purchases",), (2,))),
        (
            past("A", "Purchases", n=1) + past("A", "Rent", n=3, tag="r"),
            (("Rent", "Purchases"), (3, 1)),
        ),
    ],
)
def test_the_live_side_is_derived_by_the_same_rule_bootstrap_uses(
    history: list[Voucher], expected: tuple[tuple[str, ...], tuple[int, ...]]
) -> None:
    """Most-used first, then alphabetical. The order `_resolve` already uses."""
    assert live_vendor_accounts("A", tuple(history)) == expected


def test_a_row_with_no_party_or_no_account_teaches_the_live_side_nothing() -> None:
    """The same two rows `bootstrap._derive` counts as unusable and drops."""
    blank_party = Voucher(
        id="x1",
        date=TODAY,
        party="   ",
        narration="",
        debit_account="Purchases",
        credit_account="Cash",
        amount_paise=1,
    )
    blank_account = Voucher(
        id="x2",
        date=TODAY,
        party="A",
        narration="",
        debit_account="",
        credit_account="Cash",
        amount_paise=1,
    )
    assert live_vendor_accounts("   ", (blank_party,)) == ((), ())
    assert live_vendor_accounts("A", (blank_account,)) == ((), ())
