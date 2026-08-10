"""D-05 on the LIVE path: the evidence reaches the decision, or nothing does.

WHAT WENT WRONG, AND WHY A COMPARISON RULE COULD NOT FIX IT
-----------------------------------------------------------
`tests/test_legal_identity.py` proves the comparison itself. It calls
`compare_suppliers` with two strings and checks the answer. Every one of those
tests passed while the live product merged "Sharma Traders Pvt Ltd" into
"Sharma Traders & Co", because the comparison was never handed the evidence:

    accountant/memory/company.py  index()             fed `MemoryIndex` the
        STRIPPED key as though it were the name the source gave, so the
        decision layer compared a key against a name and called it evidence
    accountant/memory/company.py  observe()           wrote every voucher
        learned after bootstrap with no raw name at all
    accountant/memory/company.py  record_correction() wrote a person's
        explicit answer with no raw name either, so a human decision was
        stored as INCOMPLETE

Three storage and data-flow defects, one symptom. Adding a fourth comparison
rule on top of missing evidence would have changed nothing at all.

WHAT THIS FILE PROVES
---------------------
The whole production flow, end to end and through the public surface:

    bootstrap -> persist -> reload -> build the live index -> observe a new
    voucher -> persist -> record a human correction -> persist -> reload ->
    resolve an identity

and at every stage that the three fields stay three fields:

    raw_subject         the vendor text the source actually gave
    normalised_subject  the lookup key the strip produced
    identity_evidence   COMPLETE or INCOMPLETE, derived from the first

WHAT THIS FILE DOES NOT PROVE
-----------------------------
Nothing here is evidence about TallyPrime. Every run below goes through
`FakeTally`, so it proves what OUR store keeps and what OUR decision layer does
with it. It does not prove that Tally folds a ledger name this way, that a
legal form survives Tally's own round trip, or that two names Tally treats as
one supplier reach one key here.

It does not prove anything about the key itself. `normalise_vendor` keeps the
canonical legal form in the key, so an AMBIGUOUS pair usually falls in two
different buckets and never reaches the comparison; the OUTCOME the owner
required holds either way - no match, no merge, no automatic post, a question -
and the case where the comparison genuinely runs and genuinely answers
AMBIGUOUS is the legacy INCOMPLETE row, which is asserted below.

It measures a question RATE over one synthetic fixture of twenty pairs. That is
a measurement of this rule on these names, not a prediction about a real book.

EVIDENCE CLASS: FakeTally implementation, plus source structure read with `ast`.
"""

from __future__ import annotations

import ast
import datetime
import sqlite3
from pathlib import Path
from typing import NamedTuple

import pytest

from accountant import pipeline
from accountant.extract.adapter import TypedTextExtractor
from accountant.memory import company as co
from accountant.memory.bootstrap import bootstrap, resume
from accountant.memory.company import CompanyMemory
from accountant.memory.identity import (
    IdentityEvidence,
    SupplierVerdict,
    compare_recorded_supplier,
)
from accountant.memory.index import normalise_vendor
from accountant.memory.store import MemoryStore, Observation
from accountant.schema import MatchStatus, Outcome, Voucher
from accountant.tallyio.fake import FakeTally

SAME = SupplierVerdict.SAME
AMBIGUOUS = SupplierVerdict.AMBIGUOUS
COMPLETE = IdentityEvidence.COMPLETE
INCOMPLETE = IdentityEvidence.INCOMPLETE

COMPANY = "Demo Co"
ACCOUNTS = ("Purchases", "Repairs", "Rent", "Cash")
BOOKED = "Purchases"
DATE = datetime.date(2026, 3, 1)
TODAY = datetime.date(2026, 8, 7)
RUN = "run_d05_live"

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / "accountant"
COMPANY_MODULE = PACKAGE / "memory" / "company.py"

#: Ten suppliers read out of Tally by `bootstrap`.
BOOTSTRAPPED: tuple[str, ...] = (
    "M/s Sharma Traders Pvt Ltd",
    "Bharat Steel Ltd",
    "Deshmukh Electricals LLP",
    "Verma Cement & Co",
    "Gupta Hardware",
    "Kumar Motors Inc",
    "Shree Balaji Corp",
    "Ganesh Textiles Private Limited",
    "Dev Enterprises Company",
    "Café Supplies Ltd",
)

