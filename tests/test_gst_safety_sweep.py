"""The GST safety sweep — 30 cases, three arms, one invariant.

THE INVARIANT
-------------
    a bill whose tax cannot be posted faithfully  ->  never VALID, never posted
    a bill with no tax question to answer         ->  VALID, and it posts

Both halves are needed. The first one alone is satisfied by a system that
refuses everything, and a system that refuses everything is not safe, it is
broken. Arm C is the disconfirming arm and it is the reason the other twenty
mean anything.

WHY THIS FILE EXISTS SEPARATELY FROM tests/test_adapter_contract.py
-------------------------------------------------------------------
That file pins the ONE bill the defect was found on. One case proves a defect;
it does not prove a rule. This sweeps thirty, across both the shipped
typed-text path and a stubbed backend that can produce tax states the text
parser cannot reach — a tax of zero, a tax with no total behind it, a tax
larger than the bill.

WHAT "VALID TAX" MEANS HERE, STATED PLAINLY
-------------------------------------------
Arm C is "the tax question does not arise": `tax_paise` is absent, so no tax
line is required and the entry posts. It is NOT "a GST bill that posts with
CGST/SGST/IGST lines on it". **No such case exists in this system and none was
invented for this sweep.** Nothing here builds a tax line, chooses a tax
ledger, decides intra- versus inter-state, or picks a rate. That work does not
exist, and a sweep that pretended otherwise would be measuring a fixture rather
than the product. The gap is recorded in `artifacts/phase7_exits.md` as
NOT_MEASURED with that reason.

HOW A CASE IS MEASURED
----------------------
Every case runs end to end through `pipeline.run` against `FakeTally`, which
calls `RealTally.check_writable`, so a refusal here is the connector's own
refusal and not a restatement of it. For each case the sweep records the
outcome, whether anything posted, whether the books moved by a single paise,
whether a write-ahead row was opened, and whether the application and the
connector agree about postability. The counts are produced by the run and
asserted as exact numbers, never described.
"""

from __future__ import annotations

import datetime
from dataclasses import dataclass, replace

import pytest

from accountant import pipeline
from accountant.extract.adapter import (
    Extractor,
    LineItem,
    StubExtractor,
    TypedTextExtractor,
)
from accountant.memory.bootstrap import bootstrap
from accountant.memory.store import MemoryStore
from accountant.schema import Outcome, Voucher
from accountant.tallyio.fake import FakeTally
from accountant.tallyio.real import TallyError, check_writable
from tests.test_period_handoff import open_books_for

COMPANY = "Demo Co"
ACCOUNTS = ("Purchases", "Sundry Expenses", "Repairs & Maintenance", "Cash")
TODAY = datetime.date(2026, 8, 7)

PARTY = "Sharma Traders"
OTHER = "Verma Cement"
HISTORY_PAISE = 100000
HISTORY_ROWS = 40

#: Outcomes that mean "nothing was posted and the person was told something".
SAFE = frozenset({Outcome.UNCLEAR, Outcome.NOT_VALID})


def past(party: str, account: str, amount: int = HISTORY_PAISE) -> list[Voucher]:
    """Enough consistent history that no detector fires for its own reasons."""
    return [
        Voucher(
            id=f"hist-{party}-{account}-{i}",
            date=datetime.date(2026, 1, 1),
            party=party,
            narration=f"{party} supply",
            debit_account=account,
            credit_account="Cash",
            amount_paise=amount,
        )
        for i in range(HISTORY_ROWS)
    ]


def company(history: list[Voucher]) -> FakeTally:
    t = FakeTally()
    t.add_company(COMPANY, accounts=ACCOUNTS, vouchers=tuple(history), backed_up=True)
    return t


# ---- one case ----------------------------------------------------------------


@dataclass(frozen=True)
class Case:
    """One bill, one expectation, and the history it is judged against."""

    id: str
    arm: str
    bill: bytes
    backend: Extractor
    party: str = PARTY
    #: True for arm C only: this entry is expected to reach VALID and post.
    expect_valid: bool = False


def typed(
    name: str,
    arm: str,
    text: str,
    *,
    party: str = PARTY,
    expect_valid: bool = False,
) -> Case:
    """A case driven through the SHIPPED path — the real typed-text parser."""
    return Case(
        id=name,
        arm=arm,
        bill=text.encode(),
        backend=TypedTextExtractor(),
        party=party,
        expect_valid=expect_valid,
    )


