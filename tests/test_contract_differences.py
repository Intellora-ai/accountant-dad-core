"""Owner item 21: every place `FakeTally` and `RealTally` make a DIFFERENT call.

THE RULE, TAKEN FROM `accountant/tallyio/fake.py`'s OWN DOCSTRING
-----------------------------------------------------------------
    "This double may be softer than `RealTally` about HOW a voucher is fetched
     ... It may never be softer about WHAT to do with what it found. A double
     that makes an easier call than the thing it stands in for does not merely
     fail to catch a bug; it issues an alibi, because a test written against it
     can show an ambiguity being handled when it is not."

Two defects of exactly that shape have already been found and fixed:

    W4  a marker matching two vouchers: `RealTally` refused, the fake picked the
        first one
    W6  a ledger absent from the chart: `RealTally` refused, the fake never
        looked at the chart at all

Both were invisible to `tests/test_tally_contract.py`, which is frozen and runs
against the fake alone. So this file does the thing that finds the next one: it
walks all nine `TallyClient` Protocol methods and drives the SAME call into both
implementations, asserting that the refusal, its class, and the state it leaves
behind are the same.

HOW EACH BACKEND IS DRIVEN
--------------------------
`FakeTally` directly. `RealTally` over `tests/test_real_tally.TallySim`, so the
whole XML build/parse path runs. The transports differ on purpose; the safety
decision must not. Same arrangement as the `BOTH_BACKENDS` section of
`tests/test_adversarial_write_path.py`, widened from the marker-count ladder to
the full method list.

WHAT IS NOT PROVEN HERE
-----------------------
Evidence class: FAKETALLY and SIMULATOR. `TallySim` is a program in this
repository that answers the envelopes `real.py` builds; it is not TallyPrime. So
nothing here is evidence about a licensed Tally's behaviour — only about whether
this repository's two `TallyClient` implementations agree with each other.

In particular, the unknown-company section below proves what `RealTally`'s own
CODE does (it asks the transport and believes the answer). What a live Tally
gateway returns for a company that is not open has NOT been measured, and no
assertion here depends on a guess about it.

DEFECT CLAIMS IN THIS FILE
--------------------------
Two, both recorded in `artifacts/reversal_report.md`, both expected to fail
until the source is fixed:

    the fake accepts seven vouchers `RealTally` refuses at the boundary
    the ambiguity refusal is a different exception CLASS on each backend
"""

from __future__ import annotations

import datetime
import typing
from collections.abc import Callable
from dataclasses import dataclass, replace

import pytest

from accountant import reversal
from accountant.schema import Voucher
from accountant.tallyio import factory, real
from accountant.tallyio.client import (
    CompanyNotBackedUp,
    DuplicateOperation,
    TallyClient,
    marker_for,
    new_operation_id,
    operation_id_in,
)
from accountant.tallyio.factory import RealTallyRequired, real_tally
from accountant.tallyio.fake import FakeTally
from tests.test_real_tally import TallySim

COMPANY = "Demo Co"
ACCOUNTS = ("Purchases", "Cash")
TODAY = datetime.date(2026, 8, 31)

#: Every method the rest of the system is allowed to use. Named here so a tenth
#: appearing in the Protocol fails the guard below instead of quietly arriving
#: uncompared.
PROTOCOL_METHODS = (
    "backed_up",
    "list_companies",
    "list_our_vouchers",
    "read_accounts",
    "read_by_operation_id",
    "read_vouchers",
    "reverse_by_operation_id",
    "trial_balance",
    "write_voucher",
)


def a_voucher(amount_paise: int = 118_000, narration: str = "cement bags") -> Voucher:
    return Voucher(
        id="draft-1",
        date=TODAY,
        party="Sharma Traders",
        narration=narration,
        debit_account="Purchases",
        credit_account="Cash",
        amount_paise=amount_paise,
    )


