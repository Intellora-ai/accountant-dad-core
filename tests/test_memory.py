"""Child 2 - company-scoped memory, with a mandatory bootstrap.

The measured fact this package exists to serve (`accountant/ingest/crossorg.py`,
real UK central-government spend, 16,011 rows, 30 ordered department pairs):

    within the same department   53.08% at best
    across departments            0.00% on 29 of the 30 pairs

Mappings do not transfer between organisations, so a pooled model is wasted
effort. The opposite mistake is just as bad and is the one this file spends
most of its assertions on: connecting to an EXISTING company and behaving like
a fresh install.

One section per required property:

1. existing Tally history is loaded before the first proposal
       test_existing_tally_history_is_loaded_before_the_first_proposal
2. empty memory cannot auto-post
       test_empty_memory_cannot_auto_post
3. a bootstrap failure blocks automatic posting
       test_memory_bootstrap_failure_blocks_automatic_posting
4. company A never appears in a company B lookup, and back again - two
   companies with CONTRADICTORY history for one supplier
       test_company_a_context_never_appears_in_a_company_b_lookup
       test_company_b_context_never_appears_in_a_company_a_lookup
5. conflicting company history never guesses
       test_conflicting_company_history_never_guesses
6. an unseen vendor produces a question, never an account
       test_an_unseen_vendor_returns_no_match_which_becomes_a_question
7. an accepted correction updates only the correct company
       test_an_accepted_correction_updates_only_the_correct_company
8. a company switch invalidates and rebuilds
       test_a_company_switch_invalidates_and_rebuilds_memory
9. a partial bootstrap returns MEMORY_NOT_READY
       test_a_partial_bootstrap_returns_memory_not_ready
10. a posted voucher reads back and becomes future company-local context
       test_a_posted_voucher_reads_back_and_becomes_company_local_context

Plus the hard constraints, each with its own test rather than a promise:

    company scoping is structural   test_every_table_is_keyed_by_company
    no pooled row ever exists       test_no_pooled_row_exists_for_a_shared_vendor
    only derived context is stored  test_the_store_holds_no_money_column
    no model or network call        test_the_package_makes_no_network_or_model_call

The Tally side is `FakeTally`, wrapped where the test needs to see the order of
the calls or make one of them fail.
"""

from __future__ import annotations

import ast
import datetime
import socket
from pathlib import Path
from typing import NoReturn

import pytest

from accountant import memory
from accountant.memory import company as co
from accountant.memory import identity as ident
from accountant.memory import index as idx
from accountant.memory import store as st
from accountant.memory.bootstrap import STEPS, MemorySession, bootstrap, resume
from accountant.schema import MatchStatus, Voucher
from accountant.tallyio.client import TallyClient, WriteResult, new_operation_id
from accountant.tallyio.fake import FakeTally

PACKAGE = Path(memory.__file__).parent
TODAY = datetime.date(2026, 8, 8)
AT = datetime.datetime(2026, 8, 8, 6, 30, tzinfo=datetime.UTC)
LATER = datetime.datetime(2026, 8, 9, 6, 30, tzinfo=datetime.UTC)

# Two real companies, one shared supplier, two CONTRADICTORY answers. This pair
# is the whole isolation argument: if anything leaks, one of them is wrong.
A_NAME = "Nagpur Hardware Stores"
B_NAME = "Pune Auto Works"
SHARED_VENDOR = "Sharma Traders"
A_ACCOUNT = "Purchases"
B_ACCOUNT = "Repairs & Maintenance"

A_ACCOUNTS = ("Purchases", "Rent", "Cash", "Bank")
B_ACCOUNTS = ("Repairs & Maintenance", "Purchases", "Cash")

# Anything that could open a connection, plus the model SDKs. This package
# imports none of them.
NETWORK_MODULES = frozenset(
    {"urllib", "socket", "http", "ssl", "ftplib", "requests", "httpx", "asyncio"}
)
MODEL_MODULES = frozenset({"anthropic", "openai", "torch", "transformers"})


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def v(
    party: str,
    account: str,
    *,
    vid: str = "v1",
    narration: str = "monthly supply",
    amount: int = 420000,
) -> Voucher:
    return Voucher(
        id=vid,
        date=TODAY,
        party=party,
        narration=narration,
        debit_account=account,
        credit_account="Cash",
        amount_paise=amount,
    )


class RecordingTally:
    """FakeTally, with every call it receives written down in order.

    The point is ordering: a proposal that happens before `read_vouchers` is a
    proposal made without the company's history, whatever it proposes.
    """

    def __init__(self, inner: FakeTally) -> None:
        self.inner = inner
        self.calls: list[str] = []

    def list_companies(self) -> tuple[str, ...]:
        self.calls.append("list_companies")
        return self.inner.list_companies()

    def read_accounts(self, company: str) -> tuple[str, ...]:
        self.calls.append("read_accounts")
        return self.inner.read_accounts(company)

    def read_vouchers(self, company: str) -> tuple[Voucher, ...]:
        self.calls.append("read_vouchers")
        return self.inner.read_vouchers(company)

    def trial_balance(self, company: str) -> dict[str, int]:
        return self.inner.trial_balance(company)

    def write_voucher(
        self, company: str, voucher: Voucher, operation_id: str
    ) -> WriteResult:
        self.calls.append("write_voucher")
        return self.inner.write_voucher(company, voucher, operation_id)

    def read_by_operation_id(self, company: str, operation_id: str) -> Voucher | None:
        self.calls.append("read_by_operation_id")
        return self.inner.read_by_operation_id(company, operation_id)

    def reverse_by_operation_id(self, company: str, operation_id: str) -> bool:
        return self.inner.reverse_by_operation_id(company, operation_id)

    def list_our_vouchers(self, company: str) -> tuple[Voucher, ...]:
        return self.inner.list_our_vouchers(company)


