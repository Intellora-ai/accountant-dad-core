"""EVERY route serves one company, or it refuses. Nothing of anybody else's.

WHAT THIS FILE IS FOR
---------------------
`tests/test_company_identity.py` proved that the ONE company a runtime is bound
to is the company its handlers use, and that a runtime whose own identities
disagree refuses. It proved it on the routes that existed when it was written,
mostly through `/entry`.

This file asks the next question, per route and with a SECOND company open in
the same Tally the whole time: can anything belonging to that second company be
read, drawn or written from a session bound to the first? Two failures are
named separately throughout, because they cost different things:

    a wrong-company DISPLAY   somebody reads another business's figures
    a wrong-company WRITE     somebody's books are changed by a session that
                              was never bound to them, and their own audit
                              trail does not record it

The second is the worst failure this project has. Every test that claims a
refusal therefore also counts the writes, in BOTH companies, and asserts zero.

THE OTHER COMPANY IS MADE IDENTIFIABLE ON PURPOSE
-------------------------------------------------
`THEIRS` has a party, an expense account and an operation id that appear
NOWHERE in `OURS`. A leak is then a substring match on a string that has only
one possible source, rather than an argument about whether a common word on the
page came from the stylesheet. Two tests written earlier in this project were
green and vacuous for exactly that reason.

WHY THE SERVER RUNS ON THE TEST THREAD
--------------------------------------
The other files put the server on its own thread, which is right for a runtime
that never changes. Some tests here change it: they reconfigure the app for the
second company between requests, and `MemoryStore` is a SQLite connection that
belongs to whichever thread opened it. So the SERVER stays on the test thread
and the CLIENT moves off it. See `OneAtATime`.

WHAT THIS FILE DOES NOT PROVE
-----------------------------
Anything about real TallyPrime. EVIDENCE CLASS: FAKETALLY over real HTTP. The
backend is a `FakeTally` injected through `configure()`, the requests are real
sockets, and nothing here says what a real Tally gateway would do.
"""

from __future__ import annotations

import datetime
import json
import threading
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Iterator
from dataclasses import dataclass
from http.server import HTTPServer

import pytest

from accountant.memory.identity import normalise_company
from accountant.memory.store import MemoryStore
from accountant.schema import ActionLog, Voucher
from accountant.tallyio.client import stamp
from accountant.tallyio.factory import BackendIdentity, new_run_id
from accountant.tallyio.fake import FakeTally
from accountant.web import app

#: The company this session is bound to. Not `app.COMPANY`, deliberately.
OURS = "Pathak Cement Works"

#: A second company open in the SAME Tally for the whole of every test. Nothing
#: belonging to it may ever be read, drawn or written from a session bound to
#: `OURS`.
THEIRS = "Sable Iron Traders"

#: A third, used only where a company has to appear that was never connected to.
ELSEWHERE = "Bhosale Timber"

OUR_PARTY = "Sharma Traders"
OUR_ACCOUNT = "Purchases"
OUR_ACCOUNTS = (OUR_ACCOUNT, "Repairs & Maintenance", "Cash")

#: Three strings with exactly one possible source. If any of them reaches a page
#: served for `OURS`, that page showed another business's books.
THEIR_PARTY = "Deshmukh Sanitary Mart"
THEIR_ACCOUNT = "Freight Inward"
THEIR_OPERATION = "op_sable_iron_0001"
THEIR_ACCOUNTS = (THEIR_ACCOUNT, "Cash")

#: A row seeded into the action log under THEIR key, so "the activity list shows
#: only our rows" is a claim about something that exists rather than about an
#: empty table.
THEIR_LOG_REASON = "a row that belongs to Sable Iron Traders and to nobody else"

ENTRY = "paid Sharma Traders 4200 for cement"
UNKNOWN_ENTRY = "paid Gupta Hardware 1500 for tools"


