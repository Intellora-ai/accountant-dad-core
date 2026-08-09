"""ONE company identity per runtime, and every request uses it.

THE DEFECT THIS FILE EXISTS FOR
-------------------------------
`accountant/web/app.py` held `COMPANY = "Accountant Dad Final"` as a module
constant, and every request handler passed THAT to Tally: `read_accounts`,
`read_vouchers`, `list_our_vouchers`, `trial_balance`, `build_draft`,
`reverse_operation`, `reversal.preview`, and the action-log read on the home
page.

Startup did not. `serve()` reads ACCOUNTANT_COMPANY, `connect()` passes it to
the factory, and `configure()` bootstraps memory for whatever company the
identity names. So a person who set their own company name got:

    memory      keyed to THEIR company
    the page    asking Tally for "Accountant Dad Final"

The app worked for exactly one company name in the world. For every other name
it failed - closed, but by accident: `FakeTally` raises `KeyError` and
`RealTally` raises for a company that is not open, so the request died with a
traceback rather than an answer. And where it did not die, the cross-company
guard in `pipeline.build_draft` raised instead, which is the LAST line of
defence being used as the first.

FIVE PLACES NAME A COMPANY. THEY MUST BE ONE PLACE.
---------------------------------------------------
    startup   `BackendIdentity.company`, verified open in Tally by the factory
    memory    `CompanyMemory.identity`, built by `bootstrap`
    request   whatever the handler passes to the pipeline
    Tally     whatever the handler passes to `client.*`
    audit     the `company_key` on every ActionLog row

They now all read `Runtime.company` / `Runtime.company_key`, checked once at
`configure()` and re-checked on EVERY `runtime()` call. Disagreement is a
refusal: nothing is read, nothing is written, the person is told, and the
mismatch is recorded in the action log.

WHAT THIS FILE DOES NOT PROVE
-----------------------------
Anything about real TallyPrime. The backend is a `FakeTally` injected through
`configure()`, exactly as in `tests/test_web.py`.
"""

from __future__ import annotations

import ast
import dataclasses
import datetime
import json
import pathlib
import re
import threading
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Iterator
from http.server import HTTPServer

import pytest

from accountant.memory.bootstrap import bootstrap
from accountant.memory.identity import normalise_company
from accountant.memory.store import MemoryStore
from accountant.problems import FUNDING_PROBLEM
from accountant.schema import Voucher
from accountant.tallyio.factory import BackendIdentity, new_run_id
from accountant.tallyio.fake import FakeTally
from accountant.web import app

#: A company that is NOT `app.COMPANY`. Every assertion below is about this one.
OTHER = "Nanda Hardware Stores"

#: A second one, for the mismatch tests. Keys differently from `OTHER`.
THIRD = "Bhosale Timber"

ACCOUNTS = ("Purchases", "Repairs & Maintenance", "Cash", "Bank")
TODAY = datetime.date(2026, 8, 9)


def history() -> tuple[Voucher, ...]:
    """Enough consistent history that one vendor posts straight through."""
    return tuple(
        Voucher(
            id=f"h{i}",
            date=datetime.date(2026, 1 + (i % 6), 1 + (i % 27)),
            party="Sharma Traders",
            narration="cement supply",
            debit_account="Purchases",
            credit_account="Cash",
            amount_paise=380000 + i * 1000,
        )
        for i in range(12)
    )


def tally_for(*companies: str) -> FakeTally:
    t = FakeTally()
    for name in companies:
        t.add_company(name, accounts=ACCOUNTS, vouchers=history(), backed_up=True)
    return t


def identity_for(company: str, *, visible: int = 1) -> BackendIdentity:
    return BackendIdentity(
        backend="FakeTally",
        endpoint="memory://tests/test_company_identity.py",
        company=company,
        company_exists=True,
        companies_visible=visible,
        run_id=new_run_id(),
    )


@pytest.fixture(autouse=True)
def no_runtime_leaks() -> Iterator[None]:
    """No runtime and no drafts leak between tests in this file."""
    app.disconnect()
    app.DRAFTS.clear()
    app.BATCHES.clear()
    yield
    app.disconnect()
    app.DRAFTS.clear()
    app.BATCHES.clear()