#: Ten suppliers learned AFTER bootstrap, through `observe`.
OBSERVED: tuple[str, ...] = (
    "Patil Traders Pvt Ltd",
    "Nadkarni Steel Ltd",
    "Joshi Electricals LLP",
    "Rane Cement & Co",
    "Bose Hardware",
    "Iyer Motors Inc",
    "Naidu Corp",
    "Rao Textiles Private Limited",
    "Menon Enterprises Company",
    "Pillai Supplies Ltd",
)

#: Ten suppliers a person named explicitly, through `record_correction`.
CORRECTED: tuple[str, ...] = (
    "Chopra Traders Pvt Ltd",
    "Bedi Steel Ltd",
    "Sethi Electricals LLP",
    "Ahuja Cement & Co",
    "Kapoor Hardware",
    "Malhotra Motors Inc",
    "Bakshi Corp",
    "Grewal Textiles Private Limited",
    "Dhillon Enterprises Company",
    "Sandhu Supplies Ltd",
)

EVERY_WRITER: tuple[str, ...] = (*BOOTSTRAPPED, *OBSERVED, *CORRECTED)

#: The owner's five LIVE cases, 2026-08-10, as (remembered, arriving, verdict).
#: Each one is driven through persistence, reload and the live index rather
#: than through a direct call to the comparison.
LIVE_CASES: tuple[tuple[str, str, SupplierVerdict], ...] = (
    ("Sharma Traders Pvt Ltd", "Sharma Traders & Co", AMBIGUOUS),
    ("Sharma Traders Pvt Ltd", "M/s Sharma Traders Pvt Ltd", SAME),
    ("Sharma Traders & Co", "Ms. Sharma Traders & Co", SAME),
    ("Acme & Co", "Acme", AMBIGUOUS),
    ("Acme Corp", "Acme Corporation", SAME),
)

#: Every way a supplier can enter this company's memory. Each of the five cases
#: above is run through all three, because the three writers failed in three
#: different places and a case proved on one of them proves nothing about the
#: other two.
WRITERS: tuple[str, ...] = ("bootstrap", "observe", "correction")

#: A supplier nobody asks about, so a company written by `observe` or by
#: `record_correction` still has a READY bootstrap behind it.
FILLER = "Filler Agencies"

#: Twenty bare stems. Each one becomes a pair (X, X Pvt Ltd) in the question
#: rate fixture below. Twenty, because the owner asked for twenty.
QUESTION_RATE_STEMS: tuple[str, ...] = (
    "Sharma Traders",
    "Bharat Steel",
    "Deshmukh Electricals",
    "Verma Cement",
    "Gupta Hardware",
    "Kumar Motors",
    "Shree Balaji",
    "Ganesh Textiles",
    "Dev Enterprises",
    "Patil Traders",
    "Nadkarni Steel",
    "Joshi Electricals",
    "Rane Cement",
    "Bose Hardware",
    "Iyer Motors",
    "Naidu Agencies",
    "Rao Textiles",
    "Menon Enterprises",
    "Pillai Supplies",
    "Chopra Traders",
)


# ---------------------------------------------------------------------------
# the fixture: one company, on disk, driven through the public surface
# ---------------------------------------------------------------------------


def _voucher(vid: str, party: str, account: str = BOOKED) -> Voucher:
    return Voucher(
        id=vid,
        date=DATE,
        party=party,
        narration=f"{party} supply",
        debit_account=account,
        credit_account="Cash",
        amount_paise=100000,
    )


def _tally(parties: tuple[str, ...], account: str = BOOKED) -> FakeTally:
    client = FakeTally()
    client.add_company(
        COMPANY,
        accounts=ACCOUNTS,
        vouchers=tuple(
            _voucher(f"h{n}", party, account) for n, party in enumerate(parties)
        ),
        backed_up=True,
    )
    return client


def _reloaded(path: Path) -> tuple[MemoryStore, CompanyMemory]:
    """Close nothing, open a SECOND store over the same file, and resume.

    Reading back through a store that has never seen the bootstrap is what
    makes "survives persistence" a claim about the file rather than about a
    live object that happens to still be in memory.
    """
    store = MemoryStore(path)
    return store, resume(store, COMPANY)


def _rows_by_key(store: MemoryStore, memory: CompanyMemory) -> dict[str, Observation]:
    return {o.subject: o for o in store.vendors(memory.identity.key)}


# ---------------------------------------------------------------------------
# provenance of every measurement below
# ---------------------------------------------------------------------------


