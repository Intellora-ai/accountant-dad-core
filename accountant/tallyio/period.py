"""Is the company's accounting period open for a date? Measured, not inferred.

WHAT THIS ANSWERS, AND WHY IT IS NOT A CALENDAR CALCULATION
------------------------------------------------------------
`accountant/cage/gate.py` needs `period_open` before a write and has never had
it. Its module docstring says exactly why it refuses to work the fact out for
itself: an Indian financial year runs 1 April to 31 March, so `period_open`
looks computable from the date on the bill, and it is not. Whether a company's
books are open for a date is a fact about THAT COMPANY'S Tally.

TallyPrime already refuses an out-of-year date at the write door, and
`accountant/tallyio/errors.py` turns that refusal into "that date is outside the
company's financial year, so the voucher has nowhere to go." So the fact exists
- but only AFTER the attempt. This module reads it BEFORE.

THE MEASUREMENT, QUOTED RATHER THAN SUMMARISED
------------------------------------------------
Measured 2026-08-13 against the live TallyPrime 7.0 at 127.0.0.1:9000, company
"TANVEER SIDHU". `build_period_request()` below - Export/Collection, TYPE
Company - answered HTTP 200 in 61 ms with, verbatim:

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

Three more requests, same shape, same session, and they are the reason this
module is built the way it is rather than the obvious way:

  `FETCH Name, ThisMemberDoesNotExist`   answered in 39 ms with ONLY `<NAME>`.
      An unknown member is SILENTLY OMITTED. No `<LINEERROR>`, no `<ERRORMSG>`,
      HTTP 200. So a missing element means "this build did not answer", it does
      NOT mean "there is no such date", and the two must never be conflated.
      Everything below therefore treats an absent field as UNREADABLE and
      blocks, rather than reading absence as permission.

  `FETCH Name, FinancialYearEndingAt, LastVoucherDate, YearEnd`  25 ms:
       <LASTVOUCHERDATE TYPE="Date">20260812</LASTVOUCHERDATE>
       <NAME TYPE="String">TANVEER SIDHU</NAME>
      `FinancialYearEndingAt` and `YearEnd` were omitted - this build does not
      honour either. NOTHING ON THIS BUILD STATES THE FINANCIAL YEAR END.

  `FETCH Name, IsBookClosed, BooksClosedFrom, EndDate`   7 ms, all omitted.
      There is no books-closed flag to read either.

`ENDINGAT` IS NOT THE END OF THE YEAR, AND USING IT WOULD BLOCK EVERYTHING
---------------------------------------------------------------------------
This is the trap, and it is the whole reason the obvious rule is not the rule.

`ENDINGAT` came back as 20260812 while `STARTINGFROM` was 20260401. A financial
year beginning 1 April 2026 does not end on 12 August 2026. Two independent
readings show what it actually tracks: `LASTVOUCHERDATE` is the SAME value, and
a voucher export of the same company shows all of its vouchers dated 20260812.
`ENDINGAT` follows the DATA in the company, not the period.

So the obvious check - `books_from <= d <= ending_at` - would have refused every
bill dated after the last one already entered. On a perfectly healthy Tally with
open books, that is every new bill, for ever. It would have looked like a
conservative safety check and behaved like an outage.

`ENDINGAT` is therefore READ and REPORTED as evidence, and is used as a bound by
nothing.

WHAT IS MEASURED AND WHAT IS DERIVED, SAID OUT LOUD
-----------------------------------------------------
MEASURED, from the company's own Tally:
    `BOOKSFROM`     the books do not exist before this date. A bill dated
                    earlier has nowhere to go, and that is Tally's statement,
                    not our arithmetic.
    `STARTINGFROM`  the financial year Tally currently has loaded begins here.

DERIVED, and labelled as such wherever it appears:
    the upper bound = `STARTINGFROM` plus twelve months, minus one day. For the
    measurement above that is 2027-03-31, which is the correct end of an Indian
    financial year beginning 2026-04-01.

    It is arithmetic on a date TALLY STATED, not a guess pulled from the
    calendar - but it is still a derivation, and it has one known way to be
    wrong: a company's FIRST year can be short or long, which is precisely why
    Tally carries `BOOKSFROM` and `STARTINGFROM` as separate fields. They were
    equal on the instance measured, so that case was NOT exercised here.

    `PERIOD_UPPER_BOUND_IS_DERIVED` carries this sentence to the log, so a
    reader of a refusal is told which half of the window was read and which was
    computed.

FAIL CLOSED, IN ONE DIRECTION ONLY
-----------------------------------
Every unreadable answer means "we could not verify the period is open", which
blocks. It never means "open". A missing field, an unparseable date, a company
that is not among the ones Tally listed, two companies with the same name - all
of them refuse. `accountant/period.py` turns a refusal into `False` and logs it.

NO PRODUCT IMPORTS, AND THAT IS WHY THE LOGGING IS NOT HERE
-------------------------------------------------------------
Correction C3: nothing under `accountant/tallyio/` may import the product layer,
and `tests/test_reverse_all_cli.py` enforces it by AST scan. `accountant/
observability.py` imports `accountant.pipeline`, which imports this package - so
logging from here would be both a boundary violation and an import cycle. The
read lives here; `accountant/period.py` is the one caller, and it does the
timing and the logging that the owner asked for on every call.
"""

