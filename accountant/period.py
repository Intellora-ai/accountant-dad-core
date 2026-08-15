"""`is_period_open()` - the one function the web app calls before it decides.

WHY THIS IS NOT IN `accountant/tallyio/`
------------------------------------------
The read itself IS in `accountant/tallyio/period.py`, with the other Tally
reads, and that is where the request shape and the measurement live. What could
not go there is the LOGGING the owner asked for on every call.

Correction C3 forbids anything under `accountant/tallyio/` from importing the
product layer, and `tests/test_reverse_all_cli.py` enforces it by AST scan.
`accountant/observability.py` imports `accountant.pipeline`, which imports
`accountant.tallyio` - so logging from inside the connector would be a boundary
violation AND a genuine import cycle, not merely a style objection.

So the layer split is: the connector reads and raises, this module decides and
logs. One Tally probe, one log line, one boolean, every time.

FALSE MEANS "WE COULD NOT VERIFY IT IS OPEN", AND IT BLOCKS
------------------------------------------------------------
Owner instruction, for launch: a transient error, a connectivity failure or a
timeout is logged and returns `False`. Not an exception, and never `True`.

That asymmetry is the whole point and it is worth stating plainly, because a
reader will otherwise `except` this back into `True` the first time a flaky
network makes the app block: `False` here does NOT claim the period is closed.
It claims we could not establish that it is open. Those are different facts and
they happen to call for the same action, which is to stop and let a person look.
The direction that must never be reached by accident is the other one - a `True`
nobody measured authorises a write into somebody's statutory books.

WHAT IT NEVER RAISES ON, AND THE ONE THING THAT COULD
-------------------------------------------------------
The docstring the owner wrote says "raise only on hard connectivity / auth
failures". Connectivity is handled above - it returns `False`. That leaves auth,
and the honest answer is that there is nothing to raise TODAY: TallyPrime's XML
gateway on port 9000 has no authentication at all beyond network reachability,
which `real.TallyConfig` already records as the reason it prefers loopback.
There is no credential to be rejected and no exception class that would mean it.

So this function does not raise today, and the reason is a measured property of
the gateway rather than a decision to swallow everything. If Tally ever grows an
authenticated gateway, the `except` clauses below name concrete classes and an
auth error would not be among them - it would propagate, which is the behaviour
the owner asked for.

THE SIGNATURE IS `is_period_open() -> bool`, AND IT ANSWERS ABOUT A DATE
-------------------------------------------------------------------------
"Is the period open" is not a global fact. It is a question about the date on a
bill, and a bill dated three months ago is exactly the case this check exists
for. The owner's signature takes no arguments, so with none supplied this
answers about TODAY - which is the right answer for a bill typed today and the
WRONG answer for a back-dated one.

`on=` is therefore a keyword argument with a default, not a new required
parameter: `is_period_open()` still means what the owner wrote, and
`is_period_open(on=draft.voucher.date)` is the call that will be correct for
every bill once a call site has a draft to read the date off. `docs/` and the
report for this change both say so; nothing here silently pretends today is the
bill's date.
"""

from __future__ import annotations

import datetime
import threading
import time
from dataclasses import dataclass
from enum import StrEnum

from accountant import observability
from accountant import questions as Q
from accountant.tallyio.period import (
    PeriodReader,
    PeriodUnreadable,
    open_on,
    period_for,
)
from accountant.tallyio.real import TallyError

#: The event name on the log line. One word, so a person can grep for it and a
#: count of period checks is one `grep -c` rather than a regex.
PERIOD_EVENT = "period_check"

#: What the log says when the probe never got an answer to summarise.
NO_RESPONSE = "no response to summarise"


class PeriodVerdict(StrEnum):
    """Three answers, because two of them would be a lie in the audit line.

    THIS IS THE WHOLE OF THE OWNER'S POINT 2, and it is not a decision - both
    CLOSED and UNVERIFIED block, exactly like `conservation`'s INDETERMINATE.
    The distinction is for the LOG and for the SENTENCE A PERSON READS.

    Collapsing them was a real defect in the first version of this module, not a
    hypothetical: `is_period_open` returned a bare `False` for an unreachable
    Tally, the cage read `period_open=False`, and `decision._period_closed` told
    the customer *"The books for 12 March 2026 are closed"* - a confident,
    specific, wrong statement about THEIR Tally, produced by OUR dropped
    connection. It sends somebody into their accounting software to fix a
    setting that was never wrong.

    OPEN         Tally answered and the date is inside the window.
    CLOSED       Tally answered and the date is outside it. A fact about the
                 customer's books.
    UNVERIFIED   We did not find out - unreachable, timed out, unparseable, a
                 company that is not open, a field this build did not answer.
                 A fact about US.
    """

    OPEN = "open"
    CLOSED = "closed"
    UNVERIFIED = "unverified"