def test_the_accountant_package_under_test_is_the_one_in_this_worktree() -> None:
    """Two earlier measurements in this project were void for exactly this.

    The editable install resolved `accountant` to the main checkout rather than
    to the worktree, so both sides of a before/after comparison ran the same
    unchanged code and the difference measured was zero by construction. Every
    number this file reports is void unless this passes, so it is asserted here
    rather than checked by hand.
    """
    import accountant

    resolved = Path(accountant.__file__).resolve()

    assert str(resolved).startswith(str(ROOT)), (
        f"accountant resolves to {resolved}, which is outside this worktree "
        f"({ROOT}). Every measurement in this file is INVALIDATED until the "
        f"import path is fixed."
    )


# ---------------------------------------------------------------------------
# the complete flow, and the thirty records that have to survive it
# ---------------------------------------------------------------------------


def test_the_whole_flow_keeps_the_raw_name_the_key_and_the_evidence_apart(
    tmp_path: Path,
) -> None:
    """bootstrap -> persist -> reload -> index -> observe -> persist ->
    correct -> persist -> reload -> resolve. Thirty records, three writers.

    Counted rather than sampled, because the three writers fail in three
    different places and a representative sample would have passed on the one
    that already worked.
    """
    path = tmp_path / "memory.sqlite3"

    # 1. bootstrap, from Tally, into a store on disk
    writing = MemoryStore(path)
    booted = bootstrap(_tally(BOOTSTRAPPED), COMPANY, writing)
    assert booted.ready, booted.report.detail
    writing.close()

    # 2. reload from the file alone, and build the live index from it
    second, reopened = _reloaded(path)
    assert reopened.ready
    live = reopened.index()
    for name in BOOTSTRAPPED:
        assert live.lookup(name).accounts == (BOOKED,), name

    # 3. observe ten vouchers the bootstrap never saw
    for n, party in enumerate(OBSERVED):
        reopened.observe(_voucher(f"o{n}", party))
    second.close()

    # 4. reload again, and record ten explicit human answers
    third, again = _reloaded(path)
    for n, party in enumerate(CORRECTED):
        again.record_correction(party, BOOKED, source_voucher_id=f"c{n}")
    third.close()

    # 5. reload one last time and resolve identity from the file
    fourth, final = _reloaded(path)
    rows = _rows_by_key(fourth, final)

    assert len(rows) == 30, f"expected 30 stored suppliers, found {len(rows)}"

    raw_survived = sum(
        1 for name in EVERY_WRITER if rows[normalise_vendor(name)].raw_subject == name
    )
    keys_survived = sum(
        1
        for name in EVERY_WRITER
        if rows[normalise_vendor(name)].subject == normalise_vendor(name)
    )
    complete = sum(
        1
        for name in EVERY_WRITER
        if rows[normalise_vendor(name)].identity_evidence is COMPLETE
    )
    downgraded = sum(
        1
        for name in CORRECTED
        if rows[normalise_vendor(name)].identity_evidence is INCOMPLETE
    )

    assert raw_survived == 30, f"{30 - raw_survived} raw name(s) lost"
    assert keys_survived == 30, f"{30 - keys_survived} normalised key(s) lost"
    assert complete == 30, f"{30 - complete} record(s) read back INCOMPLETE"
    assert downgraded == 0, f"{downgraded} human correction(s) stored as INCOMPLETE"

    # and the reloaded index answers from the raw evidence, for all three writers
    resolved = final.index()
    for name in EVERY_WRITER:
        assert resolved.lookup(name).accounts == (BOOKED,), name
        assert (
            compare_recorded_supplier(rows[normalise_vendor(name)].raw_subject, name)
            is SAME
        ), name
    fourth.close()


def test_bootstrap_preserves_the_raw_supplier_name_through_a_reload(
    tmp_path: Path,
) -> None:
    """The producer end. Ten records, read back out of the file."""
    path = tmp_path / "memory.sqlite3"
    writing = MemoryStore(path)
    bootstrap(_tally(BOOTSTRAPPED), COMPANY, writing)
    writing.close()

    store, memory = _reloaded(path)
    rows = _rows_by_key(store, memory)

    kept = [
        name
        for name in BOOTSTRAPPED
        if rows[normalise_vendor(name)].raw_subject == name
    ]

    assert len(kept) == 10, f"bootstrap kept {len(kept)}/10 raw names"
    store.close()