def our_history() -> tuple[Voucher, ...]:
    """Enough consistent history that `OUR_PARTY` posts straight through."""
    return tuple(
        Voucher(
            id=f"ours-{i}",
            date=datetime.date(2026, 1 + (i % 6), 1 + (i % 27)),
            party=OUR_PARTY,
            narration="cement supply",
            debit_account=OUR_ACCOUNT,
            credit_account="Cash",
            amount_paise=380000 + i * 1000,
        )
        for i in range(12)
    )


def their_history() -> tuple[Voucher, ...]:
    """The other company's books, including one voucher marked as ours.

    The marked one matters: `list_our_vouchers` and `reversal.preview` both
    select on the marker, so without it "the preview lists only our own
    vouchers" would be true of an empty list and prove nothing.
    """
    typed = tuple(
        Voucher(
            id=f"theirs-{i}",
            date=datetime.date(2026, 1 + (i % 6), 1 + (i % 27)),
            party=THEIR_PARTY,
            narration="iron sheets",
            debit_account=THEIR_ACCOUNT,
            credit_account="Cash",
            amount_paise=510000 + i * 1000,
        )
        for i in range(12)
    )
    ours_in_theirs = Voucher(
        id="theirs-marked",
        date=datetime.date(2026, 5, 2),
        party=THEIR_PARTY,
        narration=stamp("iron sheets", THEIR_OPERATION),
        debit_account=THEIR_ACCOUNT,
        credit_account="Cash",
        amount_paise=777700,
        tally_id="TALLY-THEIRS-1",
    )
    return (*typed, ours_in_theirs)


def identity_for(company: str, *, visible: int = 2) -> BackendIdentity:
    return BackendIdentity(
        backend="FakeTally",
        endpoint="memory://tests/test_company_routes.py",
        company=company,
        company_exists=True,
        companies_visible=visible,
        run_id=new_run_id(),
    )


class OneAtATime:
    """A real HTTP server that serves each request on the CALLING thread.

    `handle_request()` rather than `serve_forever()` in a thread, because these
    tests reconfigure the app between requests and the app's store is a SQLite
    connection tied to the thread that opened it. Putting the server here and
    the client on a worker keeps the app, its store and its handlers on one
    thread without weakening what is being measured: the socket, the HTTP
    parsing and the status code are all real.

    `timeout` is set on the server, so a client that never arrives fails the
    test in five seconds instead of hanging the run.
    """

    def __init__(self, httpd: HTTPServer) -> None:
        self._httpd = httpd
        self._base = f"http://127.0.0.1:{httpd.server_address[1]}"

    def get(self, path: str = "/") -> tuple[int, str]:
        return self._round_trip(path, None)

    def post(self, path: str, **fields: str) -> tuple[int, str]:
        return self._round_trip(path, urllib.parse.urlencode(fields).encode())

    def _round_trip(self, path: str, data: bytes | None) -> tuple[int, str]:
        answered: list[tuple[int, str]] = []

        def ask() -> None:
            request = urllib.request.Request(self._base + path, data=data)  # noqa: S310
            try:
                with urllib.request.urlopen(request, timeout=5) as reply:  # noqa: S310
                    answered.append((reply.status, reply.read().decode()))
            except urllib.error.HTTPError as refused:
                answered.append((refused.code, refused.read().decode()))

        caller = threading.Thread(target=ask, daemon=True)
        caller.start()
        self._httpd.handle_request()
        caller.join(timeout=5)
        assert answered, f"the server never answered {path!r}"
        return answered[0]


@dataclass(frozen=True)
class Bench:
    """One Tally with two companies open, one app bound to the first of them."""

    http: OneAtATime
    tally: FakeTally
    store: MemoryStore

    def their_books(self) -> tuple[tuple[Voucher, ...], dict[str, int]]:
        """Everything about the other company that a write would change."""
        return self.tally.list_our_vouchers(THEIRS), self.tally.trial_balance(THEIRS)

    def their_log(self) -> tuple[str, ...]:
        return tuple(r.action for r in self.store.actions(THEIRS))


