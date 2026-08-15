"""`is_period_open` - the period check, and the cage decision it feeds.

MOCKS ONLY. CI never needs a real Tally and nothing here opens a socket. The
one response body these tests parse is the VERBATIM answer measured against the
live TallyPrime 7.0 at 127.0.0.1:9000 on 2026-08-13, kept as a fixture so the
parser is held to what Tally actually sent rather than to what we hoped it sent.

THE CONTROL MATTERS MORE THAN USUAL HERE
------------------------------------------
This check fails closed: an unreadable answer, an unreachable Tally and a
timeout all return False. That means a function whose body was `return False`
would pass every failure test in this file. So the open case is tested against
the SAME fixture and the SAME code path, and
`test_the_control_the_open_case_is_reachable_at_all` exists to fail the day the
check degrades into a permanent no.
"""

from __future__ import annotations

import datetime
import io
import logging
from collections.abc import Iterator

import pytest

from accountant import observability
from accountant import questions as Q
from accountant.cage.decision import Action
from accountant.period import (
    NO_RESPONSE,
    PERIOD_EVENT,
    PeriodVerdict,
    check_period,
    is_period_open,
    period_counters,
    reset_period_counters,
)
from accountant.tallyio.period import (
    PERIOD_PROBE_RETRIES,
    PERIOD_PROBE_TIMEOUT_SECONDS,
    CompanyPeriod,
    PeriodReader,
    PeriodUnreadable,
    build_period_request,
    open_on,
    parse_company_periods,
    parse_tally_date,
    period_config,
    period_for,
)
from accountant.tallyio.real import TallyConfig, TallyUnreachable
from tests.test_gate import a_draft, asked

# ---------------------------------------------------------------------------
# what the live TallyPrime actually sent
# ---------------------------------------------------------------------------

#: VERBATIM, measured 2026-08-13 against TallyPrime 7.0 at 127.0.0.1:9000. The
#: request was `build_period_request()` and the answer was HTTP 200 in 61 ms.
#:
#: `ENDINGAT` is 20260812 while `STARTINGFROM` is 20260401, and that is not a
#: transcription error - it is the measurement this whole module is shaped
#: around. See `accountant/tallyio/period.py` for what ENDINGAT was proved to
#: track and why nothing bounds on it.
MEASURED_RESPONSE = """<ENVELOPE>
 <HEADER><VERSION>1</VERSION><STATUS>1</STATUS></HEADER>
 <BODY>
  <DESC><CMPINFO><COMPANY>0</COMPANY><VOUCHER>4</VOUCHER></CMPINFO></DESC>
  <DATA>
   <COLLECTION>
    <COMPANY NAME="TANVEER SIDHU" RESERVEDNAME="">
     <ENDINGAT TYPE="Date">20260812</ENDINGAT>
     <STARTINGFROM TYPE="Date">20260401</STARTINGFROM>
     <BOOKSFROM TYPE="Date">20260401</BOOKSFROM>
     <NAME TYPE="String">TANVEER SIDHU</NAME>
    </COMPANY>
   </COLLECTION>
  </DATA>
 </BODY>
</ENVELOPE>"""

#: The CONTROL response, also measured the same day: an unknown FETCH member is
#: SILENTLY OMITTED. `FETCH Name, ThisMemberDoesNotExist` answered in 39 ms with
#: only `<NAME>` - no LINEERROR, no ERRORMSG, HTTP 200. So absence is never
#: evidence of a date, and this fixture is what proves the parser agrees.
MEASURED_OMITTED_MEMBERS = """<ENVELOPE>
 <HEADER><VERSION>1</VERSION><STATUS>1</STATUS></HEADER>
 <BODY><DATA><COLLECTION>
    <COMPANY NAME="TANVEER SIDHU" RESERVEDNAME="">
     <NAME TYPE="String">TANVEER SIDHU</NAME>
    </COMPANY>
 </COLLECTION></DATA></BODY>
</ENVELOPE>"""

COMPANY = "TANVEER SIDHU"

#: Inside the measured window 2026-04-01..2027-03-31.
IN_PERIOD = datetime.date(2026, 8, 13)
#: Before the books begin. Tally has nowhere to put it.
BEFORE_BOOKS = datetime.date(2026, 3, 12)
#: After the financial year that began 2026-04-01.
AFTER_YEAR = datetime.date(2027, 4, 1)


