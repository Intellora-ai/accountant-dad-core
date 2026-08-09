"""Two companies, one vendor name, two right answers — through the PIPELINE.

WHY THIS FILE EXISTS SEPARATELY FROM tests/test_memory.py
---------------------------------------------------------
`tests/test_memory.py` already proves isolation at the store: every row carries
a company key, every primary key starts with it, and no query can reach two
companies at once. That is necessary and it is not sufficient. The guarantee has
to survive the layer that actually proposes accounts and writes to Tally, so it
is proved here, at `accountant/pipeline.py`, end to end.

THE SETUP IS THE TEST
---------------------
    Ahuja Builders  -> "Sharma Traders" -> Purchases
    Bansal Motors   -> "Sharma Traders" -> Repairs & Maintenance

Both histories are seeded into their own Tally company and read by `bootstrap`.
Both companies live in ONE `MemoryStore`, bootstrapped one after the other.
Both resolve to the SAME normalised vendor key, so the only thing separating
them is the company key — which is the thing under test.

A single pooled row would not make one of these wrong and one right. It would
make BOTH of them CONFLICTED, and a conflicted vendor asks instead of posting.
That is the failure this file is built to catch.

THE MEASURED REASON
-------------------
`accountant/ingest/crossorg.py`, 16,011 real rows, 30 ordered department pairs:
within-organisation account prediction 53.08% at best, cross-organisation 0.00%
on 29 of the 30. A mapping borrowed from another company is not a weaker
answer. It is a wrong one.
"""

from __future__ import annotations

import datetime

import pytest

from accountant import pipeline
from accountant.extract.adapter import TypedTextExtractor
from accountant.memory.bootstrap import MemorySession, bootstrap, resume
from accountant.memory.company import (
    CompanyMatchStatus,
    CompanyMemory,
    MemoryNotReady,
)
from accountant.memory.index import normalise_vendor
from accountant.memory.store import BootstrapStatus, MemoryStore
from accountant.schema import MatchStatus, Outcome, Voucher
from accountant.tallyio.fake import FakeTally

COMPANY_A = "Ahuja Builders"
COMPANY_B = "Bansal Motors"

VENDOR = "Sharma Traders"
A_ACCOUNT = "Purchases"
B_ACCOUNT = "Repairs & Maintenance"

ACCOUNTS = ("Purchases", "Repairs & Maintenance", "Sundry Expenses", "Cash")
TODAY = datetime.date(2026, 8, 7)

ENTRY = b"paid Sharma Traders 4200 for cement"
NEW_VENDOR_ENTRY = b"paid Verma Cement 900 for bags"
UNSEEN_ENTRY = b"paid Gupta Hardware 1500 for tools"
SEEDED_PER_COMPANY = 30


def seeded(account: str) -> tuple[Voucher, ...]:
    """One company's own posted history for the shared supplier."""
    return tuple(
        Voucher(
            id=f"{normalise_vendor(VENDOR)}-{account}-{i}",
            date=datetime.date(2026, 1, 1),
            party=VENDOR,
            narration=f"{VENDOR} supply",
            debit_account=account,
            credit_account="Cash",
            amount_paise=380000,
        )
        for i in range(SEEDED_PER_COMPANY)
    )


def two_companies() -> FakeTally:
    """One Tally, two companies, one supplier name, two different practices."""
    t = FakeTally()
    t.add_company(
        COMPANY_A, accounts=ACCOUNTS, vouchers=seeded(A_ACCOUNT), backed_up=True
    )
    t.add_company(
        COMPANY_B, accounts=ACCOUNTS, vouchers=seeded(B_ACCOUNT), backed_up=True
    )
    return t


def both(t: FakeTally) -> tuple[MemoryStore, CompanyMemory, CompanyMemory]:
    """ONE store, both companies, bootstrapped one after the other.

    Deliberately one store. Two stores would prove nothing about scoping: the
    file system would be doing the isolating, not the company key.
    """
    store = MemoryStore(":memory:")
    return store, bootstrap(t, COMPANY_A, store), bootstrap(t, COMPANY_B, store)


