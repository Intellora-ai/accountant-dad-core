"""Where a missing ledger is created, and when it is refused instead.

WHAT CHANGED, AND WHY IT NEEDED A FILE OF ITS OWN
--------------------------------------------------
Until 2026-08-13 both backends REFUSED a voucher naming a ledger the company
does not have. That is safe and it is also a dead end: the measured company
`TANVEER SIDHU` answers `<LEDGER>0</LEDGER>` (`accountant/tallyio/masters.py`
:5-9), so every voucher for it was refused for ever.

The refusal became a placement. `accountant/tallyio/chart.py` reads the group
out of the company's OWN chart of accounts - `list_ledgers()` and the PARENT of
each ledger - and creates the missing ledger under it. When the chart cannot
answer, it still refuses, and it says which fact it was missing.

WHY THE GROUP IS THE DANGEROUS PART
------------------------------------
`docs/RUNBOOK_PHASE5_ACCEPTANCE.md:180-186`: the same name under Sundry
Creditors and under Sundry Debtors is a credit and a debit - opposite signs,
for ever - and the acceptance run's trial-balance comparison DOES NOT CATCH IT.
There is no later check. So a placement that is merely plausible is not good
enough, and most of this file is the cases where the rule must refuse.

WHAT THIS FILE DOES NOT PROVE
------------------------------
Anything about a licensed TallyPrime. Every response below is a string this
file wrote. No socket is opened: the real backend is driven over a scripted
transport, the fake one over its own dictionary. Evidence class FAKETALLY and
SIMULATOR throughout.
"""

from __future__ import annotations

import datetime
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path

import pytest

from accountant import pipeline
from accountant.extract.adapter import TypedTextExtractor
from accountant.memory.bootstrap import bootstrap
from accountant.memory.store import MemoryStore
from accountant.schema import Outcome, Voucher
from accountant.tallyio import audit, chart, errors, masters, real
from accountant.tallyio.client import TallyClient
from accountant.tallyio.fake import FakeTally

#: The one company `writedoor.ALLOWED_WRITES` permits `create_ledger` for.
#: Spelled exactly as Tally holds it, because the permit matches exactly.
COMPANY = "TANVEER SIDHU"

TODAY = datetime.date(2026, 8, 13)

PARTY = "Verma Steels"


def a_bill(
    *,
    party: str = PARTY,
    debit: str = "Purchases",
    credit: str = PARTY,
) -> Voucher:
    """A supplier bill: the expense is debited, the supplier is credited."""
    return Voucher(
        id="draft-1",
        date=TODAY,
        party=party,
        narration="cement bags",
        debit_account=debit,
        credit_account=credit,
        amount_paise=118_000,
    )


@pytest.fixture(autouse=True)
def logs_go_to_the_tests_own_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Iterator[None]:
    """`Masters` writes an audit line and two XML blobs for every create.

    Left alone it writes them under `./logs` in the working tree, which is a
    real file appearing in the repository while
    `tests/test_upload.py::test_an_upload_writes_nothing_to_the_working_tree_or
    _a_data_directory` is comparing that tree. Pointed somewhere else here.
    """
    monkeypatch.setenv(audit.ENV_LOG_DIR, str(tmp_path / "logs"))
    monkeypatch.setenv(audit.ENV_XML_DIR, str(tmp_path / "logs" / "xml"))
    yield


# ---------------------------------------------------------------------------
# the two backends, behind one interface
# ---------------------------------------------------------------------------


def _import_reply(*, created: int = 1, complaint: str = "") -> str:
    """An Import response, shaped after the ones recorded on 2026-08-12."""
    line_error = f"<LINEERROR>{complaint}</LINEERROR>" if complaint else "<LINEERROR/>"
    return (
        "<ENVELOPE><BODY><DATA><IMPORTRESULT>"
        f"<CREATED>{created}</CREATED><ALTERED>0</ALTERED>"
        "<IGNORED>0</IGNORED><ERRORS>0</ERRORS><EXCEPTIONS>0</EXCEPTIONS>"
        f"{line_error}"
        "</IMPORTRESULT></DATA></BODY></ENVELOPE>"
    )


