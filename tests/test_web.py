"""The web app — #14. Driven over real HTTP, not by calling handlers directly.

This is the whole user-facing surface and it had no tests at all. Everything the
person can actually do is exercised here: type an entry, get it posted, get asked
a question, answer it, and undo.

#14.1 accepts typed text
#14.2 shows the proposed voucher with provenance
#14.4 questions are plain English, one at a time, closed answers
#14.5 writes to Tally only when the outcome is Valid
#14.6 records the outcome and its reason for every entry
#14.7 lists what we wrote and offers reverse
S7    no ledger account name reaches the person inside a question

WHAT THIS FILE DOES NOT PROVE
-----------------------------
That any of this survives contact with real Tally. The backend here is a
`FakeTally` handed to `app.configure()`, and a fake is honest about the shape of
the surface, not about the integration. The connector is `tests/test_real_tally.py`
and the live evidence in `docs/PROJECT_STATE.md`.

That the runtime cannot reach the fake. That is a claim about the import graph,
not about behaviour — a behavioural test passes just as happily against a fake,
which is exactly the danger — and it is settled by `tests/test_runtime_backend.py`.
The double built here honestly reports `backend="FakeTally"`, which is the design:
the point is that the SHIPPED module cannot NAME the fake, not that a test may not
use one.

WHERE THE DEMO HISTORY CAME FROM
--------------------------------
`demo_company()` below was `accountant/web/app.py::seed()` until 2026-08-09, when
the app stopped carrying a backend of its own. The vendor behaviours it encodes
are load-bearing for most of the tests in this file, so the counts moved across
unchanged.
"""

from __future__ import annotations

import contextlib
import datetime
import html
import json
import re
import ssl
import threading
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Generator, Iterator
from pathlib import Path

import pytest

from accountant import questions as Q
from accountant.extract.adapter import Extractor
from accountant.memory.identity import normalise_company
from accountant.memory.store import IN_MEMORY, MemoryStore
from accountant.schema import Outcome, Voucher
from accountant.tallyio.factory import BackendIdentity, new_run_id
from accountant.tallyio.fake import FakeTally
from accountant.tallyio.period import PeriodReader
from accountant.web import app
from tests.test_period_handoff import open_books_for

# The demo company's chart of accounts. S7 reads this list back: no name in it
# may appear inside a question, so it has to be the same list the app was given.
ACCOUNTS = (
    "Purchases",
    "Repairs & Maintenance",
    "Sundry Expenses",
    "Printing & Stationery",
    "Rent",
    "Electricity Charges",
    "Cash",
    "Bank",
)


def demo_company() -> FakeTally:
    """A demo company with real-shaped history.

    The vendor behaviours are the fixtures the tests below depend on, so the
    counts are not decoration:

        Sharma Traders   -> Purchases 40                consistent, posts through
        Verma Cement     -> Purchases 6, Repairs 4      conflicted, asks
        Kumar Stationers -> Printing & Stationery 12    consistent
        City Power Board -> Electricity Charges 12      consistent
        Landlord         -> Rent 12                     consistent
        Gupta Hardware   -> absent                      unseen, asks
    """
    hist: list[Voucher] = []

    def add(party: str, account: str, amount: int, n: int, note: str) -> None:
        for i in range(n):
            hist.append(
                Voucher(
                    id=f"h{len(hist)}",
                    date=datetime.date(2026, 1 + (i % 6), 1 + (i % 27)),
                    party=party,
                    narration=note,
                    debit_account=account,
                    credit_account="Cash",
                    amount_paise=amount + i * 1000,
                )
            )

    add("Sharma Traders", "Purchases", 380000, 40, "cement supply")
    add("Verma Cement", "Purchases", 250000, 6, "cement")
    add("Verma Cement", "Repairs & Maintenance", 90000, 4, "site repair")
    add("Kumar Stationers", "Printing & Stationery", 45000, 12, "office supplies")
    add("City Power Board", "Electricity Charges", 720000, 12, "monthly power")
    add("Landlord", "Rent", 2000000, 12, "monthly rent")

    t = FakeTally()
    t.add_company(app.COMPANY, accounts=ACCOUNTS, vouchers=tuple(hist), backed_up=True)
    return t