# ---------------------------------------------------------------------------
# doubles
# ---------------------------------------------------------------------------


class SaysTransport:
    """A transport that answers with one canned body. Opens no socket."""

    def __init__(self, body: str) -> None:
        self.body = body
        self.sent: list[str] = []
        self.retried: list[bool] = []

    def send(self, payload: str, *, retry: bool) -> str:
        self.sent.append(payload)
        self.retried.append(retry)
        return self.body


class RaisesTransport:
    """A transport that fails the way a named error fails. Opens no socket."""

    def __init__(self, error: Exception) -> None:
        self.error = error
        self.calls = 0

    # The names are the `Transport` protocol's, not ours - `retry` is passed by
    # keyword, so renaming it to `_retry` would stop this satisfying the
    # protocol at all. Unused here because a transport that raises never looks
    # at what it was asked to send.
    def send(self, payload: str, *, retry: bool) -> str:  # noqa: ARG002
        self.calls += 1
        raise self.error


def reading(body: str) -> PeriodReader:
    return PeriodReader(transport=SaysTransport(body))


def failing(error: Exception) -> PeriodReader:
    return PeriodReader(transport=RaisesTransport(error))


@pytest.fixture
def logged() -> Iterator[io.StringIO]:
    """Capture the app's own log, through the SAME installer `serve()` uses.

    Copied in shape from `tests/test_observability.py::logged` deliberately: a
    fixture that built its own handler would prove nothing about the line a
    person actually sees, because the correlation filter lives inside
    `install_logging`.
    """
    stream = io.StringIO()
    observability.install_logging(stream)
    try:
        yield stream
    finally:
        logger = logging.getLogger(observability.LOGGER_NAME)
        for handler in list(logger.handlers):
            logger.removeHandler(handler)
            handler.close()
        logger.propagate = True
        logger.setLevel(logging.NOTSET)


def one_line(stream: io.StringIO) -> str:
    """The single `period_check` line, or a failure naming what was there."""
    lines = [
        ln for ln in stream.getvalue().splitlines() if f"event={PERIOD_EVENT}" in ln
    ]
    assert len(lines) == 1, f"expected exactly one {PERIOD_EVENT} line, got {lines}"
    return lines[0]


# ---------------------------------------------------------------------------
# the request: the shape that fails fast, never the shape that hangs
# ---------------------------------------------------------------------------


def test_the_request_is_an_export_collection_and_never_a_tdl_report() -> None:
    """A11, 2026-08-09: a custom TDL REPORT/FORM/PART/LINE/FIELD HUNG a live
    TallyPrime. This module must be incapable of sending one."""
    request = build_period_request()
    assert "<TYPE>Collection</TYPE>" in request
    assert "<TALLYREQUEST>Export</TALLYREQUEST>" in request
    for hangs in ("<REPORT", "<FORM", "<PART", "<LINE", "<FIELD"):
        assert hangs not in request.upper(), f"{hangs} is the shape that hangs"


def test_the_probe_is_bounded_by_a_short_timeout_and_one_attempt() -> None:
    """A hang has to become a caught error, so the wait is capped and is not
    multiplied by a retry count."""
    config = period_config(TallyConfig(host="127.0.0.1", timeout_seconds=30.0))
    assert config.timeout_seconds == PERIOD_PROBE_TIMEOUT_SECONDS
    assert config.timeout_seconds < 30.0
    assert config.retries == PERIOD_PROBE_RETRIES == 1
    # The caller's own host survives; only patience is overridden.
    assert config.host == "127.0.0.1"


def test_the_read_never_retries_because_a_bounded_wait_is_the_point() -> None:
    transport = SaysTransport(MEASURED_RESPONSE)
    PeriodReader(transport=transport).read()
    assert transport.retried == [False]


# ---------------------------------------------------------------------------
# parsing what Tally really sent
# ---------------------------------------------------------------------------


def test_the_measured_response_parses_into_the_dates_tally_sent() -> None:
    (period,) = parse_company_periods(MEASURED_RESPONSE)
    assert period.name == COMPANY
    assert period.books_from == datetime.date(2026, 4, 1)
    assert period.starting_from == datetime.date(2026, 4, 1)
    assert period.ending_at == datetime.date(2026, 8, 12)