class ScriptedGateway:
    """Answers the two envelope shapes a placement needs, and nothing else.

    A transport rather than a mock of `Masters`, so the whole XML build/parse
    path runs: the collection request really is parsed for its PARENT elements
    and the create really is read back by name. A mock would prove the test's
    own beliefs about those.
    """

    def __init__(
        self,
        chart_now: dict[str, str] | None = None,
        *,
        on_import: str | None = None,
        keeps_what_it_creates: bool = True,
        dies_on_import: bool = False,
    ) -> None:
        self.chart_now = dict(chart_now or {})
        self.on_import = on_import
        #: False models the one outcome that must never be reported as success:
        #: Tally says it created the master and the read-back cannot find it.
        self.keeps_what_it_creates = keeps_what_it_creates
        #: The gateway answers the read and dies on the write. Reads failing is
        #: a different test - `read_accounts` has always let that through
        #: unchanged and this changes nothing about it.
        self.dies_on_import = dies_on_import
        self.sent: list[str] = []

    def send(self, payload: str, *, retry: bool) -> str:  # noqa: ARG002
        self.sent.append(payload)
        if "Import</TALLYREQUEST>" in payload:
            if self.dies_on_import:
                raise ConnectionError("connection refused by 127.0.0.1:9000")
            name = payload.split('<LEDGER NAME="', 1)[1].split('"', 1)[0]
            group = payload.split("<PARENT>", 1)[1].split("</PARENT>", 1)[0]
            if self.keeps_what_it_creates:
                self.chart_now[name] = group
            return self.on_import or _import_reply()
        body = "".join(
            f'<LEDGER NAME="{name}"><NAME>{name}</NAME>'
            + (f"<PARENT>{parent}</PARENT>" if parent else "")
            + "</LEDGER>"
            for name, parent in self.chart_now.items()
        )
        return (
            "<ENVELOPE><BODY><DATA>"
            f"<COLLECTION>{body}</COLLECTION>"
            "</DATA></BODY></ENVELOPE>"
        )


@dataclass(frozen=True)
class Backend:
    """One `chart.LedgerBook`, plus the two things a test needs to check it."""

    name: str
    book: chart.LedgerBook
    #: The company's chart AFTER the call: ledger name -> group.
    chart_after: Callable[[], dict[str, str]]


def a_real_backend(chart_now: dict[str, str]) -> Backend:
    gateway = ScriptedGateway(chart_now)
    book = real.ChartBook(gateway, COMPANY)
    return Backend("RealTally", book, lambda: dict(gateway.chart_now))


def a_fake_backend(chart_now: dict[str, str]) -> Backend:
    tally = FakeTally()
    tally.add_company(COMPANY, accounts=tuple(chart_now), groups=dict(chart_now))
    return Backend(
        "FakeTally",
        tally.ledger_book(COMPANY),
        lambda: {
            name: tally.ledger_group(COMPANY, name)
            for name in tally.read_accounts(COMPANY)
        },
    )


MakeBackend = Callable[[dict[str, str]], Backend]

#: Both implementations of `chart.LedgerBook`, driven through the SAME
#: `chart.place_ledgers`. Two transports under one decision is the only
#: arrangement in which "both backends agree" is a fact rather than two copies
#: that happen to agree today - W4 and W6 were both the second thing.
BOTH = pytest.mark.parametrize(
    "make_backend",
    [a_real_backend, a_fake_backend],
    ids=["real", "fake"],
)


# ---------------------------------------------------------------------------
# 1. the chart already answers
# ---------------------------------------------------------------------------