class BreakingTally(RecordingTally):
    """FakeTally that fails on one named step, and only that one."""

    def __init__(self, inner: FakeTally, breaks: str) -> None:
        super().__init__(inner)
        self.breaks = breaks

    def _maybe_break(self, step: str) -> None:
        if step == self.breaks:
            raise ConnectionError(f"Tally dropped the connection during {step}")

    def read_accounts(self, company: str) -> tuple[str, ...]:
        self._maybe_break("read_accounts")
        return super().read_accounts(company)

    def read_vouchers(self, company: str) -> tuple[Voucher, ...]:
        self._maybe_break("read_vouchers")
        return super().read_vouchers(company)


def tally_with_both_companies() -> FakeTally:
    """One Tally, two companies, one supplier posted to two different accounts."""
    client = FakeTally()
    client.add_company(
        A_NAME,
        accounts=A_ACCOUNTS,
        vouchers=(
            v(SHARED_VENDOR, A_ACCOUNT, vid="a1", narration="cement 50 bags"),
            v(SHARED_VENDOR, A_ACCOUNT, vid="a2", narration="cement 20 bags"),
            v("Nagpur Realty", "Rent", vid="a3", narration="shop rent August"),
        ),
    )
    client.add_company(
        B_NAME,
        accounts=B_ACCOUNTS,
        vouchers=(
            v(SHARED_VENDOR, B_ACCOUNT, vid="b1", narration="clutch plate job"),
            v("M/s Sharma Traders Pvt Ltd", B_ACCOUNT, vid="b2", narration="brake job"),
        ),
    )
    return client


def opened(
    client: TallyClient, name: str, store: st.MemoryStore | None = None
) -> tuple[co.CompanyMemory, st.MemoryStore]:
    keep = store if store is not None else st.MemoryStore()
    return bootstrap(client, name, keep, now=AT), keep