def test_ending_at_is_read_and_reported_but_bounds_nothing() -> None:
    """THE MEASUREMENT THIS MODULE IS SHAPED AROUND.

    `ENDINGAT` came back as the last voucher date, not the year end. Bounding on
    it would refuse every bill dated after the last one already entered - which
    on a healthy Tally is every new bill, for ever.
    """
    (period,) = parse_company_periods(MEASURED_RESPONSE)
    assert period.ending_at == datetime.date(2026, 8, 12)
    # The window ignores it entirely.
    assert period.closes_after == datetime.date(2027, 3, 31)
    # And the day after ENDINGAT is still open, which is the point.
    assert open_on(period, datetime.date(2026, 8, 13))[0] is True


def test_a_silently_omitted_member_blocks_rather_than_reading_as_a_date() -> None:
    """Measured: an unknown FETCH member is dropped with no error at all. So an
    absent field means "this build did not answer", never "no restriction"."""
    (period,) = parse_company_periods(MEASURED_OMITTED_MEMBERS)
    assert period.books_from is None
    assert period.starting_from is None
    verdict, why = open_on(period, IN_PERIOD)
    assert verdict is False
    assert "unknown" in why.lower()


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("20260401", datetime.date(2026, 4, 1)),
        ("1-Apr-2026", datetime.date(2026, 4, 1)),
        ("  20260401  ", datetime.date(2026, 4, 1)),
        ("not a date", None),
        ("", None),
        (None, None),
    ],
)
def test_a_date_is_read_or_left_unread_and_never_defaulted_to_today(
    text: str | None, expected: datetime.date | None
) -> None:
    assert parse_tally_date(text) == expected


def test_a_refusal_envelope_is_not_read_as_no_companies() -> None:
    """D3. A well-formed refusal carries no `<COMPANY>`, so a bare tree walk
    would return `()` and the refusal would be reported as an empty Tally."""
    with pytest.raises(Exception, match="refused this read"):
        parse_company_periods(
            "<ENVELOPE><BODY><DATA><LINEERROR>Could not find Report!"
            "</LINEERROR></DATA></BODY></ENVELOPE>"
        )


# ---------------------------------------------------------------------------
# choosing the company
# ---------------------------------------------------------------------------


def test_two_open_companies_with_no_name_given_is_an_ambiguity_not_a_choice() -> None:
    periods = (CompanyPeriod(name="A"), CompanyPeriod(name="B"))
    with pytest.raises(PeriodUnreadable, match="ambiguous"):
        period_for(periods, None)


def test_one_open_company_answers_for_itself_when_none_is_named() -> None:
    periods = (CompanyPeriod(name="A"),)
    assert period_for(periods, None).name == "A"


def test_a_company_tally_does_not_have_open_is_refused_and_named() -> None:
    with pytest.raises(PeriodUnreadable, match="not among the companies"):
        period_for(parse_company_periods(MEASURED_RESPONSE), "Somebody Else Ltd")


# ---------------------------------------------------------------------------
# is_period_open: the four outcomes the owner asked for
# ---------------------------------------------------------------------------


def test_period_open_returns_true(logged: io.StringIO) -> None:
    """MOCKED TALLY, PERIOD OPEN -> True."""
    assert (
        is_period_open(company=COMPANY, on=IN_PERIOD, reader=reading(MEASURED_RESPONSE))
        is True
    )
    assert "result=True" in one_line(logged)


def test_the_control_the_open_case_is_reachable_at_all() -> None:
    """THE CONTROL. A function whose body was `return False` would pass every
    other assertion in this file. This one it cannot pass."""
    assert (
        is_period_open(company=COMPANY, on=IN_PERIOD, reader=reading(MEASURED_RESPONSE))
        is not False
    )


def test_period_closed_before_the_books_begin_returns_false(
    logged: io.StringIO,
) -> None:
    """MOCKED TALLY, PERIOD CLOSED -> False. Tally's own BOOKSFROM, not ours."""
    assert (
        is_period_open(
            company=COMPANY, on=BEFORE_BOOKS, reader=reading(MEASURED_RESPONSE)
        )
        is False
    )
    line = one_line(logged)
    assert "result=False" in line
    assert "2026-04-01" in line


def test_period_closed_after_the_financial_year_returns_false(
    logged: io.StringIO,
) -> None:
    assert (
        is_period_open(
            company=COMPANY, on=AFTER_YEAR, reader=reading(MEASURED_RESPONSE)
        )
        is False
    )
    assert "result=False" in one_line(logged)