from __future__ import annotations

import datetime
from dataclasses import dataclass, field, replace
from typing import Final

# B405 objects to xml.etree on sight. Only the Element TYPE is used here for
# annotations; every byte is parsed by the hardened expat parser in `real`.
from xml.etree import ElementTree  # nosec B405

from accountant.tallyio.real import (
    DEFAULT_MAX_RESPONSE_BYTES,
    HttpTransport,
    TallyConfig,
    Transport,
    parse_read_response,
)

#: The collection this module asks for. Its own name, not `AD Companies`, so a
#: reader watching the wire can tell the period probe from the startup company
#: list - they are the same request family and they are asked for different
#: reasons.
PERIOD_COLLECTION: Final = "AD Company Period"

#: The members fetched, exactly as measured. `EndingAt` is on the list because
#: it is EVIDENCE worth logging, not because anything bounds on it - see the
#: module docstring for what it was measured to actually track.
PERIOD_FETCH: Final[tuple[str, ...]] = ("Name", "StartingFrom", "EndingAt", "BooksFrom")

#: How long the probe may block, in seconds.
#:
#: DELIBERATELY SHORTER THAN `TallyConfig.timeout_seconds`, which is 30. This
#: check runs on the path to a decision about somebody's books while they are
#: sitting in front of the screen, and A11 records what an unbounded Tally probe
#: cost this project: a custom TDL report HUNG the gateway on 2026-08-09 rather
#: than answering. A hard, short socket timeout is what turns a hang into a
#: caught error instead of a wedged process.
#:
#: 5.0 against a measured 61 ms - eighty times the observed round trip, so a
#: merely slow Tally still answers and a stopped one is given up on quickly.
PERIOD_PROBE_TIMEOUT_SECONDS: Final = 5.0

#: One attempt. A read would be safe to retry, but retrying multiplies the wait
#: by three and this probe's whole value is that it is bounded. `read_licence`
#: is bounded the same way and says so.
PERIOD_PROBE_RETRIES: Final = 1

#: Said wherever the window appears, because half of it is arithmetic.
PERIOD_UPPER_BOUND_IS_DERIVED: Final = (
    "the start of the window is Tally's own BOOKSFROM; the end is DERIVED as "
    "STARTINGFROM plus twelve months minus one day, because this build states "
    "no financial year end - ENDINGAT was measured to track the last voucher "
    "date, not the period"
)

#: Tally's date formats. `%Y%m%d` is what the measurement above returned and is
#: what every date child tag in this connector uses (A6). `%d-%b-%Y` is the
#: other form Tally is known to emit - A6 records it on the delete ATTRIBUTE -
#: and is accepted so that a build using it reads rather than blocks.
_DATE_FORMATS: Final[tuple[str, ...]] = ("%Y%m%d", "%d-%b-%Y")