def imported_roots(path: Path) -> set[str]:
    """The top-level module every import in one file reaches for."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            roots.add(node.module.split(".")[0])
    return roots


# ---------------------------------------------------------------------------
# 1. existing history is loaded BEFORE the first proposal
# ---------------------------------------------------------------------------


def test_existing_tally_history_is_loaded_before_the_first_proposal() -> None:
    """The company's own books are read first, and the proposal comes from them.

    Proved three ways at once: the proposal raises before the bootstrap runs,
    the recorded call order shows identity then chart then vouchers, and the
    account that comes back is the one that was only ever in that history.
    """
    client = RecordingTally(tally_with_both_companies())
    store = st.MemoryStore()

    cold = resume(store, A_NAME)
    with pytest.raises(co.MemoryNotReady):
        co.propose_account(cold, SHARED_VENDOR)
    assert client.calls == []

    warm = bootstrap(client, A_NAME, store, now=AT)

    assert client.calls == ["list_companies", "read_accounts", "read_vouchers"]
    assert warm.report.steps == STEPS
    assert co.propose_account(warm, SHARED_VENDOR) == A_ACCOUNT


def test_the_bootstrap_records_what_it_loaded_and_when() -> None:
    """Counts and a timestamp, not a claim that it went fine."""
    memo, _ = opened(tally_with_both_companies(), A_NAME)
    counts = memo.report.counts

    assert memo.report.status is st.BootstrapStatus.READY
    assert (counts.vouchers, counts.vendors, counts.accounts) == (3, 2, 4)
    assert (counts.mappings, counts.conflicts, counts.unusable) == (2, 0, 0)
    assert memo.report.bootstrapped_at == AT.isoformat()
    assert memo.report.attempted_at == AT.isoformat()
    assert memo.chart() == tuple(sorted(A_ACCOUNTS))


def test_the_bootstrap_timestamp_is_the_clock_when_nobody_supplies_one() -> None:
    """Tests pin the clock; the product reads it. Both paths are exercised."""
    memo, _ = opened(tally_with_both_companies(), A_NAME, None)
    live = bootstrap(tally_with_both_companies(), A_NAME, st.MemoryStore())
    read_off_the_clock = datetime.datetime.fromisoformat(live.report.bootstrapped_at)

    assert memo.report.bootstrapped_at == AT.isoformat()
    assert read_off_the_clock.tzinfo is datetime.UTC


def test_a_voucher_with_no_party_or_no_account_is_counted_never_dropped() -> None:
    """A blank key would pool every unusable row into one bucket."""
    client = FakeTally()
    client.add_company(
        A_NAME,
        accounts=A_ACCOUNTS,
        vouchers=(
            v(SHARED_VENDOR, A_ACCOUNT, vid="a1"),
            v("", A_ACCOUNT, vid="a2"),
            v("Ghost Supplier", "", vid="a3"),
            # a narration of nothing but an invoice number leaves no phrase
            v("Deshmukh Electricals", "Rent", vid="a4", narration="4471"),
        ),
    )
    memo, store = opened(client, A_NAME)

    assert memo.report.counts.vouchers == 4
    assert memo.report.counts.unusable == 2
    assert memo.report.counts.vendors == 2
    # a4 is a usable voucher whose narration is only a number, so it teaches a
    # vendor mapping and no phrase at all
    assert {o.subject for o in store.phrases(memo.identity.key)} == {"monthly_supply"}


def test_every_mapping_records_the_voucher_ids_it_came_from() -> None:
    """Provenance, so any stored row can be traced back into Tally."""
    memo, store = opened(tally_with_both_companies(), A_NAME)
    rows = {o.subject: o for o in store.vendors(memo.identity.key)}
    shared = rows[idx.normalise_vendor(SHARED_VENDOR)]

    assert shared.source_voucher_ids == ("a1", "a2")
    assert shared.times == len(shared.source_voucher_ids)
    assert shared.provenance == co.FROM_TALLY_HISTORY


# ---------------------------------------------------------------------------
# 2 and 3. empty memory and failed memory cannot post
# ---------------------------------------------------------------------------


def test_empty_memory_cannot_auto_post() -> None:
    """A real, successful bootstrap of a company with no history at all.

    Status is READY - we did read the books - and every lookup still refuses to
    propose, because there is nothing in them to propose from.
    """
    client = FakeTally()
    client.add_company(A_NAME, accounts=A_ACCOUNTS, vouchers=())
    memo, _ = opened(client, A_NAME)

    assert memo.ready
    assert memo.report.counts.vouchers == 0
    assert memo.lookup(SHARED_VENDOR).status is co.CompanyMatchStatus.NO_MATCH
    assert co.propose_account(memo, SHARED_VENDOR) is None


def test_memory_bootstrap_failure_blocks_automatic_posting() -> None:
    """Tally drops mid-read. Nothing is proposed, nothing is recorded."""
    client = BreakingTally(tally_with_both_companies(), breaks="read_vouchers")
    memo, _ = opened(client, A_NAME)

    assert memo.report.status is st.BootstrapStatus.INCOMPLETE
    assert not memo.ready
    with pytest.raises(co.MemoryNotReady):
        co.propose_account(memo, SHARED_VENDOR)
    with pytest.raises(co.MemoryNotReady):
        memo.record_correction(SHARED_VENDOR, A_ACCOUNT)
    with pytest.raises(co.MemoryNotReady):
        memo.observe(v(SHARED_VENDOR, A_ACCOUNT))


def test_a_partial_bootstrap_returns_memory_not_ready() -> None:
    """Two of four steps done is not a smaller success, it is a failure."""
    client = BreakingTally(tally_with_both_companies(), breaks="read_vouchers")
    memo, store = opened(client, A_NAME)

    assert memo.report.steps == ("identity", "accounts")
    assert memo.lookup(SHARED_VENDOR).status is co.CompanyMatchStatus.MEMORY_NOT_READY
    assert (
        memo.lookup_phrase("cement 50 bags").status
        is co.CompanyMatchStatus.MEMORY_NOT_READY
    )
    assert store.vendors(memo.identity.key) == ()
    assert store.chart(memo.identity.key) == ()


def test_a_failed_bootstrap_names_the_step_that_failed() -> None:
    """'It broke' is not a report. The step and the exception are both named."""
    client = BreakingTally(tally_with_both_companies(), breaks="read_accounts")
    memo, _ = opened(client, A_NAME)

    assert memo.report.steps == ("identity",)
    assert "'accounts'" in memo.report.detail
    assert "ConnectionError" in memo.report.detail


def test_a_company_that_is_not_open_in_tally_is_reported_incomplete() -> None:
    memo, _ = opened(tally_with_both_companies(), "Some Other Firm")

    assert memo.report.status is st.BootstrapStatus.INCOMPLETE
    assert memo.report.steps == ()
    assert "not open in Tally" in memo.report.detail


def test_a_failed_rebuild_keeps_the_time_of_the_last_successful_one() -> None:
    """When we last really read this company stays true after a later failure."""
    client = tally_with_both_companies()
    memo, store = opened(client, A_NAME)
    assert memo.report.bootstrapped_at == AT.isoformat()

    broken = bootstrap(
        BreakingTally(client, breaks="read_vouchers"), A_NAME, store, now=LATER
    )

    assert broken.report.status is st.BootstrapStatus.INCOMPLETE
    assert broken.report.attempted_at == LATER.isoformat()
    assert broken.report.bootstrapped_at == AT.isoformat()


def test_a_company_that_was_never_bootstrapped_answers_memory_not_ready() -> None:
    """Never silently continue with an empty index."""
    memo = resume(st.MemoryStore(), A_NAME)

    assert memo.report.status is st.BootstrapStatus.NEVER_RUN
    assert memo.report.bootstrapped_at == ""
    assert memo.lookup(SHARED_VENDOR).status is co.CompanyMatchStatus.MEMORY_NOT_READY


def test_a_bootstrapped_company_can_be_resumed_without_touching_tally() -> None:
    client = tally_with_both_companies()
    memo, store = opened(client, A_NAME)
    resumed = resume(store, A_NAME)

    assert resumed.ready
    assert resumed.report == memo.report
    assert co.propose_account(resumed, SHARED_VENDOR) == A_ACCOUNT


def test_memory_not_ready_is_never_treated_as_no_match() -> None:
    """The single most dangerous confusion in this package, refused twice.

    NO_MATCH says the customer never used this supplier. MEMORY_NOT_READY says
    we have not read their books. The conversion into the shared type raises
    rather than picking one.
    """
    memo = resume(st.MemoryStore(), A_NAME)
    match = memo.lookup(SHARED_VENDOR)

    assert match.status is not co.CompanyMatchStatus.NO_MATCH
    assert match.status.value not in {shared.value for shared in MatchStatus}
    assert not match.may_propose
    with pytest.raises(co.MemoryNotReady, match="not NO_MATCH"):
        match.as_match_result()


def test_the_three_history_answers_convert_to_the_shared_match_result() -> None:
    """Everything except MEMORY_NOT_READY has a shared equivalent, and uses it."""
    memo, _ = opened(tally_with_both_companies(), B_NAME)

    matched = memo.lookup(SHARED_VENDOR).as_match_result()
    unseen = memo.lookup("Never Heard Of Them").as_match_result()
    memo.record_correction(SHARED_VENDOR, "Purchases")
    conflicted = memo.lookup(SHARED_VENDOR).as_match_result()

    assert matched.status is MatchStatus.MATCH
    assert matched.accounts == (B_ACCOUNT,)
    assert unseen.status is MatchStatus.NO_MATCH
    assert conflicted.status is MatchStatus.CONFLICTED


# ---------------------------------------------------------------------------
# 4. the two-company isolation proof - contradictory history for one supplier
# ---------------------------------------------------------------------------


def test_company_a_context_never_appears_in_a_company_b_lookup() -> None:
    """A says Purchases. B must never hear it.

    Both companies live in ONE store, bootstrapped one after the other, and the
    supplier key is identical in both. The only thing keeping them apart is the
    company key, which is the point of the test.
    """
    client = tally_with_both_companies()
    store = st.MemoryStore()
    bootstrap(client, A_NAME, store, now=AT)
    b = bootstrap(client, B_NAME, store, now=AT)

    match = b.lookup(SHARED_VENDOR)

    assert match.company_key == ident.normalise_company(B_NAME)
    assert match.accounts == (B_ACCOUNT,)
    assert A_ACCOUNT not in match.accounts
    assert co.propose_account(b, SHARED_VENDOR) == B_ACCOUNT


def test_company_b_context_never_appears_in_a_company_a_lookup() -> None:
    """A says Purchases and keeps saying it after B has been loaded."""
    client = tally_with_both_companies()
    store = st.MemoryStore()
    a = bootstrap(client, A_NAME, store, now=AT)
    bootstrap(client, B_NAME, store, now=AT)

    match = a.lookup(SHARED_VENDOR)

    assert match.company_key == ident.normalise_company(A_NAME)
    assert match.accounts == (A_ACCOUNT,)
    assert B_ACCOUNT not in match.accounts
    assert co.propose_account(a, SHARED_VENDOR) == A_ACCOUNT


def test_the_shared_vendor_resolves_to_one_account_in_each_company() -> None:
    """Same key, same store, two answers, and neither is CONFLICTED.

    If a pooled row existed anywhere, both of these would be CONFLICTED instead.
    """
    client = tally_with_both_companies()
    store = st.MemoryStore()
    a = bootstrap(client, A_NAME, store, now=AT)
    b = bootstrap(client, B_NAME, store, now=AT)
    key = idx.normalise_vendor(SHARED_VENDOR)

    assert a.lookup(SHARED_VENDOR).subject == key
    assert b.lookup(SHARED_VENDOR).subject == key
    assert a.lookup(SHARED_VENDOR).status is co.CompanyMatchStatus.MATCH
    assert b.lookup(SHARED_VENDOR).status is co.CompanyMatchStatus.MATCH


def test_no_pooled_row_exists_for_a_shared_vendor() -> None:
    """Read straight out of SQLite: two rows, two company keys, no third."""
    client = tally_with_both_companies()
    store = st.MemoryStore()
    a = bootstrap(client, A_NAME, store, now=AT)
    b = bootstrap(client, B_NAME, store, now=AT)
    key = idx.normalise_vendor(SHARED_VENDOR)

    a_rows = store.vendor(a.identity.key, key)
    b_rows = store.vendor(b.identity.key, key)

    assert [o.account for o in a_rows] == [A_ACCOUNT]
    assert [o.account for o in b_rows] == [B_ACCOUNT]
    assert store.vendor("", key) == ()
    assert {o.company_key for o in (*a_rows, *b_rows)} == {
        a.identity.key,
        b.identity.key,
    }


def test_a_companys_own_index_holds_only_its_own_rows() -> None:
    """The unscoped MemoryIndex is only ever handed one company's rows."""
    client = tally_with_both_companies()
    store = st.MemoryStore()
    a = bootstrap(client, A_NAME, store, now=AT)
    b = bootstrap(client, B_NAME, store, now=AT)

    assert a.index().accounts_ever_used() == frozenset({A_ACCOUNT, "Rent"})
    assert b.index().accounts_ever_used() == frozenset({B_ACCOUNT})
    assert a.index().times_posted(SHARED_VENDOR, A_ACCOUNT) == 2
    assert b.index().times_posted(SHARED_VENDOR, A_ACCOUNT) == 0