def test_observe_refuses_to_store_a_new_voucher_without_its_raw_identity(
    tmp_path: Path,
) -> None:
    """The regression test for defect 2. It failed before the fix, on all ten.

    `observe` is the ONLY writer for everything the company does after the
    bootstrap - every voucher we post and read back, and every voucher the
    accountant types into Tally between runs. A build that keeps the raw name
    at bootstrap and drops it here goes blind again on the second day.
    """
    path = tmp_path / "memory.sqlite3"
    writing = MemoryStore(path)
    memory = bootstrap(_tally(BOOTSTRAPPED), COMPANY, writing)
    for n, party in enumerate(OBSERVED):
        memory.observe(_voucher(f"o{n}", party))
    writing.close()

    store, reopened = _reloaded(path)
    rows = _rows_by_key(store, reopened)

    blind = [
        name
        for name in OBSERVED
        if rows[normalise_vendor(name)].identity_evidence is not COMPLETE
    ]

    assert blind == [], f"{len(blind)} observation(s) stored with no raw name: {blind}"
    for name in OBSERVED:
        assert rows[normalise_vendor(name)].raw_subject == name, name
    store.close()


def test_a_human_correction_is_stored_as_complete_evidence(tmp_path: Path) -> None:
    """The regression test for defect 3. A person's explicit answer IS evidence.

    Storing it as INCOMPLETE meant the next entry for the same supplier could
    not use the answer the person had just given, and asked again.
    """
    path = tmp_path / "memory.sqlite3"
    writing = MemoryStore(path)
    memory = bootstrap(_tally(BOOTSTRAPPED), COMPANY, writing)
    for n, party in enumerate(CORRECTED):
        memory.record_correction(party, "Rent", source_voucher_id=f"c{n}")
    writing.close()

    store, reopened = _reloaded(path)
    rows = _rows_by_key(store, reopened)

    for name in CORRECTED:
        row = rows[normalise_vendor(name)]
        assert row.raw_subject == name, name
        assert row.identity_evidence is COMPLETE, name
        assert row.provenance == co.FROM_HUMAN_ANSWER, name
        assert row.company_key == reopened.identity.key, name
        assert row.account == "Rent", name

    # the answer is usable on the live path, which is the point of storing it
    assert reopened.index().lookup(CORRECTED[0]).accounts == ("Rent",)
    store.close()


def test_a_correction_keeps_the_candidates_it_did_not_choose(tmp_path: Path) -> None:
    """A correction is evidence, not an override, so the losers stay on file.

    The previous candidates are exactly what makes the row auditable: without
    them "the person chose Rent" cannot be distinguished from "Rent was the
    only thing on offer".
    """
    path = tmp_path / "memory.sqlite3"
    writing = MemoryStore(path)
    memory = bootstrap(_tally(("Gupta Hardware",)), COMPANY, writing)
    before = memory.lookup("Gupta Hardware")

    after = memory.record_correction("Gupta Hardware", "Rent", source_voucher_id="c9")
    writing.close()

    store, reopened = _reloaded(path)
    rows = store.vendor(reopened.identity.key, normalise_vendor("Gupta Hardware"))
    kept = {o.account: o for o in rows}

    assert before.accounts == (BOOKED,)
    assert set(after.accounts) == {BOOKED, "Rent"}
    assert after.status is co.CompanyMatchStatus.CONFLICTED
    # the candidate the person did NOT pick is still there, with its own count
    assert kept[BOOKED].times == 1
    assert kept[BOOKED].provenance == co.FROM_TALLY_HISTORY
    # and the one they did pick names them as the source
    assert kept["Rent"].provenance == co.FROM_HUMAN_ANSWER
    assert kept["Rent"].source_voucher_ids == ("c9",)
    assert kept["Rent"].raw_subject == "Gupta Hardware"
    store.close()