@BOTH
def test_a_ledger_already_in_the_chart_is_used_and_nothing_is_created(
    make_backend: MakeBackend,
) -> None:
    """Step one of the rule: if it is there, its PARENT is the answer.

    Nothing is inferred and nothing is sent. The control for every creation
    test below - a placement that created a ledger it already had would pass
    those and fail this.
    """
    backend = make_backend({"Purchases": "Purchase Accounts", PARTY: "Sundry Debtors"})

    refusal = chart.place_ledgers(backend.book, COMPANY, a_bill(), "op-existing")

    assert refusal == ""
    assert backend.chart_after() == {
        "Purchases": "Purchase Accounts",
        # STILL Sundry Debtors. The party is on the credit side, so the rule
        # would have chosen Sundry Creditors for a NEW ledger - and it must not
        # move or re-group one the company already has.
        PARTY: "Sundry Debtors",
    }


def test_a_ledger_the_chart_reports_with_no_group_is_still_not_created_again() -> None:
    """A gateway that sends no `<PARENT>` is a chart that cannot say the group.

    That is not permission to create a second ledger of the same name. `Derived
    .existing` and `Derived.group` are separate fields for this case.
    """
    placed = chart.derive_group(
        [chart.Placement("Purchases", ""), chart.Placement(PARTY, "")],
        a_bill(),
        PARTY,
    )

    assert placed.existing is True
    assert placed.group == ""
    assert placed.refusal == ""


# ---------------------------------------------------------------------------
# 2. the chart is read for a ledger that is not in it
# ---------------------------------------------------------------------------


@BOTH
def test_a_missing_party_is_created_under_the_group_this_company_uses_for_parties(
    make_backend: MakeBackend,
) -> None:
    """The party leg, derived from the company's own comparable ledgers.

    `Verma Steels` is this bill's party on the CREDIT side - somebody we owe -
    and this company already keeps a supplier under `Sundry Creditors`. So the
    group is read out of the chart, not out of a table here.
    """
    backend = make_backend(
        {"Purchases": "Purchase Accounts", "Sharma Traders": "Sundry Creditors"}
    )

    refusal = chart.place_ledgers(backend.book, COMPANY, a_bill(), "op-new-party")

    assert refusal == ""
    assert backend.chart_after()[PARTY] == "Sundry Creditors"


@BOTH
def test_a_party_on_the_debit_side_is_a_debtor_and_not_a_creditor(
    make_backend: MakeBackend,
) -> None:
    """The side decides, and getting it wrong is the failure no check catches.

    `docs/RUNBOOK_PHASE5_ACCEPTANCE.md:180-186`: same name, opposite sign, for
    ever, and the trial-balance comparison does not see it. This is the same
    company and the same party name as the test above, posted the other way
    round.
    """
    backend = make_backend(
        {
            "Sales": "Sales Accounts",
            "Sharma Traders": "Sundry Creditors",
            "Gupta Stores": "Sundry Debtors",
        }
    )
    invoice = a_bill(debit=PARTY, credit="Sales")

    refusal = chart.place_ledgers(backend.book, COMPANY, invoice, "op-new-customer")

    assert refusal == ""
    assert backend.chart_after()[PARTY] == "Sundry Debtors", (
        "the party is DEBITED here, so it is somebody who owes us. Sundry "
        "Creditors would put the balance on the opposite side for ever"
    )


@BOTH
def test_a_funding_leg_is_created_under_the_money_group_the_company_uses(
    make_backend: MakeBackend,
) -> None:
    """The other half of the owner's rule, derived the same way.

    `Petty Cash` is the credit leg and is not the party, so it is where the
    money came from. This company keeps its money in one group - Cash-in-Hand -
    so that is the group, read from the chart.
    """
    backend = make_backend(
        {
            "Purchases": "Purchase Accounts",
            "Cash": "Cash-in-Hand",
            PARTY: "Sundry Creditors",
        }
    )
    payment = a_bill(credit="Petty Cash")

    refusal = chart.place_ledgers(backend.book, COMPANY, payment, "op-new-funding")

    assert refusal == ""
    assert backend.chart_after()["Petty Cash"] == "Cash-in-Hand"