@pytest.fixture(autouse=True)
def no_runtime_leaks() -> Iterator[None]:
    """No runtime, no drafts and no batches leak between tests in this file.

    `app.configure()` clears none of these. That is the subject of two tests
    near the end of this file; here it is only housekeeping.
    """
    app.disconnect()
    app.DRAFTS.clear()
    app.BATCHES.clear()
    yield
    app.disconnect()
    app.DRAFTS.clear()
    app.BATCHES.clear()


@pytest.fixture
def bench() -> Iterator[Bench]:
    tally = FakeTally()
    tally.add_company(
        OURS, accounts=OUR_ACCOUNTS, vouchers=our_history(), backed_up=True
    )
    tally.add_company(
        THEIRS, accounts=THEIR_ACCOUNTS, vouchers=their_history(), backed_up=True
    )
    store = MemoryStore(":memory:")
    store.record_action(
        ActionLog(
            ts=datetime.datetime(2026, 8, 1, 9, 0, tzinfo=datetime.UTC),
            action="posted",
            company_key=normalise_company(THEIRS),
            outcome="posted",
            reason=THEIR_LOG_REASON,
            run_id=new_run_id(),
            backend="FakeTally",
        )
    )
    app.configure(tally, identity_for(OURS), store=store)

    httpd = HTTPServer(("127.0.0.1", 0), app.Handler)
    httpd.timeout = 5
    try:
        yield Bench(OneAtATime(httpd), tally, store)
    finally:
        httpd.server_close()


# ---- the open company changes underneath us ---------------------------------
#
# The real-world shape: somebody closes the company in TallyPrime and opens
# another. `tests/test_company_identity.py` proves POST /entry refuses. Every
# other way in has to refuse too, and the reason is not symmetry - `/reverse`
# and `/reverse-all` DELETE, and a delete aimed at a company that is no longer
# the one we measured is the worst request this app can serve.

CLOSED_COMPANY_ROUTES: tuple[tuple[str, dict[str, str]], ...] = (
    ("/", {}),
    ("/entry", {"text": ENTRY}),
    ("/answer", {"draft": "draft-none", "problem": "which_account", "value": "Cash"}),
    ("/dismiss", {"draft": "draft-none", "detector": "vendor_switch"}),
    ("/reverse", {"op": "op-none"}),
    ("/reverse-all", {}),
    ("/reverse-all", {"batch": "bulk-none", "confirm": "yes"}),
)


def _drive(bench: Bench, path: str, fields: dict[str, str]) -> tuple[int, str]:
    return bench.http.get(path) if path == "/" else bench.http.post(path, **fields)


@pytest.mark.parametrize(("path", "fields"), CLOSED_COMPANY_ROUTES)
def test_every_route_refuses_once_our_company_is_no_longer_open_in_tally(
    bench: Bench, path: str, fields: dict[str, str]
) -> None:
    """One route, one refusal, and the other company's books never move.

    Closing a company in `FakeTally` DISCARDS its books, exactly as Tally
    serves nothing at all for a company that is not open. So the surviving
    measurement is the OTHER company, which is the one a misdirected request
    would land in.
    """
    before_vouchers, before_balance = bench.their_books()
    before_log = bench.their_log()

    bench.tally.close_company(OURS)
    code, body = _drive(bench, path, fields)

    assert code == 503, f"{path} answered {code} for a company that is not open"
    assert app.REFUSAL in body, f"{path} refused without saying so"
    assert bench.tally.list_our_vouchers(THEIRS) == before_vouchers
    assert bench.tally.trial_balance(THEIRS) == before_balance
    assert bench.their_log() == before_log, "and nothing was filed under them either"


def test_the_refusal_names_the_company_we_expected_and_the_one_that_is_open(
    bench: Bench,
) -> None:
    """A refusal that names neither company leaves the reader unable to act."""
    bench.tally.close_company(OURS)

    code, body = bench.http.get("/")

    assert code == 503
    assert OURS in body, "the 503 does not say which company we were working in"
    assert THEIRS in body, "nor which company Tally actually has open"