def test_a_row_written_before_the_column_existed_is_incomplete_on_the_live_path(
    tmp_path: Path,
) -> None:
    """Zero fabricated legacy raw names, and no confident match off one either.

    The NULLs are set by hand because that is exactly what an old file holds: a
    key the strip produced, and no record of the name it came from. Backfilling
    one from `subject` would manufacture the evidence, so the row stays
    INCOMPLETE for ever and never contributes an account.

    Ten of them, not one, so "zero fabricated" is a count rather than a
    sentence - and because this is the ONE shape where the AMBIGUOUS verdict is
    genuinely computed on the live path. Every other ambiguous pair simply
    misses the other's bucket; here the query lands squarely on the stored key
    and the comparison has to refuse it on the evidence.
    """
    path = tmp_path / "memory.sqlite3"
    writing = MemoryStore(path)
    bootstrap(_tally(BOOTSTRAPPED), COMPANY, writing)
    writing.close()

    aged = sqlite3.connect(str(path))
    aged.execute("UPDATE vendor_account SET raw_subject = NULL")
    aged.commit()
    aged.close()

    store, memory = _reloaded(path)
    rows = _rows_by_key(store, memory)
    live = memory.index()

    assert len(rows) == 10
    fabricated = [n for n in BOOTSTRAPPED if rows[normalise_vendor(n)].raw_subject]
    blind = [
        n
        for n in BOOTSTRAPPED
        if rows[normalise_vendor(n)].identity_evidence is INCOMPLETE
    ]
    refused = [n for n in BOOTSTRAPPED if live.lookup(n).status is MatchStatus.NO_MATCH]

    assert fabricated == [], f"{len(fabricated)} legacy raw name(s) fabricated"
    assert len(blind) == 10, f"only {len(blind)}/10 legacy rows read back INCOMPLETE"
    assert len(refused) == 10, f"{10 - len(refused)} legacy row(s) answered confidently"
    for name in BOOTSTRAPPED:
        # AMBIGUOUS genuinely computed on the live path, and contributing nothing
        row = rows[normalise_vendor(name)]
        assert compare_recorded_supplier(row.raw_subject, name) is AMBIGUOUS, name
        assert live.lookup(name).accounts == (), name
        assert live.times_posted(name, BOOKED) == 0, name
    store.close()


@pytest.mark.parametrize("nameless", ["", "   ", "...", " - "])
def test_a_write_with_no_name_in_it_is_stored_incomplete(
    tmp_path: Path, nameless: str
) -> None:
    """The OTHER half of the ruling, and the half a blanket fix would miss.

    "If the input genuinely lacks the information -> INCOMPLETE." A party that
    is blank, or is nothing but punctuation, says nothing about who was paid.
    Storing it as though it were a name would claim COMPLETE evidence over
    nothing - the same false confidence as backfilling a legacy row from its
    key, arrived at from the opposite direction.

    Both writers, because "always keep the raw text" is the obvious fix to
    defects 2 and 3 and it is wrong in exactly this case.
    """
    path = tmp_path / "memory.sqlite3"
    writing = MemoryStore(path)
    memory = bootstrap(_tally((FILLER,)), COMPANY, writing)

    memory.observe(_voucher("o0", nameless, "Rent"))
    memory.record_correction(nameless, "Repairs", source_voucher_id="c0")
    writing.close()

    store, reopened = _reloaded(path)
    nameless_rows = store.vendor(reopened.identity.key, normalise_vendor(nameless))

    assert {o.account for o in nameless_rows} == {"Rent", "Repairs"}
    for row in nameless_rows:
        assert row.raw_subject is None, f"{nameless!r} was stored as a name"
        assert row.identity_evidence is INCOMPLETE, repr(nameless)
    # and the filler, which DOES have a name, is untouched by any of it
    assert reopened.index().lookup(FILLER).accounts == (BOOKED,)
    store.close()


# ---------------------------------------------------------------------------
# the owner's five cases, each one through the live path
# ---------------------------------------------------------------------------


class LiveAnswer(NamedTuple):
    """What the live path did, and the evidence it did it on."""

    status: MatchStatus
    accounts: tuple[str, ...]
    verdict: SupplierVerdict
    proposal: str | None


def _remember(writer: str, path: Path, remembered: str) -> None:
    """Put one supplier into this company's memory by one of the three writers."""
    writing = MemoryStore(path)
    if writer == "bootstrap":
        bootstrap(_tally((FILLER, remembered)), COMPANY, writing)
    else:
        memory = bootstrap(_tally((FILLER,)), COMPANY, writing)
        if writer == "observe":
            memory.observe(_voucher("o0", remembered))
        else:
            memory.record_correction(remembered, BOOKED, source_voucher_id="c0")
    writing.close()