#: December, so `_days_in` has no month after it to step back from.
_DECEMBER: Final = 12


class PeriodUnreadable(Exception):
    """The period could not be established, for a named reason.

    NOT a `real.TallyError`: that tree describes things Tally told us, and most
    of these are things Tally did NOT tell us. A caller that sees this has one
    correct response, which is to block.
    """


def build_period_request() -> str:
    """The one envelope this module sends. Chosen for what it is NOT.

    Export/Collection, TYPE Company - the SAME request family as
    `real.build_company_list_request`, which has answered this instance since
    2026-08-08 and answered it again in 43 ms during the measurement recorded in
    the module docstring.

    A custom TDL REPORT/FORM/PART/LINE/FIELD is the shape that HUNG this
    gateway (A11, 2026-08-09). It is not built here, not as an option, not
    behind a flag. There is nothing in this module that could send one.

    No value is interpolated. The collection name and the member list are
    module constants and the company is NOT named in the request - every open
    company comes back and the match happens in Python. That removes the only
    place a caller's string could have reached the wire.
    """
    fetched = ", ".join(PERIOD_FETCH)
    return (
        "<ENVELOPE>"
        "<HEADER>"
        "<VERSION>1</VERSION>"
        "<TALLYREQUEST>Export</TALLYREQUEST>"
        "<TYPE>Collection</TYPE>"
        f"<ID>{PERIOD_COLLECTION}</ID>"
        "</HEADER>"
        "<BODY><DESC>"
        "<STATICVARIABLES>"
        "<SVEXPORTFORMAT>$$SysName:XML</SVEXPORTFORMAT>"
        "</STATICVARIABLES>"
        "<TDL><TDLMESSAGE>"
        f'<COLLECTION NAME="{PERIOD_COLLECTION}" ISMODIFY="No" ISFIXED="No">'
        "<TYPE>Company</TYPE>"
        f"<FETCH>{fetched}</FETCH>"
        "</COLLECTION>"
        "</TDLMESSAGE></TDL>"
        "</DESC></BODY>"
        "</ENVELOPE>"
    )


def parse_tally_date(text: str | None) -> datetime.date | None:
    """One Tally date, or None when it is not one.

    None rather than an exception, and never `date.today()`. A date we could not
    read is a fact we do not have; substituting today's date would answer the
    question with the input we were trying to check.
    """
    if text is None:
        return None
    stripped = text.strip()
    if not stripped:
        return None
    for form in _DATE_FORMATS:
        try:
            return datetime.datetime.strptime(stripped, form).date()
        except ValueError:
            continue
    return None


def _days_in(year: int, month: int) -> int:
    """How many days that month has. Stdlib arithmetic, no `calendar` import."""
    if month == _DECEMBER:
        return 31
    return (datetime.date(year, month + 1, 1) - datetime.timedelta(days=1)).day


def _derived_year_end(starting_from: datetime.date) -> datetime.date:
    """The day before the same date twelve months later.

    2026-04-01 gives 2027-03-31, which is the end of the Indian financial year
    that began on the measured `STARTINGFROM`. 2026-01-01 gives 2026-12-31.

    The day is CLAMPED to the length of the target month before the step back,
    so a year beginning 2028-02-29 - which has no 2029-02-29 to subtract from -
    produces a real date instead of a `ValueError` in front of somebody's bill.
    Every financial year start this connector has seen is the 1st of a month, so
    the clamp has never fired; it is here because a crash on a date is a worse
    failure than a day's imprecision on a bound that is already labelled derived.
    """
    year = starting_from.year + 1
    month = starting_from.month
    day = min(starting_from.day, _days_in(year, month))
    return datetime.date(year, month, day) - datetime.timedelta(days=1)