def fake_backend() -> BackendIdentity:
    """An honest identity for the double.

    `backend` is a plain string, not a computed class name, so a test double can
    say what it actually is. `backend="FakeTally"` is the correct value here and
    it is the safe one: a report carrying it cannot be mistaken for evidence
    about real Tally. `companies_visible=1` because `demo_company()` opens one.
    """
    return BackendIdentity(
        backend="FakeTally",
        endpoint="memory://tests/test_web.py",
        company=app.COMPANY,
        company_exists=True,
        companies_visible=1,
        run_id=new_run_id(),
    )


def open_books_reader() -> PeriodReader:
    """A TEST DOUBLE that answers OPEN for this company. In memory, no socket.

    WHY IT HAD TO EXIST. `Runtime.period_reader` defaults to `None`, and `None`
    BLOCKS: `app.Runtime.period_open` reads it as "nobody looked", which is not
    the same fact as "the books are open" and is deliberately not treated as
    one. Every test through `serving` posts a voucher dated today, so every one
    of them needs somebody to have looked. Without this they all stopped on
    `decision._PERIOD_UNKNOWN` - measured 2026-08-16, 9 of them in this file.

    THE TRANSPORT IS CANNED AND THE READER IS REAL, which is the choice
    `tests/test_period_handoff.py` already made and it is reused rather than
    re-made: a doubled `PeriodReader` would prove only that `check_period`
    returns what a double told it to, where this way `build_period_request`,
    `parse_company_periods`, `period_for` and `open_on` all really run. No
    socket is opened, no Tally is asked and nothing here is reachable from
    shipped code.

    THE WINDOW IS DERIVED FROM TODAY, not pinned to a literal financial year.
    A pinned `20260401` passes now and starts blocking every test in this file
    on 1 April 2027 - a test that expires is a test that fails for a reason
    nobody changed.
    """
    return open_books_for(app.COMPANY)