# ---------------------------------------------------------------------------
# the two backends, driven side by side
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Backend:
    """One `TallyClient`, plus the two backend-specific things a test needs.

    `plant` places a voucher that ALREADY carries a marker, the way a duplicate
    entry or a restored copy of a company file would — the only way to make a
    marker match twice without going through `write_voucher`, which refuses it.
    """

    name: str
    client: TallyClient
    plant: Callable[[str, int], None]
    #: A voucher a person typed. No marker at all, so it is never ours.
    plant_unmarked: Callable[[int], None]

    def ours(self) -> int:
        return len(self.client.list_our_vouchers(COMPANY))

    def balance(self) -> dict[str, int]:
        return self.client.trial_balance(COMPANY)


def a_fake(*, backed_up: bool = True) -> Backend:
    t = FakeTally()
    t.add_company(COMPANY, accounts=ACCOUNTS, backed_up=backed_up)

    def plant(op: str, amount_paise: int) -> None:
        t.seed_voucher(
            COMPANY,
            replace(
                a_voucher(amount_paise, f"cement bags {marker_for(op)}"),
                id=f"planted-{amount_paise}",
            ),
        )

    def plant_unmarked(amount_paise: int) -> None:
        t.seed_voucher(
            COMPANY,
            replace(
                a_voucher(amount_paise, "rent paid by hand"),
                id=f"human-{amount_paise}",
                party="Verma Properties",
            ),
        )

    return Backend("FakeTally", t, plant, plant_unmarked)


def a_real(*, backed_up: bool = True) -> Backend:
    sim = TallySim()
    sim.add_company(COMPANY, ACCOUNTS)
    client = real.RealTally(
        transport=sim,
        backups=real.RecordedBackups(
            frozenset({COMPANY}) if backed_up else frozenset()
        ),
    )

    def plant(op: str, amount_paise: int) -> None:
        sim.seed(
            COMPANY,
            narration=f"cement bags {marker_for(op)}",
            amount_paise=amount_paise,
            debit="Purchases",
            credit="Cash",
            party="Sharma Traders",
        )

    def plant_unmarked(amount_paise: int) -> None:
        sim.seed(
            COMPANY,
            narration="rent paid by hand",
            amount_paise=amount_paise,
            debit="Purchases",
            credit="Cash",
            party="Verma Properties",
        )

    return Backend("RealTally-over-TallySim", client, plant, plant_unmarked)


MakeBackend = Callable[..., Backend]

#: Both implementations this repository has. The ids name the backend, so a
#: failure says WHICH one broke the agreement.
BOTH = pytest.mark.parametrize(
    "make",
    [pytest.param(a_fake, id="FakeTally"), pytest.param(a_real, id="RealTally")],
)


def refusal_from(make: MakeBackend, call: Callable[[Backend], object]) -> BaseException:
    """Run `call` against a fresh backend and hand back what it raised.

    Returns the exception rather than asserting on it, so a test can compare the
    two backends' refusals to each other instead of to a hard-coded string that
    would have to be kept in step with both.
    """
    backend = make()
    try:
        call(backend)
    except BaseException as exc:
        return exc
    raise AssertionError(f"{backend.name} did not refuse; it accepted the call")


# ---------------------------------------------------------------------------
# 0. the surface being compared
# ---------------------------------------------------------------------------


@BOTH
def test_both_backends_satisfy_the_tally_client_protocol(make: MakeBackend):
    assert isinstance(make().client, TallyClient)


def test_the_protocol_still_has_exactly_the_nine_methods_this_file_compares():
    """The guard on the guard.

    A tenth method added to `TallyClient` is a tenth place the two backends can
    disagree. Without this, it would arrive uncompared and this file would go on
    reporting full coverage of a surface that had grown.
    """
    members = set(typing.get_protocol_members(TallyClient))
    assert sorted(members) == sorted(PROTOCOL_METHODS)


@BOTH
def test_both_backends_implement_every_protocol_method_as_a_real_callable(
    make: MakeBackend,
):
    backend = make()
    missing = [
        m for m in PROTOCOL_METHODS if not callable(getattr(backend.client, m, None))
    ]
    assert missing == [], f"{backend.name} is missing {missing}"


# ---------------------------------------------------------------------------
# 1. the backup gate - the same record, the same refusal, on both
# ---------------------------------------------------------------------------