def _live_answer(
    tmp_path: Path, writer: str, remembered: str, arriving: str
) -> LiveAnswer:
    """Remember `remembered`, persist, reload, and ask about `arriving`.

    Returns what the LIVE index answered, the verdict the PERSISTED evidence
    supports, and the account `propose_account` would post - so a test can
    check the decision and the reason for it in one place.
    """
    path = tmp_path / "memory.sqlite3"
    _remember(writer, path, remembered)

    store, memory = _reloaded(path)
    row = _rows_by_key(store, memory)[normalise_vendor(remembered)]
    answer = memory.index().lookup(arriving)
    live = LiveAnswer(
        status=answer.status,
        accounts=answer.accounts,
        verdict=compare_recorded_supplier(row.raw_subject, arriving),
        proposal=co.propose_account(memory, arriving),
    )
    store.close()
    return live


@pytest.mark.parametrize("writer", WRITERS)
@pytest.mark.parametrize(("remembered", "arriving", "expected"), LIVE_CASES)
def test_the_owners_live_identity_case(
    tmp_path: Path,
    writer: str,
    remembered: str,
    arriving: str,
    expected: SupplierVerdict,
) -> None:
    """One owner ruling, driven through the file to a decision, by one writer.

    SAME must reach the remembered account. AMBIGUOUS must reach nothing at
    all: no accounts on the live index and no proposal, which is what the
    review screen turns into a question.
    """
    live = _live_answer(tmp_path, writer, remembered, arriving)
    case = f"{writer}: {remembered} / {arriving}"

    assert live.verdict is expected, case
    if expected is SAME:
        assert live.status is MatchStatus.MATCH, case
        assert live.accounts == (BOOKED,), case
        assert live.proposal == BOOKED, case
    else:
        assert live.status is MatchStatus.NO_MATCH, case
        assert live.accounts == (), case
        assert live.proposal is None, case


def test_every_naming_prefix_reaches_the_same_supplier_on_the_live_path(
    tmp_path: Path,
) -> None:
    """M/s, Ms. and M.S. address an invoice. They never split a supplier."""
    path = tmp_path / "memory.sqlite3"
    writing = MemoryStore(path)
    bootstrap(_tally(("M/s Sharma Traders Pvt Ltd",)), COMPANY, writing)
    writing.close()

    store, memory = _reloaded(path)
    live = memory.index()

    for spelling in (
        "Sharma Traders Pvt Ltd",
        "M/s Sharma Traders Pvt Ltd",
        "Ms. Sharma Traders Pvt Ltd",
        "M.S. Sharma Traders Pvt Ltd",
        "Messrs Sharma Traders Pvt. Ltd.",
    ):
        assert live.lookup(spelling).accounts == (BOOKED,), spelling
        assert co.propose_account(memory, spelling) == BOOKED, spelling
    store.close()


def test_a_bare_name_never_inherits_a_private_limiteds_history(
    tmp_path: Path,
) -> None:
    """Bare name against Pvt Ltd is AMBIGUOUS, in both directions."""
    path = tmp_path / "memory.sqlite3"
    writing = MemoryStore(path)
    bootstrap(_tally(("Sharma Traders Pvt Ltd",)), COMPANY, writing)
    writing.close()

    store, memory = _reloaded(path)
    (row,) = store.vendors(memory.identity.key)

    assert compare_recorded_supplier(row.raw_subject, "Sharma Traders") is AMBIGUOUS
    assert memory.index().lookup("Sharma Traders").status is MatchStatus.NO_MATCH
    assert co.propose_account(memory, "Sharma Traders") is None
    store.close()


# ---------------------------------------------------------------------------
# the three safety invariants, on the production path
# ---------------------------------------------------------------------------


def test_an_ambiguous_supplier_never_silently_merges_two_histories(
    tmp_path: Path,
) -> None:
    """Forty vouchers for one legal person answer for nobody else."""
    path = tmp_path / "memory.sqlite3"
    writing = MemoryStore(path)
    client = FakeTally()
    client.add_company(
        COMPANY,
        accounts=ACCOUNTS,
        vouchers=(
            *(_voucher(f"p{n}", "Sharma Traders Pvt Ltd", BOOKED) for n in range(40)),
            _voucher("c0", "Sharma Traders & Co", "Repairs"),
        ),
        backed_up=True,
    )
    bootstrap(client, COMPANY, writing)
    writing.close()

    store, memory = _reloaded(path)
    live = memory.index()

    assert live.lookup("Sharma Traders Pvt Ltd").accounts == (BOOKED,)
    assert live.lookup("Sharma Traders & Co").accounts == ("Repairs",)
    assert live.times_posted("Sharma Traders & Co", BOOKED) == 0
    assert live.times_posted("Sharma Traders Pvt Ltd", "Repairs") == 0
    # and the bare name, which is ambiguous against both, gets neither
    assert live.lookup("Sharma Traders").status is MatchStatus.NO_MATCH
    assert co.propose_account(memory, "Sharma Traders") is None
    store.close()