@contextlib.contextmanager
def serving(
    tally: FakeTally,
    identity: BackendIdentity,
    *,
    extractor: Extractor | None = None,
    seed: Callable[[MemoryStore], None] | None = None,
    store_path: str | Path = IN_MEMORY,
    tls: ssl.SSLContext | None = None,
    period_reader: PeriodReader | None = None,
) -> Generator[str]:
    """A real server on a real ephemeral port, torn down on the way out.

    Port 0 lets the OS choose, so tests never collide with a dev instance or
    with each other.

    The backend is INJECTED through `app.configure()`. That is the seam the
    module exposes precisely so it never has to import an implementation
    itself — the shipped app can no longer name a fake, and this is where the
    fake enters instead.

    `extractor` goes through the same seam, and defaults to None so the app
    resolves its own through `registry.default_extractor()`. THIS FUNCTION
    NAMES NO BACKEND: a default of `TypedTextExtractor()` here would be a
    fixture quietly deciding what the shipped path uses.

    THREADING, since 2026-08-11, because `app.serve()` is. `HTTPServer` stood
    here and served one request at a time, which meant this fixture — the one
    spin-up path in the suite — could not reproduce the shape of the shipped
    server at all: every cross-request hazard threads create was invisible to
    every test that runs through here. It builds the same class production does.

    The memory store is still opened INSIDE the serving thread, and it no longer
    has to be. `MemoryStore` opens its connection with `check_same_thread=False`
    behind one lock (Task 11), so any thread may use it; what has NOT changed is
    that `:memory:` is private to its connection, so a store built out here
    would be a second, empty database. `configure()` still bootstraps memory
    exactly once, from this company's own Tally, before a single request is
    served.

    A CONTEXT MANAGER RATHER THAN A SECOND FIXTURE, 2026-08-10. The HTTP reader
    outage in `tests/test_extract_outage.py` needs a server carrying a failing
    backend, and copying a threaded spin-up is how two spin-up paths drift
    apart — the same argument `tests/conftest.py` makes for re-exporting the
    fixture instead of duplicating it. There is one spin-up path and this is it.

    `seed` JOINED 2026-08-10 with tenancy, and it takes a CALLBACK rather than a
    ready-made store for the reason two paragraphs up: a `:memory:` database is
    private to the connection that opened it, so a store built in the test
    thread is not the store the server reads. The callback runs where it lives.
    `tests/test_auth.py` uses it to write tenants and sessions; anything a test
    needs to carry back out — a token, say — is generated in the test and passed
    IN, not read out.

    `store_path` is the other half of that. A test that has to READ what the
    server wrote — an audit row, say — cannot reach the in-memory database at
    all: `:memory:` is private to its connection, so there is no second way in.
    Pointing the store at a file lets the test open its own connection AFTER the
    server has shut down, which is sequential access rather than shared access
    and needs no locking argument. Default unchanged, so every existing caller
    still gets the in-memory store it had.

    `tls` JOINED 2026-08-11 with Task 7, and it is EXTENDED here rather than
    copied into `tests/test_tls.py` for the reason two paragraphs up: a second
    threaded spin-up is a second implementation of the thing under test, and
    the TLS claim is precisely a claim about how the socket was bound. Passing
    an `ssl.SSLContext` makes this the HTTPS path; passing nothing leaves every
    existing caller on plain HTTP, byte for byte as before. The wrapping itself
    is NOT done here — `app.start_server` does it, so the shipped `serve()` and
    this fixture bind through one function. The yielded base URL carries the
    scheme that was actually served, so a caller cannot address an HTTPS server
    as `http://` by accident.
    """
    app.DRAFTS.clear()
    # And `BATCHES`, for the same reason. It was not cleared here, so a pending
    # bulk-reversal preview survived into the next test and any assertion that
    # said "there is exactly one batch" was measuring the leftovers of whatever
    # ran before it.
    app.BATCHES.clear()
    # And `DELETIONS`, added with data deletion 2026-08-11, for the identical
    # reason: a pending delete-my-data plan surviving into the next test would
    # make any "there is exactly one plan" assertion a measurement of leftovers.
    app.DELETIONS.clear()
    # And the owner map beside `DRAFTS`, so a draft id reused by a later test
    # cannot inherit the previous test's tenant.
    app.DRAFT_TENANT.clear()
    # `EVENTS` used to be cleared here: a module-level list that leaked rows
    # from one test into the next. The log now lives in this test's own
    # MemoryStore, so there is nothing global left to reset.

    # Through `app.start_server`, which is the ONE binding site: it chooses the
    # server class and wraps the socket, so this fixture cannot drift from what
    # `serve()` actually runs. It built its own ThreadingHTTPServer for a few
    # hours between the two tasks landing; a fixture that constructs its own
    # server is a second definition of what a running app is.
    httpd = app.start_server("127.0.0.1", 0, tls)
    ready = threading.Event()

    def serve() -> None:
        store = MemoryStore(store_path)
        # `period_reader` goes in through the SAME `configure` seam the backend
        # and the extractor use. Left unset it is `open_books_reader()` and not
        # `None`: `None` is what the shipped app gets when nobody built a reader
        # and it must go on blocking, so a test that wants the blocking path
        # asks for it by name - `unreachable_reader()` from
        # `tests/test_period_handoff.py` reaches it through the real code rather
        # than by removing the reader.
        app.configure(
            tally,
            identity,
            store=store,
            extractor=extractor,
            period_reader=period_reader or open_books_reader(),
        )
        if seed is not None:
            seed(store)
        ready.set()
        httpd.serve_forever()

    thread = threading.Thread(target=serve, daemon=True)
    thread.start()
    assert ready.wait(timeout=5), "the server thread never bootstrapped memory"
    scheme = "https" if tls is not None else "http"
    try:
        yield f"{scheme}://127.0.0.1:{httpd.server_address[1]}"
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=5)
        app.disconnect()