@pytest.fixture
def other_company_server() -> Iterator[tuple[str, FakeTally]]:
    """A live server whose ONLY open company is `OTHER`.

    Deliberately built the same way `tests/test_web.py::server` is, and
    deliberately NOT sharing it: that fixture opens `app.COMPANY`, which is the
    one name the old code happened to work for.
    """
    tally = tally_for(OTHER)
    httpd = HTTPServer(("127.0.0.1", 0), app.Handler)
    ready = threading.Event()

    def serve() -> None:
        app.configure(tally, identity_for(OTHER), store=MemoryStore(":memory:"))
        ready.set()
        httpd.serve_forever()

    thread = threading.Thread(target=serve, daemon=True)
    thread.start()
    assert ready.wait(timeout=5), "the server thread never bootstrapped memory"
    try:
        yield f"http://127.0.0.1:{httpd.server_address[1]}", tally
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


# ---- the regression: a configured company that is not the default -----------


def test_a_non_default_company_can_be_used_end_to_end(
    other_company_server: tuple[str, FakeTally],
) -> None:
    """THE regression. Type an entry, have it posted, into MY company.

    Before the fix every one of these five steps asked Tally for
    "Accountant Dad Final", which is not open, so the home page itself died with
    `KeyError: no such company`. The person could not reach the entry box.
    """
    base, tally = other_company_server

    home = get(base)
    assert OTHER in home, "the page names the company we actually connected to"

    body = post(base, "/entry", text="paid Sharma Traders 4200 for cement")
    assert "posted" in body

    written = tally.list_our_vouchers(OTHER)
    assert len(written) == 1, "the voucher landed in OUR company's books"
    assert written[0].party == "Sharma Traders"
    assert written[0].debit_account == "Purchases"
    assert tally.trial_balance(OTHER)["Purchases"] > 0


def test_the_home_page_reads_the_configured_company_and_not_the_default() -> None:
    """No HTTP, no thread: the renderer alone, so the failure is unambiguous."""
    tally = tally_for(OTHER)
    app.configure(tally, identity_for(OTHER), store=MemoryStore(":memory:"))

    body = app.render_home().decode()

    assert OTHER in body
    assert app.COMPANY not in body, (
        "the page printed the module default rather than the live company"
    )


def test_the_audit_trail_is_written_under_the_configured_company() -> None:
    """The fifth of the five. A row filed under the wrong company is lost."""
    tally = tally_for(OTHER)
    store = MemoryStore(":memory:")
    app.configure(tally, identity_for(OTHER), store=store)

    app.note("tested", "recorded", "a row written by this test")

    ours = store.actions(OTHER)
    assert [r.action for r in ours] == ["tested"]
    assert ours[0].company_key == normalise_company(OTHER)
    assert store.actions(app.COMPANY) == (), "and nothing under the default name"


def test_health_reports_the_configured_company() -> None:
    tally = tally_for(OTHER)
    app.configure(tally, identity_for(OTHER), store=MemoryStore(":memory:"))

    body = app.health()

    assert body["company"] == OTHER
    assert body["company_identifier"] == OTHER
    assert body["ready"] is True


def test_reversal_undoes_the_entry_in_the_configured_company(
    other_company_server: tuple[str, FakeTally],
) -> None:
    """`/reverse` and `/reverse-all` name a company too, and named the wrong one."""
    base, tally = other_company_server
    before = tally.trial_balance(OTHER)

    body = post(base, "/entry", text="paid Sharma Traders 4200 for cement")
    op = re.search(r'name=op value="([^"]+)"', body)
    assert op, f"nothing posted, so nothing to undo:\n{body[:400]}"
    assert tally.trial_balance(OTHER) != before

    post(base, "/reverse", op=op.group(1))

    assert tally.trial_balance(OTHER) == before, "criterion #6.5, on MY company"


