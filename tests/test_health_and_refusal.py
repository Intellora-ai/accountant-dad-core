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