def test_a_connectivity_failure_returns_false_and_is_logged(
    logged: io.StringIO,
) -> None:
    """CONNECTIVITY FAILURE -> False, AND LOGGED. Returned, never raised."""
    unreachable = TallyUnreachable("no response from Tally at http://127.0.0.1:9000")
    assert is_period_open(company=COMPANY, reader=failing(unreachable)) is False
    line = one_line(logged)
    assert "result=False" in line
    assert "TallyUnreachable" in line
    assert "no response from Tally" in line


def test_a_timeout_returns_false_is_logged_and_does_not_hang(
    logged: io.StringIO,
) -> None:
    """A TIMEOUT SPECIFICALLY. `TimeoutError` is an `OSError`, so it would walk
    past a handler that only caught `TallyError` and leave this function as an
    exception. The call must RETURN."""
    assert (
        is_period_open(company=COMPANY, reader=failing(TimeoutError("timed out")))
        is False
    )
    line = one_line(logged)
    assert "result=False" in line
    assert "TimeoutError" in line


def test_an_unreadable_body_returns_false_rather_than_raising(
    logged: io.StringIO,
) -> None:
    assert is_period_open(company=COMPANY, reader=reading("not xml at all")) is False
    assert "result=False" in one_line(logged)


def test_a_company_that_is_not_open_returns_false_and_says_so(
    logged: io.StringIO,
) -> None:
    assert (
        is_period_open(company="Somebody Else Ltd", reader=reading(MEASURED_RESPONSE))
        is False
    )
    assert "not among the companies" in one_line(logged)


# ---------------------------------------------------------------------------
# the log line carries what the owner asked for
# ---------------------------------------------------------------------------


def test_every_call_logs_a_timestamp_a_summary_a_result_and_elapsed_ms(
    logged: io.StringIO,
) -> None:
    is_period_open(company=COMPANY, on=IN_PERIOD, reader=reading(MEASURED_RESPONSE))
    line = one_line(logged)
    assert " at=2026-" in line, "the explicit timestamp field"
    assert "ms=" in line, "how long it took"
    assert "result=True" in line, "the boolean"
    assert "books_from=2026-04-01" in line, "a summary of what Tally said"
    assert "ending_at=2026-08-12" in line, "including the field nothing bounds on"


def test_a_failure_logs_a_summary_saying_there_was_nothing_to_summarise_or_why(
    logged: io.StringIO,
) -> None:
    """A log line for a failed probe must not look like a successful one with
    blank fields."""
    is_period_open(company=COMPANY, reader=failing(TimeoutError("timed out")))
    line = one_line(logged)
    assert NO_RESPONSE not in line, "a named error replaces the placeholder"
    assert "tally=" in line


def test_the_default_date_is_today_and_is_said_on_the_line(logged: io.StringIO) -> None:
    """`is_period_open()` with no date answers about TODAY. That is a real
    limitation for a back-dated bill, so the line says which date it judged."""
    is_period_open(company=COMPANY, reader=reading(MEASURED_RESPONSE))
    assert f"on={datetime.date.today().isoformat()}" in one_line(logged)


# ---------------------------------------------------------------------------
# the cage: the decision has to actually differ
# ---------------------------------------------------------------------------


def test_the_cage_blocks_when_the_period_is_closed() -> None:
    assert asked(a_draft(), period_open=False).action is Action.BLOCK


def test_the_cage_does_not_block_when_the_period_is_open() -> None:
    assert asked(a_draft(), period_open=True).action is not Action.BLOCK


def test_the_two_cage_decisions_differ_so_the_fact_is_load_bearing() -> None:
    """THE WIRING TEST. If both answers produced the same decision, passing a
    measured `period_open` into the cage would change nothing and this whole
    module would be decoration."""
    opened = asked(a_draft(), period_open=True)
    closed = asked(a_draft(), period_open=False)
    assert opened.action is not closed.action
    assert closed.action is Action.BLOCK


def test_a_period_nobody_looked_up_blocks_exactly_like_a_closed_one() -> None:
    """`None` is "nobody looked" and it must not be softer than `False`. This is
    what makes returning False on a connectivity failure safe."""
    assert asked(a_draft(), period_open=None).action is Action.BLOCK