def stubbed(
    name: str,
    arm: str,
    *,
    total_paise: int | None,
    tax_paise: int | None,
    net_paise: int | None = None,
    party: str | None = PARTY,
    expect_valid: bool = False,
    date: datetime.date | None = None,
    line_items: tuple[LineItem, ...] = (),
) -> Case:
    """A case with the tax state set exactly, including states no text produces.

    `net_paise` DEFAULTS TO NONE, and that default is the point. A case that does
    not state the pre-tax figure leaves `net_plus_tax_equals_gross` unable to
    evaluate, which blocks - so the arms below that expect a refusal keep getting
    one for a reason they can name. Only a case that expects to POST states the
    net, because only that case is claiming the arithmetic was checkable.

    It is stated here rather than worked out from `total_paise - tax_paise`:
    `gate.py:119` refuses a derived net because both of those are already inputs
    to that same law, and a number checked against itself passes for ever.
    """
    return Case(
        id=name,
        arm=arm,
        bill=b"a bill",
        backend=StubExtractor(
            party=party,
            total_paise=total_paise,
            tax_paise=tax_paise,
            net_paise=net_paise,
            date=date,
            line_items=line_items,
        ),
        party=party or PARTY,
        expect_valid=expect_valid,
    )


# =============================================================================
# ARM A — the tax is on the bill and no tax line can be built for it
# =============================================================================

MISSING_TAX: tuple[Case, ...] = (
    typed("a5", "missing", "paid Sharma Traders 4200 for cement including 5% GST"),
    typed("a12", "missing", "paid Sharma Traders 4200 for cement including 12% GST"),
    typed("a18", "missing", "paid Sharma Traders 4200 for cement including 18% GST"),
    typed("a28", "missing", "paid Sharma Traders 4200 for cement including 28% GST"),
    typed("a_at_rate", "missing", "paid Sharma Traders 1500 for cement GST @ 18%"),
    stubbed("a_small", "missing", total_paise=50000, tax_paise=7627),
    stubbed("a_round", "missing", total_paise=100000, tax_paise=15254),
    stubbed("a_large", "missing", total_paise=900000, tax_paise=137288),
    stubbed("a_one_rupee_tax", "missing", total_paise=420000, tax_paise=100),
    stubbed("a_tax_is_most_of_it", "missing", total_paise=420000, tax_paise=419999),
)

# =============================================================================
# ARM B — tax data that is present but partial, inconsistent or unsupported
# =============================================================================

INCOMPLETE_TAX: tuple[Case, ...] = (
    # Zero is not absence. A field that says "the tax is nought" is a claim
    # about tax, and nothing in this system can post the line that would carry
    # it, so it is refused rather than read as "no tax at all".
    stubbed("b_zero_tax", "incomplete", total_paise=420000, tax_paise=0),
    stubbed("b_tax_with_no_total", "incomplete", total_paise=None, tax_paise=64068),
    stubbed("b_tax_equals_total", "incomplete", total_paise=420000, tax_paise=420000),
    stubbed("b_tax_exceeds_total", "incomplete", total_paise=420000, tax_paise=500000),
    stubbed(
        "b_tax_no_party", "incomplete", total_paise=420000, tax_paise=64068, party=None
    ),
    stubbed("b_one_paise_tax", "incomplete", total_paise=420000, tax_paise=1),
    stubbed("b_tax_on_one_paise", "incomplete", total_paise=1, tax_paise=1),
    stubbed("b_negative_tax", "incomplete", total_paise=420000, tax_paise=-100),
    stubbed(
        "b_tax_unknown_vendor",
        "incomplete",
        total_paise=420000,
        tax_paise=64068,
        party="Nobody Has Ever Heard Of This Firm",
    ),
    typed(
        "b_tax_new_vendor",
        "incomplete",
        "paid Verma Cement 4200 for bags including 18% GST",
        party=OTHER,
    ),
)

# =============================================================================
# ARM C — no tax question arises, so the entry must still post
# =============================================================================
#
# The disconfirming arm. Without it, "refuse every bill" scores 20/20 above.