def test_bulk_reversal_previews_the_configured_company(
    other_company_server: tuple[str, FakeTally],
) -> None:
    base, tally = other_company_server
    post(base, "/entry", text="paid Sharma Traders 4200 for cement")
    before = tally.trial_balance(OTHER)
    assert len(tally.list_our_vouchers(OTHER)) == 1

    preview = post(base, "/reverse-all")
    batch = re.search(r'name=batch value="([^"]+)"', preview)
    assert batch, f"no batch id in the preview:\n{preview[:400]}"
    assert tally.list_our_vouchers(OTHER), "the preview writes nothing"

    post(base, "/reverse-all", batch=batch.group(1), confirm="yes")

    assert tally.list_our_vouchers(OTHER) == ()
    assert tally.trial_balance(OTHER) != before


def test_a_question_and_its_answer_stay_in_the_configured_company(
    other_company_server: tuple[str, FakeTally],
) -> None:
    """The `/answer` handler reads accounts and history too, and named the default."""
    base, tally = other_company_server

    body = post(base, "/entry", text="paid Gupta Hardware 1500 for tools")
    assert "needs an answer" in body, f"expected a question:\n{body[:400]}"
    draft = re.search(r'name=draft value="([^"]+)"', body)
    assert draft

    # An unseen vendor has neither an expense account nor a funding history in
    # THIS company's books, so it takes two answers. Both go through the same
    # handler, and both used to read the module default.
    for problem, value in (("which_account", "Purchases"), (FUNDING_PROBLEM, "Cash")):
        body = post(base, "/answer", draft=draft.group(1), problem=problem, value=value)

    assert "posted" in body, f"still not posted after both answers:\n{body[:600]}"
    written = [v for v in tally.list_our_vouchers(OTHER) if v.party == "Gupta Hardware"]
    assert len(written) == 1
    assert written[0].debit_account == "Purchases"
    assert written[0].credit_account == "Cash"


# ---- the five identities must agree, and disagreement is a refusal ----------


def _mismatched(live: app.Runtime, other: str) -> app.Runtime:
    """A runtime whose startup identity and memory name two different companies.

    No public route produces one - `configure()` bootstraps memory from
    `identity.company`, so the two agree by construction. That is exactly why
    the guard must not depend on the route: it is checked at the point of USE,
    and this builds the state a future route could hand it.
    """
    return dataclasses.replace(live, identity=identity_for(other, visible=2))


def test_the_check_names_both_companies_when_memory_and_startup_disagree() -> None:
    """The pure function, on its own. A refusal must say which two, not that."""
    tally = tally_for(OTHER, THIRD)
    live = app.configure(tally, identity_for(OTHER, visible=2), store=MemoryStore())

    assert app.company_mismatch(live) == "", "the honest runtime agrees with itself"

    wrong = app.company_mismatch(_mismatched(live, THIRD))

    assert OTHER in wrong and THIRD in wrong, (
        "the refusal names BOTH companies, or nobody can tell which is wrong"
    )


def test_a_mismatched_runtime_refuses_every_request_rather_than_serving_one() -> None:
    """Installed by force, then caught on the way in. Fails closed AND legibly."""
    tally = tally_for(OTHER, THIRD)
    store = MemoryStore(":memory:")
    live = app.configure(tally, identity_for(OTHER, visible=2), store=store)

    app.install(_mismatched(live, THIRD))

    with pytest.raises(RuntimeError, match=app.REFUSAL) as caught:
        app.runtime()
    assert OTHER in str(caught.value) and THIRD in str(caught.value)


def test_a_mismatch_is_recorded_in_the_action_log_and_not_only_raised() -> None:
    """A refusal nobody can find afterwards is a refusal nobody can investigate."""
    tally = tally_for(OTHER, THIRD)
    store = MemoryStore(":memory:")
    live = app.configure(tally, identity_for(OTHER, visible=2), store=store)
    app.install(_mismatched(live, THIRD))

    for _ in range(3):
        with pytest.raises(RuntimeError):
            app.runtime()

    rows = [r for r in store.actions(THIRD) if r.action == app.COMPANY_MISMATCH]
    assert len(rows) == 1, "recorded once per distinct mismatch, not once per call"
    assert OTHER in rows[0].reason and THIRD in rows[0].reason
    assert rows[0].outcome == "refused"