def test_no_route_writes_anything_at_all_while_our_company_is_closed(
    bench: Bench,
) -> None:
    """The whole surface at once, counted rather than reasoned about.

    Each route is refused above one at a time. This drives all of them against
    one Tally and asserts the write count is exactly zero afterwards, because
    "each refused" and "nothing was written" are two claims and only the second
    is the one that protects somebody's books.
    """
    before_vouchers, before_balance = bench.their_books()
    bench.tally.close_company(OURS)

    codes = [_drive(bench, path, fields)[0] for path, fields in CLOSED_COMPANY_ROUTES]

    assert codes == [503] * len(CLOSED_COMPANY_ROUTES)
    assert bench.tally.list_our_vouchers(THEIRS) == before_vouchers
    assert bench.tally.trial_balance(THEIRS) == before_balance
    assert bench.their_log() == ("posted",), "only the row the fixture seeded"


# ---- a request may not name a company of its own ----------------------------
#
# `tests/test_company_identity.py` proves this for POST /entry. There is no
# `company` field on any route today; these are here so that adding one to ANY
# of them is a test failure rather than a quiet privilege escalation. A person
# on the network could otherwise read and write every company open in that
# Tally.

NAMED_COMPANY_ROUTES: tuple[tuple[str, dict[str, str]], ...] = (
    ("/entry", {"text": ENTRY}),
    ("/reverse", {"op": THEIR_OPERATION}),
    ("/reverse-all", {}),
    ("/answer", {"draft": "draft-none", "problem": "which_account", "value": "Cash"}),
    ("/dismiss", {"draft": "draft-none", "detector": "vendor_switch"}),
)


@pytest.mark.parametrize(("path", "fields"), NAMED_COMPANY_ROUTES)
def test_no_route_lets_a_form_field_choose_a_different_company(
    bench: Bench, path: str, fields: dict[str, str]
) -> None:
    """`company=THEIRS` on the form must change nothing about where we work."""
    before_vouchers, before_balance = bench.their_books()

    bench.http.post(path, company=THEIRS, **fields)

    assert bench.tally.list_our_vouchers(THEIRS) == before_vouchers, (
        f"{path} let a form field aim a write at {THEIRS!r}"
    )
    assert bench.tally.trial_balance(THEIRS) == before_balance
    assert bench.their_log() == ("posted",), "and wrote nothing into their trail"


def test_a_query_string_cannot_choose_a_different_company(bench: Bench) -> None:
    """The GET half. `/?company=...` is the same escalation through a URL."""
    before_vouchers, before_balance = bench.their_books()

    code, body = bench.http.get(f"/?company={urllib.parse.quote(THEIRS)}")

    assert code == 200
    assert THEIR_ACCOUNT not in body, "the query string chose the other company"
    assert THEIR_PARTY not in body
    assert bench.tally.list_our_vouchers(THEIRS) == before_vouchers
    assert bench.tally.trial_balance(THEIRS) == before_balance


# ---- nothing belonging to the other company is ever drawn -------------------


def test_the_home_page_never_shows_the_other_open_companys_trial_balance(
    bench: Bench,
) -> None:
    """Criterion: the trial balance on the page is OUR trial balance.

    `THEIR_ACCOUNT` exists only in the other company's chart, so its presence
    in the table is unambiguous - it cannot have come from ours.
    """
    code, body = bench.http.get("/")

    assert code == 200
    assert OUR_ACCOUNT in body, "our own trial balance is not on the page at all"
    assert THEIR_ACCOUNT not in body, (
        f"the trial balance printed {THEIR_ACCOUNT!r}, which exists only in "
        f"{THEIRS!r}'s chart of accounts"
    )


def test_the_home_page_never_shows_the_other_open_companys_parties(
    bench: Bench,
) -> None:
    """ "What we posted" is scoped by company too, and it names people."""
    bench.http.post("/entry", text=ENTRY)

    code, body = bench.http.get("/")

    assert code == 200
    assert OUR_PARTY in body, "our own posting is missing from the page"
    assert THEIR_PARTY not in body, "a party from the other company's books"
    assert THEIR_OPERATION not in body, "and an operation id we never wrote here"