@BOTH
def test_both_backends_refuse_a_write_to_a_company_with_no_recorded_backup(
    make: MakeBackend,
):
    backend = make(backed_up=False)

    with pytest.raises(CompanyNotBackedUp, match="refusing to write"):
        backend.client.write_voucher(COMPANY, a_voucher(), new_operation_id())

    assert backend.ours() == 0
    assert backend.balance() == {}


@BOTH
def test_both_backends_refuse_a_delete_with_no_recorded_backup(make: MakeBackend):
    """The destructive half. Until G5.2 this path was gated on neither backend."""
    backed = make(backed_up=True)
    op = new_operation_id()
    backed.client.write_voucher(COMPANY, a_voucher(), op)
    before = backed.balance()

    unbacked = make(backed_up=False)
    unbacked.plant(op, 118_000)

    with pytest.raises(CompanyNotBackedUp, match="refusing to reverse"):
        unbacked.client.reverse_by_operation_id(COMPANY, op)

    assert unbacked.ours() == 1, "nothing was deleted"
    assert backed.balance() == before


def test_the_two_backends_word_the_backup_refusal_identically():
    """Same sentence, both paths, both backends. A refusal a person can only
    recognise on one backend is a refusal that will be handled on one backend."""
    op = new_operation_id()

    def write(b: Backend) -> object:
        return b.client.write_voucher(COMPANY, a_voucher(), op)

    def delete(b: Backend) -> object:
        b.plant(op, 118_000)
        return b.client.reverse_by_operation_id(COMPANY, op)

    for call in (write, delete):
        fake = refusal_from(lambda: a_fake(backed_up=False), call)
        live = refusal_from(lambda: a_real(backed_up=False), call)
        assert type(fake) is type(live) is CompanyNotBackedUp
        assert str(fake) == str(live)


@BOTH
def test_both_backends_report_the_backup_record_the_same_way(make: MakeBackend):
    assert make(backed_up=True).client.backed_up(COMPANY) is True
    assert make(backed_up=False).client.backed_up(COMPANY) is False


# ---------------------------------------------------------------------------
# 2. the ledger checks - W6's territory
# ---------------------------------------------------------------------------


@BOTH
@pytest.mark.parametrize(
    "ledger",
    ["Nonexistent Ledger", "purchases", "PURCHASES", " Purchases"],
    ids=["absent", "lowercase", "uppercase", "leading_space"],
)
def test_both_backends_refuse_a_write_to_a_ledger_the_chart_does_not_have(
    make: MakeBackend, ledger: str
):
    """Names are compared EXACTLY on both, because Tally creates a ledger it has
    never heard of rather than refusing — so "purchases" makes a second ledger
    beside "Purchases" and the books quietly split in two."""
    backend = make()

    with pytest.raises(real.TallyDataError):
        backend.client.write_voucher(
            COMPANY, replace(a_voucher(), debit_account=ledger), new_operation_id()
        )

    assert backend.ours() == 0
    assert backend.balance() == {}


def test_the_missing_ledger_refusal_opens_with_the_same_sentence_on_both_backends():
    """The identifying half of the message has to match: which operation, which
    company, which ledger. `RealTally` adds two sentences of advice after it,
    which is a difference in helpfulness and not in the decision."""
    op = "ad_missing_ledger_probe"

    def call(b: Backend) -> object:
        return b.client.write_voucher(
            COMPANY, replace(a_voucher(), debit_account="Nope"), op
        )

    fake = str(refusal_from(a_fake, call))
    live = str(refusal_from(a_real, call))
    opening = (
        f"refusing to write operation {op!r} to {COMPANY!r}: the ledger(s) 'Nope' "
        "do not exist there"
    )
    assert fake.startswith(opening), fake
    assert live.startswith(opening), live


# ---- DEFECT CLAIM ----------------------------------------------------------