def test_every_table_is_keyed_by_company() -> None:
    """Structural, read off the live schema rather than promised in a comment."""
    store = st.MemoryStore()

    tables = store.table_names()
    assert set(tables) == {
        "company",
        "vendor_account",
        "phrase_account",
        "chart_account",
    }
    for table in tables:
        assert "company_key" in store.columns_of(table), table
        assert store.primary_key_of(table)[0] == "company_key", table


def test_the_store_refuses_an_observation_carrying_another_companys_key() -> None:
    """One company's history cannot be written into another's scope."""
    store = st.MemoryStore()
    identity = ident.CompanyIdentity.from_name(A_NAME)
    report = st.BootstrapReport(
        identity=identity,
        status=st.BootstrapStatus.READY,
        detail="hand-built for this test",
        attempted_at=AT.isoformat(),
        bootstrapped_at=AT.isoformat(),
    )
    stowaway = st.Observation(
        company_key=ident.normalise_company(B_NAME),
        subject=idx.normalise_vendor(SHARED_VENDOR),
        account=B_ACCOUNT,
        times=1,
    )

    with pytest.raises(ValueError, match="refusing to store"):
        store.save_bootstrap(report, vendors=(stowaway,))
    with pytest.raises(ValueError, match="refusing to store"):
        store.save_bootstrap(report, phrases=(stowaway,))