def test_a_mismatched_runtime_writes_nothing_and_serves_a_503() -> None:
    """What the PERSON gets. A traceback and a closed socket is not an answer.

    Everything that touches the store runs on the SERVING thread, because
    SQLite hands a connection to the thread that opened it. `runtime()` now
    reads the store on every call, so a store opened here would raise the wrong
    error for the wrong reason.
    """
    tally = tally_for(OTHER, THIRD)
    before = tally.trial_balance(OTHER)
    httpd = HTTPServer(("127.0.0.1", 0), app.Handler)
    ready = threading.Event()

    def serve() -> None:
        live = app.configure(
            tally, identity_for(OTHER, visible=2), store=MemoryStore(":memory:")
        )
        app.install(_mismatched(live, THIRD))
        ready.set()
        httpd.serve_forever()

    thread = threading.Thread(target=serve, daemon=True)
    thread.start()
    assert ready.wait(timeout=5)
    base = f"http://127.0.0.1:{httpd.server_address[1]}"
    try:
        with pytest.raises(urllib.error.HTTPError) as caught:
            post(base, "/entry", text="paid Sharma Traders 4200 for cement")
        assert caught.value.code == 503
        body = caught.value.read().decode()
        assert app.REFUSAL in body
        assert THIRD in body, "the 503 names the company it will not work in"
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=5)

    assert tally.trial_balance(OTHER) == before, "and the books never moved"
    assert tally.list_our_vouchers(OTHER) == ()
    assert tally.list_our_vouchers(THIRD) == ()


def test_the_company_cannot_change_after_startup_without_a_refusal() -> None:
    """The open company changes underneath us. Every request must stop.

    The real-world shape: somebody closes the company in TallyPrime and opens
    another. The runtime is frozen at startup, so the NAME cannot drift - what
    drifts is whether that name is still open, and a request served against a
    company that is no longer there is served against nothing at all.

    It already failed closed before this - the connector raises for a company
    it cannot find - but it failed as a dropped socket. Now it is a 503 that
    names what IS open.
    """
    tally = tally_for(OTHER)
    httpd = HTTPServer(("127.0.0.1", 0), app.Handler)
    ready = threading.Event()

    def serve() -> None:
        app.configure(tally, identity_for(OTHER), store=MemoryStore(":memory:"))
        ready.set()
        httpd.serve_forever()

    thread = threading.Thread(target=serve, daemon=True)
    thread.start()
    assert ready.wait(timeout=5)
    base = f"http://127.0.0.1:{httpd.server_address[1]}"
    try:
        assert OTHER in get(base), "working, before the company is closed"

        # Exactly what closing one company and opening another looks like from
        # our side: `list_companies()` stops naming ours.
        tally.close_company(OTHER)
        tally.add_company(THIRD, accounts=ACCOUNTS, vouchers=history(), backed_up=True)

        with pytest.raises(urllib.error.HTTPError) as caught:
            post(base, "/entry", text="paid Sharma Traders 4200 for cement")
        assert caught.value.code == 503
        body = caught.value.read().decode()
        assert app.REFUSAL in body
        assert THIRD in body, "and it names what IS open, so the person can act"
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=5)

    assert tally.list_our_vouchers(THIRD) == (), "nothing reached the other company"


def test_a_stale_memory_row_for_a_colliding_company_is_refused() -> None:
    """Two names, one key, one store row. The second bootstrap steals the first.

    `MemoryStore.company` is keyed on `company_key` alone with an
    `INSERT OR REPLACE` writer, so `"Ganesh  Textiles"` and `"Ganesh Textiles"`
    share one row. `bootstrap` refuses the pair only when BOTH are open in Tally
    at once - two processes, or two runs, are not covered by that check.

    So the live runtime's own memory row can be rewritten under it to name a
    different company. That is the stale one, and it must not answer.
    """
    spaced, tight = "Ganesh  Textiles", "Ganesh Textiles"
    assert normalise_company(spaced) == normalise_company(tight), "same key"

    store = MemoryStore(":memory:")
    ours = tally_for(spaced)
    app.configure(ours, identity_for(spaced), store=store)
    assert app.runtime().company == spaced
    app.runtime().confirm_company()  # agrees, before the other run happens

    # A second run, against a Tally where only the other spelling is open, so
    # `bootstrap`'s collision check cannot see the pair.
    bootstrap(tally_for(tight), tight, store)

    with pytest.raises(RuntimeError, match=app.REFUSAL) as caught:
        app.runtime().confirm_company()
    assert tight in str(caught.value), "the refusal names the row that replaced ours"

    rows = [r for r in store.actions(spaced) if r.action == app.COMPANY_MISMATCH]
    assert len(rows) == 1, "and it is written down where somebody can find it"