#: Vouchers `real._check_writable` and `real.check_amount_is_paise`
#: (`real.py:842` and `real.py:871`) refuse at the boundary, before anything
#: reaches the wire. `FakeTally.write_voucher` (`fake.py:139`) mirrors exactly
#: one of these checks — the chart lookup added for W6 — and none of the others.
UNWRITABLE = {
    "a_leg_naming_no_ledger": replace(a_voucher(), credit_account=""),
    "a_zero_amount": replace(a_voucher(), amount_paise=0),
    "a_negative_amount": replace(a_voucher(), amount_paise=-500),
    "a_float_amount": replace(a_voucher(), amount_paise=1000.5),  # type: ignore[arg-type]
    "a_bool_amount": replace(a_voucher(), amount_paise=True),  # type: ignore[arg-type]
    "one_ledger_on_both_legs": replace(a_voucher(), credit_account="Purchases"),
    "an_unbuildable_tax_line": replace(a_voucher(), gst_paise=1800),
}


@pytest.mark.parametrize("case", sorted(UNWRITABLE), ids=sorted(UNWRITABLE))
def test_both_backends_refuse_the_same_unwritable_voucher(case: str):
    """DEFECT CLAIM. Seven vouchers `RealTally` refuses and the double accepts.

    This is W6's exact shape and it was only ever half fixed. `fake.py:158-168`
    added the chart lookup; it did not add the other five clauses of
    `real._check_writable` (`real.py:871-897`) or the type check in
    `real.check_amount_is_paise` (`real.py:842`). So against the double:

        a leg naming no ledger   writes a voucher into a ledger called ''
        a zero or negative amount is accepted
        a float or a bool amount reaches the register unconverted
        one ledger on both legs  writes a voucher that nets to nothing
        a voucher carrying GST   is written with the tax silently dropped

    Measured, the empty-leg case: `FakeTally` returns a clean `WriteResult` and
    the trial balance comes back `{'Purchases': 100000, '': -100000}`. There is
    now a ledger named the empty string in the books of the double every test in
    this repository runs against.

    Why it matters even though `pipeline.post` will not send one of these today:
    the alibi is the point. A test that drives `client.write_voucher` and shows
    an invalid voucher being handled is showing something the double did, not
    something the connector would do — and `pipeline.post`'s gate is one refactor
    away from being the only thing standing between these and somebody's books.

    Fix: mirror `_check_writable` in `FakeTally.write_voucher`, or better, call
    it — it is a module-level function in `real.py`, which `fake.py` already
    imports from.
    """
    voucher = UNWRITABLE[case]
    op = new_operation_id()

    live = refusal_from(a_real, lambda b: b.client.write_voucher(COMPANY, voucher, op))

    backend = a_fake()
    try:
        backend.client.write_voucher(COMPANY, voucher, op)
    except BaseException as exc:
        assert type(exc) is type(live), (
            f"both refuse {case} but with different classes: "
            f"FakeTally {type(exc).__name__}, RealTally {type(live).__name__}"
        )
        return

    raise AssertionError(
        f"FakeTally ACCEPTED {case} and RealTally refused it with "
        f"{type(live).__name__}: {live}. The register now holds "
        f"{backend.ours()} voucher(s) of ours and the trial balance is "
        f"{backend.balance()}."
    )


# ---------------------------------------------------------------------------
# 3. duplicate operation ids - C5
# ---------------------------------------------------------------------------


@BOTH
def test_both_backends_refuse_a_second_write_of_one_operation_id(make: MakeBackend):
    backend = make()
    op = new_operation_id()
    backend.client.write_voucher(COMPANY, a_voucher(), op)
    before = backend.balance()

    with pytest.raises(DuplicateOperation):
        backend.client.write_voucher(COMPANY, a_voucher(), op)

    assert backend.ours() == 1, "a dropped response must not become a second entry"
    assert backend.balance() == before


def test_the_duplicate_refusal_is_the_same_class_and_the_same_sentence_on_both():
    op = "ad_duplicate_probe"

    def call(b: Backend) -> object:
        b.client.write_voucher(COMPANY, a_voucher(), op)
        return b.client.write_voucher(COMPANY, a_voucher(), op)

    fake = refusal_from(a_fake, call)
    live = refusal_from(a_real, call)
    assert type(fake) is type(live) is DuplicateOperation
    assert str(fake) == str(live)


# ---------------------------------------------------------------------------
# 4. two vouchers carrying one operation id - W4
# ---------------------------------------------------------------------------