def test_the_value_this_check_produces_is_the_type_the_cage_takes() -> None:
    """The seam. `is_period_open` returns a real `bool`, which is what
    `gate(period_open=...)` is annotated for - not a truthy string, not None."""
    answer = is_period_open(
        company=COMPANY, on=IN_PERIOD, reader=reading(MEASURED_RESPONSE)
    )
    assert isinstance(answer, bool)
    assert asked(a_draft(), period_open=answer).action is not Action.BLOCK


# ---------------------------------------------------------------------------
# the third verdict: an outage is not a closed year
# ---------------------------------------------------------------------------


# THE IGNORE IS ABOUT THE CHECKER, NOT ABOUT THE CODE. An `autouse` fixture is
# called by pytest for every test in this module and is never called by name, so
# pyright reports it unused and CI fails on a function that runs constantly.
# Deleting it would let each test inherit the previous one's call counts, which
# is the one thing this fixture exists to prevent.
@pytest.fixture(autouse=True)
def _counters_start_at_zero() -> Iterator[None]:  # pyright: ignore[reportUnusedFunction]
    """Every test counts only its own calls. A shared total is not a count."""
    reset_period_counters()
    yield
    reset_period_counters()


@pytest.mark.usefixtures("logged")
def test_a_closed_year_and_an_unreachable_tally_are_different_verdicts() -> None:
    """THE DEFECT THIS EXISTS TO PREVENT.

    Both block. But a bare bool made an unreachable Tally render as *"The books
    for 12 March 2026 are closed"* - a confident, specific, wrong claim about
    the customer's books, produced by our own dropped connection.
    """
    closed = check_period(
        company=COMPANY, on=BEFORE_BOOKS, reader=reading(MEASURED_RESPONSE)
    )
    down = check_period(company=COMPANY, reader=failing(TimeoutError("timed out")))

    assert closed.verdict is PeriodVerdict.CLOSED
    assert down.verdict is PeriodVerdict.UNVERIFIED
    assert closed.verdict is not down.verdict
    # Both refuse the auto-post. The distinction is never a decision.
    assert closed.open_for_posting is False
    assert down.open_for_posting is False


def test_the_audit_line_tells_an_outage_from_a_closed_year(logged: io.StringIO) -> None:
    """The owner's question, answered in the log rather than in prose."""
    check_period(company=COMPANY, on=BEFORE_BOOKS, reader=reading(MEASURED_RESPONSE))
    check_period(company=COMPANY, reader=failing(TimeoutError("timed out")))
    lines = [
        ln for ln in logged.getvalue().splitlines() if f"event={PERIOD_EVENT}" in ln
    ]
    assert len(lines) == 2
    assert "verdict=closed" in lines[0]
    assert "verdict=unverified" in lines[1]
    # `result` alone CANNOT answer it, which is why `verdict` is on the line.
    assert "result=False" in lines[0]
    assert "result=False" in lines[1]


@pytest.mark.usefixtures("logged")
def test_only_a_real_closure_reaches_the_cage_as_false() -> None:
    """`for_cage` keeps the third state, so the cage picks its own sentence."""
    closed = check_period(
        company=COMPANY, on=BEFORE_BOOKS, reader=reading(MEASURED_RESPONSE)
    )
    down = check_period(company=COMPANY, reader=failing(TimeoutError("timed out")))
    opened = check_period(
        company=COMPANY, on=IN_PERIOD, reader=reading(MEASURED_RESPONSE)
    )
    assert closed.for_cage is False
    assert down.for_cage is None
    assert opened.for_cage is True


@pytest.mark.usefixtures("logged")
def test_a_missing_bound_is_unverified_and_not_a_closure() -> None:
    """A field this build did not answer is us failing to look, not Tally
    saying the date is out of range."""
    check = check_period(company=COMPANY, reader=reading(MEASURED_OMITTED_MEMBERS))
    assert check.verdict is PeriodVerdict.UNVERIFIED
    assert check.for_cage is None


# ---------------------------------------------------------------------------
# the sentence a person reads
# ---------------------------------------------------------------------------


@pytest.mark.usefixtures("logged")
def test_the_closed_sentence_names_the_date_and_the_window() -> None:
    """WHICH period and WHY, in plain words - not "the period is closed"."""
    said = check_period(
        company=COMPANY, on=BEFORE_BOOKS, reader=reading(MEASURED_RESPONSE)
    ).said
    assert "12 March 2026" in said
    assert "1 April 2026" in said
    assert "31 March 2027" in said
    assert "Nothing was posted" in said