def test_the_activity_log_never_shows_the_other_open_companys_rows(
    bench: Bench,
) -> None:
    """The audit trail is read by company key, and the fixture seeded theirs."""
    bench.http.post("/entry", text=ENTRY)

    code, body = bench.http.get("/")

    assert code == 200
    assert THEIR_LOG_REASON not in body, "the activity list printed their row"
    assert [r.reason for r in bench.store.actions(THEIRS)] == [THEIR_LOG_REASON], (
        "and their trail still holds exactly the one row the fixture seeded"
    )


def test_health_names_our_company_and_never_the_other_open_one(bench: Bench) -> None:
    """/health is exempt from the per-request check, so it gets its own."""
    code, body = bench.http.get("/health")

    assert code == 200
    reported = json.loads(body)
    assert reported["company"] == OURS
    assert reported["company_identifier"] == OURS
    assert reported["company_key"] == normalise_company(OURS)
    assert THEIRS not in body, "the readiness endpoint named the other company"


def test_the_bulk_reversal_preview_lists_only_our_own_vouchers(bench: Bench) -> None:
    """Undo-everything is the one action whose blast radius must be shown.

    The other company has a voucher of ours in it, seeded by the fixture. If it
    appears in this preview then the confirmation that follows would delete it.
    """
    bench.http.post("/entry", text=ENTRY)

    code, body = bench.http.post("/reverse-all")

    assert code == 200
    assert "Undo 1 voucher(s)?" in body, f"unexpected preview:\n{body[:400]}"
    assert THEIR_OPERATION not in body, (
        "the preview listed an operation that lives in the other company's books"
    )


# ---- work cached between requests must not outlive its company --------------
#
# `DRAFTS` and `BATCHES` are module-level dictionaries. `configure()` clears
# `_recorded_mismatches` and neither of them, and `disconnect()` does the same,
# so a draft or a batch built for one company survives into a runtime bound to
# another. Nothing on the way back in compares the cached object's company to
# `runtime().company`.
#
# The autouse fixture in `tests/test_company_identity.py` clears both by hand.
# That is the tell: the test harness is doing the cleanup the module does not.


def _rebind(bench: Bench) -> None:
    """Point the live app at the other company, on the store's own thread."""
    app.configure(bench.tally, identity_for(THEIRS), store=bench.store)


def test_a_bulk_reversal_previewed_for_another_company_never_reverses_it(
    bench: Bench,
) -> None:
    """THE wrong-company WRITE. A batch is confirmed against `batch.company`.

    `reversal.execute` reverses in `batch.company` (`accountant/reversal.py`,
    `_drive` -> `_classify(client, batch.company, ...)`), while the handler
    passes `company_key=live.memory.identity.key` for the audit rows. Nothing
    compares the two. So a batch previewed while bound to one company, and
    confirmed after the app is bound to another, deletes vouchers out of the
    FIRST company's books and files the record under the SECOND company's key -
    which means the company that was actually changed has no record of it.

    Every voucher this could delete is a real posting in a real business's
    books, and a reversal is a delete.
    """
    bench.http.post("/entry", text=ENTRY)
    assert len(bench.tally.list_our_vouchers(OURS)) == 1, "nothing posted to undo"
    balance_of_ours = bench.tally.trial_balance(OURS)

    code, _preview = bench.http.post("/reverse-all")
    assert code == 200
    batch_id = next(iter(app.BATCHES))
    assert app.BATCHES[batch_id][0].company == OURS

    _rebind(bench)
    assert app.runtime().company == THEIRS, "the app is now bound to the other company"

    bench.http.post("/reverse-all", batch=batch_id, confirm="yes")

    assert len(bench.tally.list_our_vouchers(OURS)) == 1, (
        f"a batch previewed for {OURS!r} was confirmed by a session bound to "
        f"{THEIRS!r} and it deleted {OURS!r}'s voucher. That is a wrong-company "
        f"WRITE into a real business's books"
    )
    assert bench.tally.trial_balance(OURS) == balance_of_ours
    assert [r.action for r in bench.store.actions(OURS)] != [], (
        "and whatever happened has to be recorded against the company it "
        "happened to, not against the one that happened to be connected"
    )