@pytest.fixture
def server() -> Iterator[str]:
    """The demo company, served, with whatever backend the app chooses itself."""
    with serving(demo_company(), fake_backend()) as base:
        yield base


def get(base: str, path: str = "/") -> str:
    with urllib.request.urlopen(base + path, timeout=5) as r:  # noqa: S310
        return r.read().decode()


def post(base: str, path: str, **fields: str) -> str:
    data = urllib.parse.urlencode(fields).encode()
    with urllib.request.urlopen(base + path, data=data, timeout=5) as r:  # noqa: S310
        return r.read().decode()


def post_for_status(base: str, path: str, **fields: str) -> tuple[int, str]:
    """POST and return the status even when it is a failure.

    `post` above lets `urlopen` raise on a 5xx, which is right for the paths it
    drives: a 503 there is a test failure and should read as one. For the
    outage and GST paths the STATUS IS THE MEASUREMENT — "the person got a page
    rather than a dropped socket" cannot be asserted by a helper that turns the
    page into an exception.

    Lives here rather than in either caller because two copies of an HTTP
    helper is how two test files end up disagreeing about what a 503 means.
    """
    body = urllib.parse.urlencode(fields).encode()
    try:
        with urllib.request.urlopen(base + path, data=body, timeout=5) as answer:  # noqa: S310
            return answer.status, answer.read().decode()
    except urllib.error.HTTPError as failed:
        return failed.code, failed.read().decode()


def draft_id(html: str) -> str:
    m = re.search(r'name=draft value="([^"]+)"', html)
    assert m, f"no draft id in page:\n{html[:600]}"
    return m.group(1)


def operation(html: str) -> str:
    m = re.search(r'name=op value="([^"]+)"', html)
    assert m, f"no operation id in page:\n{html[:600]}"
    return m.group(1)


# ---- the front door ---------------------------------------------------------


def test_health_is_served_before_anything_else(server: str):
    """Served, and reporting the runtime rather than a constant.

    This asserted `"ok" in body` until 2026-08-09, when `/health` returned a
    hardcoded `{"ok": true}`. That string was present whether or not anything
    worked, so the test passed for a reason unrelated to health. It now reads
    the measured fields, which cannot be true unless a runtime is really
    connected and bootstrapped.
    """
    body = json.loads(get(server, "/health"))

    assert body["ready"] is True
    assert body["bootstrap_status"] == "ready"
    assert body["backend"] == "FakeTally"
    assert body["vouchers_read"] > 0


def test_home_page_loads(server: str):
    body = get(server)
    assert "<form" in body
    assert "Trial balance" in body


def test_an_unknown_path_serves_the_home_page_rather_than_an_error(server: str):
    """Every GET lands somewhere usable. There is no dead end and no 500."""
    body = get(server, "/no-such-page")
    assert "<form" in body


# ---- nothing is served without a backend ------------------------------------


def test_the_app_serves_no_work_at_all_once_the_runtime_is_dropped(server: str):
    """Fail closed. A dropped backend must stop the app, not degrade it.

    `disconnect()` is the cheap stand-in for the expensive case — Tally going
    away mid-session — and it fails in the same place, at `runtime()`. The
    assertion is on the property, not on the accident of how the request dies:
    no page is served, so nothing can be silently accepted, and the books are
    untouched.

    `OSError` is the honest catch. Today the handler has no error page, so the
    connection is closed and urllib raises `RemoteDisconnected`; a proper 5xx
    refusal would arrive as `HTTPError`. Both are `OSError`, so this test keeps
    holding if the app learns to answer with a refusal page, and fails the
    moment it answers with a working one.
    """
    tally = app.runtime().client
    before = tally.trial_balance(app.COMPANY)
    app.disconnect()

    with pytest.raises(OSError):
        post(server, "/entry", text="paid Sharma Traders 4200 for cement")

    assert app.connected() is False
    assert tally.list_our_vouchers(app.COMPANY) == ()
    assert tally.trial_balance(app.COMPANY) == before
    assert app.DRAFTS == {}


