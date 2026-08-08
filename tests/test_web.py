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
"""

from __future__ import annotations

import html
import re
import threading
import urllib.parse
import urllib.request
from collections.abc import Iterator
from http.server import HTTPServer

import pytest

from accountant.memory.bootstrap import bootstrap
from accountant.memory.identity import normalise_company
from accountant.memory.store import MemoryStore
from accountant.web import app


@pytest.fixture
def server() -> Iterator[str]:
    """A real server on a real ephemeral port, torn down after each test.

    Port 0 lets the OS choose, so tests never collide with a dev instance or
    with each other.

    The memory store is opened INSIDE the serving thread. SQLite gives a
    connection to the thread that opened it and to no other; in production
    `app.serve()` runs the server on the thread that imported the module, so
    there is only ever one. Here the server needs its own thread so the test can
    make requests, which means the store has to be opened there too. That is a
    fixture detail, not a change of behaviour: memory is still bootstrapped
    exactly once, from this company's own Tally, before a single request is
    served.
    """
    app.TALLY = app.seed()
    app.DRAFTS.clear()
    app.EVENTS.clear()

    httpd = HTTPServer(("127.0.0.1", 0), app.Handler)
    ready = threading.Event()

    def serve() -> None:
        app.MEMORY_STORE = MemoryStore(":memory:")
        app.MEMORY = bootstrap(app.TALLY, app.COMPANY, app.MEMORY_STORE)
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
    assert "ok" in get(server, "/health").lower()


def test_home_page_loads(server: str):
    body = get(server)
    assert "<form" in body
    assert "Trial balance" in body


def test_an_unknown_path_serves_the_home_page_rather_than_an_error(server: str):
    """Every GET lands somewhere usable. There is no dead end and no 500."""
    body = get(server, "/no-such-page")
    assert "<form" in body


# ---- the app reads the books before it answers anything ---------------------


def test_the_app_bootstraps_this_companys_memory_before_serving(server: str):
    """Not "an index exists" — a successful bootstrap of THIS company.

    Ready is the gate. Without it every lookup is MEMORY_NOT_READY and nothing
    may be proposed, so a page that answers at all is a page whose books were
    read.
    """
    get(server)
    assert app.MEMORY.ready is True
    assert app.MEMORY.identity.key == normalise_company(app.COMPANY)
    assert app.MEMORY.report.counts.vouchers == len(
        app.TALLY.read_vouchers(app.COMPANY)
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
    written = app.TALLY.list_our_vouchers(app.COMPANY)[-1]
    assert written.debit_account == "Purchases"
    assert written.amount_paise == 160000


# ---- #14.1 / #14.5: a known vendor posts straight through -------------------


def test_a_known_vendor_posts_without_asking(server: str):
    body = post(server, "/entry", text="paid Sharma Traders 4200 for cement")
    assert "posted" in body.lower()
    assert len(app.TALLY.list_our_vouchers(app.COMPANY)) == 1


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
    written = app.TALLY.list_our_vouchers(app.COMPANY)[0]
    assert written.amount_paise == 420000
    assert isinstance(written.amount_paise, int)


# ---- #14.4 / S7: an unknown vendor asks, in plain English -------------------


def test_an_unseen_vendor_asks_instead_of_guessing(server: str):
    body = post(server, "/entry", text="paid Gupta Hardware 1500 for tools")
    assert "Gupta Hardware" in body
    assert app.TALLY.list_our_vouchers(app.COMPANY) == ()


def test_the_question_never_shows_a_ledger_account_name(server: str):
    """S7. The person is asked about the thing, never about the account."""
    body = post(server, "/entry", text="paid Gupta Hardware 1500 for tools")
    question = re.search(r"<p class=ask>(.*?)</p>", body, re.S)
    assert question, f"no question rendered:\n{body[:600]}"
    asked = question.group(1)
    leaked = [a for a in app.ACCOUNTS if a in asked]
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
    assert len(app.TALLY.list_our_vouchers(app.COMPANY)) == 1


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
    assert app.TALLY.list_our_vouchers(app.COMPANY) == ()


# ---- #14.5: nothing posts unless the outcome is Valid -----------------------


def test_an_entry_we_cannot_read_posts_nothing(server: str):
    before = len(app.TALLY.list_our_vouchers(app.COMPANY))
    post(server, "/entry", text="asdfghjkl")
    assert len(app.TALLY.list_our_vouchers(app.COMPANY)) == before


def test_an_empty_entry_posts_nothing_and_does_not_crash(server: str):
    post(server, "/entry", text="")
    assert app.TALLY.list_our_vouchers(app.COMPANY) == ()


# ---- #14.6: every entry is recorded, whatever the outcome -------------------


def test_a_posted_entry_is_written_to_the_action_log(server: str):
    post(server, "/entry", text="paid Sharma Traders 4200 for cement")
    assert any(kind == "post" for kind, _ in app.EVENTS)


def test_an_asked_entry_is_written_to_the_action_log(server: str):
    post(server, "/entry", text="paid Gupta Hardware 1500 for tools")
    assert any(kind == "ask" for kind, _ in app.EVENTS)


def test_the_log_is_visible_on_the_page(server: str):
    post(server, "/entry", text="paid Sharma Traders 4200 for cement")
    assert "posted" in get(server).lower()


# ---- #14.7: everything we wrote, and undo -----------------------------------


def test_what_we_wrote_is_listed_with_its_operation_id(server: str):
    body = post(server, "/entry", text="paid Sharma Traders 4200 for cement")
    op = operation(body)
    assert op in get(server)


def test_reversing_removes_exactly_that_voucher(server: str):
    before = app.TALLY.trial_balance(app.COMPANY)
    body = post(server, "/entry", text="paid Sharma Traders 4200 for cement")
    assert app.TALLY.trial_balance(app.COMPANY) != before

    post(server, "/reverse", op=operation(body))
    assert app.TALLY.trial_balance(app.COMPANY) == before
    assert app.TALLY.list_our_vouchers(app.COMPANY) == ()


def test_reversing_an_unknown_operation_says_so_and_changes_nothing(server: str):
    post(server, "/entry", text="paid Sharma Traders 4200 for cement")
    before = app.TALLY.trial_balance(app.COMPANY)
    post(server, "/reverse", op="ad_not_a_real_operation")
    assert app.TALLY.trial_balance(app.COMPANY) == before


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