# ---------------------------------------------------------------------------
# 3. the chart cannot answer. Every one of these must refuse.
# ---------------------------------------------------------------------------


@BOTH
def test_an_empty_chart_refuses_and_creates_nothing(
    make_backend: MakeBackend,
) -> None:
    """The measured `TANVEER SIDHU` case: `<LEDGER>0</LEDGER>`.

    There is nothing to learn a group from, so there is no answer to give. A
    fallback group here would be a wrong group in somebody's books permanently,
    and it cannot be un-posted.
    """
    backend = make_backend({})

    refusal = chart.place_ledgers(backend.book, COMPANY, a_bill(), "op-empty")

    assert refusal.startswith(
        "refusing to write operation 'op-empty' to 'TANVEER SIDHU': the "
        "ledger(s) 'Purchases', 'Verma Steels' do not exist there"
    ), refusal
    assert "no ledgers under any group" in refusal
    assert backend.chart_after() == {}, "a refusal that created something is not one"


@BOTH
def test_a_name_differing_only_in_case_is_refused_and_never_created_beside_it(
    make_backend: MakeBackend,
) -> None:
    """THE CONTROL. 'sharma traders' does not satisfy 'Sharma Traders'.

    This is the accident that actually happens - an extractor or a copy-paste -
    and it is worse than a typo nobody notices, because both ledgers then hold
    half a balance and neither looks wrong on its own. The chart HAS a Sundry
    Creditors group here, so the party rule would otherwise have placed it.
    """
    backend = make_backend(
        {"Purchases": "Purchase Accounts", "Sharma Traders": "Sundry Creditors"}
    )
    bill = a_bill(party="sharma traders", credit="sharma traders")

    refusal = chart.place_ledgers(backend.book, COMPANY, bill, "op-case")

    assert "differ only in case" in refusal, refusal
    assert "'sharma traders'" in refusal and "'Sharma Traders'" in refusal
    assert "sharma traders" not in backend.chart_after(), (
        "a second ledger was created beside the real one; that splits a "
        "balance in two and neither half looks wrong"
    )


@BOTH
def test_the_party_group_is_refused_when_the_company_has_no_ledger_under_it(
    make_backend: MakeBackend,
) -> None:
    """A chart with groups in it can still be unable to answer THIS question.

    This company has an expense group and no suppliers at all, so there is no
    comparable ledger to read `Sundry Creditors` off.
    """
    backend = make_backend({"Purchases": "Purchase Accounts"})

    refusal = chart.place_ledgers(backend.book, COMPANY, a_bill(), "op-no-suppliers")

    assert "no ledger under 'Sundry Creditors'" in refusal, refusal
    assert backend.chart_after() == {"Purchases": "Purchase Accounts"}


def test_two_money_groups_in_one_chart_refuse_rather_than_choose() -> None:
    """Unanimous or nothing - the same rule `pipeline.funding_from_history` uses.

    A company with both a bank and a cash group has not told us which one a new
    money ledger belongs in, and a majority vote is not evidence.
    """
    company_chart = [
        chart.Placement("Purchases", "Purchase Accounts"),
        chart.Placement(PARTY, "Sundry Creditors"),
        chart.Placement("Cash", "Cash-in-Hand"),
        chart.Placement("HDFC Current", "Bank Accounts"),
    ]

    placed = chart.derive_group(
        company_chart, a_bill(credit="Petty Cash"), "Petty Cash"
    )

    assert placed.group == ""
    assert "more than one money group" in placed.refusal
    assert "Bank Accounts, Cash-in-Hand" in placed.refusal