@pytest.mark.usefixtures("server")
def test_asking_for_the_runtime_while_disconnected_says_real_tally_required():
    """The refusal names itself, and it names itself the same way everywhere.

    `accountant/tallyio/factory.py` opens every one of its refusals with
    `REAL TALLY REQUIRED: no operation performed.` — unreachable, unlicensed,
    wrong company. A missing runtime is the same class of event and must not
    arrive as a different-shaped error, or an operator grepping their logs for
    the one string misses the other case.

    `usefixtures` rather than a parameter: the fixture is here for the connected
    starting state and the teardown, not for its URL. Without it this test would
    pass or fail on whatever the previous test left behind.
    """
    tally = app.runtime().client
    app.disconnect()

    with pytest.raises(RuntimeError) as refused:
        app.runtime()

    assert str(refused.value).startswith("REAL TALLY REQUIRED")
    assert app.connected() is False
    assert tally.list_our_vouchers(app.COMPANY) == ()


# ---- the app reads the books before it answers anything ---------------------


def test_the_app_bootstraps_this_companys_memory_before_serving(server: str):
    """Not "an index exists" — a successful bootstrap of THIS company.

    Ready is the gate. Without it every lookup is MEMORY_NOT_READY and nothing
    may be proposed, so a page that answers at all is a page whose books were
    read.
    """
    get(server)
    live = app.runtime()
    assert live.memory.ready is True
    assert live.memory.identity.key == normalise_company(app.COMPANY)
    assert live.memory.report.counts.vouchers == len(
        live.client.read_vouchers(app.COMPANY)
    )


def test_an_answer_is_stored_under_the_vendor_key_not_the_typed_spelling(server: str):
    """The correction is a row in this company's memory, not a process variable.

    Proved through the app's own surface: a DIFFERENT spelling of the same
    supplier posts straight through afterwards, which only happens if the answer
    was written against the normalised vendor key inside this company's scope.

    REWRITTEN 2026-08-10, owner ruling D-05. The second entry used to read
    "M/s Gupta Hardware Pvt Ltd" and was expected to post straight through off
    an answer given for a bare "Gupta Hardware". Under the ruling that is not a
    respelling, it is a different legal person, and the two are AMBIGUOUS. The
    re-spelling used here is now "M/s GUPTA HARDWARE." - prefix, case and
    punctuation, which is what this test was always actually about.

    The Pvt Ltd case is asserted below, in its own test, where it belongs.
    """
    asked = post(server, "/entry", text="paid Gupta Hardware 1500 for tools")
    answer_purpose_and_funding(server, asked, "Purchases")

    again = post(server, "/entry", text="paid M/s GUPTA HARDWARE. 1600 for tools")
    # THE BADGE, NOT THE BARE WORD. `render_decision` draws "not posted" for
    # NOT_VALID, so `"posted" in body` is satisfied by the exact refusal these
    # tests exist to rule out - green for the wrong reason, and silently so.
    # `class="badge b-valid">posted<` is drawn only for Outcome.VALID; the
    # NOT_VALID badge is `b-notvalid">not posted<` and cannot match it. The
    # negated form of this assertion is already in use at
    # tests/test_error_responses.py:1115. Repeated at :507, :578 and :599 below.
    assert 'class="badge b-valid">posted<' in again
    written = app.runtime().client.list_our_vouchers(app.COMPANY)[-1]
    assert written.debit_account == "Purchases"
    assert written.amount_paise == 160000


def test_an_answer_for_the_bare_name_does_not_post_for_the_private_limited(
    server: str,
):
    """Required regression, through the running app: ambiguity asks.

    OWNER RULING D-05, 2026-08-10. An answer given for "Gupta Hardware" is
    evidence about the sole proprietor. It is not evidence about "Gupta
    Hardware Pvt Ltd", which is a separate registration with its own GSTIN and
    its own books, so the second entry must ask rather than post.
    """
    asked = post(server, "/entry", text="paid Gupta Hardware 1500 for tools")
    answer_purpose_and_funding(server, asked, "Purchases")
    before = len(app.runtime().client.list_our_vouchers(app.COMPANY))

    post(server, "/entry", text="paid M/s Gupta Hardware Pvt Ltd 1600 for tools")

    # the WRITE COUNT is the assertion. A page can say "posted" for reasons
    # that have nothing to do with this entry; a voucher in Tally cannot.
    after = app.runtime().client.list_our_vouchers(app.COMPANY)
    assert len(after) == before
    assert all(v.amount_paise != 160000 for v in after)


