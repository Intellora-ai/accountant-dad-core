"""Readiness is measured, and a refusal is an answer.

TWO BUGS THIS FILE PINS, BOTH FOUND 2026-08-09
----------------------------------------------
1. `/health` returned a hardcoded `{"ok": true}`. It never consulted the
   runtime, so after `disconnect()` — and equally after a failed bootstrap —
   it kept reporting healthy. A readiness endpoint that cannot say "not ready"
   is not a readiness endpoint; it is a constant with a misleading name, and
   anything that trusts it (a load balancer, a person, a script) is misled by
   design rather than by accident.

2. A request with no runtime raised inside the handler, so `socketserver`
   printed a traceback to stderr and dropped the socket. It DID fail closed —
   nothing was posted — but the person got no page and no reason. "Refuses
   safely" and "refuses legibly" are different properties and this had only
   the first.

WHY A SEPARATE FILE
-------------------
`tests/test_web.py` proves the app's behaviour when it is working. This proves
what it says when it is NOT, which is the half that is easy to leave untested
because nothing appears to be wrong while it is missing.

WHAT THIS FILE DOES NOT PROVE
-----------------------------
That the numbers `/health` reports are correct for a real Tally — only that
they are read from the live runtime rather than invented. Real-backend evidence
lives in `docs/PROJECT_STATE.md` §21 and may never come from here.
"""

from __future__ import annotations

import datetime
import json
import threading
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Iterator
from http.server import HTTPServer

import pytest

from accountant.memory.store import BootstrapStatus, MemoryStore
from accountant.schema import Voucher
from accountant.tallyio.factory import BackendIdentity, new_run_id
from accountant.tallyio.fake import FakeTally
from accountant.web import app

ACCOUNTS = ("Purchases", "Cash")


def _tally() -> FakeTally:
    t = FakeTally()
    history = tuple(
        Voucher(
            id=f"h{i}",
            date=datetime.date(2026, 4, 1),
            party="Sharma Traders",
            narration="cement supply",
            debit_account="Purchases",
            credit_account="Cash",
            amount_paise=118000,
        )
        for i in range(4)
    )
    t.add_company(app.COMPANY, accounts=ACCOUNTS, vouchers=history, backed_up=True)
    return t


def _identity() -> BackendIdentity:
    """A double's identity. `backend` is a plain string, so it can say so.

    The rule is that the SHIPPED module cannot name the fake, not that a test
    cannot use one — so this honestly reports FakeTally rather than pretending.
    """
    return BackendIdentity(
        backend="FakeTally",
        endpoint="memory://tests/test_health_and_refusal.py",
        company=app.COMPANY,
        company_exists=True,
        companies_visible=1,
        run_id=new_run_id(),
    )


@pytest.fixture
def server() -> Iterator[str]:
    """A live server on an ephemeral port, configured inside its own thread.

    SQLite binds a connection to the thread that opened it, so the store must
    be created on the serving thread. The `threading.Event` handshake makes the
    first request wait for that to finish.
    """
    app.DRAFTS.clear()
    httpd = HTTPServer(("127.0.0.1", 0), app.Handler)
    ready = threading.Event()

    def serve() -> None:
        app.configure(_tally(), _identity(), store=MemoryStore(":memory:"))
        ready.set()
        httpd.serve_forever()

    thread = threading.Thread(target=serve, daemon=True)
    thread.start()
    assert ready.wait(timeout=5), "the server thread never configured a runtime"

    yield f"http://127.0.0.1:{httpd.server_address[1]}"

    httpd.shutdown()
    httpd.server_close()
    thread.join(timeout=5)
    app.disconnect()


def _health(base: str) -> tuple[int, dict[str, object]]:
    """Status AND body, because a readiness endpoint answers 503 when unready.

    `urlopen` raises on 4xx/5xx, so a helper that only handled 200 could not
    read the very case this file exists to test.
    """
    try:
        with urllib.request.urlopen(f"{base}/health", timeout=5) as r:  # noqa: S310
            return r.status, json.loads(r.read().decode())
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read().decode())


def _post(base: str, path: str, **fields: str) -> tuple[int, str]:
    """POST, returning status and body even when the app refuses."""
    data = urllib.parse.urlencode(fields).encode()
    try:
        with urllib.request.urlopen(f"{base}{path}", data=data, timeout=5) as r:  # noqa: S310
            return r.status, r.read().decode()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode()


