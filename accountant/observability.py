"""Is it working right now, and which lines belong to the same entry.

WHAT THIS EXISTS TO ANSWER, AND WHY `/health` COULD NOT
-------------------------------------------------------
`accountant/web/app.py::health` is a MEASURED readiness check and it is the
shape everything here follows: every value read from the live runtime, and an
honest refusal where there is nothing to read. What it cannot answer is the
other two questions somebody has at three in the morning:

    how much has this thing done, and how did it go
    which of these log lines belong to the entry the customer is complaining
    about

One entry is SEVERAL HTTP requests — `/entry`, then `/answer` once or twice —
and until 2026-08-11 nothing tied them together, because nothing was logged at
all. `BaseHTTPRequestHandler.log_message` is overridden to `pass` in the web
app, so the product's entire observable output was whatever `serve()` printed
at startup.

THE TWO RULES THIS MODULE IS BUILT AROUND
-----------------------------------------
1. A COUNT COMES FROM THE DURABLE STORE, OR IT IS NOT A COUNT.
   `MemoryStore.actions()` is append-only, has no update path and survives the
   process. A process-local counter resets on restart and then reports a
   smaller number than the truth, which is worse than no number: it is a
   number a person will act on.

2. AN UNMEASURED VALUE IS `NOT_MEASURED`, NEVER `0`.
   Standing owner rule. Zero and unknown look identical in every dashboard
   ever built, and only one of them is a fact. `question_rate` over zero
   entries is the case that matters: the share of entries we had to ask about
   is UNDEFINED when there are no entries, and writing 0 there says "we never
   have to ask", which is the single most flattering lie this system could
   tell about itself.

   A MEASURED zero is different and is written as `0`: we counted the rows,
   and there were none. `docs/OBSERVABILITY.md` states the difference in the
   same words.

STDLIB ONLY. No `prometheus_client`, and the exposition format is deliberately
not Prometheus's — see `render_metrics`.
"""

from __future__ import annotations

import contextlib
import contextvars
import logging
import time
import uuid
from collections.abc import Generator, Sequence
from typing import TextIO

from accountant.pipeline import WRITE_ATTEMPTED, WRITE_OUTCOME_UNKNOWN
from accountant.reversal import BATCH_ACTION, VoucherState
from accountant.schema import NOT_RECORDED, ActionLog, Outcome

#: The only honest answer for a value this system has not measured.
#:
#: NEVER `0`, NEVER `""`, NEVER an omitted line. An omitted line reads as a
#: broken scrape and a zero reads as a fact; this reads as what it is.
NOT_MEASURED = "NOT_MEASURED"

# ---- what "slow" means, as a number a reader can find -----------------------
#
# THESE ARE NOT MEASURED BASELINES AND THIS FILE WILL NOT PRETEND THEY ARE.
#
# There is no recorded round-trip time for a licensed TallyPrime anywhere in
# this repository — `docs/PROJECT_STATE.md` records the connector as
# licence-blocked, so nobody here has ever timed one. What follows is a
# THRESHOLD, chosen so the log says something the first time a real Tally is
# slow, and it is written down here rather than left implicit precisely so the
# first real measurement can replace it in one place.
#
# Chosen from what the two numbers have to separate:
#
#   SLOW_TALLY_MS    one Tally round trip. `Runtime.confirm_company` makes one
#                    on EVERY request by design, and a person typing an entry
#                    tolerates roughly a second in total, so a single round
#                    trip over 500ms is already eating the whole budget.
#   SLOW_REQUEST_MS  one HTTP request end to end. `POST /entry` makes at least
#                    three Tally calls (list_companies, read_accounts,
#                    read_vouchers) before it decides anything, so the request
#                    threshold is deliberately larger than three times the
#                    call threshold rather than equal to it.
#
# What to do with them: `slow=yes` on a `tally_call` line and `slow=no` on the
# `request` line that contains it means Tally is slow. The reverse means we
# are. That is the whole point of measuring both.
SLOW_TALLY_MS = 500.0
SLOW_REQUEST_MS = 2000.0