def test_a_request_cannot_name_a_company_of_its_own(
    other_company_server: tuple[str, FakeTally],
) -> None:
    """A form field called `company` must change nothing. Ever.

    There is no such field today. This is here so that adding one is a test
    failure rather than a quiet privilege escalation: a person on the network
    could otherwise read and write any company open in that Tally.
    """
    base, tally = other_company_server
    tally.add_company(THIRD, accounts=ACCOUNTS, vouchers=(), backed_up=True)

    post(
        base,
        "/entry",
        text="paid Sharma Traders 4200 for cement",
        company=THIRD,
    )

    assert tally.list_our_vouchers(THIRD) == (), "nothing reached the named company"
    assert len(tally.list_our_vouchers(OTHER)) == 1, "it went to ours instead"


def test_health_says_which_two_companies_disagree_without_raising() -> None:
    """`/health` must answer on the worst day, and this is one of them."""
    tally = tally_for(OTHER, THIRD)
    store = MemoryStore(":memory:")
    live = app.configure(tally, identity_for(OTHER, visible=2), store=store)
    app.install(dataclasses.replace(live, identity=identity_for(THIRD, visible=2)))

    body = app.health()

    assert body["ready"] is False
    assert body["failure_code"] == "COMPANY_MISMATCH"
    assert OTHER in str(body["detail"]) and THIRD in str(body["detail"])


def test_the_health_endpoint_still_answers_503_when_the_companies_disagree() -> None:
    tally = tally_for(OTHER, THIRD)
    store = MemoryStore(":memory:")
    live = app.configure(tally, identity_for(OTHER, visible=2), store=store)

    httpd = HTTPServer(("127.0.0.1", 0), app.Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{httpd.server_address[1]}"
    try:
        app.install(dataclasses.replace(live, identity=identity_for(THIRD, visible=2)))
        with pytest.raises(urllib.error.HTTPError) as caught:
            get(base, "/health")
        assert caught.value.code == 503
        assert json.loads(caught.value.read())["failure_code"] == "COMPANY_MISMATCH"
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=5)


# ---- the default is a default, and nothing else -----------------------------


