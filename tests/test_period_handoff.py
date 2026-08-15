"""The period, from the company's own Tally to the cage that needs it.

WHY THIS FILE EXISTS
--------------------
`accountant/pipeline.py::run` hardcoded `period_open=None`, under a comment
saying "Nothing in this repository reads whether a company's books are open for
a date". That sentence was FALSE when it was written. `accountant/period.py`
has `check_period` and `is_period_open`, `accountant/tallyio/period.py` has the
bounded read behind them, and both shipped web call sites already passed a real
value through `web/app.py::Runtime.period_open` (`app.py:2690` and `:3424`).
One call site declined to fetch a fact the codebase could already read.

MEASURED, 2026-08-15, over the 173 tests failing on this branch: `period_open`
was `None` on 299 of 299 `cage.gate` calls, and every one of those blocks cited
*"I could not tell whether the books for this date are still open"*. The cage
was refusing bills over a fact nobody had asked for.

THE DEFECT THIS FILE IS ACTUALLY GUARDING IS THE SECOND ONE
------------------------------------------------------------
Wiring alone is not the fix, and the tempting wiring is worse than no wiring.
`PeriodCheck.open_for_posting` is a bare `bool`, so an unreachable Tally comes
back `False`; the cage renders `False` through `decision._period_closed` as
*"The books for this date are closed, so nothing can be added to them"* - a
confident, specific claim about the customer's Tally, produced by OUR dropped
connection. It sends somebody into their accounting software to fix a setting
that was never wrong.

So `for_cage` is what crosses the seam, and it keeps three states apart:

    OPEN        -> True    may post
    CLOSED      -> False   blocks, and says the books are closed
    UNVERIFIED  -> None    blocks, and says WE could not check

`test_an_unreachable_tally_blames_us_and_never_the_customers_books` is the one
that matters most here: it asserts the two refusals are DIFFERENT SENTENCES.
A collapse to `False` passes every other test in this file.

WHAT THIS FILE IS FOR THAT `tests/test_period_open.py` IS NOT
--------------------------------------------------------------
That file already holds `check_period` to its three verdicts and hands the
result straight to `cage.gate` itself, so it proves the CHECK is right. It
cannot see the seam: it never calls `pipeline.run`, so every one of its tests
passed on 2026-08-15 while the live path was still passing a hardcoded `None`.
This file is about the seam and nothing else - what `run` fetches, and what
arrives at the gate.

WHAT THIS FILE DOES NOT CLAIM
------------------------------
That more bills now post. Nothing here asserts a post, and the measurement says
why: with an OPEN period supplied, the same bill still blocks on two other hard
refusals - `net_plus_tax_equals_gross` is INDETERMINATE because
`TypedTextExtractor` reads no pre-tax figure, and the reading sits below
`ASK_FLOOR`. What changed is that one of the four sentences a person reads is
now about something we looked at.

That any of this survives contact with real TallyPrime. Every probe below is a
canned response through a fake transport; the live measurement is quoted in
`accountant/tallyio/period.py`'s module docstring and was taken against
TallyPrime 7.0 at 127.0.0.1:9000 on 2026-08-13. No test here opens a socket.

EVIDENCE CLASS
--------------
Behavioural, through `pipeline.run`, with a spy on the real `cage.gate` call -
plus one structural check on the call site, because every downstream assertion
here is about a REFUSAL and a refusal looks identical whether the value arrived
or not. That is the same trap `tests/test_net_handoff.py` documents for
`net_paise`, and it is why both files pin the seam by source as well.
"""

from __future__ import annotations

import ast
import datetime
import inspect
import textwrap
from typing import TYPE_CHECKING, Any

import pytest

from accountant import period as period_module
from accountant import pipeline
from accountant.cage import gate as cage_gate
from accountant.cage.decision import Decided
from accountant.extract.adapter import TypedTextExtractor
from accountant.memory.bootstrap import bootstrap
from accountant.memory.company import CompanyMemory
from accountant.memory.store import MemoryStore
from accountant.schema import Voucher
from accountant.tallyio.fake import FakeTally
from accountant.tallyio.period import PeriodReader
from accountant.tallyio.real import TallyUnreachable