# ---- correlation ------------------------------------------------------------

#: This request's id, for the duration of ONE request.
#:
#: A ContextVar rather than a module global, and that is not a style choice.
#: Task 11 replaces `HTTPServer` with a threading one, and a plain global would
#: then be one customer's request id stamped on another customer's log lines —
#: which makes the correlation id worse than useless, because it would tie
#: together lines that have nothing to do with each other. Every thread gets its
#: own context, so a `set` here is invisible to every other request.
#: `accountant/web/app.py::_principal` is the same pattern for the same reason.
_request_id: contextvars.ContextVar[str] = contextvars.ContextVar(
    "accountant_request_id", default=""
)

#: Which ENTRY this request belongs to — the draft id the form carries.
#:
#: The request id ties the lines of one request together. This ties the two or
#: three REQUESTS of one entry together, which is the question actually asked:
#: "show me everything about the bill they typed at 14:32". `/entry` sets it
#: once the draft exists; `/answer` and `/dismiss` set it from the form before
#: they touch anything.
_entry_id: contextvars.ContextVar[str] = contextvars.ContextVar(
    "accountant_entry_id", default=""
)

#: Milliseconds spent inside Tally during THIS request, and how many calls.
#:
#: Reset at the start of every request rather than merely defaulted. The
#: shipped server is a single-threaded `HTTPServer`, so one context serves
#: every request in turn and a default that is only consulted once would make
#: request two report request one's Tally time as well as its own.
_tally_ms: contextvars.ContextVar[float] = contextvars.ContextVar(
    "accountant_tally_ms", default=0.0
)
_tally_calls: contextvars.ContextVar[int] = contextvars.ContextVar(
    "accountant_tally_calls", default=0
)

#: When this process started, on the monotonic clock.
#:
#: `time.monotonic` rather than the wall clock: uptime must not go backwards
#: because somebody corrected the machine's time, and a negative uptime is the
#: kind of number that makes a reader distrust everything beside it.
_STARTED = time.monotonic()


def new_request_id() -> str:
    """A fresh id for one request. Never reused, never derived from the caller.

    Not the client's `X-Request-Id` if it sends one. An id chosen by whoever is
    calling can be repeated on purpose, which lets one caller's lines be filed
    under another caller's investigation.
    """
    return f"req_{uuid.uuid4().hex[:12]}"


def current_request_id() -> str:
    """This request's id, or "" outside a request."""
    return _request_id.get()


def current_entry_id() -> str:
    """The entry this request belongs to, or "" when it belongs to none."""
    return _entry_id.get()


def set_entry_id(entry_id: str) -> None:
    """Say which entry this request is about, so its lines can be joined."""
    _entry_id.set(entry_id)


def uptime_seconds() -> float:
    """How long this process has been up.

    PROCESS-LOCAL BY NATURE, and the one value on `/metrics` that is allowed to
    be: uptime is a fact about this process and there is nothing durable it
    could be read from. Every count beside it comes from the store.
    """
    return time.monotonic() - _STARTED


# ---- the log ----------------------------------------------------------------

LOGGER_NAME = "accountant"

#: Every line carries both correlation ids, in fixed positions, before the
#: message. `%(request_id)s` and `%(entry_id)s` are filled by `_Correlation`
#: below rather than by each call site, because a field every caller must
#: remember is a field some caller will forget — the same argument
#: `Handler._confirm_company` is sited where it is.
LOG_FORMAT = (
    "%(asctime)s %(levelname)s request=%(request_id)s entry=%(entry_id)s %(message)s"
)