# ---------------------------------------------------------------------------
# 5 and 6. conflicted history asks, unseen vendors ask
# ---------------------------------------------------------------------------


def test_conflicting_company_history_never_guesses() -> None:
    """Posted to two accounts, one of them far more often. Still no pick."""
    client = FakeTally()
    client.add_company(
        A_NAME,
        accounts=A_ACCOUNTS,
        vouchers=(
            v(SHARED_VENDOR, A_ACCOUNT, vid="a1"),
            v(SHARED_VENDOR, A_ACCOUNT, vid="a2"),
            v(SHARED_VENDOR, A_ACCOUNT, vid="a3"),
            v(SHARED_VENDOR, "Rent", vid="a4"),
        ),
    )
    memo, _ = opened(client, A_NAME)
    match = memo.lookup(SHARED_VENDOR)

    assert match.status is co.CompanyMatchStatus.CONFLICTED
    assert match.accounts == (A_ACCOUNT, "Rent")
    assert match.times == (3, 1)
    assert not match.may_propose
    assert co.propose_account(memo, SHARED_VENDOR) is None
    assert memo.report.counts.conflicts == 1


def test_an_unseen_vendor_returns_no_match_which_becomes_a_question() -> None:
    """No fallback account. Not Suspense, not Sundry Expenses, not anything."""
    memo, _ = opened(tally_with_both_companies(), A_NAME)
    match = memo.lookup("Somebody Entirely New")

    assert match.status is co.CompanyMatchStatus.NO_MATCH
    assert match.accounts == ()
    assert co.propose_account(memo, "Somebody Entirely New") is None
    assert match.as_match_result().status is MatchStatus.NO_MATCH


def test_spelling_variants_of_one_supplier_collapse_inside_the_company() -> None:
    """Company B posted the same firm under two spellings. One vendor, one key."""
    memo, _ = opened(tally_with_both_companies(), B_NAME)

    assert memo.report.counts.vendors == 1
    assert memo.lookup("SHARMA TRADERS.").accounts == (B_ACCOUNT,)
    assert memo.lookup("M/s Sharma Traders Pvt Ltd").times == (2,)


def test_a_narration_phrase_answers_from_this_companys_own_history() -> None:
    """Exact match on the normalised phrase, and never across companies."""
    client = tally_with_both_companies()
    store = st.MemoryStore()
    a = bootstrap(client, A_NAME, store, now=AT)
    b = bootstrap(client, B_NAME, store, now=AT)

    assert a.lookup_phrase("cement 50 bags").accounts == (A_ACCOUNT,)
    assert a.lookup_phrase("clutch plate job").status is co.CompanyMatchStatus.NO_MATCH
    assert b.lookup_phrase("clutch plate job").accounts == (B_ACCOUNT,)


# ---------------------------------------------------------------------------
# 7. corrections land on one company and no other
# ---------------------------------------------------------------------------


