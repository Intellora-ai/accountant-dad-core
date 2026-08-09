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

import datetime
import html
import json
import re
import threading
import urllib.parse
import urllib.request
from collections.abc import Iterator
from http.server import HTTPServer

import pytest

from accountant import questions as Q
from accountant.memory.identity import normalise_company
from accountant.memory.store import MemoryStore
from accountant.schema import Outcome, Voucher
from accountant.tallyio.factory import BackendIdentity, new_run_id
from accountant.tallyio.fake import FakeTally
from accountant.web import app

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


@pytest.fixture
def server() -> Iterator[str]:
    """A real server on a real ephemeral port, torn down after each test.

    Port 0 lets the OS choose, so tests never collide with a dev instance or
    with each other.

    The backend is INJECTED through `app.configure()`. That is the seam the
    module exposes precisely so it never has to import an implementation
    itself — the shipped app can no longer name a fake, and this fixture is
    where the fake enters instead.

    The memory store is opened INSIDE the serving thread. SQLite gives a
    connection to the thread that opened it and to no other; in production
    `app.serve()` runs the server on the thread that imported the module, so
    there is only ever one. Here the server needs its own thread so the test can
    make requests, which means the store has to be opened there too. That is a
    fixture detail, not a change of behaviour: `configure()` still bootstraps
    memory exactly once, from this company's own Tally, before a single request
    is served.
    """
    tally = demo_company()
    identity = fake_backend()
    app.DRAFTS.clear()
    # `EVENTS` used to be cleared here: a module-level list that leaked rows
    # from one test into the next. The log now lives in this test's own
    # MemoryStore, so there is nothing global left to reset.

    httpd = HTTPServer(("127.0.0.1", 0), app.Handler)
    ready = threading.Event()

    def serve() -> None:
        app.configure(tally, identity, store=MemoryStore(":memory:"))
        ready.set()
        httpd.serve_forever()

    thread = threading.Thread(target=serve, daemon=True)
    thread.start()
    assert ready.wait(timeout=5), "the server thread never bootstrapped memory"
    try:
        yield f"http://127.0.0.1:{httpd.server_address[1]}"
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=5)
        app.disconnect()


def get(base: str, path: str = "/") -> str:
    with urllib.request.urlopen(base + path, timeout=5) as r:  # noqa: S310
        return r.read().decode()


def post(base: str, path: str, **fields: str) -> str:
    data = urllib.parse.urlencode(fields).encode()
    with urllib.request.urlopen(base + path, data=data, timeout=5) as r:  # noqa: S310
        return r.read().decode()


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
    """
    asked = post(server, "/entry", text="paid Gupta Hardware 1500 for tools")
    post(
        server,
        "/answer",
        draft=draft_id(asked),
        value="Purchases",
        problem="which_account",
    )

    again = post(
        server, "/entry", text="paid M/s Gupta Hardware Pvt Ltd 1600 for tools"
    )
    assert "posted" in again.lower()
    written = app.runtime().client.list_our_vouchers(app.COMPANY)[-1]
    assert written.debit_account == "Purchases"
    assert written.amount_paise == 160000


# ---- #14.1 / #14.5: a known vendor posts straight through -------------------


def test_a_known_vendor_posts_without_asking(server: str):
    body = post(server, "/entry", text="paid Sharma Traders 4200 for cement")
    assert "posted" in body.lower()
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


def test_answering_the_question_posts_the_entry(server: str):
    asked = post(server, "/entry", text="paid Gupta Hardware 1500 for tools")
    done = post(
        server,
        "/answer",
        draft=draft_id(asked),
        value="Purchases",
        problem="which_account",
    )
    assert "posted" in done.lower()
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
    post(
        server,
        "/answer",
        draft=draft_id(asked),
        value="Purchases",
        problem="which_account",
    )
    again = post(server, "/entry", text="paid Gupta Hardware 1600 for tools")
    assert "posted" in again.lower()


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