VALID_TAX: tuple[Case, ...] = (
    typed(
        "c_cement", "valid", "paid Sharma Traders 4200 for cement", expect_valid=True
    ),
    typed("c_sand", "valid", "paid Sharma Traders 1800 for sand", expect_valid=True),
    typed("c_steel", "valid", "paid Sharma Traders 900 for steel", expect_valid=True),
    typed(
        "c_bricks", "valid", "paid Sharma Traders 2500 for bricks", expect_valid=True
    ),
    typed(
        "c_timber", "valid", "paid Sharma Traders 3300 for timber", expect_valid=True
    ),
    # THE FIVE BELOW STATE THREE AMOUNTS, NOT ONE. They read `tax_paise=None`
    # and no net at all until 2026-08-16, and that is why every one of them was
    # refused: `tax_paise=None` is "nobody looked for the tax", not "there is no
    # tax on this bill", and with no net stated `net_plus_tax_equals_gross` could
    # not be evaluated either. The arm is called "no tax question arises", so the
    # facts now say exactly that - the tax was looked at and it is zero, and the
    # pre-tax figure equals the total because zero tax was added to it.
    #
    # The net is written out per case rather than computed from the total, for
    # the reason `stubbed` states: a derived net is checked against itself.
    # `tax_paise=0`, NOT `None`, SINCE 2026-08-17. These five are arm C - "a bill
    # with no tax on it" - and until today every one of them said `None`, which
    # does not mean that. `None` is the field NOBODY READ, and an unread tax
    # field must block: reading it as zero would post a GST bill without its
    # input credit, which is real money lost with nothing on screen to notice.
    # So the arm meant to prove a tax-free bill POSTS was quietly asking the
    # system to guess, and was correctly refused for it.
    #
    # Zero is the fact the arm is actually about, and stating it is what makes
    # this the disconfirming arm rather than a fifth copy of arm A. Nothing is
    # derived: `net_paise` equals `total_paise` here because these bills carry no
    # tax, and both figures are HANDED IN. `tax_paise=None` still blocks, and
    # arms A and B above are the cases that prove it.
    stubbed(
        "c_stub_small",
        "valid",
        total_paise=90000,
        tax_paise=0,
        net_paise=90000,
        expect_valid=True,
        # Both HANDED IN, neither derived. The date because an invoice's date
        # applies and an unread one scores 0.0, which drags the minimum under
        # ASK_FLOOR before any law is reached. The single line because
        # `lines_sum_to_total` is INDETERMINATE on an empty tuple - no reader
        # here fills `line_items`, so empty means "nobody read the itemisation",
        # not "there are no lines".
        date=TODAY,
        line_items=(LineItem(description="one line", amount_paise=90000),),
    ),
    stubbed(
        "c_stub_mid",
        "valid",
        total_paise=180000,
        tax_paise=0,
        net_paise=180000,
        expect_valid=True,
        # Both HANDED IN, neither derived. The date because an invoice's date
        # applies and an unread one scores 0.0, which drags the minimum under
        # ASK_FLOOR before any law is reached. The single line because
        # `lines_sum_to_total` is INDETERMINATE on an empty tuple - no reader
        # here fills `line_items`, so empty means "nobody read the itemisation",
        # not "there are no lines".
        date=TODAY,
        line_items=(LineItem(description="one line", amount_paise=180000),),
    ),
    stubbed(
        "c_stub_par",
        "valid",
        total_paise=250000,
        tax_paise=0,
        net_paise=250000,
        expect_valid=True,
        # Both HANDED IN, neither derived. The date because an invoice's date
        # applies and an unread one scores 0.0, which drags the minimum under
        # ASK_FLOOR before any law is reached. The single line because
        # `lines_sum_to_total` is INDETERMINATE on an empty tuple - no reader
        # here fills `line_items`, so empty means "nobody read the itemisation",
        # not "there are no lines".
        date=TODAY,
        line_items=(LineItem(description="one line", amount_paise=250000),),
    ),
    stubbed(
        "c_stub_high",
        "valid",
        total_paise=330000,
        tax_paise=0,
        net_paise=330000,
        expect_valid=True,
        # Both HANDED IN, neither derived. The date because an invoice's date
        # applies and an unread one scores 0.0, which drags the minimum under
        # ASK_FLOOR before any law is reached. The single line because
        # `lines_sum_to_total` is INDETERMINATE on an empty tuple - no reader
        # here fills `line_items`, so empty means "nobody read the itemisation",
        # not "there are no lines".
        date=TODAY,
        line_items=(LineItem(description="one line", amount_paise=330000),),
    ),
    stubbed(
        "c_stub_top",
        "valid",
        total_paise=420000,
        tax_paise=0,
        net_paise=420000,
        expect_valid=True,
        # Both HANDED IN, neither derived. The date because an invoice's date
        # applies and an unread one scores 0.0, which drags the minimum under
        # ASK_FLOOR before any law is reached. The single line because
        # `lines_sum_to_total` is INDETERMINATE on an empty tuple - no reader
        # here fills `line_items`, so empty means "nobody read the itemisation",
        # not "there are no lines".
        date=TODAY,
        line_items=(LineItem(description="one line", amount_paise=420000),),
    ),
)