def test_an_ambiguous_correction_never_reassigns_the_memory_it_is_near(
    tmp_path: Path,
) -> None:
    """Answering for "& Co" must leave the Pvt Ltd's forty rows exactly as they were."""
    path = tmp_path / "memory.sqlite3"
    writing = MemoryStore(path)
    client = FakeTally()
    client.add_company(
        COMPANY,
        accounts=ACCOUNTS,
        vouchers=tuple(
            _voucher(f"p{n}", "Sharma Traders Pvt Ltd", BOOKED) for n in range(40)
        ),
        backed_up=True,
    )
    memory = bootstrap(client, COMPANY, writing)
    (before,) = writing.vendors(memory.identity.key)

    memory.record_correction("Sharma Traders & Co", "Repairs", source_voucher_id="c1")
    writing.close()

    store, reopened = _reloaded(path)
    rows = _rows_by_key(store, reopened)
    untouched = rows["sharma_traders_pvt_ltd"]

    assert untouched == before, "the correction rewrote a history it was not about"
    assert untouched.times == 40
    assert untouched.account == BOOKED
    assert rows["sharma_traders_and_co"].account == "Repairs"
    assert reopened.index().lookup("Sharma Traders Pvt Ltd").accounts == (BOOKED,)
    store.close()


def test_an_ambiguous_supplier_never_reaches_an_automatic_posting() -> None:
    """The whole pipeline, not the index. Nothing is written to Tally.

    Forty vouchers of "Sharma Traders Pvt Ltd" in the book, an entry typed for
    a bare "Sharma Traders": the two are AMBIGUOUS, so the run has to end in a
    question and the company's books have to be untouched afterwards.
    """
    store = MemoryStore(":memory:")
    client = FakeTally()
    client.add_company(
        COMPANY,
        accounts=ACCOUNTS,
        vouchers=tuple(
            _voucher(f"p{n}", "Sharma Traders Pvt Ltd", BOOKED) for n in range(40)
        ),
        backed_up=True,
    )
    memory = bootstrap(client, COMPANY, store)

    draft = pipeline.run(
        COMPANY,
        b"paid Sharma Traders 4200 for cement",
        "text/plain",
        TypedTextExtractor(),
        client,
        memory,
        today=TODAY,
        log=store,
        run_id=RUN,
    )

    assert draft.voucher.party == "Sharma Traders"
    assert draft.outcome is not Outcome.VALID
    assert client.list_our_vouchers(COMPANY) == (), "an ambiguous entry was posted"
    store.close()


# ---------------------------------------------------------------------------
# the question rate fixture: twenty pairs, every number measured
# ---------------------------------------------------------------------------


class QuestionRate(NamedTuple):
    """What twenty (X, X Pvt Ltd) pairs actually did. No number is inferred."""

    pairs: int
    same: int
    ambiguous: int
    questions: int
    unsafe_merges: int


def _measure_question_rate(path: Path) -> QuestionRate:
    """Remember twenty bare names, then ask about twenty Pvt Ltds of the same stem."""
    writing = MemoryStore(path)
    bootstrap(_tally(QUESTION_RATE_STEMS), COMPANY, writing)
    writing.close()

    store, memory = _reloaded(path)
    rows = _rows_by_key(store, memory)
    live = memory.index()

    same = ambiguous = questions = unsafe = 0
    for stem in QUESTION_RATE_STEMS:
        arriving = f"{stem} Pvt Ltd"
        verdict = compare_recorded_supplier(
            rows[normalise_vendor(stem)].raw_subject, arriving
        )
        if verdict is SAME:
            same += 1
        elif verdict is AMBIGUOUS:
            ambiguous += 1
        if co.propose_account(memory, arriving) is None:
            questions += 1
        if live.lookup(arriving).accounts and verdict is not SAME:
            unsafe += 1
    store.close()

    return QuestionRate(
        pairs=len(QUESTION_RATE_STEMS),
        same=same,
        ambiguous=ambiguous,
        questions=questions,
        unsafe_merges=unsafe,
    )