if TYPE_CHECKING:  # pragma: no cover - a type, never a runtime dependency
    from accountant.pipeline import Draft

COMPANY = "Demo Co"
ACCOUNTS = ("Purchases", "Sundry Expenses", "Repairs & Maintenance", "Cash")
PARTY = "Sharma Traders"
BILL = b"paid Sharma Traders 4200 for cement"

#: The date the bill is entered on, and therefore the date on the voucher.
#: `TypedTextExtractor` finds no date on `BILL`, so `build_draft` uses this one -
#: which is what makes `test_the_probe_asks_about_the_date_on_the_bill` able to
#: tell "the bill's date" from "whatever today happens to be".
TODAY = datetime.date(2026, 8, 7)

#: The financial year the measured TallyPrime instance had loaded on 2026-08-13,
#: quoted from `accountant/tallyio/period.py`'s module docstring rather than
#: invented here. Window: 2026-04-01 .. 2027-03-31, and `TODAY` is inside it.
FY_OPEN_START = "20260401"

#: The SAME company, one financial year earlier. Window: 2025-04-01 ..
#: 2026-03-31, and `TODAY` is 129 days after the end of it. This is a CLOSED
#: year - a fact about the customer's books, not about our connection.
FY_CLOSED_START = "20250401"

#: What Tally answered in the measurement, verbatim, and it is here because it
#: is the trap: `ENDINGAT` tracked the LAST VOUCHER DATE, not the end of the
#: period. Nothing bounds on it. A test that bounded on it would refuse every
#: bill dated after the last one already entered - for ever, on healthy books.
ENDING_AT = "20260812"


# ---------------------------------------------------------------------------
# a company, its books, and a Tally that answers about its period
# ---------------------------------------------------------------------------


def past(n: int = 40) -> tuple[Voucher, ...]:
    """`n` entries for `PARTY`, all to Purchases.

    Forty, so `party_known` is True and `propose_account` has something to
    propose. Without them the cage blocks on the PARTY as well, and a test
    reading the refusal could not tell which sentence it was looking at.
    """
    return tuple(
        Voucher(
            id=f"hist-{i}",
            date=datetime.date(2026, 1, 1),
            party=PARTY,
            narration=f"{PARTY} purchase",
            debit_account="Purchases",
            credit_account="Cash",
            amount_paise=100000,
        )
        for i in range(n)
    )


def tally() -> FakeTally:
    t = FakeTally()
    t.add_company(COMPANY, accounts=ACCOUNTS, vouchers=past(), backed_up=True)
    return t


def memory_for(t: FakeTally) -> CompanyMemory:
    """A fresh store per call. A shared one lets a test pass for the wrong reason."""
    return bootstrap(t, COMPANY, MemoryStore(":memory:"))


def period_response(starting_from: str, name: str = COMPANY) -> str:
    """One company's period, in the exact shape TallyPrime 7.0 answered with.

    Copied from the live measurement in `accountant/tallyio/period.py` - same
    tags, same `TYPE="Date"` attributes, same `%Y%m%d` dates, `BOOKSFROM` equal
    to `STARTINGFROM` as it was on the instance measured. A response invented
    here would prove that `parse_company_periods` parses what this file writes.
    """
    return (
        "<ENVELOPE><DATA><COLLECTION>"
        f'<COMPANY NAME="{name}" RESERVEDNAME="">'
        f'<ENDINGAT TYPE="Date">{ENDING_AT}</ENDINGAT>'
        f'<STARTINGFROM TYPE="Date">{starting_from}</STARTINGFROM>'
        f'<BOOKSFROM TYPE="Date">{starting_from}</BOOKSFROM>'
        f'<NAME TYPE="String">{name}</NAME>'
        "</COMPANY>"
        "</COLLECTION></DATA></ENVELOPE>"
    )