def test_an_accepted_correction_updates_only_the_correct_company() -> None:
    """A person answers for company A. Company B learns nothing."""
    client = tally_with_both_companies()
    store = st.MemoryStore()
    a = bootstrap(client, A_NAME, store, now=AT)
    b = bootstrap(client, B_NAME, store, now=AT)
    newcomer = "Deshmukh Electricals"

    after = a.record_correction(newcomer, "Rent", source_voucher_id="a9")

    assert after.status is co.CompanyMatchStatus.MATCH
    assert co.propose_account(a, newcomer) == "Rent"
    assert b.lookup(newcomer).status is co.CompanyMatchStatus.NO_MATCH
    assert co.propose_account(b, newcomer) is None
    assert store.vendor(b.identity.key, idx.normalise_vendor(newcomer)) == ()


def test_a_correction_is_recorded_as_a_human_answer_with_its_voucher() -> None:
    memo, store = opened(tally_with_both_companies(), A_NAME)
    memo.record_correction("Deshmukh Electricals", "Rent", source_voucher_id="a9")
    memo.record_correction("Deshmukh Electricals", "Rent", source_voucher_id="a9")
    row = store.vendor(memo.identity.key, idx.normalise_vendor("Deshmukh Electricals"))

    assert row[0].provenance == co.FROM_HUMAN_ANSWER
    assert row[0].times == 2
    assert row[0].source_voucher_ids == ("a9",)


def test_a_correction_is_evidence_and_never_overrides_conflicting_history() -> None:
    """One answer does not delete the ledger, so the vendor keeps asking."""
    memo, _ = opened(tally_with_both_companies(), A_NAME)
    after = memo.record_correction(SHARED_VENDOR, "Rent")

    assert after.status is co.CompanyMatchStatus.CONFLICTED
    assert set(after.accounts) == {A_ACCOUNT, "Rent"}
    assert co.propose_account(memo, SHARED_VENDOR) is None


# ---------------------------------------------------------------------------
# 8. a company switch invalidates and rebuilds
# ---------------------------------------------------------------------------


def test_a_company_switch_invalidates_and_rebuilds_memory() -> None:
    """The handle to the company you left stops answering. It does not lie."""
    client = tally_with_both_companies()
    session = MemorySession(st.MemoryStore())

    assert session.current is None
    a = session.open(client, A_NAME, now=AT)
    assert co.propose_account(a, SHARED_VENDOR) == A_ACCOUNT

    b = session.open(client, B_NAME, now=AT)

    assert session.current is b
    assert not a.ready
    assert a.lookup(SHARED_VENDOR).status is co.CompanyMatchStatus.MEMORY_NOT_READY
    with pytest.raises(co.MemoryNotReady):
        co.propose_account(a, SHARED_VENDOR)
    assert co.propose_account(b, SHARED_VENDOR) == B_ACCOUNT


def test_the_previous_companys_answer_is_never_reused_after_a_switch() -> None:
    """Switching back rebuilds from Tally rather than reusing the old handle."""
    client = tally_with_both_companies()
    session = MemorySession(st.MemoryStore())
    first = session.open(client, A_NAME, now=AT)
    session.open(client, B_NAME, now=AT)
    again = session.open(client, A_NAME, now=LATER)

    assert first is not again
    assert not first.ready
    assert "superseded" in first.report.detail
    assert again.report.bootstrapped_at == LATER.isoformat()
    assert co.propose_account(again, SHARED_VENDOR) == A_ACCOUNT


def test_an_invalidated_handle_keeps_the_time_it_last_really_read() -> None:
    memo, _ = opened(tally_with_both_companies(), A_NAME)
    memo.invalidate("this company is no longer open")

    assert memo.report.status is st.BootstrapStatus.INCOMPLETE
    assert memo.report.bootstrapped_at == AT.isoformat()
    assert memo.report.detail == "this company is no longer open"


# ---------------------------------------------------------------------------
# 10. a posted voucher comes back and becomes context
# ---------------------------------------------------------------------------


def test_a_posted_voucher_reads_back_and_becomes_company_local_context() -> None:
    """Learned from what the ledger holds, not from what we believe we sent."""
    client = FakeTally()
    client.add_company(A_NAME, accounts=A_ACCOUNTS, vouchers=())
    memo, store = opened(client, A_NAME)
    newcomer = "Deshmukh Electricals"
    assert memo.lookup(newcomer).status is co.CompanyMatchStatus.NO_MATCH

    op = new_operation_id()
    client.write_voucher(A_NAME, v(newcomer, "Rent", vid="w1"), op)
    back = client.read_by_operation_id(A_NAME, op)
    assert back is not None

    memo.observe(back)

    assert co.propose_account(memo, newcomer) == "Rent"
    row = store.vendor(memo.identity.key, idx.normalise_vendor(newcomer))
    assert row[0].provenance == co.FROM_OUR_POSTING
    assert row[0].source_voucher_ids == ("w1",)


def test_a_posted_voucher_is_still_context_after_the_next_bootstrap() -> None:
    """It is in their ledger now, so a rebuild reads it like any other voucher."""
    client = FakeTally()
    client.add_company(A_NAME, accounts=A_ACCOUNTS, vouchers=())
    memo, store = opened(client, A_NAME)
    newcomer = "Deshmukh Electricals"

    op = new_operation_id()
    client.write_voucher(A_NAME, v(newcomer, "Rent", vid="w1"), op)
    back = client.read_by_operation_id(A_NAME, op)
    assert back is not None
    memo.observe(back)

    rebuilt = bootstrap(client, A_NAME, store, now=LATER)

    assert co.propose_account(rebuilt, newcomer) == "Rent"
    row = store.vendor(rebuilt.identity.key, idx.normalise_vendor(newcomer))
    assert row[0].provenance == co.FROM_OUR_POSTING