def draft_for(
    company: str, memory: CompanyMemory, text: bytes = ENTRY
) -> pipeline.Draft:
    return pipeline.build_draft(
        company,
        text,
        "text/plain",
        TypedTextExtractor(),
        ACCOUNTS,
        memory,
        today=TODAY,
    )


def run_for(
    t: FakeTally, company: str, memory: CompanyMemory, text: bytes = ENTRY
) -> pipeline.Draft:
    return pipeline.run(
        company, text, "text/plain", TypedTextExtractor(), t, memory, today=TODAY
    )


# ---- the headline ----------------------------------------------------------


def test_one_vendor_name_two_companies_two_accounts_and_neither_is_conflicted():
    """The whole point, at the layer that posts.

    A pooled row makes BOTH of these conflicted, so `neither is conflicted` is
    the assertion that fails first if the scope ever leaks.
    """
    t = two_companies()
    _, mem_a, mem_b = both(t)

    a = run_for(t, COMPANY_A, mem_a)
    b = run_for(t, COMPANY_B, mem_b)

    assert a.voucher.debit_account == A_ACCOUNT
    assert b.voucher.debit_account == B_ACCOUNT

    assert mem_a.lookup(VENDOR).status is CompanyMatchStatus.MATCH
    assert mem_b.lookup(VENDOR).status is CompanyMatchStatus.MATCH
    assert mem_a.lookup(VENDOR).status is not CompanyMatchStatus.CONFLICTED
    assert mem_b.lookup(VENDOR).status is not CompanyMatchStatus.CONFLICTED

    # Not conflicted means not asked. Both post, and neither raises a problem.
    assert a.outcome is Outcome.VALID
    assert b.outcome is Outcome.VALID
    assert [p.id for p in a.problems] == []
    assert [p.id for p in b.problems] == []
    assert a.posted_tally_id is not None
    assert b.posted_tally_id is not None


def test_both_companies_resolve_the_same_vendor_key_from_the_same_store():
    """Only the company key separates them.

    If the vendor keys differed, the accounts would differ for an uninteresting
    reason and this file would prove nothing.
    """
    t = two_companies()
    store, mem_a, mem_b = both(t)

    key = normalise_vendor(VENDOR)
    a_match = mem_a.lookup(VENDOR)
    b_match = mem_b.lookup(VENDOR)

    assert a_match.subject == key
    assert b_match.subject == key
    assert a_match.company_key != b_match.company_key
    assert a_match.accounts == (A_ACCOUNT,)
    assert b_match.accounts == (B_ACCOUNT,)

    # one store, holding both, and each read sees only its own rows
    rows_a = store.vendors(mem_a.identity.key)
    rows_b = store.vendors(mem_b.identity.key)
    assert {o.account for o in rows_a} == {A_ACCOUNT}
    assert {o.account for o in rows_b} == {B_ACCOUNT}
    assert {o.company_key for o in rows_a} == {mem_a.identity.key}
    assert {o.company_key for o in rows_b} == {mem_b.identity.key}


# ---- handing one company's memory to another company's entry ---------------


def test_one_companys_memory_cannot_build_a_draft_for_another_company():
    t = two_companies()
    _, mem_a, _ = both(t)

    with pytest.raises(ValueError, match=COMPANY_A):
        draft_for(COMPANY_B, mem_a)


def test_one_companys_memory_cannot_evaluate_another_companys_draft():
    """The check is on `evaluate` too, not only on `build_draft`.

    A draft built correctly and then evaluated against the wrong company's
    memory would read that company's history through `memory.index()`, which is
    the same leak arriving one function later.
    """
    t = two_companies()
    _, mem_a, mem_b = both(t)

    d = draft_for(COMPANY_B, mem_b)
    with pytest.raises(ValueError, match=COMPANY_A):
        pipeline.evaluate(d, ACCOUNTS, t.read_vouchers(COMPANY_B), mem_a)

    assert d.decision is None
    assert t.list_our_vouchers(COMPANY_B) == ()