class Answering:
    """A transport that hands back one canned body, and remembers what it was sent.

    NOT a stubbed `PeriodReader`. The real `PeriodReader` sits on top of this, so
    `build_period_request`, `parse_company_periods`, `period_for` and `open_on`
    all really run - a doubled reader would have proved only that `check_period`
    returns what a double told it to.
    """

    def __init__(self, payload: str) -> None:
        self.payload = payload
        self.sent: list[str] = []

    # The parameter names are the `Transport` protocol's, not ours - `retry` is
    # passed by keyword, so renaming it would stop this satisfying the protocol.
    def send(self, payload: str, *, retry: bool) -> str:  # noqa: ARG002 - protocol
        self.sent.append(payload)
        return self.payload


class Unreachable:
    """A Tally that is not there. `TallyUnreachable` is what `HttpTransport`
    raises for a refused connection and a socket timeout alike, and `TallyError`
    is the class `check_period` catches. This is the state the third answer
    exists for."""

    def __init__(self) -> None:
        self.calls = 0

    def send(self, payload: str, *, retry: bool) -> str:  # noqa: ARG002 - protocol
        self.calls += 1
        raise TallyUnreachable("connection refused by 127.0.0.1:9000")


def reader_for(starting_from: str) -> PeriodReader:
    return PeriodReader(transport=Answering(period_response(starting_from)))


def unreachable_reader() -> PeriodReader:
    return PeriodReader(transport=Unreachable())


# ---------------------------------------------------------------------------
# the spy - the only thing that can see the value cross the seam
# ---------------------------------------------------------------------------


#: The real `cage.gate`, captured ONCE at import and never re-read.
#:
#: NOT `cage_gate.gate` read inside `run_with`. Two runs in one test patch the
#: module attribute twice, and a spy that wrapped whatever was installed at the
#: time would wrap the PREVIOUS spy - so every earlier spy would go on recording
#: calls it never saw and `GateSpy.only` would be reading somebody else's bill.
#: That is not hypothetical; it is what this file did until it was run.
_REAL_GATE = cage_gate.gate


class GateSpy:
    """Every `cage.gate` call `pipeline.run` made, and what the real cage decided.

    Wraps the REAL `gate` and returns its real answer. A spy that returned a
    canned `Decided` would make every sentence assertion below a statement about
    this file rather than about the cage.
    """

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self.decided: list[Decided] = []

    @property
    def only(self) -> dict[str, Any]:
        assert len(self.calls) == 1, (
            f"expected exactly one gate call per run, saw {len(self.calls)}. "
            "The assertions below name a single value and cannot say which."
        )
        return self.calls[0]

    @property
    def period_open(self) -> object:
        return self.only["period_open"]

    @property
    def said(self) -> str:
        return self.decided[0].said


def run_with(
    monkeypatch: pytest.MonkeyPatch,
    reader: PeriodReader | None,
    *,
    today: datetime.date = TODAY,
) -> tuple[GateSpy, Draft]:
    """One whole `pipeline.run`, with the gate call recorded on the way past."""
    spy = GateSpy()

    def watched(draft: Draft, **given: Any) -> Decided:
        spy.calls.append(given)
        answer = _REAL_GATE(draft, **given)
        spy.decided.append(answer)
        return answer

    monkeypatch.setattr(cage_gate, "gate", watched)

    t = tally()
    draft = pipeline.run(
        COMPANY,
        BILL,
        "text/plain",
        TypedTextExtractor(),
        t,
        memory_for(t),
        today=today,
        period_reader=reader,
    )
    return spy, draft


# =============================================================================
# THE HANDOFF - the three states, each one arriving at the cage
# =============================================================================