@pytest.mark.usefixtures("logged")
def test_the_unverified_sentence_never_claims_the_books_are_closed() -> None:
    """The whole point. It says WE could not check."""
    said = check_period(company=COMPANY, reader=failing(TimeoutError("t"))).said
    assert "could not check" in said
    assert "closed" not in said.lower()
    assert "Nothing in Tally was changed" in said


def test_both_sentences_come_from_questions_and_not_from_here() -> None:
    """Routed through `accountant/questions.py`, so the words a person reads
    have one home and are held to the plain-language rule."""
    assert Q.books_closed_on(
        BEFORE_BOOKS, datetime.date(2026, 4, 1), datetime.date(2027, 3, 31)
    ).startswith("This bill is dated")
    assert Q.books_could_not_be_checked("Tally did not answer").startswith(
        "I could not check"
    )


def test_the_sentences_carry_no_ledger_jargon() -> None:
    """S7. A refusal a person cannot read is a refusal that gets ignored."""
    sentences = (
        Q.books_closed_on(
            BEFORE_BOOKS, datetime.date(2026, 4, 1), datetime.date(2027, 3, 31)
        ),
        Q.books_could_not_be_checked("Tally did not answer"),
    )
    jargon = [name for name in Q.PLAIN if Q.is_jargon(name)]
    assert jargon, "the control: there must be jargon names to leak"
    for said in sentences:
        leaked = [name for name in jargon if name.lower() in said.lower()]
        assert leaked == [], f"jargon reached the person: {leaked} in {said!r}"


def test_a_plain_date_is_words_and_not_a_timestamp() -> None:
    assert Q.plain_date(datetime.date(2026, 3, 12)) == "12 March 2026"


# ---------------------------------------------------------------------------
# the counters the owner asked for
# ---------------------------------------------------------------------------


@pytest.mark.usefixtures("logged")
def test_the_counters_count_calls_and_each_verdict() -> None:
    check_period(company=COMPANY, on=IN_PERIOD, reader=reading(MEASURED_RESPONSE))
    check_period(company=COMPANY, on=BEFORE_BOOKS, reader=reading(MEASURED_RESPONSE))
    check_period(company=COMPANY, reader=failing(TimeoutError("t")))

    totals = period_counters()
    assert totals.calls == 3
    assert totals.open == 1
    assert totals.closed == 1
    assert totals.unverified == 1
    # The error count and the fallback-usage count are the same number, and
    # that is a fact worth asserting rather than two fields that can disagree.
    assert totals.blocked == 2


def test_the_open_ratio_is_undefined_over_zero_calls_and_never_zero() -> None:
    """`observability` rule 2. A 0.0 here would say "this never passes" on the
    day it had been asked nothing at all."""
    assert period_counters().ratio_open is None


@pytest.mark.usefixtures("logged")
def test_the_open_ratio_is_measured_once_there_are_calls() -> None:
    check_period(company=COMPANY, on=IN_PERIOD, reader=reading(MEASURED_RESPONSE))
    check_period(company=COMPANY, on=BEFORE_BOOKS, reader=reading(MEASURED_RESPONSE))
    assert period_counters().ratio_open == 0.5


def test_the_running_totals_are_on_every_log_line(logged: io.StringIO) -> None:
    check_period(company=COMPANY, on=IN_PERIOD, reader=reading(MEASURED_RESPONSE))
    line = one_line(logged)
    assert "calls=1" in line
    assert "open=1" in line
    assert "closed=0" in line
    assert "unverified=0" in line
    assert "ratio_open=1.0" in line


def test_the_ratio_on_the_line_is_not_measured_before_it_can_be(
    logged: io.StringIO,
) -> None:
    """The first line ever written has one call on it, so the ratio IS defined.
    The control is that the field exists at all rather than being omitted."""
    check_period(company=COMPANY, reader=failing(TimeoutError("t")))
    assert "ratio_open=0.0" in one_line(logged)


@pytest.mark.usefixtures("logged")
def test_the_counters_are_a_snapshot_and_not_the_live_object() -> None:
    before = period_counters()
    check_period(company=COMPANY, on=IN_PERIOD, reader=reading(MEASURED_RESPONSE))
    assert before.calls == 0, "a snapshot that moved is not a snapshot"
    assert period_counters().calls == 1