def test_running_one_companys_entry_with_anothers_memory_posts_nothing():
    t = two_companies()
    _, mem_a, _ = both(t)

    with pytest.raises(ValueError, match=COMPANY_A):
        run_for(t, COMPANY_B, mem_a)

    assert t.list_our_vouchers(COMPANY_A) == ()
    assert t.list_our_vouchers(COMPANY_B) == ()


# ---- a correction belongs to the company that gave it ----------------------


def test_a_correction_recorded_on_one_company_leaves_the_other_unchanged():
    """The person answered for THEIR books. Nobody else's books learned anything."""
    t = two_companies()
    _, mem_a, mem_b = both(t)

    mem_a.record_correction("Verma Cement", "Sundry Expenses")

    assert mem_a.lookup("Verma Cement").status is CompanyMatchStatus.MATCH
    assert mem_a.lookup("Verma Cement").accounts == ("Sundry Expenses",)
    assert mem_b.lookup("Verma Cement").status is CompanyMatchStatus.NO_MATCH
    assert mem_b.lookup("Verma Cement").accounts == ()

    # and through the pipeline: A posts straight through, B still asks
    a = run_for(t, COMPANY_A, mem_a, NEW_VENDOR_ENTRY)
    b = run_for(t, COMPANY_B, mem_b, NEW_VENDOR_ENTRY)

    # Both are UNCLEAR, and that is the point: they are unclear about DIFFERENT
    # things. A has learned what the money was for and is only missing how it
    # was paid; B has learned nothing and is still on the first question. Before
    # 2026-08-09 A came out VALID here because `_default_credit` invented its
    # funding leg, so the two companies differed by one field rather than by a
    # whole question.
    assert a.voucher.debit_account == "Sundry Expenses", "A learned"
    assert b.voucher.debit_account == "", "B learned nothing"

    q_a = pipeline.next_question(a)
    q_b = pipeline.next_question(b)
    assert q_a is not None and q_a.problem_id == pipeline.FUNDING_PROBLEM
    assert q_b is not None and q_b.problem_id == "which_account"

    assert b.outcome is Outcome.UNCLEAR
    assert b.posted_tally_id is None
    assert t.list_our_vouchers(COMPANY_B) == ()

    # A finishes and posts; B still cannot.
    accounts_a = t.read_accounts(COMPANY_A)
    a = pipeline.answer(a, "Cash", problem_id=q_a.problem_id)
    a = pipeline.evaluate(a, accounts_a, t.read_vouchers(COMPANY_A), mem_a)
    assert a.outcome is Outcome.VALID
    a = pipeline.post(a, t)
    assert a.posted_tally_id is not None
    assert t.list_our_vouchers(COMPANY_B) == (), "B's books stayed empty throughout"


# ---- switching company ------------------------------------------------------


def test_opening_the_next_company_stops_the_previous_ones_mapping_answering():
    """Exactly one company is open. The old handle stops answering, it does not
    merely get ignored — an ignored handle is one call away from being used."""
    t = two_companies()
    session = MemorySession(MemoryStore(":memory:"))

    mem_a = session.open(t, COMPANY_A)
    a = run_for(t, COMPANY_A, mem_a)
    assert a.voucher.debit_account == A_ACCOUNT

    mem_b = session.open(t, COMPANY_B)
    assert session.current is mem_b
    assert mem_a.ready is False

    with pytest.raises(MemoryNotReady):
        draft_for(COMPANY_A, mem_a)

    b = run_for(t, COMPANY_B, mem_b)
    assert b.voucher.debit_account == B_ACCOUNT
    assert b.voucher.debit_account != A_ACCOUNT
    assert b.outcome is Outcome.VALID


# ---- memory that was never bootstrapped ------------------------------------