@BOTH
def test_both_backends_refuse_to_read_one_of_two_vouchers_sharing_a_marker(
    make: MakeBackend,
):
    backend = make()
    op = "ad_ambiguous_probe"
    backend.plant(op, 118_000)
    backend.plant(op, 250_000)
    before = backend.balance()

    with pytest.raises(real.TallyDataError, match="matches 2 vouchers"):
        backend.client.read_by_operation_id(COMPANY, op)

    assert backend.ours() == 2
    assert backend.balance() == before


@BOTH
def test_both_backends_refuse_to_delete_either_of_two_vouchers_sharing_a_marker(
    make: MakeBackend,
):
    """The expensive half. A read that picks wrong shows a wrong number; a
    delete that picks wrong removes a statutory entry and leaves its twin."""
    backend = make()
    op = "ad_ambiguous_probe"
    backend.plant(op, 118_000)
    backend.plant(op, 250_000)
    before = backend.balance()

    with pytest.raises(real.TallyDataError, match="a person has to decide"):
        backend.client.reverse_by_operation_id(COMPANY, op)

    assert backend.ours() == 2, "nothing may be deleted"
    assert backend.balance() == before


def test_the_ambiguity_refusal_is_worded_the_same_on_both_backends():
    """Everything except the locator list, which is backend-specific by
    necessity: the fake has draft ids, the connector has MASTERIDs."""
    op = "ad_ambiguous_probe"

    def call(b: Backend) -> object:
        b.plant(op, 118_000)
        b.plant(op, 250_000)
        return b.client.read_by_operation_id(COMPANY, op)

    fake = str(refusal_from(a_fake, call))
    live = str(refusal_from(a_real, call))

    head = f"operation {op!r} matches 2 vouchers in {COMPANY!r} ("
    tail = (
        "The narration marker is this system's identity and it has to be "
        "unique. Refusing to read one back or delete any of them: a person has "
        "to decide which is real."
    )
    assert fake.startswith(head) and live.startswith(head)
    assert fake.endswith(tail) and live.endswith(tail)

    # The locator list is the one part that legitimately differs. It still has
    # to NAME two things on each backend, or the refusal sends a person hunting
    # through a whole ledger for vouchers it declined to identify.
    for said in (fake, live):
        locators = said[len(head) : -len(tail)]
        assert locators.count(";") == 1, f"two locators, one separator: {locators!r}"
    assert "118000" in fake and "250000" in fake, "the fake names the amounts"
    assert "MASTERID=M1" in live and "MASTERID=M2" in live, (
        "the connector names the MASTERIDs, which is what a person types into "
        "Tally to find them"
    )


# ---- DEFECT CLAIM ----------------------------------------------------------


def test_the_ambiguity_refusal_is_the_same_exception_class_on_both_backends():
    """DEFECT CLAIM. `RealTally` raises `AmbiguousMarker`; the fake raises the
    bare `TallyDataError` it was written against before that class existed.

    `fake.py:23-27` states the agreement: the two "raise the SAME
    `TallyDataError`, worded the same way, so one assertion holds both
    backends". That was true when it was written. `real.AmbiguousMarker`
    (`real.py:341`) was added afterwards — "its own class so a caller can branch
    on the ambiguity without reading the English" — and `fake.py:216` was not
    moved with it.

    The consequence is narrow and real. `AmbiguousMarker` carries
    `outcome = ReadBackOutcome.MULTIPLE_MATCHES`, and `RealTally._prove_it_is_ours`
    (`real.py:2326`) already branches on the class rather than the message. Any
    caller that does the same is a caller whose ambiguity handling cannot be
    exercised against the double: `except AmbiguousMarker` simply will not catch
    what the fake throws.

    Fix: import `AmbiguousMarker` in `fake.py` — it already imports
    `TallyDataError` from `real.py`, so the direction of the dependency does not
    change — and raise that instead.
    """
    op = "ad_ambiguous_probe"

    def call(b: Backend) -> object:
        b.plant(op, 118_000)
        b.plant(op, 250_000)
        return b.client.read_by_operation_id(COMPANY, op)

    fake = refusal_from(a_fake, call)
    live = refusal_from(a_real, call)

    assert isinstance(fake, real.TallyDataError)
    assert isinstance(live, real.TallyDataError)
    assert type(fake) is type(live), (
        f"the ambiguity refusal is {type(fake).__name__} on FakeTally and "
        f"{type(live).__name__} on RealTally, so a caller branching on the "
        "class is tested against a backend that cannot produce it"
    )