def test_an_open_period_arrives_at_the_cage_as_true(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """OPEN -> `True`. Tally answered, and the bill's date is inside the window.

    THIS IS THE ASSERTION THE REVERTED ATTEMPT DID NOT HAVE. Wiring
    `period_reader` through without a test that supplies one left `period_open`
    at `None` on all 299 gate calls, so nothing measurable changed and the
    change was correctly reverted.
    """
    spy, _ = run_with(monkeypatch, reader_for(FY_OPEN_START))

    assert spy.period_open is True
    # And the refusal has stopped citing the period at all. The bill still
    # blocks, on other laws - see the module docstring.
    assert "books" not in spy.said, spy.said


def test_a_closed_period_arrives_as_false_and_says_the_books_are_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """CLOSED -> `False`. A fact about the customer's books, stated as one.

    The window here is the financial year BEFORE the bill's, so `open_on`
    answers False with both bounds read - which is the only path to CLOSED.
    """
    spy, _ = run_with(monkeypatch, reader_for(FY_CLOSED_START))

    assert spy.period_open is False
    assert "closed" in spy.said, spy.said


def test_an_unreachable_tally_blames_us_and_never_the_customers_books(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """UNVERIFIED -> `None`, AND THE TWO REFUSALS ARE DIFFERENT SENTENCES.

    THE TEST THIS FILE IS FOR. `is_period_open` would return `False` here, the
    cage would read `False` as "closed", and a person whose books are perfectly
    open would be told they are shut - by our own dropped connection.

    Both block. That is not what is being tested. What is being tested is that
    the two blocks do not say the same thing, because the sentence is the only
    part a customer ever sees and it is the part that sends them somewhere.
    """
    unverified, _ = run_with(monkeypatch, unreachable_reader())
    closed, _ = run_with(monkeypatch, reader_for(FY_CLOSED_START))

    assert unverified.period_open is None
    assert "could not tell whether the books" in unverified.said, unverified.said
    assert "are closed" not in unverified.said, unverified.said

    assert unverified.said != closed.said, (
        "an unreachable Tally and a closed financial year produced the SAME "
        "sentence, so the third state has been collapsed into False somewhere "
        "between check_period and the cage. That sentence tells a customer "
        "their books are closed on the evidence of our own network failure."
    )


def test_no_reader_is_still_none_and_still_blocks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """UNCHANGED BEHAVIOUR, PINNED. A caller with no reader has not looked.

    `TallyClient` has no period method - `accountant/tallyio/client.py` defines
    none - so this is not a fallback, it is the truth about that caller. `None`
    blocks, and it blocks with the "we could not check" sentence rather than
    with a claim about the books.
    """
    spy, draft = run_with(monkeypatch, None)

    assert spy.period_open is None
    assert "could not tell whether the books" in spy.said, spy.said
    assert draft.decision is not None
    assert draft.posted_tally_id is None


def test_the_three_states_are_three_values_and_not_two(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The whole table in one place, so a collapse cannot hide in one branch.

    `is not` rather than `==`, deliberately: `False == 0` and `None` is falsy,
    so an equality test would pass on a value that had lost its identity on the
    way through - which is exactly the failure this file exists for.
    """
    seen = {
        "open": run_with(monkeypatch, reader_for(FY_OPEN_START))[0].period_open,
        "closed": run_with(monkeypatch, reader_for(FY_CLOSED_START))[0].period_open,
        "unverified": run_with(monkeypatch, unreachable_reader())[0].period_open,
        "nobody looked": run_with(monkeypatch, None)[0].period_open,
    }

    assert seen["open"] is True
    assert seen["closed"] is False
    assert seen["unverified"] is None
    assert seen["nobody looked"] is None
    assert len({id(value) for value in seen.values()}) == 3


# =============================================================================
# THE SEAM - that the value genuinely reaches cage.gate, by source
# =============================================================================


def _what_run_passes_for(name: str) -> ast.expr:
    """The expression `pipeline.run` hands `evaluate` for keyword `name`.

    AST AND NOT A SUBSTRING SEARCH, and the reason is this very file: it argues
    at length in its own prose about the `period_open=None` that used to be
    there, and `"period_open=None" not in source` therefore fails on the
    COMMENT explaining why the line is gone. A test that a docstring can turn
    red is a test nobody keeps.
    """
    tree = ast.parse(textwrap.dedent(inspect.getsource(pipeline.run)))
    passed = [
        keyword.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        for keyword in node.keywords
        if keyword.arg == name
    ]
    assert len(passed) == 1, (
        f"pipeline.run passes {name!r} {len(passed)} times, so this test cannot "
        "say which call site it is describing"
    )
    return passed[0]


def test_run_hands_the_period_it_read_to_the_cage() -> None:
    """PINNED AT THE CALL SITE, the way `tests/test_net_handoff.py` pins the net.

    A spy proves the value arrived on the day the spy ran. This proves the call
    site still FETCHES it - the reverted attempt's failure mode was a parameter
    that existed, typechecked, and was never given a value, and no behavioural
    assertion in this repository could see the difference.
    """
    given = _what_run_passes_for("period_open")

    assert not isinstance(given, ast.Constant), (
        "pipeline.run passes a hardcoded period_open again, so every bill "
        "through this path blocks on 'I could not tell whether the books for "
        "this date are still open' whether a reader was supplied or not."
    )
    assert isinstance(given, ast.Call) and isinstance(given.func, ast.Name), given
    assert given.func.id == "_period_open", (
        f"pipeline.run gets period_open from {given.func.id!r} rather than from "
        "_period_open, which is the only function here that keeps the three "
        "states apart."
    )


def test_the_pipeline_never_builds_a_period_reader_of_its_own() -> None:
    """No default reader, anywhere in this module. Not tidiness - a socket.

    A default of `PeriodReader()` builds an `HttpTransport` aimed at port 9000
    of whatever machine is running, so a doubled client would be answered by a
    real gateway and this suite would pass on one laptop and fail on another.
    The parameter defaults to `None`, which blocks.
    """
    signature = inspect.signature(pipeline.run)
    built = [
        node
        for node in ast.walk(ast.parse(inspect.getsource(pipeline)))
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "PeriodReader"
    ]

    assert signature.parameters["period_reader"].default is None
    assert built == [], (
        "accountant/pipeline.py constructs a PeriodReader, which builds an "
        "HttpTransport aimed at port 9000 of whatever machine is running it."
    )


def test_the_period_module_is_imported_lazily_and_not_at_module_scope() -> None:
    """The cycle is real, and this is the test that says so rather than a comment.

    `accountant.period` imports `accountant.observability`, which imports
    `accountant.pipeline`. A module-scope import in `pipeline.py` would raise
    `ImportError` before a single test ran, so the import lives inside
    `_period_open` and the TYPE_CHECKING block carries the type.
    """
    assert "from accountant.period import check_period" in inspect.getsource(
        pipeline._period_open  # pyright: ignore[reportPrivateUsage]
    )

    observability = inspect.getsource(
        __import__("accountant.observability", fromlist=["x"])
    )
    assert "from accountant.pipeline import" in observability, (
        "accountant/observability.py no longer imports accountant.pipeline, so "
        "the cycle this lazy import works around may be gone. Check before "
        "moving the import back to module scope."
    )


# =============================================================================
# WHAT IS ASKED, AND ABOUT WHOM
# =============================================================================


def test_the_probe_asks_about_the_date_on_the_bill_and_never_about_today(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A back-dated bill is the case this check exists for.

    `check_period(on=...)` defaults to `date.today()`, which is the right answer
    for a bill typed today and the WRONG one for a purchase made in March and
    entered in August. `run` has `build_draft`'s voucher in hand by the time it
    asks, so it passes the bill's own date.
    """
    asked: list[tuple[str | None, datetime.date | None]] = []
    real_check = period_module.check_period

    def watched(**given: Any) -> object:
        asked.append((given.get("company"), given.get("on")))
        return real_check(**given)

    monkeypatch.setattr(period_module, "check_period", watched)
    back_dated = datetime.date(2026, 3, 20)
    spy, draft = run_with(monkeypatch, reader_for(FY_OPEN_START), today=back_dated)

    assert draft.voucher.date == back_dated
    assert asked == [(COMPANY, back_dated)]
    # 2026-03-20 is BEFORE the 2026-04-01 books-from that Tally answered with,
    # so the correct answer is CLOSED. Asking about today instead would have
    # said True and let a bill into a year it does not belong to.
    assert spy.period_open is False


def test_the_probe_names_this_company_and_not_whichever_one_is_open(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`company=None` means "whichever company Tally has open", and `period_for`
    refuses that when two are. Passing the company we are posting into is the
    difference between reading this customer's period and reading somebody
    else's - the same scoping rule `CompanyMemory` holds everywhere else."""
    reader = PeriodReader(
        transport=Answering(
            period_response(FY_OPEN_START).replace(
                "</COLLECTION>",
                '<COMPANY NAME="Someone Else Ltd">'
                f'<STARTINGFROM TYPE="Date">{FY_CLOSED_START}</STARTINGFROM>'
                f'<BOOKSFROM TYPE="Date">{FY_CLOSED_START}</BOOKSFROM>'
                '<NAME TYPE="String">Someone Else Ltd</NAME>'
                "</COMPANY></COLLECTION>",
            )
        )
    )
    spy, _ = run_with(monkeypatch, reader)

    # Two companies are open and they are in different financial years. An
    # unnamed probe would have refused as ambiguous and come back UNVERIFIED.
    assert spy.period_open is True


def test_one_bill_costs_exactly_one_probe(monkeypatch: pytest.MonkeyPatch) -> None:
    """The probe is on the path to a decision while somebody waits at a screen.

    `PERIOD_PROBE_TIMEOUT_SECONDS` is 5.0 and `PERIOD_PROBE_RETRIES` is 1
    precisely because A11 recorded a Tally probe HANGING the gateway on
    2026-08-09. A call site that probed twice per bill would double a bounded
    cost without anybody noticing, because both answers agree.
    """
    transport = Answering(period_response(FY_OPEN_START))
    spy, _ = run_with(monkeypatch, PeriodReader(transport=transport))

    assert len(transport.sent) == 1
    assert spy.period_open is True


def test_an_unreadable_answer_is_unverified_and_never_open(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """FAIL CLOSED, IN ONE DIRECTION ONLY - and the direction is measured.

    An unknown member comes back SILENTLY OMITTED from TallyPrime: measured
    2026-08-13, `FETCH ThisMemberDoesNotExist` answered HTTP 200 in 39 ms with
    only `<NAME>`, no `<LINEERROR>`. So a missing bound means "this build did
    not answer", NOT "there is no such date", and reading absence as permission
    is the one mistake that ends with a voucher in a closed year.
    """
    no_dates = (
        "<ENVELOPE><DATA><COLLECTION>"
        f'<COMPANY NAME="{COMPANY}"><NAME TYPE="String">{COMPANY}</NAME></COMPANY>'
        "</COLLECTION></DATA></ENVELOPE>"
    )
    spy, _ = run_with(monkeypatch, PeriodReader(transport=Answering(no_dates)))

    assert spy.period_open is None
    assert "could not tell whether the books" in spy.said, spy.said


def test_a_company_tally_does_not_have_open_is_unverified(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`PeriodUnreadable`, caught and turned into the third state.

    Tally's gateway only serves a company loaded on screen. That we could not
    find these books is a fact about OUR reach, not about their period, so it
    must not render as "closed"."""
    spy, _ = run_with(
        monkeypatch,
        PeriodReader(
            transport=Answering(period_response(FY_OPEN_START, name="Other Co"))
        ),
    )

    assert spy.period_open is None
    assert "are closed" not in spy.said, spy.said


def test_the_period_probe_never_stops_a_bill_with_a_traceback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A raw `TimeoutError` from a transport that does not wrap its own socket
    errors would walk past `TallyError` and out of `run` as an exception in
    front of somebody's bill. `check_period` catches `OSError` for exactly that,
    and this is the test that says the catch is reachable rather than
    theoretical."""

    class Timing:
        def send(self, payload: str, *, retry: bool) -> str:  # noqa: ARG002 - protocol
            raise TimeoutError("timed out")

    spy, draft = run_with(monkeypatch, PeriodReader(transport=Timing()))

    assert spy.period_open is None
    assert draft.decision is not None
    assert draft.posted_tally_id is None