def test_health_reports_the_measured_bootstrap_state(server: str) -> None:
    """Every field read off the live runtime, none of them constants."""
    status, body = _health(server)

    assert status == 200, "a ready service answers 200"
    assert body["bootstrap_status"] == BootstrapStatus.READY.value
    assert body["backend"] == "FakeTally"
    assert body["company_exists"] is True
    assert body["accounts_read"] == len(ACCOUNTS)
    assert body["vouchers_read"] == 4
    # int() rather than a bare >=: health() is typed dict[str, object] because
    # its values genuinely differ in type, so the comparison needs the cast to
    # mean anything to a reader or a type checker.
    assert int(str(body["vendor_mappings_derived"])) >= 1
    assert int(str(body["index_entries"])) >= 1
    assert body["run_id"].startswith("run_")  # type: ignore[union-attr]


def test_health_says_not_ready_once_the_runtime_is_gone(server: str) -> None:
    """The bug, pinned. This returned {"ok": true} forever.

    Readiness must mean "safe to receive work". A disconnected app is not, and
    saying otherwise is worse than saying nothing, because it is believed.
    """
    app.disconnect()
    status, body = _health(server)

    assert status == 503, "an unready service must not answer 200"
    assert body["ready"] is False
    assert body["bootstrap_status"] == "not_connected"
    assert body["failure_code"] == "NO_RUNTIME"
    assert "REAL TALLY REQUIRED" in str(body["detail"])


def test_a_request_with_no_runtime_gets_a_reason_not_a_dropped_socket(
    server: str,
) -> None:
    """Fails closed AND legibly. Previously only the first was true.

    The old behaviour raised inside the handler, so socketserver logged a
    traceback and closed the connection. Nothing was posted, which was correct,
    but the person saw a browser error and could not tell a broken app from an
    unreachable Tally.
    """
    client = app.runtime().client
    before = client.trial_balance(app.COMPANY)
    app.disconnect()

    status, body = _post(server, "/entry", text="paid Sharma Traders 4200 cement")

    assert status == 503, "an unusable service must say so in the status line"
    assert "REAL TALLY REQUIRED" in body
    assert client.list_our_vouchers(app.COMPANY) == ()
    assert client.trial_balance(app.COMPANY) == before


# ---- G6: the banner. A person must be TOLD, not left guessing -----------------
#
# /health answers a machine. Nobody watching a browser reads it. Before the
# banner, a company whose books had not been read served either a normal-looking
# entry form — the app looked fine and simply never suggested anything — or a
# stack trace out of pipeline.build_draft.
#
# These drive a REAL server over HTTP, like the fixture above, because a banner
# that only appears when a helper is called directly is not a banner a person
# can see.
#
# Assertions match app.CANNOT_HELP, the marker every message shares, rather than
# the prose around it. That is deliberate twice over: the wording is meant to be
# edited freely without breaking tests, and — the reason that matters — two tests
# written earlier today were GREEN AND VACUOUS because they searched a whole page
# for a common word the stylesheet already contained. The marker appears in
# exactly one place in the document.


def _serve(tally: FakeTally) -> Iterator[str]:
    httpd = HTTPServer(("127.0.0.1", 0), app.Handler)
    ready = threading.Event()

    def run() -> None:
        app.configure(tally, _identity(), store=MemoryStore(":memory:"))
        ready.set()
        httpd.serve_forever()

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    assert ready.wait(timeout=5), "the server thread never configured a runtime"
    try:
        yield f"http://127.0.0.1:{httpd.server_address[1]}"
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=5)
        app.disconnect()


def _page(base: str) -> str:
    with urllib.request.urlopen(base + "/", timeout=5) as r:  # noqa: S310
        return r.read().decode()


def _company_with(vouchers: tuple[Voucher, ...]) -> FakeTally:
    t = FakeTally()
    t.add_company(app.COMPANY, accounts=ACCOUNTS, vouchers=vouchers, backed_up=True)
    return t