# ---------------------------------------------------------------------------
# 5. an unknown operation id - the zero row of the ladder
# ---------------------------------------------------------------------------


@BOTH
def test_both_backends_answer_none_for_an_operation_id_that_was_never_written(
    make: MakeBackend,
):
    backend = make()
    backend.client.write_voucher(COMPANY, a_voucher(), new_operation_id())

    assert backend.client.read_by_operation_id(COMPANY, "ad_never_written") is None


@BOTH
def test_both_backends_answer_false_when_asked_to_delete_an_unknown_operation_id(
    make: MakeBackend,
):
    """False, not an exception. "It is not there" is an answer, and turning it
    into an error would make a second reversal look like a failure."""
    backend = make()
    backend.client.write_voucher(COMPANY, a_voucher(), new_operation_id())
    before = backend.balance()

    assert backend.client.reverse_by_operation_id(COMPANY, "ad_never_written") is False

    assert backend.ours() == 1
    assert backend.balance() == before


@BOTH
def test_reversing_the_same_operation_twice_is_safe_on_both_backends(
    make: MakeBackend,
):
    backend = make()
    op = new_operation_id()
    backend.client.write_voucher(COMPANY, a_voucher(), op)

    assert backend.client.reverse_by_operation_id(COMPANY, op) is True
    assert backend.client.reverse_by_operation_id(COMPANY, op) is False
    assert backend.ours() == 0
    assert backend.balance() == {}


@BOTH
def test_both_backends_agree_on_the_marker_count_ladder(make: MakeBackend):
    """Zero, one, two, in one run, so the two ends anchor the middle. A backend
    that refused EVERY read would pass the ambiguity tests and be useless."""
    backend = make()
    op = "ad_ladder_probe"

    assert backend.client.read_by_operation_id(COMPANY, op) is None
    assert backend.client.reverse_by_operation_id(COMPANY, op) is False

    backend.plant(op, 118_000)
    found = backend.client.read_by_operation_id(COMPANY, op)
    assert found is not None
    assert found.amount_paise == 118_000

    backend.plant(op, 250_000)
    before = backend.balance()
    with pytest.raises(real.TallyDataError, match="matches 2 vouchers"):
        backend.client.read_by_operation_id(COMPANY, op)
    assert backend.ours() == 2
    assert backend.balance() == before


@BOTH
def test_both_backends_delete_exactly_the_marked_voucher_and_no_lookalike(
    make: MakeBackend,
):
    """Same landlord, same rent, same narration, three months running. C4."""
    backend = make()
    ops = [new_operation_id() for _ in range(3)]
    for op in ops:
        backend.client.write_voucher(COMPANY, a_voucher(2_000_000, "monthly rent"), op)

    assert backend.client.reverse_by_operation_id(COMPANY, ops[1]) is True

    remaining = {
        operation_id_in(v.narration) for v in backend.client.list_our_vouchers(COMPANY)
    }
    assert remaining == {ops[0], ops[2]}
    assert backend.balance() == {"Purchases": 4_000_000, "Cash": -4_000_000}


# ---------------------------------------------------------------------------
# 6. a company that is not there - a difference, in the OTHER direction
# ---------------------------------------------------------------------------


class _EmptyGateway:
    """A gateway that answers every Export with an empty collection.

    Not a claim about TallyPrime. It is the transport that lets `RealTally`'s
    OWN behaviour be seen: the connector has no company-existence check of any
    kind, so whatever the transport says about an unknown company is what the
    connector reports. That is the property under test, and it holds whatever a
    live gateway would actually return.
    """

    def __init__(self) -> None:
        self.sent: list[str] = []

    def send(self, payload: str, *, retry: bool) -> str:  # noqa: ARG002 - protocol
        self.sent.append(payload)
        return (
            "<ENVELOPE><BODY><DATA><COLLECTION></COLLECTION></DATA></BODY></ENVELOPE>"
        )


GHOST = "Ghots Co"  # a company name with a typo in it