def test_an_income_ledger_in_the_chart_stops_a_credit_leg_being_read_as_money() -> None:
    """A company that sells things credits income, not only money.

    Without this the Sales ledger of a company with one bank account would be
    filed under Bank Accounts - an income line in the balance sheet.
    """
    company_chart = [
        chart.Placement(PARTY, "Sundry Debtors"),
        chart.Placement("Cash", "Cash-in-Hand"),
        chart.Placement("Consultancy", "Sales Accounts"),
    ]
    invoice = a_bill(debit=PARTY, credit="Product Sales")

    placed = chart.derive_group(company_chart, invoice, "Product Sales")

    assert placed.group == ""
    assert "may be income instead of money" in placed.refusal


def test_the_debit_leg_that_is_not_the_party_is_never_placed_by_this_code() -> None:
    """Purchase, direct expense, indirect expense or fixed asset.

    Four groups, four different places in the accounts, and three of them
    change this year's profit while the fourth does not. A list of ledgers and
    parents contains no fact that tells them apart, so this refuses.
    """
    company_chart = [
        chart.Placement("Purchases", "Purchase Accounts"),
        chart.Placement(PARTY, "Sundry Creditors"),
    ]

    placed = chart.derive_group(
        company_chart, a_bill(debit="Freight Inward"), "Freight Inward"
    )

    assert placed.group == ""
    assert "a decision for a person" in placed.refusal


def test_a_voucher_leg_with_no_name_is_never_created() -> None:
    """An empty leg is a ledger named '', and creating one is how a trial
    balance comes back holding `{'Purchases': 100000, '': -100000}`."""
    placed = chart.derive_group(
        [chart.Placement("Purchases", "Purchase Accounts")], a_bill(credit=""), ""
    )

    assert placed.group == ""
    assert "no ledger name" in placed.refusal


def test_a_party_name_on_both_legs_names_no_side_and_is_not_placed() -> None:
    """The side is what decides creditor or debtor. A voucher that puts the
    party on both legs states neither, and answering one of them would be a
    guess dressed as a reading."""
    placed = chart.derive_group(
        [chart.Placement("Sharma Traders", "Sundry Creditors")],
        a_bill(party=PARTY, debit=PARTY, credit=PARTY),
        PARTY,
    )

    assert placed.group == ""


@BOTH
def test_nothing_is_created_when_one_of_two_missing_ledgers_cannot_be_placed(
    make_backend: MakeBackend,
) -> None:
    """Two passes, and the first one sends nothing.

    The party here IS placeable and the expense leg is not. Creating the party
    anyway would add a master to somebody's books for a write that never
    happened, and nothing would ever remove it.
    """
    backend = make_backend({"Sharma Traders": "Sundry Creditors"})
    bill = a_bill(debit="Freight Inward")

    refusal = chart.place_ledgers(backend.book, COMPANY, bill, "op-partial")

    assert "'Freight Inward', 'Verma Steels' do not exist there" in refusal
    assert backend.chart_after() == {"Sharma Traders": "Sundry Creditors"}, (
        "the placeable half was created for a write that was then refused"
    )


# ---------------------------------------------------------------------------
# 4. both backends give the SAME answer, word for word
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("case", "company_chart", "voucher"),
    [
        ("empty_chart", {}, a_bill()),
        (
            "case_only_difference",
            {"Sharma Traders": "Sundry Creditors", "Purchases": "Purchase Accounts"},
            a_bill(party="sharma traders", credit="sharma traders"),
        ),
        ("no_comparable_party", {"Purchases": "Purchase Accounts"}, a_bill()),
        (
            "unplaceable_expense_leg",
            {"Sharma Traders": "Sundry Creditors"},
            a_bill(
                debit="Freight Inward",
                credit="Sharma Traders",
                party="Sharma Traders",
            ),
        ),
    ],
    ids=[
        "empty_chart",
        "case_only_difference",
        "no_comparable_party",
        "unplaceable_expense_leg",
    ],
)
def test_both_backends_refuse_with_the_identical_sentence(
    case: str, company_chart: dict[str, str], voucher: Voucher
) -> None:
    """Not "both refuse" - the SAME sentence, character for character.

    W4 and W6 were both a rule mirrored in two files that then drifted, and
    `accountant/schema.py:115-132` records the third. There is one function
    here and two transports under it, so this asserts the arrangement rather
    than the coincidence.
    """
    live = chart.place_ledgers(
        a_real_backend(dict(company_chart)).book, COMPANY, voucher, f"op-{case}"
    )
    doubled = chart.place_ledgers(
        a_fake_backend(dict(company_chart)).book, COMPANY, voucher, f"op-{case}"
    )

    assert live, "the case must actually refuse, or this proves nothing"
    assert live == doubled