# ---- #14.1 / #14.5: a known vendor posts straight through -------------------


def test_a_known_vendor_posts_without_asking(server: str):
    body = post(server, "/entry", text="paid Sharma Traders 4200 for cement")
    # The VALID badge, because "not posted" contains "posted". See the comment
    # on the first of these, above.
    assert 'class="badge b-valid">posted<' in body
    assert len(app.runtime().client.list_our_vouchers(app.COMPANY)) == 1


def test_the_posted_entry_shows_where_every_field_came_from(server: str):
    """#14.2 - provenance is visible, so a value with no source is obvious."""
    body = post(server, "/entry", text="paid Sharma Traders 4200 for cement")
    assert "Where each field came from" in body
    assert "typed_text" in body


def test_the_posted_entry_states_how_many_checks_ran(server: str):
    body = post(server, "/entry", text="paid Sharma Traders 4200 for cement")
    assert re.search(r"\d+ checks run", body)


def test_the_amount_reaches_tally_as_integer_paise(server: str):
    post(server, "/entry", text="paid Sharma Traders 4200 for cement")
    written = app.runtime().client.list_our_vouchers(app.COMPANY)[0]
    assert written.amount_paise == 420000
    assert isinstance(written.amount_paise, int)


# ---- #14.4 / S7: an unknown vendor asks, in plain English -------------------


def test_an_unseen_vendor_asks_instead_of_guessing(server: str):
    body = post(server, "/entry", text="paid Gupta Hardware 1500 for tools")
    assert "Gupta Hardware" in body
    assert app.runtime().client.list_our_vouchers(app.COMPANY) == ()


def test_the_question_never_shows_a_ledger_account_name(server: str):
    """S7. The person is asked about the thing, never about the account."""
    body = post(server, "/entry", text="paid Gupta Hardware 1500 for tools")
    question = re.search(r"<p class=ask>(.*?)</p>", body, re.S)
    assert question, f"no question rendered:\n{body[:600]}"
    asked = question.group(1)
    leaked = [a for a in ACCOUNTS if a in asked]
    assert leaked == [], f"question leaked {leaked}: {asked}"


def test_the_answers_offered_are_plain_words_not_accounts(server: str):
    # The page is HTML-escaped, so the apostrophe arrives as &#x27;.
    body = html.unescape(
        post(server, "/entry", text="paid Gupta Hardware 1500 for tools")
    )
    assert "stuff you'll sell on" in body
    assert "fixing something you already own" in body


def answer_purpose_and_funding(server: str, asked: str, account: str) -> str:
    """Answer BOTH questions an unknown vendor now raises, and return the page.

    An unknown vendor has two unknowns, not one: what the money was for, and
    where it came from. Until 2026-08-09 the second was not asked - the funding
    leg was filled by `_default_credit`, which preferred the string "Cash" and
    carried no provenance. These tests answered once and posted, which is why
    none of them noticed.
    """
    d = draft_id(asked)
    purpose = post(server, "/answer", draft=d, value=account, problem="which_account")
    assert "how did you pay" in purpose.lower(), (
        "the funding question must be asked before anything is posted"
    )
    return post(server, "/answer", draft=d, value="Cash", problem="funding_is_named")


def test_answering_the_question_posts_the_entry(server: str):
    asked = post(server, "/entry", text="paid Gupta Hardware 1500 for tools")
    done = answer_purpose_and_funding(server, asked, "Purchases")
    # The VALID badge, because "not posted" contains "posted". See the comment
    # on the first of these, above.
    assert 'class="badge b-valid">posted<' in done
    assert len(app.runtime().client.list_our_vouchers(app.COMPANY)) == 1