def test_observing_a_voucher_with_no_phrase_records_the_vendor_only() -> None:
    """A narration of nothing but our own marker leaves no phrase behind."""
    memo, store = opened(tally_with_both_companies(), A_NAME)
    op = new_operation_id()
    memo.observe(
        v("Deshmukh Electricals", "Rent", vid="w2", narration=f"[ACCOUNTANT_DAD:{op}]")
    )
    key = idx.normalise_vendor("Deshmukh Electricals")

    assert store.vendor(memo.identity.key, key)[0].account == "Rent"
    assert store.phrase(memo.identity.key, "") == ()


def test_a_voucher_typed_by_the_accountant_is_recorded_as_tally_history() -> None:
    memo, store = opened(tally_with_both_companies(), A_NAME)
    memo.observe(v("Deshmukh Electricals", "Rent", vid="t1", narration="fan repair"))
    key = idx.normalise_vendor("Deshmukh Electricals")

    assert store.vendor(memo.identity.key, key)[0].provenance == co.FROM_TALLY_HISTORY
    assert store.phrase(memo.identity.key, "fan_repair")[0].account == "Rent"


# ---------------------------------------------------------------------------
# derived context only, and no model or network call
# ---------------------------------------------------------------------------


def test_the_store_holds_no_money_column() -> None:
    """Tally keeps the books. We keep counts, and counts are not money."""
    store = st.MemoryStore()
    banned = ("amount", "paise", "pence", "value", "money", "total", "tax")

    for table in store.table_names():
        for column in store.columns_of(table):
            assert not any(word in column for word in banned), (table, column)


def test_no_module_in_this_package_uses_a_float() -> None:
    """Money is integer paise everywhere; here it is absent entirely.

    No float literal, and no call to `float`, anywhere in the package.
    """
    for path in sorted(PACKAGE.glob("*.py")):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Constant):
                assert not isinstance(node.value, float), path
            if isinstance(node, ast.Name):
                assert node.id != "float", path


def test_the_package_makes_no_network_or_model_call() -> None:
    """#2.6 - read off the source, so a future import cannot quietly add one."""
    for path in sorted(PACKAGE.glob("*.py")):
        roots = imported_roots(path)
        assert not roots & NETWORK_MODULES, path
        assert not roots & MODEL_MODULES, path