def test_the_write_path_still_refuses_a_chart_that_cannot_place_the_ledger() -> None:
    """The refusal reaches `write_voucher`, as the same exception class it always
    did, on both backends."""
    tally = FakeTally()
    tally.add_company(COMPANY, accounts=("Purchases",), backed_up=True)
    before = tally.trial_balance(COMPANY)

    with pytest.raises(real.TallyDataError) as raised:
        tally.write_voucher(COMPANY, a_bill(), "op-write-refused")

    assert "do not exist there" in str(raised.value)
    assert tally.list_our_vouchers(COMPANY) == ()
    assert tally.trial_balance(COMPANY) == before
    assert tally.read_accounts(COMPANY) == ("Purchases",), "nothing was created"


def test_the_write_path_creates_the_ledger_and_then_writes_the_voucher() -> None:
    """The control for the test above. A backend that refused everything would
    pass it."""
    tally = FakeTally()
    tally.add_company(
        COMPANY,
        accounts=("Purchases", "Sharma Traders"),
        groups={"Purchases": "Purchase Accounts", "Sharma Traders": "Sundry Creditors"},
        backed_up=True,
    )

    written = tally.write_voucher(COMPANY, a_bill(), "op-write-placed")

    assert written.operation_id == "op-write-placed"
    assert tally.ledger_group(COMPANY, PARTY) == "Sundry Creditors"
    assert len(tally.list_our_vouchers(COMPANY)) == 1


# ---------------------------------------------------------------------------
# 5. the create can fail, and a failure is never a success
# ---------------------------------------------------------------------------