def _nameless(n: int) -> tuple[Voucher, ...]:
    """History that exists but teaches nothing — no party on any row."""
    return tuple(
        Voucher(
            id=f"n{i}",
            date=datetime.date(2026, 4, 1),
            party="",
            narration="cash paid",
            debit_account="Purchases",
            credit_account="Cash",
            amount_paise=5000,
        )
        for i in range(n)
    )


def test_a_company_we_really_read_shows_no_banner_at_all() -> None:
    """THE CONTROL. Without it every test below passes on an always-on banner."""
    for base in _serve(_tally()):
        page = _page(base)
        assert app.runtime().memory.report.status is BootstrapStatus.READY
        assert app.CANNOT_HELP not in page
        # Scoped to the banner's own marker. `class=warn` is used by four other
        # things on this page — the backend notice, an expired draft, a retype
        # prompt, the refusal box — so asserting on it would fail for reasons
        # that have nothing to do with the bootstrap banner.
        assert "We have not read" not in page
        assert "no past entries" not in page


def test_a_company_with_no_history_at_all_is_told_so_in_plain_words() -> None:
    for base in _serve(_company_with(())):
        page = _page(base)
        assert app.runtime().memory.report.status is BootstrapStatus.EMPTY_SOURCE
        assert app.CANNOT_HELP in page
        assert "no past entries" in page


def test_a_company_whose_history_teaches_nothing_is_told_so_in_plain_words() -> None:
    for base in _serve(_company_with(_nameless(4))):
        page = _page(base)
        status = app.runtime().memory.report.status
        assert status is BootstrapStatus.EMPTY_VENDOR_INDEX
        assert app.CANNOT_HELP in page
        assert "says who you paid" in page


def test_a_company_that_cannot_help_yet_still_posts_nothing() -> None:
    """The banner explains; it does not replace the refusal."""
    for base in _serve(_company_with(())):
        code, _ = _post(base, "/entry", text="paid Sharma Traders 4200 for cement")
        assert code in (200, 503)
        assert app.runtime().client.list_our_vouchers(app.COMPANY) == ()


def test_every_status_has_words_and_none_of_them_is_jargon() -> None:
    """Plain language is a product requirement, so it is asserted, not trusted.

    A twelve-year-old must be able to read these. The forbidden list is checked
    WHOLE rather than sampled, and every non-READY status must have a message —
    a status with no words would silently show a blank warning box.
    """
    forbidden = (
        "bootstrap",
        "index",
        "mapping",
        "vendor_key",
        "enum",
        "status",
        "EMPTY_SOURCE",
        "EMPTY_VENDOR_INDEX",
        "INCOMPLETE",
        "NEVER_RUN",
    )
    for status in BootstrapStatus:
        message = app.BOOTSTRAP_TROUBLE.get(status, "")
        if status is BootstrapStatus.READY:
            assert message == "", "a company we read must produce no banner"
            continue
        assert message, f"{status.value} would render an empty warning box"
        assert app.CANNOT_HELP in message
        for word in forbidden:
            assert word.lower() not in message.lower(), f"{status.value}: {word}"


def test_a_backend_that_is_not_real_tally_says_so_on_every_page() -> None:
    """A SURVIVING MUTANT found this gap; it is not a hypothetical.

    Deleting the `ident.backend != "RealTally"` branch — so a fake is presented
    exactly as a real Tally would be — left all 879 tests green. Nothing
    anywhere asserted that the page tells the truth about which Tally it is on.

    The page used to carry a hardcoded "Demo mode... fake Tally... Nothing here
    touches any real books." That was true when the app built its own FakeTally
    and became a lie the moment P3.1 wired it to a real one. Both directions are
    dangerous. The false-reassurance direction is worse: a person told nothing
    is real will type freely into books that are.
    """
    for base in _serve(_tally()):
        page = _page(base)
        assert app.runtime().identity.backend == "FakeTally"
        assert "Not real accounting software" in page
        assert "FakeTally" in page
        assert "Nothing here reaches any real books" in page


def test_the_page_never_claims_a_fake_backend_is_tally() -> None:
    """The inverse, and the one that protects somebody's real books.

    Asserted as an absence, because the failure is a sentence that should not
    be there. The wording is taken from the notice itself so it cannot drift.
    """
    for base in _serve(_tally()):
        page = _page(base)
        assert "writing into <b>RealTally</b>" not in page, (
            "the page presented a fake backend as if it were a real Tally"
        )