def test_no_memory_operation_opens_a_socket(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verified by breaking the socket, not by reading the imports again."""

    def explode(*_args: object, **_kwargs: object) -> NoReturn:
        raise AssertionError("memory attempted a network call")

    monkeypatch.setattr(socket, "socket", explode)
    monkeypatch.setattr(socket, "create_connection", explode)

    memo, _ = opened(tally_with_both_companies(), A_NAME)
    memo.record_correction("Deshmukh Electricals", "Rent")

    assert co.propose_account(memo, SHARED_VENDOR) == A_ACCOUNT


def test_a_store_on_disk_survives_being_closed_and_reopened(tmp_path: Path) -> None:
    """SQLite, ours, one file. Reopening reads back the same company scope."""
    path = tmp_path / "memory.sqlite3"
    first = st.MemoryStore(path)
    memo = bootstrap(tally_with_both_companies(), A_NAME, first, now=AT)
    first.close()

    second = st.MemoryStore(path)
    resumed = resume(second, A_NAME)

    assert resumed.report == memo.report
    assert co.propose_account(resumed, SHARED_VENDOR) == A_ACCOUNT
    assert second.phrases(resumed.identity.key)[0].company_key == resumed.identity.key
    second.close()


# ---------------------------------------------------------------------------
# the types refuse to describe something untrue
# ---------------------------------------------------------------------------


def test_a_company_name_that_carries_no_identity_is_refused() -> None:
    for blank in ("", "   ", "!!!"):
        with pytest.raises(ValueError, match="carries no identity"):
            ident.CompanyIdentity.from_name(blank)


def test_a_forged_company_key_is_refused() -> None:
    """An identity cannot be handed a key belonging to somebody else's books."""
    with pytest.raises(ValueError, match="is not the identity of"):
        ident.CompanyIdentity(name=A_NAME, key=ident.normalise_company(B_NAME))


def test_company_names_are_normalised_conservatively() -> None:
    """Punctuation and case only. A removed word could merge two ledgers."""
    assert ident.normalise_company("Acme Traders") == "acme_traders"
    assert ident.normalise_company("  ACME,  Traders.  ") == "acme_traders"
    assert ident.normalise_company("Acme Ltd") != ident.normalise_company("Acme LLP")


def test_a_match_inconsistent_with_its_accounts_is_refused() -> None:
    with pytest.raises(ValueError, match="inconsistent"):
        co.CompanyMatch(
            status=co.CompanyMatchStatus.MATCH, company_key="k", subject="s"
        )
    with pytest.raises(ValueError, match="inconsistent"):
        co.CompanyMatch(
            status=co.CompanyMatchStatus.CONFLICTED,
            company_key="k",
            subject="s",
            accounts=("A",),
            times=(1,),
        )
    with pytest.raises(ValueError, match="inconsistent"):
        co.CompanyMatch(
            status=co.CompanyMatchStatus.NO_MATCH,
            company_key="k",
            subject="s",
            accounts=("A",),
            times=(1,),
        )
    with pytest.raises(ValueError, match="inconsistent"):
        co.CompanyMatch(
            status=co.CompanyMatchStatus.MEMORY_NOT_READY,
            company_key="k",
            subject="s",
            accounts=("A",),
            times=(1,),
        )


def test_a_match_must_carry_one_count_per_account() -> None:
    with pytest.raises(ValueError, match="observed count"):
        co.CompanyMatch(
            status=co.CompanyMatchStatus.MATCH,
            company_key="k",
            subject="s",
            accounts=("A",),
        )


def test_a_bootstrap_report_must_state_what_happened() -> None:
    identity = ident.CompanyIdentity.from_name(A_NAME)
    with pytest.raises(ValueError, match="must state what happened"):
        st.BootstrapReport(
            identity=identity,
            status=st.BootstrapStatus.INCOMPLETE,
            detail="   ",
            attempted_at=AT.isoformat(),
        )


def test_a_ready_report_must_carry_the_time_it_succeeded() -> None:
    identity = ident.CompanyIdentity.from_name(A_NAME)
    with pytest.raises(ValueError, match="time it succeeded"):
        st.BootstrapReport(
            identity=identity,
            status=st.BootstrapStatus.READY,
            detail="loaded everything",
            attempted_at=AT.isoformat(),
        )


def test_an_observation_that_was_never_observed_is_refused() -> None:
    with pytest.raises(ValueError, match="never observed"):
        st.Observation(company_key="k", subject="s", account="Rent", times=0)


def test_the_package_exports_what_it_documents() -> None:
    for name in memory.__all__:
        assert hasattr(memory, name)
    assert memory.STEPS == STEPS
    assert len(memory.SCHEMA) == 4


# ---------------------------------------------------------------------------
# the unscoped index underneath, which only ever sees one company's rows
# ---------------------------------------------------------------------------


def test_normalise_vendor_collapses_prefixes_suffixes_and_punctuation() -> None:
    key = idx.normalise_vendor(SHARED_VENDOR)

    assert idx.normalise_vendor("M/s Sharma Traders Pvt Ltd") == key
    assert idx.normalise_vendor("SHARMA TRADERS.") == key
    assert idx.normalise_vendor("Messrs Sharma Traders Private Limited") == key
    assert idx.normalise_vendor("Ms. Sharma Traders & Co") == key
    assert idx.normalise_vendor("Sharma Traders") == "sharma_traders"


def test_normalise_phrase_drops_our_marker_and_every_number() -> None:
    """An invoice number is not a phrase. Leaving it in makes every row unique."""
    op = new_operation_id()

    assert idx.normalise_phrase("Cement 50 bags, inv 4471") == "cement_bags_inv"
    assert idx.normalise_phrase(f"Cement bags [ACCOUNTANT_DAD:{op}]") == "cement_bags"
    assert idx.normalise_phrase("   ") == ""


def test_the_index_learns_from_the_accountant_and_can_be_told_otherwise() -> None:
    """`skip_our_own` is the difference between learning from them and from us."""
    op = new_operation_id()
    ours = v(
        "Deshmukh Electricals",
        "Rent",
        vid="w1",
        narration=f"fan repair [ACCOUNTANT_DAD:{op}]",
    )
    theirs = v(SHARED_VENDOR, A_ACCOUNT, vid="a1")

    skipped = idx.MemoryIndex.from_vouchers((ours, theirs))
    kept = idx.MemoryIndex.from_vouchers((ours, theirs), skip_our_own=False)

    assert skipped.vendors() == frozenset({idx.normalise_vendor(SHARED_VENDOR)})
    assert kept.vendors() == frozenset(
        {idx.normalise_vendor(SHARED_VENDOR), idx.normalise_vendor(ours.party)}
    )
    assert kept.lookup(ours.party).status is MatchStatus.MATCH
    assert kept.lookup(ours.party).accounts == ("Rent",)
    assert skipped.lookup(ours.party).status is MatchStatus.NO_MATCH


def test_the_index_ranks_a_conflicted_vendor_by_how_often_each_was_used() -> None:
    index = idx.MemoryIndex()
    for _ in range(3):
        index.record(SHARED_VENDOR, A_ACCOUNT)
    index.record(SHARED_VENDOR, "Rent")

    conflicted = index.lookup(SHARED_VENDOR)

    assert conflicted.status is MatchStatus.CONFLICTED
    assert conflicted.accounts == (A_ACCOUNT, "Rent")
    assert index.times_posted(SHARED_VENDOR, A_ACCOUNT) == 3
    assert index.lookup("Nobody").status is MatchStatus.NO_MATCH
    assert index.accounts_ever_used() == frozenset({A_ACCOUNT, "Rent"})