def test_a_draft_built_for_another_company_is_never_drawn_under_ours(
    bench: Bench,
) -> None:
    """A wrong-company DISPLAY. `/dismiss` renders a draft it never scoped.

    `DRAFTS` is keyed by draft id alone. The handler looks one up, renders it,
    and `page()` wraps it in a header naming `runtime().company`. So a draft
    built for one company is drawn - party, ledgers, amount, Tally id, and an
    "Undo this entry" button carrying its operation id - inside a page that
    says the reader is working in a different one.
    """
    bench.http.post("/entry", text=ENTRY)
    draft_id = next(iter(app.DRAFTS))
    assert app.DRAFTS[draft_id].company == OURS

    _rebind(bench)

    code, body = bench.http.post("/dismiss", draft=draft_id, detector="vendor_switch")

    assert OUR_PARTY not in body, (
        f"a page served for {THEIRS!r} (HTTP {code}) drew a draft belonging to "
        f"{OURS!r}, naming its party. Nothing scopes app.DRAFTS by company"
    )
    assert bench.tally.list_our_vouchers(THEIRS) == their_history()[-1:], (
        "and nothing was written into the company we are now bound to"
    )


def test_a_draft_built_for_another_company_cannot_be_answered_into_ours(
    bench: Bench,
) -> None:
    """The write half of the same hole, and this one holds.

    `pipeline.evaluate` refuses memory whose key is not the draft's company, so
    answering a foreign draft raises before anything is posted. It is the LAST
    line of defence doing the first one's job - the answer arrives as the
    generic "something broke" 503 rather than as a company refusal - but no
    voucher reaches either set of books, which is what matters here.
    """
    bench.http.post("/entry", text=UNKNOWN_ENTRY)
    draft_id = next(iter(app.DRAFTS))
    assert app.DRAFTS[draft_id].company == OURS
    ours_before = bench.tally.list_our_vouchers(OURS)
    theirs_before, their_balance = bench.their_books()

    _rebind(bench)
    code, _ = bench.http.post(
        "/answer", draft=draft_id, problem="which_account", value=OUR_ACCOUNT
    )

    assert code == 503, "a draft from another company was answered, not refused"
    assert bench.tally.list_our_vouchers(OURS) == ours_before
    assert bench.tally.list_our_vouchers(THEIRS) == theirs_before
    assert bench.tally.trial_balance(THEIRS) == their_balance


def test_reversing_an_operation_id_from_another_company_changes_nothing(
    bench: Bench,
) -> None:
    """`/reverse` takes an operation id straight off the form.

    The id used here is real and findable - it is stamped on a voucher in the
    other company's books - so this is not a test about a made-up string. It is
    a test that the id is looked for in OUR company and nowhere else.
    """
    theirs_before, their_balance = bench.their_books()
    assert len(theirs_before) == 1, "the fixture's marked voucher is there to delete"

    code, _ = bench.http.post("/reverse", op=THEIR_OPERATION)

    assert code == 200
    assert bench.tally.list_our_vouchers(THEIRS) == theirs_before, (
        "an operation id from another company's books was reversed"
    )
    assert bench.tally.trial_balance(THEIRS) == their_balance
    recorded = [r for r in bench.store.actions(OURS) if r.action == "reversed"]
    assert [r.outcome for r in recorded] == ["not_found"], (
        "and it is recorded as not found in OUR company, under OUR key"
    )


def test_a_company_that_was_never_connected_to_is_named_by_nothing(
    bench: Bench,
) -> None:
    """The control. Without it, a page that names no company would pass above.

    `ELSEWHERE` is not open in this Tally and was never configured, so it must
    appear nowhere - while `OURS` must appear on the page, which is what stops
    these assertions being satisfied by an empty response.
    """
    code, body = bench.http.get("/")

    assert code == 200
    assert OURS in body
    assert ELSEWHERE not in body
    assert THEIRS not in body