class _Correlation(logging.Filter):
    """Stamp the two correlation ids onto every record that reaches a handler.

    Attached to the HANDLER, not to the logger. A filter on a logger runs only
    for records logged directly to it and NOT for records propagated up from a
    child logger — so `logging.getLogger("accountant.web")` would produce lines
    with no request id and a formatter that raised on the missing field. On the
    handler it runs for everything that gets as far as being written.

    Absent ids read `NOT_RECORDED`, which is this codebase's existing word for
    "we did not record this" (`ActionLog.actor`, `Principal`-less audit rows).
    A blank there would render `request= entry=` and look like a bug in the
    formatter rather than a fact about the line.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = _request_id.get() or NOT_RECORDED
        record.entry_id = _entry_id.get() or NOT_RECORDED
        return True


def install_logging(
    stream: TextIO | None = None, *, level: int = logging.INFO
) -> logging.Handler:
    """Give the `accountant` logger exactly one handler, carrying correlation.

    IDEMPOTENT, and it removes what it installed before rather than adding a
    second handler. `serve()` calls it once, but a test calls it per test, and
    two handlers means every line twice — which quietly doubles every count a
    person makes by grepping the log.

    `propagate = False` so lines do not also reach the root handler that
    `logging.basicConfig` installs for anybody else in the process. Two copies
    of one line, one of them without the request id, is the failure this whole
    module exists to avoid.
    """
    logger = logging.getLogger(LOGGER_NAME)
    for existing in list(logger.handlers):
        logger.removeHandler(existing)
        existing.close()

    handler: logging.Handler = logging.StreamHandler(stream)
    handler.setFormatter(logging.Formatter(LOG_FORMAT))
    handler.addFilter(_Correlation())
    logger.addHandler(handler)
    logger.setLevel(level)
    logger.propagate = False
    return handler


def _field(value: object) -> str:
    """One `key=value` value, with the line breaks taken out of it.

    A path, a company name and an exception message all reach the log and all
    are influenced by somebody outside this system. A newline in any of them
    would end the line early and let the rest be read as a SECOND log line —
    forged, with whatever request id the forger chose. Tabs and carriage
    returns go the same way for the same reason.

    Quoted when it contains a space, so `reason="two words"` stays one field.
    """
    text = str(value)
    for bad in ("\n", "\r", "\t"):
        text = text.replace(bad, " ")
    return f'"{text}"' if " " in text else text


def log(event: str, **fields: object) -> None:
    """One structured line: `event=<event>` then `key=value` pairs.

    NEVER GIVEN A SECRET. Session tokens, passwords, cookies and uploaded
    document bytes do not reach this function and there is no field for them:
    a log is the copy of your data that ends up in the widest number of places,
    so it gets identifiers and durations and nothing a thief could use.
    """
    pairs = " ".join(f"{name}={_field(value)}" for name, value in fields.items())
    logging.getLogger(LOGGER_NAME).info(f"event={event} {pairs}".rstrip())


# ---- timing -----------------------------------------------------------------


def begin_request(request_id: str) -> None:
    """Start a request: new id, no entry yet, Tally clocks back to zero."""
    _request_id.set(request_id)
    _entry_id.set("")
    _tally_ms.set(0.0)
    _tally_calls.set(0)


def finish_request(method: str, path: str, status: int, elapsed_ms: float) -> None:
    """The one line per request, with the split that answers "who is slow".

    `tally_ms` is time this request spent waiting for Tally; `app_ms` is the
    rest, which is us. Reporting only a total makes a slow Tally and a slow app
    indistinguishable, and they need completely different people to fix them.
    """
    tally = _tally_ms.get()
    log(
        "request",
        method=method,
        path=path,
        status=status,
        ms=round(elapsed_ms, 1),
        tally_ms=round(tally, 1),
        # Clamped at zero. Floating-point subtraction of two independently
        # rounded numbers can go a hair negative, and a negative duration on a
        # dashboard costs more attention than it is worth.
        app_ms=round(max(elapsed_ms - tally, 0.0), 1),
        tally_calls=_tally_calls.get(),
        slow="yes" if elapsed_ms >= SLOW_REQUEST_MS else "no",
    )


@contextlib.contextmanager
def tally_call(name: str) -> Generator[None]:
    """Time one call out to Tally, and count it against this request.

    Measured in a `finally`, so a call that RAISES is still timed. That is the
    case worth having: a Tally that has stopped answering is slow first and
    absent second, and the timing of the failure is the evidence for which of
    the two happened.
    """
    started = time.perf_counter()
    try:
        yield
    finally:
        elapsed = (time.perf_counter() - started) * 1000.0
        _tally_ms.set(_tally_ms.get() + elapsed)
        _tally_calls.set(_tally_calls.get() + 1)
        log(
            "tally_call",
            name=name,
            ms=round(elapsed, 1),
            slow="yes" if elapsed >= SLOW_TALLY_MS else "no",
        )


def tally_ms_this_request() -> float:
    """Milliseconds spent in Tally so far in this request. For tests and /metrics."""
    return _tally_ms.get()


def tally_calls_this_request() -> int:
    """How many Tally calls this request has made so far."""
    return _tally_calls.get()


# ---- metrics ----------------------------------------------------------------

#: The action name a single-voucher undo writes into the log.
#:
#: Spelled here and imported by `accountant/web/app.py::do_POST`, rather than
#: written as a literal at each end. A metric that reads a different word than
#: the writer writes counts zero forever and nothing ever complains.
SINGLE_REVERSAL_ACTION = "reversed"

#: The outcome that action carries when the undo actually happened, as opposed
#: to `not_found`, which is a request for an operation we never posted.
SINGLE_REVERSAL_DONE = "reversed"

#: The three words a DECISION row writes into `outcome`.
#:
#: Derived from the enum rather than copied, so a fourth outcome cannot appear
#: in the product and be silently missing from the metrics. No other writer
#: uses these strings: `note()` writes saved/abandoned/FAILED/DISMISSED/
#: reversed/not_found/refused, `_record_write` writes its own action name, and
#: the reversal rows write `VoucherState`/`BatchState` values.
_DECISION_OUTCOMES: tuple[str, ...] = tuple(outcome.value for outcome in Outcome)


def _decision_rows(rows: Sequence[ActionLog]) -> tuple[ActionLog, ...]:
    return tuple(row for row in rows if row.outcome in _DECISION_OUTCOMES)


def _entries(rows: Sequence[ActionLog]) -> set[str]:
    """The distinct operation ids that reached a decision.

    ENTRIES, NOT ROWS. One entry that was asked about and then posted writes
    two decision rows and is one entry; counting rows would report the app
    doing twice the work whenever it had to ask, which is exactly backwards.
    Rows carrying no operation id are excluded rather than counted as one
    nameless entry.
    """
    return {row.operation_id for row in _decision_rows(rows) if row.operation_id}


def _entries_with(rows: Sequence[ActionLog], outcome: Outcome) -> set[str]:
    return {
        row.operation_id
        for row in rows
        if row.operation_id and row.outcome == outcome.value
    }


def _line(name: str, value: object, why: str = "") -> str:
    """One metric. `name: value`, and a reason when the value is NOT_MEASURED."""
    return f"{name}: {value}" + (f"  # {why}" if why else "")


#: Why the replay count cannot be read out of the durable log TODAY.
#:
#: Both halves are measured, and both are pinned by existing tests:
#:
#:   the write replay   `pipeline.post` catches `DuplicateOperation` and writes
#:                      `write_outcome_unknown` — DEFECT I2, held by the strict
#:                      xfail in `tests/test_idempotency.py`. Counting those
#:                      rows as refused replays would quietly report a defect
#:                      as fixed while the customer's audit trail still says a
#:                      voucher may exist and must be checked by hand.
#:   the answer replay  `answer_refusal` returns 400 and writes NO ROW AT ALL,
#:                      by design: nothing was touched. So there is nothing in
#:                      the store to count.
_WHY_NO_REPLAYS = (
    "a refused write replay is recorded as write_outcome_unknown (defect I2, "
    "tests/test_idempotency.py) and a refused answer replay writes no row at "
    "all, so the durable log cannot tell a replay from an unknown outcome"
)

#: Why auth refusals are not in the durable log, and why that is deliberate.
#:
#: An unauthenticated caller could otherwise append an unbounded number of rows
#: to whichever company this server is bound to, from outside the credential —
#: a log-flooding hole opened in order to produce a metric. The refusals ARE on
#: the log lines (`status=401`), which is the place that costs nothing.
_WHY_NO_AUTH_COUNTS = (
    "an auth refusal writes no durable row on purpose - a caller with no "
    "credential must not be able to append to a customer's audit trail - so "
    "these are countable only in the request log lines (status=401 / 403)"
)


def render_metrics(rows: Sequence[ActionLog], *, company: str, uptime: float) -> str:
    """The `/metrics` body. Plain text, one metric per line, no dependency.

    DELIBERATELY NOT THE PROMETHEUS EXPOSITION FORMAT, and the reason is rule 2
    at the top of this file. That format has exactly one type of value, a
    float, so there is no way to write `NOT_MEASURED` in it. The two ways out
    are to emit `0`, which the owner rule forbids and which is a lie, or to
    omit the series, which reads to a reader as a broken scrape rather than as
    an honest gap. So the format says what it means instead, and a scraper for
    it is nine lines of `split(": ")`.

    Every count comes from `rows` — the durable, append-only action log for
    this company — and nothing here holds a counter of its own. `uptime` is the
    single exception and it is passed in rather than read here so that this
    function stays pure and can be tested against a known set of rows.
    """
    decisions = _decision_rows(rows)
    entries = _entries(rows)
    asked = _entries_with(rows, Outcome.UNCLEAR)

    lines = [
        "# Accountant Dad metrics. Plain text; one metric per line.",
        "# Counts are read from the durable action log for this company, so",
        "# they survive a restart. A 0 below means we counted and found none.",
        f"# {NOT_MEASURED} means this system has not measured it. It is never 0.",
        "",
        _line("company", company),
        _line("uptime_seconds", round(uptime, 1)),
        _line("action_log_rows", len(rows)),
        _line("entries_seen", len(entries)),
    ]

    # Every outcome, always, including the ones with no rows. A kind that
    # simply vanishes from the output when it has not happened is a kind
    # nobody can write an alert against. NOT_VALID is the live example: it is
    # currently unreachable from a typed entry, so it is a measured 0 here and
    # saying so is the useful thing.
    for outcome in Outcome:
        lines.append(
            _line(
                f"outcome_{outcome.value}",
                sum(1 for row in decisions if row.outcome == outcome.value),
            )
        )

    lines += [
        _line(
            "writes_attempted",
            sum(1 for row in rows if row.action == WRITE_ATTEMPTED),
        ),
        _line(
            "writes_outcome_unknown",
            sum(1 for row in rows if row.action == WRITE_OUTCOME_UNKNOWN),
        ),
        _line(
            "reversals_single",
            sum(
                1
                for row in rows
                if row.action == SINGLE_REVERSAL_ACTION
                and row.outcome == SINGLE_REVERSAL_DONE
            ),
        ),
        _line(
            "reversals_bulk_vouchers",
            sum(
                1
                for row in rows
                if row.action == BATCH_ACTION
                and row.outcome == VoucherState.REVERSED_VERIFIED.value
            ),
        ),
        _line("refused_replays", NOT_MEASURED, _WHY_NO_REPLAYS),
        _line("auth_refusals_401", NOT_MEASURED, _WHY_NO_AUTH_COUNTS),
        _line("auth_refusals_403", NOT_MEASURED, _WHY_NO_AUTH_COUNTS),
    ]

    # THE RULE, IN THE ONE PLACE IT IS EASIEST TO BREAK.
    #
    # A rate over zero entries is UNDEFINED, not zero. `0.0` here would read as
    # "this system never has to ask a question", which is the most flattering
    # thing it could possibly say about itself and would be said on the day it
    # had done nothing at all.
    if entries:
        lines.append(_line("question_rate", round(len(asked) / len(entries), 3)))
    else:
        lines.append(
            _line(
                "question_rate",
                NOT_MEASURED,
                "no entries yet, so the share of entries we had to ask about "
                "is undefined rather than zero",
            )
        )
    return "\n".join(lines) + "\n"