def test_the_derived_group_is_exactly_what_reaches_ensure_ledger(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The one argument that decides which side of the books a balance lands on.

    Asserted at the call rather than at the response, because a create that is
    sent with the wrong group and then confirmed reads as a clean success
    everywhere else.
    """
    asked: list[tuple[str, str]] = []

    def record(_self: masters.Masters, name: str, group: str) -> masters.MasterResult:
        asked.append((name, group))
        return masters.MasterResult(success=True, name=name, confirmed=True)

    monkeypatch.setattr(masters.Masters, "ensure_ledger", record)
    backend = a_real_backend(
        {"Purchases": "Purchase Accounts", "Sharma Traders": "Sundry Creditors"}
    )

    assert chart.place_ledgers(backend.book, COMPANY, a_bill(), "op-args") == ""
    assert asked == [(PARTY, "Sundry Creditors")]


def test_a_create_whose_read_back_cannot_confirm_it_is_a_failure() -> None:
    """Tally answered `CREATED 1` and the ledger is not there afterwards.

    `masters.MasterResult` keeps `success` and `confirmed` apart for exactly
    this (`masters.py:117-131`). A write Tally accepts and does not keep is a
    failure, and the voucher must not go out against a ledger nobody can find.
    """
    gateway = ScriptedGateway(
        {"Purchases": "Purchase Accounts", "Sharma Traders": "Sundry Creditors"},
        keeps_what_it_creates=False,
    )
    book = real.ChartBook(gateway, COMPANY)

    refusal = chart.place_ledgers(book, COMPANY, a_bill(), "op-unconfirmed")

    assert "do not exist there" in refusal
    assert "NOT confirmed" in refusal, refusal
    assert PARTY not in gateway.chart_now


def test_a_gateway_that_dies_mid_create_is_reported_and_nothing_is_written() -> None:
    """Tally closed, or the port shut, between reading the chart and writing.

    The exact error text travels out - `errors.UNREACHABLE` exists precisely
    because a dead gateway used to escape as a raw traceback where a sentence
    was promised (`errors.py`, the UNREACHABLE note).
    """
    gateway = ScriptedGateway(
        {"Purchases": "Purchase Accounts", "Sharma Traders": "Sundry Creditors"},
        dies_on_import=True,
    )
    book = real.ChartBook(gateway, COMPANY)

    refusal = chart.place_ledgers(book, COMPANY, a_bill(), "op-unreachable")

    assert "do not exist there" in refusal
    assert errors.UNREACHABLE in refusal, refusal
    assert "connection refused by 127.0.0.1:9000" in refusal, (
        "the exact error is what a person needs; a paraphrase is not evidence"
    )
    assert PARTY not in gateway.chart_now


def test_a_tally_refusal_of_the_ledger_is_reported_rather_than_swallowed() -> None:
    """`STATUS 1` is not `it worked`. Tally's own complaint reaches the caller.

    Tally answers HTTP 200 with a `<LINEERROR>` for requests it declines, and
    `CREATED 0` with no complaint at all for some of them. Both are failures
    here and neither may become a voucher written against a ledger that was
    never made.
    """
    gateway = ScriptedGateway(
        {"Purchases": "Purchase Accounts", "Sharma Traders": "Sundry Creditors"},
        on_import=_import_reply(created=0, complaint="Could not set value for Parent"),
        keeps_what_it_creates=False,
    )
    book = real.ChartBook(gateway, COMPANY)

    refusal = chart.place_ledgers(book, COMPANY, a_bill(), "op-refused")

    assert "Could not set value for Parent" in refusal, refusal
    assert "do not exist there" in refusal
    assert PARTY not in gateway.chart_now


def test_a_create_the_write_door_does_not_permit_is_a_refusal_and_not_a_write() -> None:
    """The door is asked before any bytes leave, and it names one company.

    A company the permit does not cover is refused with nothing sent - which is
    reported the same way a dead gateway is, because the caller's next move is
    the same and `FakeTally` has no door to raise a different class from.
    """
    gateway = ScriptedGateway(
        {"Purchases": "Purchase Accounts", "Sharma Traders": "Sundry Creditors"}
    )
    book = real.ChartBook(gateway, "Some Other Company")

    refusal = chart.place_ledgers(book, "Some Other Company", a_bill(), "op-door")

    assert "NOT_ALLOW_LISTED" in refusal, refusal
    assert not any("Import</TALLYREQUEST>" in sent for sent in gateway.sent)


# ---------------------------------------------------------------------------
# 6. a refused placement is recorded by the mechanism that already exists
# ---------------------------------------------------------------------------

_POST_COMPANY = "Demo Co"
_ACCOUNTS = ("Purchases", "Sundry Expenses", "Repairs & Maintenance", "Cash")


class LosesTheChart:
    """A client whose chart is empty by the time the write reaches it.

    The one way a VALID draft can meet an unplaceable ledger: the chart said
    the ledger was there when `evaluate` looked, and says nothing by the time
    `post` writes - a company closed and reopened, or a restore. The refusal is
    the REAL one, produced by `chart.place_ledgers` against an empty company,
    not a sentence this test made up. Everything else delegates, so the rest of
    `post` runs unchanged.
    """

    def __init__(self, inner: FakeTally) -> None:
        self._inner = inner
        self.writes = 0
        self._empty = FakeTally()
        self._empty.add_company("emptied", backed_up=True)

    def __getattr__(self, name: str) -> object:
        return getattr(self._inner, name)

    def write_voucher(
        self, company: str, voucher: Voucher, operation_id: str
    ) -> object:
        self.writes += 1
        raise real.TallyDataError(
            chart.place_ledgers(
                self._empty.ledger_book("emptied"), company, voucher, operation_id
            )
        )


def test_a_refused_placement_is_recorded_by_the_existing_write_ahead_row() -> None:
    """No parallel mechanism. `pipeline.post` already writes a row BEFORE the
    socket opens and a terminal row after, and a placement refusal travels that
    path like every other write failure: two rows naming the operation, nothing
    recorded as posted, and the books unmoved."""
    inner = FakeTally()
    inner.add_company(
        _POST_COMPANY,
        accounts=_ACCOUNTS,
        vouchers=tuple(
            Voucher(
                id=f"hist-{i}",
                date=datetime.date(2026, 1, 1),
                party="Sharma Traders",
                narration="cement supply",
                debit_account="Purchases",
                credit_account="Cash",
                amount_paise=100_000,
            )
            for i in range(40)
        ),
        backed_up=True,
    )
    store = MemoryStore(":memory:")
    memory = bootstrap(inner, _POST_COMPANY, store)
    client: TallyClient = LosesTheChart(inner)  # type: ignore[assignment]

    draft = pipeline.build_draft(
        _POST_COMPANY,
        b"paid Sharma Traders 4200 for cement",
        "text/plain",
        TypedTextExtractor(),
        memory,
        today=TODAY,
    )
    draft = pipeline.evaluate(
        draft, _ACCOUNTS, inner.read_vouchers(_POST_COMPANY), memory
    )
    assert draft.outcome is Outcome.VALID, draft.reason

    before = inner.trial_balance(_POST_COMPANY)
    with pytest.raises(real.TallyDataError, match="do not exist there"):
        pipeline.post(draft, client, log=store, memory=memory, run_id="run-placement")

    rows = store.actions(_POST_COMPANY)
    assert [r.action for r in rows] == [
        pipeline.WRITE_ATTEMPTED,
        pipeline.WRITE_OUTCOME_UNKNOWN,
    ]
    assert {r.operation_id for r in rows} == {draft.operation_id}
    assert "do not exist there" in rows[1].reason, "the exact error is written down"
    assert draft.posted_tally_id is None
    assert inner.list_our_vouchers(_POST_COMPANY) == ()
    assert inner.trial_balance(_POST_COMPANY) == before


# ---------------------------------------------------------------------------
# 7. the vocabulary is Tally's own, spelled Tally's way
# ---------------------------------------------------------------------------


def test_every_group_this_module_can_choose_is_one_tally_spells_that_way() -> None:
    """`chart.py` cannot import `masters` - `masters` imports `real` and `real`
    imports `chart` - so the two spellings are pinned here instead.

    A group Tally does not know is refused by `validate_ledger` with an
    unhelpful message, and the misspelling is the commonest master failure
    there is.
    """
    choosable = (
        set(chart.PARTY_GROUP_FOR_SIDE.values())
        | chart.MONEY_GROUPS
        | chart.INCOME_GROUPS
    )

    assert choosable <= masters.KNOWN_GROUPS, sorted(choosable - masters.KNOWN_GROUPS)


def test_no_group_this_module_names_is_one_of_the_forbidden_buckets() -> None:
    """Suspense, Sundry Expenses, Miscellaneous - the classic places a guess
    goes. Phase 4 exit 4 forbids them and this module must not reintroduce one
    under the name of a group."""
    named = {
        value.lower()
        for value in (
            *chart.PARTY_GROUP_FOR_SIDE.values(),
            *chart.MONEY_GROUPS,
            *chart.INCOME_GROUPS,
        )
    }

    assert not named & {"suspense", "sundry expenses", "miscellaneous", "misc"}