def test_the_account_chosen_is_shown_after_the_answer(server: str):
    """The account is hidden in the question, never hidden in the result."""
    asked = post(server, "/entry", text="paid Gupta Hardware 1500 for tools")
    done = post(
        server,
        "/answer",
        draft=draft_id(asked),
        value="Purchases",
        problem="which_account",
    )
    assert "Purchases" in done


def test_an_answer_is_remembered_so_the_same_vendor_is_not_asked_twice(server: str):
    asked = post(server, "/entry", text="paid Gupta Hardware 1500 for tools")
    answer_purpose_and_funding(server, asked, "Purchases")
    again = post(server, "/entry", text="paid Gupta Hardware 1600 for tools")
    # The VALID badge, because "not posted" contains "posted". See the comment
    # on the first of these, above.
    assert 'class="badge b-valid">posted<' in again


def test_answering_an_expired_draft_says_so_and_posts_nothing(server: str):
    """An answer to a draft we no longer hold must never be applied to some
    other draft, and must never post."""
    body = post(
        server,
        "/answer",
        draft="no-such-draft",
        value="Purchases",
        problem="which_account",
    )
    assert "expired" in body.lower()
    assert app.runtime().client.list_our_vouchers(app.COMPANY) == ()


# ---- #14.5: nothing posts unless the outcome is Valid -----------------------


def test_an_entry_we_cannot_read_posts_nothing(server: str):
    before = len(app.runtime().client.list_our_vouchers(app.COMPANY))
    post(server, "/entry", text="asdfghjkl")
    assert len(app.runtime().client.list_our_vouchers(app.COMPANY)) == before


def test_an_empty_entry_posts_nothing_and_does_not_crash(server: str):
    post(server, "/entry", text="")
    assert app.runtime().client.list_our_vouchers(app.COMPANY) == ()


# ---- #14.6: every entry is recorded, whatever the outcome -------------------


# These read the LOG THROUGH THE PAGE rather than the store directly, and that
# is deliberate twice over. The store is SQLite and belongs to the server
# thread, so a test-thread read would be a thread-affinity error rather than an
# assertion. More usefully: the old renderer discarded the outcome at the last
# moment (`for _, m in EVENTS`), so a row could be perfectly written and still
# never reach a person. Going through the page is what catches that.
# tests/test_action_log.py asserts the stored fields directly, in one thread.


# EVERY assertion here runs against `log_block(page)`, not the page.
#
# Two vacuous versions of these tests were written and caught before landing,
# and the pattern is worth stating so a third is not written. Searching the
# whole page for "valid" passed on an EMPTY log, because the stylesheet
# contains the word. Searching it for "Sharma Traders" passed with the reason
# deleted from the renderer, because the vendor is also in the voucher table
# and the hint text. Both tests were green and neither could fail.
#
# Scoping to the `<section id=log>` fixes the class of mistake rather than the
# two instances: anything asserted inside that slice came from the log.


def log_block(page: str) -> str:
    """The Activity section only. Raises rather than returning the whole page.

    A helper that silently fell back to the full document would reintroduce
    exactly the vacuity it exists to prevent.
    """
    start = page.index("<section id=log>") + len("<section id=log>")
    return page[start : page.index("</section>", start)]


def test_every_outcome_has_a_log_word() -> None:
    """`ACTION_FOR` must cover the enum, not just the outcomes we see in tests.

    This replaced an `if VALID / elif UNCLEAR / else` chain. The `else` arm was
    unreachable — NOT_VALID needs an unanswerable check, and the only one is
    `amount_is_integer_paise`, which a typed entry cannot trigger. An
    unreachable branch cannot be tested, so the branch became data and the
    totality is asserted here instead. Add a fourth Outcome and this fails
    before the KeyError reaches somebody's books.
    """
    assert set(app.ACTION_FOR) == set(Outcome)
    assert len(set(app.ACTION_FOR.values())) == len(Outcome), "no two share a word"