def test_twenty_same_stem_pairs_produce_questions_and_never_a_silent_merge(
    tmp_path: Path,
) -> None:
    """The fixture that never existed, which is why this was never measurable.

    Under the owner's ruling a bare name against a Pvt Ltd is AMBIGUOUS, so all
    twenty pairs must ask. A pair that MERGED here would be a defect in the
    fix, not a quirk of the fixture, which is why the merge count is asserted
    separately from the verdict count.
    """
    measured = _measure_question_rate(tmp_path / "memory.sqlite3")

    assert measured.pairs == 20
    assert measured.same == 0, f"{measured.same} pair(s) claimed SAME"
    assert measured.ambiguous == 20, f"only {measured.ambiguous}/20 were AMBIGUOUS"
    assert measured.questions == 20, f"only {measured.questions}/20 asked"
    assert measured.unsafe_merges == 0, f"{measured.unsafe_merges} silent merge(s)"


# ---------------------------------------------------------------------------
# the structural guard: the live index may never be built from the key alone
# ---------------------------------------------------------------------------

#: `MemoryIndex.record` documents its first argument as a RAW observed vendor
#: name and records identity evidence as complete. Handing it a stored subject
#: is the defect, so the call has no place in `CompanyMemory.index`.
RAW_NAME_METHOD = "record"

#: `MemoryIndex.record_observed` takes the store's two fields and keeps them
#: two fields. It is the only recording call `index` may make.
EVIDENCE_METHOD = "record_observed"

REQUIRED_KEYWORDS = frozenset({"normalised_subject", "raw_subject", "account"})


class Finding(NamedTuple):
    """One place the live index throws identity evidence away."""

    line: int
    what: str


def _method(module: Path, class_name: str, method_name: str) -> ast.FunctionDef:
    tree = ast.parse(module.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == class_name:
            for child in node.body:
                if isinstance(child, ast.FunctionDef) and child.name == method_name:
                    return child
    raise AssertionError(
        f"{module.relative_to(ROOT)} defines no {class_name}.{method_name}"
    )


def _called_name(call: ast.Call) -> str:
    if isinstance(call.func, ast.Attribute):
        return call.func.attr
    if isinstance(call.func, ast.Name):
        return call.func.id
    return ""


def _live_index_findings() -> list[Finding]:
    """Every place `CompanyMemory.index` presents a stripped key as a raw name."""
    index = _method(COMPANY_MODULE, "CompanyMemory", "index")
    found: list[Finding] = []
    for node in ast.walk(index):
        if not isinstance(node, ast.Call):
            continue
        name = _called_name(node)
        if name == RAW_NAME_METHOD:
            found.append(
                Finding(
                    node.lineno,
                    f"{RAW_NAME_METHOD}(...) records its first argument as a raw "
                    f"observed name; a stored subject is not one",
                )
            )
        if name != EVIDENCE_METHOD:
            continue
        given = {kw.arg for kw in node.keywords}
        missing = sorted(REQUIRED_KEYWORDS - given)
        if missing:
            found.append(
                Finding(node.lineno, f"{EVIDENCE_METHOD}(...) is missing {missing}")
            )
        for kw in node.keywords:
            if kw.arg != "raw_subject":
                continue
            if not (
                isinstance(kw.value, ast.Attribute) and kw.value.attr == "raw_subject"
            ):
                found.append(
                    Finding(
                        node.lineno,
                        "raw_subject= must read the stored observation's own "
                        "raw_subject, never a key or a reconstruction",
                    )
                )
    return found


def _describe(findings: list[Finding]) -> str:
    rel = COMPANY_MODULE.relative_to(ROOT).as_posix()
    return "; ".join(f"{rel}:{f.line} {f.what}" for f in findings)


def test_the_live_index_is_never_built_from_the_stripped_subject_alone() -> None:
    """Read off the source, because the behavioural tests can be satisfied by
    luck: a bucket that happens to hold one supplier answers correctly whether
    or not the evidence reached the comparison. This cannot."""
    findings = _live_index_findings()

    assert findings == [], (
        f"{len(findings)} live-index regression finding(s): {_describe(findings)}"
    )


def test_the_live_index_reads_the_key_and_the_raw_name_as_two_separate_fields() -> None:
    """The control for the guard above. One field cannot carry two facts."""
    index = _method(COMPANY_MODULE, "CompanyMemory", "index")
    read = {n.attr for n in ast.walk(index) if isinstance(n, ast.Attribute)}

    assert "subject" in read, "the live index reads no normalised key"
    assert "raw_subject" in read, "the live index reads no raw name"