SWEEP: tuple[Case, ...] = MISSING_TAX + INCOMPLETE_TAX + VALID_TAX


# ---- what a run of one case produced -----------------------------------------


@dataclass(frozen=True)
class Measured:
    case: Case
    outcome: Outcome
    posted: bool
    books_moved: bool
    vouchers_written: int
    write_attempted: bool
    connector_accepts: bool
    blank_sources: tuple[str, ...]
    reason: str


def run_case(case: Case) -> Measured:
    """One bill, end to end, against a company that has seen this vendor before."""
    t = company(past(PARTY, "Purchases") + past(OTHER, "Sundry Expenses"))
    store = MemoryStore(":memory:")
    memory = bootstrap(t, COMPANY, store)
    before = t.trial_balance(COMPANY)

    draft = pipeline.run(
        COMPANY,
        case.bill,
        "text/plain",
        case.backend,
        t,
        memory,
        today=TODAY,
        log=store,
        run_id="gst-sweep",
        period_reader=open_books_for(COMPANY),
    )

    # Does the CONNECTOR agree the entry is postable? Asked of the same function
    # the wire uses, not of a copy of it. Both of its refusal types are caught,
    # because the question is "would this have been refused", not "how".
    try:
        check_writable(draft.voucher)
    except (ValueError, TallyError):
        connector_accepts = False
    else:
        connector_accepts = True

    provenance = draft.voucher.provenance or {}
    return Measured(
        case=case,
        outcome=draft.outcome,
        posted=draft.posted_tally_id is not None,
        books_moved=t.trial_balance(COMPANY) != before,
        vouchers_written=len(t.list_our_vouchers(COMPANY)),
        write_attempted=any(
            r.action == pipeline.WRITE_ATTEMPTED for r in store.actions(COMPANY)
        ),
        connector_accepts=connector_accepts,
        blank_sources=tuple(
            sorted(field for field, source in provenance.items() if not source.strip())
        ),
        reason=draft.reason,
    )


@pytest.fixture(scope="module")
def swept() -> list[Measured]:
    """Every case, run once, so the counts below all describe the same run."""
    return [run_case(case) for case in SWEEP]


def of_arm(swept: list[Measured], arm: str) -> list[Measured]:
    return [m for m in swept if m.case.arm == arm]


# =============================================================================
# PER CASE
# =============================================================================


@pytest.mark.parametrize("case", MISSING_TAX + INCOMPLETE_TAX, ids=lambda c: c.id)
def test_a_bill_whose_tax_cannot_be_posted_is_never_called_valid(case: Case) -> None:
    """Arms A and B, one case at a time, with the whole safety claim on each.

    Seven properties, asserted separately, because "it was handled safely" is
    several different claims wearing one sentence and only one of them is about
    the outcome word.
    """
    m = run_case(case)

    assert m.outcome in SAFE, f"{case.id} reached {m.outcome.value}"
    assert not m.posted, f"{case.id} posted"
    assert m.vouchers_written == 0, f"{case.id} wrote {m.vouchers_written} voucher(s)"
    assert not m.books_moved, f"{case.id} moved the trial balance"
    assert not m.write_attempted, (
        f"{case.id} opened a write-ahead row for an entry that was refused; "
        "that row is the durable signature of a voucher that may exist"
    )
    assert m.blank_sources == (), f"{case.id} left a silent blank: {m.blank_sources}"
    assert m.reason.strip(), f"{case.id} refused without saying why"