def test_the_fake_refuses_every_call_about_a_company_it_does_not_have():
    """`FakeTally` knows its own company list and answers from it."""
    t = a_fake().client
    assert isinstance(t, FakeTally)

    for call in (
        lambda: t.read_accounts(GHOST),
        lambda: t.read_vouchers(GHOST),
        lambda: t.trial_balance(GHOST),
        lambda: t.list_our_vouchers(GHOST),
        lambda: t.read_by_operation_id(GHOST, "ad_x"),
        lambda: t.reverse_by_operation_id(GHOST, "ad_x"),
        lambda: t.backed_up(GHOST),
    ):
        with pytest.raises(KeyError, match="no such company"):
            call()


def test_the_connector_reports_a_company_it_cannot_see_as_an_empty_company():
    """The difference, and it is the double being HARDER than the connector.

    Same alibi shape as W4 and W6, running the other way: a test written against
    the fake shows a misspelled company being refused, and the connector does not
    refuse it. Nothing here is destructive — every answer is empty — but "this
    company has no vouchers of ours" and "there is no such company" are opposite
    facts, and only one of them means a cleanup succeeded.
    """
    gateway = _EmptyGateway()
    client = real.RealTally(
        transport=gateway, backups=real.RecordedBackups(frozenset({GHOST}))
    )

    assert client.read_accounts(GHOST) == ()
    assert client.read_vouchers(GHOST) == ()
    assert client.trial_balance(GHOST) == {}
    assert client.list_our_vouchers(GHOST) == ()
    assert client.read_by_operation_id(GHOST, "ad_x") is None
    assert client.reverse_by_operation_id(GHOST, "ad_x") is False


def test_the_connector_answers_backed_up_for_a_company_with_no_round_trip_at_all():
    """The sharpest instance, and it needs no assumption about any transport.

    `RealTally.backed_up` reads the operator's declared list and nothing else. A
    company name that has never existed comes back True if somebody typed it
    into `ACCOUNTANT_BACKED_UP` or after `--backed-up`.
    """
    gateway = _EmptyGateway()
    client = real.RealTally(
        transport=gateway, backups=real.RecordedBackups(frozenset({GHOST}))
    )

    assert client.backed_up(GHOST) is True
    assert gateway.sent == [], "no request was made to find that out"


def test_a_bulk_preview_of_a_misspelled_company_reports_nothing_of_ours():
    """The consequence, spelled out at the layer an operator sees.

    On the connector, a typo in `--company` produces a batch with zero
    candidates and a COMPLETED run. On the double the same typo raises. Neither
    is dangerous; they are different, and only the second one tells the person.
    """
    client = real.RealTally(
        transport=_EmptyGateway(), backups=real.RecordedBackups(frozenset({GHOST}))
    )

    batch = reversal.preview(client, GHOST, batch_id="b1")
    done = reversal.execute(reversal.confirm(batch), client, company_key=GHOST)

    assert done.state is reversal.BatchState.COMPLETED
    assert done.outcomes == ()
    assert f"nothing of ours in {GHOST!r}" in done.detail

    t = a_fake().client
    with pytest.raises(KeyError, match="no such company"):
        reversal.preview(t, GHOST, batch_id="b1")


def test_the_factory_is_what_closes_that_gap_on_both_operator_surfaces(
    monkeypatch: pytest.MonkeyPatch,
):
    """And it does close it, which is why the difference above is a finding and
    not a defect. `real_tally` lists the open companies and refuses a name that
    is not among them, before any client is handed out — and both operator
    surfaces, the CLI and the web app, construct their client through it.

    The transport is substituted, not the factory: everything from
    `list_companies` down is the real code path.
    """
    sim = TallySim()
    sim.add_company(COMPANY, ACCOUNTS)

    class _Gateway:
        """`TallySim`, plus the one thing the factory asks that it cannot answer.

        A11, measured 2026-08-09: the live gateway does not answer
        `$$LicenseInfo` at all. Refusing it here is the honest simulation, and
        `read_licence` is documented never to raise, so the factory must survive
        it and report UNKNOWN.
        """

        def send(self, payload: str, *, retry: bool) -> str:
            if real.LICENCE_FUNCTION in payload:
                raise real.TallyResponseError("this gateway does not answer that")
            return sim.send(payload, retry=retry)

    def over_the_gateway(
        config: real.TallyConfig, *, backups: real.RecordedBackups
    ) -> real.RealTally:
        return real.RealTally(config, transport=_Gateway(), backups=backups)

    monkeypatch.setattr(factory, "RealTally", over_the_gateway)
    config = real.TallyConfig(host="127.0.0.1", port=9000)
    backups = real.RecordedBackups(frozenset({COMPANY, GHOST}))

    client, identity = real_tally(config, COMPANY, backups=backups)
    assert identity.company_exists is True
    assert client.list_companies() == (COMPANY,)

    with pytest.raises(RealTallyRequired, match="is not open in Tally"):
        real_tally(config, GHOST, backups=backups)