def test_an_empty_log_renders_no_outcome_at_all(server: str):
    """The control. Without this, the three tests below could all be vacuous."""
    block = log_block(get(server))
    assert "data-outcome=" not in block
    assert "nothing yet" in block


def test_a_posted_entry_reaches_the_page_with_its_outcome(server: str):
    post(server, "/entry", text="paid Sharma Traders 4200 for cement")
    assert 'data-outcome="valid"' in log_block(get(server))


def test_an_asked_entry_reaches_the_page_with_its_outcome(server: str):
    post(server, "/entry", text="paid Gupta Hardware 1500 for tools")
    assert 'data-outcome="unclear"' in log_block(get(server))


def test_an_answer_that_does_not_clear_it_posts_nothing_and_says_why(server: str):
    """The other half of /answer, and the half that must not post.

    An answer is new information, never permission. The entry re-enters the
    decision order and can come out still-unclear, in which case the log must
    carry a row saying so and Tally must be untouched.
    """
    post(server, "/entry", text="paid Gupta Hardware 1500 for tools")
    draft_id = next(iter(app.DRAFTS))
    before = app.runtime().client.trial_balance(app.COMPANY)

    # HANDOVER exhausts the question budget: answered, still not valid.
    post(server, "/answer", draft=draft_id, value=Q.HANDOVER, problem="which_account")

    assert app.runtime().client.trial_balance(app.COMPANY) == before
    block = log_block(get(server))
    assert 'data-action="asked"' in block
    assert 'data-outcome="valid"' not in block


def test_the_log_states_why_and_not_only_what(server: str):
    """The reason was the field `EVENTS` carried only on refusals.

    A posted row is the case that used to have none, and "why did you post
    this" is the question asked six months later.
    """
    post(server, "/entry", text="paid Sharma Traders 4200 for cement")
    block = log_block(get(server))
    assert 'data-action="posted"' in block
    # The decision's OWN words, which exist nowhere else in the document.
    # Asserting the vendor name here did not work: the vendor is in the
    # voucher table and the hint text, so deleting the reason from the
    # renderer left the test green.
    assert "nothing unclear and nothing surprising" in block


# ---- #14.7: everything we wrote, and undo -----------------------------------


def test_what_we_wrote_is_listed_with_its_operation_id(server: str):
    body = post(server, "/entry", text="paid Sharma Traders 4200 for cement")
    op = operation(body)
    assert op in get(server)


def test_reversing_removes_exactly_that_voucher(server: str):
    before = app.runtime().client.trial_balance(app.COMPANY)
    body = post(server, "/entry", text="paid Sharma Traders 4200 for cement")
    assert app.runtime().client.trial_balance(app.COMPANY) != before

    post(server, "/reverse", op=operation(body))
    assert app.runtime().client.trial_balance(app.COMPANY) == before
    assert app.runtime().client.list_our_vouchers(app.COMPANY) == ()


def test_reversing_an_unknown_operation_says_so_and_changes_nothing(server: str):
    post(server, "/entry", text="paid Sharma Traders 4200 for cement")
    before = app.runtime().client.trial_balance(app.COMPANY)
    post(server, "/reverse", op="ad_not_a_real_operation")
    assert app.runtime().client.trial_balance(app.COMPANY) == before


# ---- rendering helpers ------------------------------------------------------


def test_rupees_keeps_paise_exact():
    assert app.rupees(420000) == "4,200.00"
    assert app.rupees(1) == "0.01"
    assert app.rupees(0) == "0.00"


def test_esc_neutralises_html_so_a_vendor_name_cannot_inject_markup():
    assert "<script>" not in app.esc("<script>alert(1)</script>")


def test_a_vendor_name_containing_markup_is_escaped_on_the_page(server: str):
    body = post(server, "/entry", text="paid <script>Evil</script> 100 for x")
    assert "<script>Evil</script>" not in body


def test_page_returns_bytes_with_the_body_inside():
    out = app.page("<p>hello</p>")
    assert isinstance(out, bytes)
    assert b"<p>hello</p>" in out