#: Arm C, split by WHO READ THE BILL. Owner ruling 2026-08-17, decision 2.
#:
#: Both halves have identical arithmetic - a stated net, a stated tax of zero,
#: and a total they add up to - so conservation passes for all ten. What differs
#: is the tier that produced them, and the whole point of the split is that the
#: arithmetic alone does NOT decide whether a bill may post on its own.
#: `decision.AUTO_POST_ALLOWED_TIERS` is `{pdf_text_layer, typed_text}`; `stub`
#: is deliberately absent, so a test double cannot buy auto-post by handing in
#: tidy numbers. That is the rule, and these two tuples measure both sides of it.
TRUSTED_VALID: tuple[Case, ...] = tuple(
    case for case in VALID_TAX if isinstance(case.backend, TypedTextExtractor)
)
UNTRUSTED_VALID: tuple[Case, ...] = tuple(
    case for case in VALID_TAX if isinstance(case.backend, StubExtractor)
)


@pytest.mark.parametrize("case", TRUSTED_VALID, ids=lambda c: c.id)
def test_a_bill_with_no_tax_on_it_still_reaches_valid_and_posts(case: Case) -> None:
    """Arm C. The disconfirming arm.

    If the tax rule had been written as "refuse anything with a money field on
    it", or the funding leg had quietly stopped resolving, arms A and B would
    still score 20/20 and mean nothing. This is the case that fails then.

    TRUSTED TIERS ONLY, since 2026-08-17. These five come through the SHIPPED
    typed-text path, which is on `AUTO_POST_ALLOWED_TIERS`, so they are the
    cases entitled to prove that a tax-free bill really does post. The stubbed
    five are measured just below, in the opposite direction.
    """
    m = run_case(case)

    assert m.outcome is Outcome.VALID, f"{case.id} did not reach VALID: {m.reason}"
    assert m.posted, f"{case.id} was VALID and did not post"
    assert m.vouchers_written == 1
    assert m.books_moved, f"{case.id} posted and moved nothing"
    assert m.connector_accepts, f"{case.id} was VALID and the connector refuses it"
    assert m.blank_sources == ()


@pytest.mark.parametrize("case", UNTRUSTED_VALID, ids=lambda c: c.id)
def test_clean_arithmetic_alone_does_not_let_an_untrusted_tier_post(
    case: Case,
) -> None:
    """Arm C's other half: the arithmetic is perfect and it STILL may not post.

    THIS IS THE TEST THAT STOPS THE OBVIOUS SHORTCUT. These five hand in a net,
    a tax of exactly zero and a total that agrees with both, so every
    conservation law it is possible to satisfy is satisfied - the same numbers
    that let the typed five above post. They still do not post, because
    `decision.AUTO_POST_ALLOWED_TIERS` is `{pdf_text_layer, typed_text}` and the
    `stub` backend is not on it.

    Owner ruling 2026-08-17, decision 2: `stub` is NOT added to that allowlist.
    A fixture that could buy auto-post by stating tidy numbers would make every
    other case in this file meaningless, because any of them could be rewritten
    to do the same.

    NOTHING WRITTEN IS ASSERTED THREE WAYS, not one. "It refused" without "and
    wrote nothing" is exactly the assertion that lets a silent write through.
    """
    m = run_case(case)

    assert m.outcome in SAFE, (
        f"{case.id} reached {m.outcome.value} from an untrusted tier"
    )
    assert not m.posted, f"{case.id} posted from a tier that may not auto-post"
    assert m.vouchers_written == 0
    assert not m.books_moved, f"{case.id} moved the books without posting"


# =============================================================================
# THE COUNTS — produced by the run above, asserted as exact numbers
# =============================================================================


def test_the_sweep_is_thirty_cases_in_three_arms_of_ten() -> None:
    """A sweep of the wrong size passes every count below for the wrong reason."""
    assert len(SWEEP) == 30
    assert len(MISSING_TAX) == 10
    assert len(INCOMPLETE_TAX) == 10
    assert len(VALID_TAX) == 10
    assert len({case.id for case in SWEEP}) == 30, "two cases share an id"


def test_every_missing_tax_case_is_unclear_or_not_valid(swept: list[Measured]) -> None:
    safe = [m for m in of_arm(swept, "missing") if m.outcome in SAFE]

    assert len(safe) == 10, [
        (m.case.id, m.outcome.value) for m in of_arm(swept, "missing")
    ]


def test_every_incomplete_tax_case_is_unclear_or_not_valid(
    swept: list[Measured],
) -> None:
    safe = [m for m in of_arm(swept, "incomplete") if m.outcome in SAFE]

    assert len(safe) == 10, [
        (m.case.id, m.outcome.value) for m in of_arm(swept, "incomplete")
    ]