def test_the_module_default_is_what_configuration_falls_back_to(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`COMPANY` may seed configuration. It may never BE the company."""
    monkeypatch.delenv(app.ENV_COMPANY, raising=False)

    assert app.config_from_environment()[1] == app.COMPANY


def test_the_environment_variable_wins_over_the_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(app.ENV_COMPANY, OTHER)

    _, company, _, provenance = app.config_from_environment()

    assert company == OTHER
    assert f"{app.ENV_COMPANY}={OTHER!r} (environment)" in provenance


# ---- the structural half: no handler may read the constant again ------------
#
# The behaviour tests above prove the handlers are right TODAY. They cannot
# prove that a handler added tomorrow will not reach for the constant again,
# and that is the defect that shipped: not one wrong line, but a module-level
# name that every function could see and that looked like the answer.
#
# Written the way `tests/test_runtime_backend.py` and
# `tests/test_lifecycle.py::test_the_web_handler_calls_the_verified_reversal_...`
# already scan for a forbidden call.

APP = pathlib.Path(app.__file__)

#: The only two functions allowed to read `COMPANY`. Both are CONFIGURATION:
#: they decide what to connect to, before there is a runtime to ask.
CONFIGURATION_ONLY = frozenset({"connect", "config_from_environment"})

#: The name a handler must use instead.
THE_RIGHT_ANSWER = "company"


def _reads_of(name: str, source: str) -> list[tuple[int, str]]:
    """Every place `name` is READ, as (line, enclosing function or class).

    Loads only. `COMPANY = "..."` is the definition and is not a read, and a
    read is the whole of the defect: the constant may exist, it may not be
    consulted.
    """
    found: list[tuple[int, str]] = []

    def walk(node: ast.AST, scope: tuple[str, ...]) -> None:
        for child in ast.iter_child_nodes(node):
            if (
                isinstance(child, ast.Name)
                and child.id == name
                and isinstance(child.ctx, ast.Load)
            ):
                found.append((child.lineno, ".".join(scope) or "<module>"))
            inner = scope
            if isinstance(child, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
                inner = (*scope, child.name)
            walk(child, inner)

    walk(ast.parse(source), ())
    return found


def test_no_request_handler_reads_the_module_default() -> None:
    """`COMPANY` is configuration. Only configuration may read it."""
    source = APP.read_text(encoding="utf-8")

    offenders = [
        f"accountant/web/app.py:{line} in {scope}"
        for line, scope in _reads_of("COMPANY", source)
        if scope.split(".")[0] not in CONFIGURATION_ONLY
    ]

    assert offenders == [], (
        "accountant/web/app.py reads the module default outside configuration:\n  "
        + "\n  ".join(offenders)
        + f"\nA handler must use runtime().{THE_RIGHT_ANSWER}, which is measured "
        "off the live connection and checked against memory on every request. "
        "Reading the constant is how the app came to work for exactly one "
        "company name in the world."
    )


def test_the_two_configuration_readers_really_do_read_it() -> None:
    """An allowlist naming functions that do not read it excludes nothing.

    Without this, deleting the last honest read would leave `CONFIGURATION_ONLY`
    describing a rule about nothing, and the test above would pass over an
    app that had lost its default entirely.
    """
    scopes = {scope.split(".")[0] for _, scope in _reads_of("COMPANY", APP.read_text())}

    assert scopes == set(CONFIGURATION_ONLY), (
        f"the allowlist says {sorted(CONFIGURATION_ONLY)} read the default; "
        f"the file says {sorted(scopes)}"
    )


def test_the_reader_finds_a_read_it_is_given_and_ignores_the_definition() -> None:
    """A scan that finds nothing passes every assertion above at zero."""
    assert _reads_of("COMPANY", "COMPANY = 'x'\n") == []
    assert _reads_of("COMPANY", "def h():\n    return COMPANY\n") == [(2, "h")]
    assert _reads_of(
        "COMPANY",
        "class H:\n    def do(self):\n        return COMPANY\n",
    ) == [(3, "H.do")]
    assert _reads_of("COMPANY", "x = ENV_COMPANY\n") == [], "not a substring match"


def _scopes_naming_a_company(source: str) -> set[str]:
    """Every function that reads a company name from the live runtime.

    Both spellings count: `live.company` straight off the runtime, and a local
    `company = live.company` read afterwards. The point is which FUNCTIONS take
    their company from the runtime, not how many times each one says so.
    """
    seen: set[str] = set()

    def walk(node: ast.AST, scope: tuple[str, ...]) -> None:
        for child in ast.iter_child_nodes(node):
            named = (
                isinstance(child, ast.Attribute)
                and child.attr == THE_RIGHT_ANSWER
                and isinstance(child.ctx, ast.Load)
            ) or (
                isinstance(child, ast.Name)
                and child.id == THE_RIGHT_ANSWER
                and isinstance(child.ctx, ast.Load)
            )
            if named and scope:
                seen.add(".".join(scope))
            inner = scope
            if isinstance(child, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
                inner = (*scope, child.name)
            walk(child, inner)

    walk(ast.parse(source), ())
    return seen


def test_every_function_that_reaches_tally_takes_its_company_from_runtime() -> None:
    """The positive half.

    Forbidding the constant is not the same as using the right thing: a file
    that named no company at all would pass the test above, and be a different
    broken app.
    """
    naming = _scopes_naming_a_company(APP.read_text(encoding="utf-8"))

    for scope in ("render_home", "_run", "Handler.do_POST"):
        assert scope in naming, (
            f"accountant/web/app.py::{scope} reaches Tally but never reads a "
            f"company off the runtime"
        )