@dataclass(frozen=True)
class PeriodCheck:
    """One period check: what we concluded, what to tell the person, how long.

    `said` is the sentence for a human and comes from `accountant/questions.py`,
    never from here. `detail` is for the log and may name an exception class.
    """

    verdict: PeriodVerdict
    said: str
    detail: str
    elapsed_ms: float

    @property
    def open_for_posting(self) -> bool:
        """The owner's boolean. OPEN is the only True; both others block."""
        return self.verdict is PeriodVerdict.OPEN

    @property
    def for_cage(self) -> bool | None:
        """What `cage.gate(period_open=...)` takes, keeping the third state.

        `None` is the cage's OWN word for "nobody looked" and it already has a
        distinct sentence for it (`decision._PERIOD_UNKNOWN`). So UNVERIFIED
        maps to `None` rather than to `False`, which is what stops a dropped
        connection being reported to a customer as a closed year. Both block.
        """
        if self.verdict is PeriodVerdict.OPEN:
            return True
        if self.verdict is PeriodVerdict.CLOSED:
            return False
        return None


@dataclass
class PeriodCounters:
    """How many checks, and how they came out. PROCESS-LOCAL, and it says so.

    `accountant/observability.py` rule 1 is that a COUNT COMES FROM THE DURABLE
    STORE OR IT IS NOT A COUNT, because a process-local counter resets on
    restart and then reports a smaller number than the truth. These are
    therefore NOT offered on `/metrics` and are not a durable count of anything.

    They exist because every one of them is ALSO recoverable from the durable
    log - each check writes one `event=period_check` line carrying
    `verdict=`, so `calls` is `grep -c event=period_check` and `unverified` is
    `grep -c verdict=unverified`. The counters are the same numbers for this
    process, carried on the line so a reader gets the running totals without
    piping anything, and the log is what survives a restart.

    `ratio_open` is `None` over zero calls and never `0.0` - rule 2, and the
    case it exists for is the one where a flattering zero would be read as a
    fact about a system that has done nothing at all.
    """

    calls: int = 0
    open: int = 0
    closed: int = 0
    unverified: int = 0

    @property
    def ratio_open(self) -> float | None:
        """Share of checks that said OPEN, or `None` when nothing was checked."""
        if self.calls == 0:
            return None
        return round(self.open / self.calls, 3)

    @property
    def blocked(self) -> int:
        """Every check that stopped an auto-post, of either kind."""
        return self.closed + self.unverified


#: The counters, and the lock that keeps them honest under the threading server.
#:
#: `ThreadingHTTPServer` serves requests concurrently (see `observability`'s note
#: on why the correlation ids are ContextVars), so `calls += 1` from two threads
#: can lose an increment. A counter that under-reports is exactly the failure
#: rule 1 warns about, so the increments are taken under a lock.
_COUNTERS = PeriodCounters()
_COUNTERS_LOCK = threading.Lock()


def period_counters() -> PeriodCounters:
    """A snapshot of the process-local counters. Never the live object."""
    with _COUNTERS_LOCK:
        return PeriodCounters(
            calls=_COUNTERS.calls,
            open=_COUNTERS.open,
            closed=_COUNTERS.closed,
            unverified=_COUNTERS.unverified,
        )


def reset_period_counters() -> None:
    """Back to zero. For tests, which must not inherit each other's totals."""
    with _COUNTERS_LOCK:
        _COUNTERS.calls = 0
        _COUNTERS.open = 0
        _COUNTERS.closed = 0
        _COUNTERS.unverified = 0


def _count(verdict: PeriodVerdict) -> PeriodCounters:
    with _COUNTERS_LOCK:
        _COUNTERS.calls += 1
        if verdict is PeriodVerdict.OPEN:
            _COUNTERS.open += 1
        elif verdict is PeriodVerdict.CLOSED:
            _COUNTERS.closed += 1
        else:
            _COUNTERS.unverified += 1
        return PeriodCounters(
            calls=_COUNTERS.calls,
            open=_COUNTERS.open,
            closed=_COUNTERS.closed,
            unverified=_COUNTERS.unverified,
        )