def test_every_case_with_no_tax_question_reaches_the_expected_valid_result(
    swept: list[Measured],
) -> None:
    valid = [
        m for m in of_arm(swept, "valid") if m.outcome is Outcome.VALID and m.posted
    ]
    # FIVE, NOT TEN, since the owner's ruling of 2026-08-17. Arm C is ten cases
    # and every one of them satisfies the arithmetic; only the five from a
    # trusted tier may post on their own. The other five are accounted for
    # immediately below rather than dropped, so the arm still adds up to ten and
    # a case that silently vanished from either half fails this test.
    trusted = {case.id for case in TRUSTED_VALID}
    assert {m.case.id for m in valid} == trusted, [
        (m.case.id, m.outcome.value, m.posted) for m in of_arm(swept, "valid")
    ]

    blocked = [m for m in of_arm(swept, "valid") if m.case.id not in trusted]
    assert len(valid) + len(blocked) == 10
    assert all(m.outcome in SAFE and not m.posted for m in blocked), [
        (m.case.id, m.outcome.value, m.posted) for m in blocked
    ]


def test_no_case_in_the_sweep_reaches_valid_unsafely(swept: list[Measured]) -> None:
    """Zero unsafe VALIDs. A VALID is unsafe when the tax could not be posted."""
    unsafe = [
        m.case.id for m in swept if m.outcome is Outcome.VALID and m.case.arm != "valid"
    ]

    assert unsafe == []


def test_no_case_in_the_sweep_posts_unsafely(swept: list[Measured]) -> None:
    """Zero unsafe posts, counted three ways: the id, the register, the paise."""
    posted_unsafely = [m.case.id for m in swept if m.posted and m.case.arm != "valid"]
    wrote_unsafely = [
        m.case.id for m in swept if m.vouchers_written and m.case.arm != "valid"
    ]
    moved_unsafely = [
        m.case.id for m in swept if m.books_moved and m.case.arm != "valid"
    ]

    assert posted_unsafely == []
    assert wrote_unsafely == []
    assert moved_unsafely == []


def test_the_connector_never_refuses_what_the_application_called_valid(
    swept: list[Measured],
) -> None:
    """The contract, measured thirty times instead of argued about once.

    This is the defect the sweep was built around: VALID meant "post this" and
    the connector then said no. Both directions are asserted, because agreement
    in one direction only is a system that refuses everything.
    """
    said_valid_and_refused = [
        m.case.id
        for m in swept
        if m.outcome is Outcome.VALID and not m.connector_accepts
    ]

    assert said_valid_and_refused == []
    # FIVE. Arm C's trusted half is the only half that reaches VALID - the
    # stubbed half satisfies the same arithmetic and is still refused, because
    # `stub` is not on `AUTO_POST_ALLOWED_TIERS`. Owner ruling 2026-08-17.
    # Asserted against the trusted set rather than a bare count, so moving a case
    # between halves cannot keep this green by accident.
    assert {m.case.id for m in swept if m.outcome is Outcome.VALID} == {
        case.id for case in TRUSTED_VALID
    }


def test_no_case_in_the_sweep_leaves_a_silent_blank(swept: list[Measured]) -> None:
    """Every field on every voucher says where it came from, refused or not."""
    blanks = {m.case.id: m.blank_sources for m in swept if m.blank_sources}

    assert blanks == {}


def test_every_refused_case_says_something_about_the_tax(
    swept: list[Measured],
) -> None:
    """A refusal the person cannot act on is a breakage with better manners.

    Only the cases refused BY THE TAX RULE are held to this. A bill refused for
    an unknown vendor is refused correctly and owes no sentence about tax, so
    the check is on the rule's own reason string, keyed by the check name.
    """
    by_tax = [m for m in swept if "tax_lines_can_be_posted" in m.reason]
    silent = [m.case.id for m in by_tax if "gst" not in m.reason.lower()]

    assert by_tax, "no case was refused by the tax rule; the sweep proves nothing"
    assert silent == []


def test_the_sweep_actually_exercised_both_backends_and_the_shipped_path(
    swept: list[Measured],
) -> None:
    """A sweep that ran one backend thirty times measures one code path."""
    typed_cases = [m for m in swept if isinstance(m.case.backend, TypedTextExtractor)]
    stub_cases = [m for m in swept if isinstance(m.case.backend, StubExtractor)]

    assert len(typed_cases) >= 10
    assert len(stub_cases) >= 10
    assert len(typed_cases) + len(stub_cases) == 30