def test_run_with_memory_that_was_never_bootstrapped_raises_and_posts_nothing():
    """ "We have not read your books" is not a quiet state. It stops the run."""
    t = two_companies()
    never = resume(MemoryStore(":memory:"), COMPANY_A)

    assert never.ready is False
    assert never.report.status is BootstrapStatus.NEVER_RUN

    with pytest.raises(MemoryNotReady):
        run_for(t, COMPANY_A, never)

    assert t.list_our_vouchers(COMPANY_A) == ()
    assert len(t.read_vouchers(COMPANY_A)) == SEEDED_PER_COMPANY
    assert t.trial_balance(COMPANY_A) == two_companies().trial_balance(COMPANY_A)


# ---- MEMORY_NOT_READY is not NO_MATCH --------------------------------------


def test_memory_not_ready_never_reaches_the_decision_as_no_match():
    """The two look alike and mean opposite things.

    NO_MATCH is a fact about the customer's books — they have never used this
    supplier — and the right response is to ask them. MEMORY_NOT_READY is a fact
    about us — we have not read their books — and we have no standing to ask
    anything yet. Collapsing the second into the first is how a tool with thirty
    years of history in front of it behaves like a fresh install.

    Both halves are asserted, because only the contrast makes the point: the
    same vendor, the same draft, one ready and one not.
    """
    t = two_companies()
    store = MemoryStore(":memory:")
    memory = bootstrap(t, COMPANY_A, store)
    accounts = t.read_accounts(COMPANY_A)
    history = t.read_vouchers(COMPANY_A)

    # READY, and this company genuinely never used this supplier
    # -> NO_MATCH -> one question, nothing posted.
    unseen = draft_for(COMPANY_A, memory, UNSEEN_ENTRY)
    unseen = pipeline.evaluate(unseen, accounts, history, memory)
    assert memory.lookup("Gupta Hardware").status is CompanyMatchStatus.NO_MATCH
    assert unseen.outcome is Outcome.UNCLEAR
    assert "which_account" in [p.id for p in unseen.problems]

    # The same supplier, the same draft, memory no longer ready. It RAISES.
    # No decision is reached, so no question is asked and nothing is posted.
    pending = draft_for(COMPANY_A, memory, UNSEEN_ENTRY)
    memory.invalidate("the Tally connection dropped before the history was read")

    assert memory.lookup("Gupta Hardware").status is (
        CompanyMatchStatus.MEMORY_NOT_READY
    )
    with pytest.raises(MemoryNotReady):
        memory.lookup("Gupta Hardware").as_match_result()
    with pytest.raises(MemoryNotReady):
        pipeline.evaluate(pending, accounts, history, memory)

    assert pending.decision is None
    assert pending.problems == []
    assert pipeline.next_question(pending) is None
    assert t.list_our_vouchers(COMPANY_A) == ()


def test_the_shared_match_status_has_no_value_for_not_ready():
    """There is no NO_MATCH-shaped hole for it to fall into.

    The decision path speaks `MatchStatus`, which has three values and no
    fourth. Giving MEMORY_NOT_READY a home in there — or mapping it onto one of
    the three — is the change this asserts against.
    """
    shared = {s.value for s in MatchStatus}
    assert shared == {"match", "conflicted", "no_match"}
    assert CompanyMatchStatus.MEMORY_NOT_READY.value not in shared
    assert {s.value for s in CompanyMatchStatus} - shared == {"memory_not_ready"}


def test_a_draft_cannot_even_be_built_while_memory_is_not_ready():
    """`propose_account` raises rather than returning None.

    None means "this company's history cannot answer, so ask". Not-ready must
    not be able to borrow that meaning on the way in either.
    """
    t = two_companies()
    memory = bootstrap(t, COMPANY_A, MemoryStore(":memory:"))
    memory.invalidate("bootstrap superseded")

    with pytest.raises(MemoryNotReady):
        draft_for(COMPANY_A, memory)

    assert t.list_our_vouchers(COMPANY_A) == ()