@dataclass(frozen=True)
class CompanyPeriod:
    """One company's period, exactly as Tally answered, plus the derived bound.

    Every field is `| None` because the measurement above proved that an
    unknown member comes back SILENTLY OMITTED. A `None` here means "this build
    did not answer that", which is a different fact from any date and is never
    filled in with a default.
    """

    name: str
    books_from: datetime.date | None = None
    starting_from: datetime.date | None = None
    #: Read and reported, used as a bound by NOTHING. Measured to track the last
    #: voucher date. See the module docstring.
    ending_at: datetime.date | None = None

    @property
    def opens_on(self) -> datetime.date | None:
        """The first date the books accept. Tally's own `BOOKSFROM`.

        Falls back to `STARTINGFROM` when the build did not answer `BOOKSFROM`,
        because a year that begins on a date is not open before it either. Both
        absent is `None`, which blocks.
        """
        return self.books_from or self.starting_from

    @property
    def closes_after(self) -> datetime.date | None:
        """The last date the books accept. DERIVED - see the module docstring."""
        if self.starting_from is None:
            return None
        return _derived_year_end(self.starting_from)

    def summary(self) -> str:
        """What Tally said, for a log line. Sibling of `ImportResult.summary`."""
        return (
            f"company={self.name!r} books_from={self.books_from} "
            f"starting_from={self.starting_from} ending_at={self.ending_at} "
            f"window={self.opens_on}..{self.closes_after}"
        )


def _child_date(node: ElementTree.Element, tag: str) -> datetime.date | None:
    found = node.find(tag)
    return None if found is None else parse_tally_date(found.text)


def _company_name(node: ElementTree.Element) -> str | None:
    """Tally puts the name on the attribute and in a child. Either will do."""
    attribute = node.get("NAME")
    if attribute is not None and attribute.strip():
        return attribute.strip()
    child = node.find("NAME")
    if child is None or child.text is None:
        return None
    return child.text.strip() or None


def parse_company_periods(
    payload: str, limit: int = DEFAULT_MAX_RESPONSE_BYTES
) -> tuple[CompanyPeriod, ...]:
    """Every open company's period, out of one response.

    `parse_read_response`, not `parse_xml`. That is D3's rule and it matters
    here for the same reason it mattered for vouchers: a well-formed refusal
    envelope contains no `<COMPANY>`, so a bare tree walk would return `()` and
    "Tally refused the request" would become "no companies", which this check
    would then read as "we could not find the company" - true by accident, for
    entirely the wrong reason, and impossible to diagnose.
    """
    root = parse_read_response(payload, limit)
    return tuple(
        CompanyPeriod(
            name=name,
            books_from=_child_date(node, "BOOKSFROM"),
            starting_from=_child_date(node, "STARTINGFROM"),
            ending_at=_child_date(node, "ENDINGAT"),
        )
        for node in root.iter("COMPANY")
        if (name := _company_name(node)) is not None
    )


def period_for(
    periods: tuple[CompanyPeriod, ...], company: str | None
) -> CompanyPeriod:
    """The one company this question is about, or a refusal naming the problem.

    `company=None` is allowed and means "whichever company Tally has open",
    which is only answerable when there is exactly one. Two open companies with
    no name to choose between them is an ambiguity, and A5's rule holds here
    too: an ambiguity is refused, never resolved by picking one.

    Matched with `str.strip()` and case-sensitively. TallyPrime reports the
    spelling a company was created with and `reports.touches` already refuses to
    case-fold for the same reason - a loose match here would answer about
    somebody else's books.
    """
    if not periods:
        raise PeriodUnreadable(
            "Tally listed no companies at all, so there is no period to read. "
            "The gateway only serves a company that is loaded on screen."
        )
    if company is None:
        if len(periods) == 1:
            return periods[0]
        raise PeriodUnreadable(
            f"{len(periods)} companies are open ({[p.name for p in periods]}) "
            "and no company was named, so which books to ask about is "
            "ambiguous. Refusing to pick one."
        )

    wanted = company.strip()
    matches = [item for item in periods if item.name.strip() == wanted]
    if not matches:
        raise PeriodUnreadable(
            f"{company!r} is not among the companies Tally has open "
            f"({[p.name for p in periods]}), so its period cannot be read."
        )
    if len(matches) > 1:
        raise PeriodUnreadable(
            f"{len(matches)} open companies are called {company!r}. That is an "
            "ambiguity, and it is refused rather than resolved by picking one."
        )
    return matches[0]