def test_every_bill_in_the_unsafe_arms_really_did_carry_a_tax_field() -> None:
    """The fixture check the brief asks for.

    Twenty cases claim to be about tax. If the extractor quietly returned
    `tax_paise=None` for any of them, that case would be refused — or posted —
    for some reason unrelated to tax, and the arm would be measuring nothing.
    """
    without_tax: list[str] = []
    for case in MISSING_TAX + INCOMPLETE_TAX:
        record = case.backend.extract(case.bill, "text/plain")
        if record.tax_paise is None:
            without_tax.append(case.id)

    assert without_tax == [], (
        f"these cases are filed under a tax arm and carry no tax: {without_tax}"
    )


def test_the_arm_c_bills_really_do_carry_no_tax_field() -> None:
    """The other half of the same fixture check.

    IT ASKS FOR ZERO NOW, NOT FOR ABSENCE. Owner ruling 2026-08-17: `tax_paise`
    has three states, and the two this test used to conflate are the two that
    matter most - `None` is "nobody read the tax field", `0` is "somebody read it
    and there is no tax". Arm C is the second. Asserting `is None` here pinned
    the first, so the arm meant to prove a tax-free bill posts was in fact asking
    the system to guess, and being refused for it.

    BOTH NO-TAX STATES ARE ALLOWED HERE, and that is not a softening - it is the
    arm having two kinds of bill in it. The typed cases go through
    `TypedTextExtractor`, which reads no tax field from a sentence at all, so
    they are honestly `None`: nothing was read because there was nothing to read,
    and `TYPED_EXPENSE_NOTE` is a kind the tax law does not apply to. The stub
    cases are judged as full invoices, where the law DOES apply, so they have to
    state the fact: `0`. Demanding one answer for both would force a fixture to
    lie about one of them.

    WHAT IT STILL CATCHES IS THE DIRECTION THAT HURTS: a POSITIVE tax smuggled
    into arm C would be claiming a GST bill posts with no tax line, which is the
    exact failure this whole file exists to make impossible.
    """
    carrying_tax = [
        case.id
        for case in VALID_TAX
        if (case.backend.extract(case.bill, "text/plain").tax_paise or 0) != 0
    ]

    assert carrying_tax == []


def test_a_voucher_that_needs_tax_lines_is_the_same_question_on_both_sides() -> None:
    """One expression, two readers — asserted, not assumed.

    `checks.tax_lines_can_be_posted` and `tallyio.real.check_writable` both ask
    `Voucher.needs_tax_lines`. If somebody re-inlines `gst_paise is not None` in
    one of them, this is the test that notices before the drift reaches a bill.
    """
    postable = Voucher(
        id="v",
        date=TODAY,
        party=PARTY,
        narration="",
        debit_account="Purchases",
        credit_account="Cash",
        amount_paise=420000,
    )

    assert postable.needs_tax_lines is False
    check_writable(postable)

    # ZERO LEFT THIS TUPLE 2026-08-17, and it did not leave the test - it moved
    # below, where it is asserted in the opposite direction. Owner ruling of the
    # same day: `tax_paise` carries three states, not two - a figure that was
    # read and is positive, a figure that was read and is ZERO, and a field
    # nobody read. Only the first two were ever distinguishable here, and this
    # loop collapsed them by asserting that a bill with no tax on it "carries
    # tax". There is no CGST/SGST/IGST line to build for zero, so there was
    # nothing here the connector could not do.
    #
    # -1 STAYS, and it is the reason this is `!= 0` and not `> 0`. A voucher
    # carrying MINUS one paise of GST is a figure no reader should ever produce,
    # which is exactly why it must not be waved through.
    for tax in (1, -1, 64068):
        carrying = replace(postable, gst_paise=tax)

        assert carrying.needs_tax_lines is True
        with pytest.raises(ValueError, match="builds no tax lines"):
            check_writable(carrying)

    # THE THIRD STATE, PINNED. Read, and it is zero: no tax line is owed, so both
    # readers of `needs_tax_lines` must say so and the connector must take it.
    # Asserted here rather than merely removed from the loop above, so that
    # re-inlining `gst_paise is not None` in either reader still fails a test.
    no_tax = replace(postable, gst_paise=0)
    assert no_tax.needs_tax_lines is False
    check_writable(no_tax)