# ---------------------------------------------------------------------------
# 7. what the two DO agree on, so the file is not only a list of complaints
# ---------------------------------------------------------------------------


@BOTH
def test_both_backends_stamp_the_marker_and_find_it_again(make: MakeBackend):
    backend = make()
    op = new_operation_id()

    result = backend.client.write_voucher(COMPANY, a_voucher(), op)

    assert marker_for(op) in result.narration
    found = backend.client.read_by_operation_id(COMPANY, op)
    assert found is not None
    assert operation_id_in(found.narration) == op
    assert found.tally_id == result.tally_id


@BOTH
def test_both_backends_exclude_a_hand_typed_voucher_from_our_own_list(
    make: MakeBackend,
):
    """The whole safety of an undo-everything button rests on this line."""
    backend = make()
    op = new_operation_id()
    backend.client.write_voucher(COMPANY, a_voucher(), op)

    backend.plant_unmarked(500_000)

    ours = backend.client.list_our_vouchers(COMPANY)
    register = backend.client.read_vouchers(COMPANY)
    assert len(register) == 2, "the register shows both, ours and theirs"
    assert len(ours) == 1
    assert operation_id_in(ours[0].narration) == op
    assert all(v.amount_paise != 500_000 for v in ours)
    assert backend.balance() == {"Purchases": 618_000, "Cash": -618_000}


@BOTH
def test_a_reversal_restores_the_exact_prior_trial_balance_on_both(make: MakeBackend):
    """#6.5, to the paise, with a prior balance already on the books so the
    claim is a delta and not an absolute."""
    backend = make()
    backend.client.write_voucher(COMPANY, a_voucher(250_000), new_operation_id())
    before = backend.balance()
    assert before == {"Purchases": 250_000, "Cash": -250_000}

    op = new_operation_id()
    backend.client.write_voucher(COMPANY, a_voucher(118_000), op)
    assert backend.balance() != before

    assert backend.client.reverse_by_operation_id(COMPANY, op) is True
    assert backend.balance() == before


@BOTH
def test_every_refusal_in_this_file_leaves_the_trial_balance_where_it_was(
    make: MakeBackend,
):
    """One run, every refusal in sequence, one arithmetic claim at the end.

    Individually each test above already asserts state. This one catches the
    case none of them can: a refusal that leaves a partial write behind, which
    only shows up once several have happened against the same company.
    """
    backend = make()
    op = new_operation_id()
    backend.client.write_voucher(COMPANY, a_voucher(118_000), op)
    before = backend.balance()

    with pytest.raises(DuplicateOperation):
        backend.client.write_voucher(COMPANY, a_voucher(), op)
    with pytest.raises(real.TallyDataError):
        backend.client.write_voucher(
            COMPANY,
            replace(a_voucher(), debit_account="Nope"),
            new_operation_id(),
        )
    assert backend.client.reverse_by_operation_id(COMPANY, "ad_never") is False

    backend.plant("ad_twin", 700)
    backend.plant("ad_twin", 900)
    with pytest.raises(real.TallyDataError):
        backend.client.reverse_by_operation_id(COMPANY, "ad_twin")

    assert backend.ours() == 3, "the original and the two planted twins"
    assert backend.balance() == {
        ledger: paise + (1600 if ledger == "Purchases" else -1600)
        for ledger, paise in before.items()
    }