def open_on(period: CompanyPeriod, day: datetime.date) -> tuple[bool, str]:
    """Is `day` inside this company's open window? The verdict and WHY.

    The reason comes back beside the boolean rather than being reconstructed by
    the caller, because "the period is closed" sends somebody through their
    whole Tally and "the books begin on 1 April 2026 and this bill is dated 12
    March 2026" does not.

    A missing bound BLOCKS. Both bounds have to be readable for this to say
    True: an unread `BOOKSFROM` is "we do not know when these books begin", and
    answering "open" to that is the one direction this check must never fail in.
    """
    opens = period.opens_on
    closes = period.closes_after
    if opens is None:
        return False, (
            f"Tally did not answer BOOKSFROM or STARTINGFROM for {period.name!r}, "
            "so when these books begin is unknown. Unknown is not open."
        )
    if closes is None:
        return False, (
            f"Tally did not answer STARTINGFROM for {period.name!r}, so the end "
            "of the financial year cannot be worked out. Unknown is not open."
        )
    if day < opens:
        return False, (
            f"{day} is before {opens}, the date {period.name!r}'s books begin. "
            "Tally has nowhere to put an entry dated earlier than that."
        )
    if day > closes:
        return False, (
            f"{day} is after {closes}, the last day of the financial year that "
            f"began {period.starting_from}. {PERIOD_UPPER_BOUND_IS_DERIVED}."
        )
    return True, (
        f"{day} is inside {opens}..{closes} for {period.name!r}. "
        f"{PERIOD_UPPER_BOUND_IS_DERIVED}."
    )


def period_config(base: TallyConfig | None = None) -> TallyConfig:
    """The caller's Tally, made impatient for this one probe.

    Host, port and response cap are the caller's. The timeout and the retry
    count are NOT: they are what makes this bounded, and a caller who has set a
    thirty second timeout for a voucher export has not thereby decided that a
    period check may block a person's screen for thirty seconds.
    """
    return replace(
        base or TallyConfig(),
        timeout_seconds=PERIOD_PROBE_TIMEOUT_SECONDS,
        retries=PERIOD_PROBE_RETRIES,
    )


@dataclass
class PeriodReader:
    """One bounded read of the open companies' periods. No writes exist here."""

    transport: Transport = field(default_factory=lambda: HttpTransport(period_config()))
    limit: int = DEFAULT_MAX_RESPONSE_BYTES

    def read(self) -> tuple[CompanyPeriod, ...]:
        """Send the probe and parse it. RAISES; it does not decide.

        `retry=False` for the reason in `PERIOD_PROBE_RETRIES`. The caller -
        `accountant/period.py` - is the layer that turns every failure into
        `False` and logs it, because that is the layer allowed to import the
        logger.
        """
        return parse_company_periods(
            self.transport.send(build_period_request(), retry=False), self.limit
        )


def reader_for(config: TallyConfig) -> PeriodReader:
    """A bounded period reader aimed at the caller's Tally.

    THE TRANSPORT IS CONSTRUCTED HERE AND NOWHERE ELSE, and that is not tidiness.
    `HttpTransport` is what puts bytes on the wire, and
    `tests/test_write_door.py::test_nothing_outside_the_connector_imports_the_tally_transport`
    fails if any module outside `accountant/tallyio/` so much as imports it - so
    that a socket cannot be opened without going through the connector.
    `accountant/web/app.py` needs a READER, not a transport, and this function is
    the difference between the two.
    """
    return PeriodReader(transport=HttpTransport(period_config(config)))