def check_period(
    *,
    company: str | None = None,
    on: datetime.date | None = None,
    reader: PeriodReader | None = None,
) -> PeriodCheck:
    """Ask Tally, and come back with one of three answers and a sentence.

    THE FUNCTION THAT ACTUALLY DOES THE WORK. `is_period_open` is the owner's
    boolean over the top of it, and this one exists so the caller that wants the
    third state - "we did not find out" - can have it without a second probe.

    NEVER RAISES on a connectivity failure, a timeout, an unparseable body or a
    company that is not open. Every one of those becomes UNVERIFIED, which
    blocks. See the module docstring for the one thing that could raise and why
    it cannot today.

    Logs exactly one line per call, whatever happened.
    """
    started = time.perf_counter()
    day = on or datetime.date.today()
    probe = reader if reader is not None else PeriodReader()

    verdict = PeriodVerdict.UNVERIFIED
    detail = NO_RESPONSE
    said = ""
    try:
        periods = probe.read()
        period = period_for(periods, company)
        detail = period.summary()
        is_open, why = open_on(period, day)
        detail = f"{detail} verdict={why}"
        opens, closes = period.opens_on, period.closes_after
        if is_open:
            verdict = PeriodVerdict.OPEN
        elif opens is not None and closes is not None:
            # Tally ANSWERED and the date is outside the window it stated. This
            # is the only path to CLOSED: a fact about the customer's books.
            verdict = PeriodVerdict.CLOSED
            said = Q.books_closed_on(day, opens, closes)
        else:
            # A bound this build did not answer. `open_on` said False, but it
            # said it because a field was MISSING, which is not the same as the
            # date being outside a window nobody read.
            verdict = PeriodVerdict.UNVERIFIED
            said = Q.books_could_not_be_checked("Tally did not give the dates")
    except PeriodUnreadable as refusal:
        # The connector could not name a period - no company open, the named
        # company is not open, or two share a name.
        detail = f"{type(refusal).__name__}: {refusal}"
        said = Q.books_could_not_be_checked("the company's books were not found")
    except TallyError as exc:
        # Tally answered with something we refuse to treat as an answer, or did
        # not answer at all. `TallyUnreachable` - which is what a socket timeout
        # becomes inside `HttpTransport` - is in this tree.
        detail = f"{type(exc).__name__}: {exc}"
        said = Q.books_could_not_be_checked("Tally did not answer")
    except OSError as exc:
        # A transport that is not `HttpTransport` need not wrap its own socket
        # errors, and an unwrapped `TimeoutError` would otherwise walk straight
        # past the handler above and out of this function as an exception. The
        # owner asked for a blocked post, not a traceback, so it is caught here
        # for the same reason `reports._collection` catches it.
        detail = f"{type(exc).__name__}: {exc}"
        said = Q.books_could_not_be_checked("Tally could not be reached")

    elapsed_ms = round((time.perf_counter() - started) * 1000.0, 1)
    totals = _count(verdict)
    observability.log(
        PERIOD_EVENT,
        # The formatter already stamps `%(asctime)s` on every line. This field
        # is here anyway so the line is self-describing if it is ever read
        # through a handler this project did not install.
        at=datetime.datetime.now().astimezone().isoformat(timespec="milliseconds"),
        company=company if company is not None else "ANY_OPEN_COMPANY",
        on=day.isoformat(),
        # THE THREE-WAY ANSWER, so an unreachable Tally is distinguishable from
        # a closed year in the audit line. `result` is the boolean the owner
        # asked for and is kept beside it rather than instead of it: both of the
        # blocking verdicts render `result=False`, which is why `result` alone
        # cannot answer "was Tally down".
        verdict=verdict.value,
        result=verdict is PeriodVerdict.OPEN,
        ms=elapsed_ms,
        # Running totals for THIS PROCESS. The durable count is the log itself -
        # one line per check, each carrying `verdict=`. See `PeriodCounters`.
        calls=totals.calls,
        open=totals.open,
        closed=totals.closed,
        unverified=totals.unverified,
        ratio_open=(
            observability.NOT_MEASURED
            if totals.ratio_open is None
            else totals.ratio_open
        ),
        tally=detail,
    )
    return PeriodCheck(verdict=verdict, said=said, detail=detail, elapsed_ms=elapsed_ms)


def is_period_open(
    *,
    company: str | None = None,
    on: datetime.date | None = None,
    reader: PeriodReader | None = None,
) -> bool:
    """Return True if the current accounting period in Tally is open for posting.

    Return False if the period is closed, AND if we could not establish that it
    is open - see the module docstring for why those share an answer and why
    that answer is never `True`.

    THE OWNER'S SIGNATURE, and a thin one on purpose. It collapses the three
    verdicts to the two the owner asked for, which is safe HERE because both
    non-open verdicts block. A caller that needs to tell a dropped connection
    from a closed year - which is every caller that shows a person a sentence -
    calls `check_period` instead and reads `verdict`. `accountant/web/app.py`
    does exactly that.

    Every call logs one line through `accountant/observability.py`: the
    timestamp, a summary of what Tally said, the verdict, the boolean, the
    running counters, and the elapsed milliseconds.

    `company` names the books to ask about. `None` means "whichever company
    Tally has open", which is answerable only when there is exactly one; two
    open companies with nothing to choose between them refuses.

    `on` is the date being asked about, defaulting to today. A bill carries its
    own date and a back-dated bill is the case this check exists for, so a call
    site holding a draft should pass `on=draft.voucher.date`.

    `reader` is the injection point for tests and for a caller with a configured
    `TallyConfig`. The default builds a `PeriodReader` whose timeout is five
    seconds and whose retry count is one, so a hung gateway becomes a caught
    error rather than a wedged request.
    """
    return check_period(company=company, on=on, reader=reader).open_for_posting
