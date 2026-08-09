"""`serve()` itself — the code path a person actually runs, and nothing else.

THE BUG THIS FILE EXISTS FOR
----------------------------
`accountant/web/app.py::serve()` is the process entry point. Until 2026-08-09 it
never called `connect()`, so `python -m accountant.web.app` — the exact command
in README.md — started a server on which EVERY page answered "REAL TALLY
REQUIRED". The product could not be run at all, and the whole suite was green.

No existing test could have caught it. Every web test calls
`app.configure(client, identity)` and injects a double, so none of them ever
executes `serve()`'s own body. That is the gap this file closes: the chain
`serve() -> connect() -> real_tally() -> RealTally over HTTP -> identity check
-> app available`, driven end to end with nothing injected.

WHAT IS AND IS NOT A DOUBLE HERE
--------------------------------
`StubTally` is a mock of TALLY, not of `TallyClient`. It is a stdlib
`http.server` on an ephemeral port that speaks the XML dialect
`accountant/tallyio/real.py` builds and parses. The real `TallyConfig`, the real
`RealTally`, the real `urllib` transport, the real hardened XML parser, the real
identity check and the real bootstrap all run. The seam is one layer lower than
the client, which is the whole point: everything above the socket is production
code.

The only other seam is `app.HTTPServer`, replaced by a factory that builds a
GENUINE `HTTPServer` and hands the test a handle to it. `serve()` calls
`serve_forever()` and keeps no reference, so without that handle the suite could
neither stop the server nor prove one was never opened.

WHAT THIS FILE DOES NOT PROVE
-----------------------------
Anything about TallyPrime. `StubTally` answers the shapes this project BUILDS
and PARSES; a real Tally may answer differently, and every claim about a real
one lives in `accountant/tallyio/real.py`'s ASSUMPTIONS list and in
`docs/PROJECT_STATE.md`. It also proves nothing about the CONTENT of the books —
only that the numbers `/health` reports were read from the connection that was
actually made, rather than invented.
"""

from __future__ import annotations

import contextlib
import importlib.util
import json
import re
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from collections.abc import Generator, Iterator, Sequence
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import cast

import pytest

from accountant.memory.store import BootstrapStatus
from accountant.tallyio import real
from accountant.tallyio.factory import RealTallyRequired
from accountant.web import app

# Every wait in this file is bounded by this. `serve()` calls `serve_forever()`,
# so a test that waits without a deadline does not fail, it hangs the suite.
HARD_TIMEOUT = 15.0

REPO = Path(__file__).resolve().parent.parent
README = REPO / "README.md"


# ---------------------------------------------------------------------------
# a stub TALLY - the XML dialect, over a real socket
# ---------------------------------------------------------------------------
#
# Every shape below is derived from accountant/tallyio/real.py: the request
# builders say what is sent, the parsers say what must come back. Nothing here
# is guessed.


@dataclass(frozen=True)
class StubVoucher:
    """One row of posted history, in the shape Tally exports it."""

    master_id: str
    date: str  # YYYYMMDD, the child-tag form _date_from_tally reads
    party: str
    narration: str
    debit: str
    credit: str
    rupees: str


# Three ledgers, four vouchers, two vendors. The three numbers are deliberately
# different from each other so that a `/health` that returned a constant could
# not satisfy all three at once.
LEDGERS: tuple[str, ...] = ("Purchases", "Repairs", "Cash")

HISTORY: tuple[StubVoucher, ...] = (
    StubVoucher(
        "1", "20260401", "Sharma Traders", "cement", "Purchases", "Cash", "1180.00"
    ),
    StubVoucher(
        "2", "20260402", "Sharma Traders", "cement", "Purchases", "Cash", "1180.00"
    ),
    StubVoucher(
        "3", "20260403", "Verma Cement", "roof repair", "Repairs", "Cash", "900.00"
    ),
    StubVoucher(
        "4", "20260404", "Verma Cement", "roof repair", "Repairs", "Cash", "900.00"
    ),
)

VENDORS = 2
MAPPINGS = 2

# What those four vouchers leave on a trial balance, debit positive, with the
# Dr/Cr suffix A8 treats as authoritative. They sum to zero, as books do.
BALANCES: tuple[tuple[str, str], ...] = (
    ("Purchases", "2360.00 Dr"),
    ("Repairs", "1800.00 Dr"),
    ("Cash", "4160.00 Cr"),
)

_COLLECTION_ID = re.compile(r"<ID>([^<]*)</ID>")
_IMPORT = "<TALLYREQUEST>Import</TALLYREQUEST>"


def _collection_of(request: str) -> str:
    found = _COLLECTION_ID.search(request)
    return found.group(1) if found is not None else ""


def _collections(requests: Sequence[str]) -> set[str]:
    return {_collection_of(request) for request in requests}


def _imports(requests: Sequence[str]) -> list[str]:
    """Every request that would have CHANGED somebody's books."""
    return [request for request in requests if _IMPORT in request]


def _envelope(data: str, *, desc: str = "") -> str:
    return (
        "<ENVELOPE><HEADER><STATUS>1</STATUS></HEADER>"
        f"<BODY><DESC>{desc}</DESC><DATA><COLLECTION>{data}</COLLECTION></DATA>"
        "</BODY></ENVELOPE>"
    )


def _companies_xml(names: Sequence[str]) -> str:
    return _envelope("".join(f'<COMPANY NAME="{name}"></COMPANY>' for name in names))


def _ledgers_xml(names: Sequence[str]) -> str:
    return _envelope(
        "".join(
            f'<LEDGER NAME="{name}" RESERVEDNAME=""><PARENT>Primary</PARENT></LEDGER>'
            for name in names
        )
    )


def _balances_xml() -> str:
    return _envelope(
        "".join(
            f'<LEDGER NAME="{name}" RESERVEDNAME="">'
            f"<CLOSINGBALANCE>{amount}</CLOSINGBALANCE></LEDGER>"
            for name, amount in BALANCES
        )
    )


def _leg(ledger: str, amount: str, *, debit: bool) -> str:
    """A1: a debit is a NEGATIVE amount with ISDEEMEDPOSITIVE=Yes."""
    return (
        "<ALLLEDGERENTRIES.LIST>"
        f"<LEDGERNAME>{ledger}</LEDGERNAME>"
        f"<ISDEEMEDPOSITIVE>{'Yes' if debit else 'No'}</ISDEEMEDPOSITIVE>"
        f"<AMOUNT>{'-' if debit else ''}{amount}</AMOUNT>"
        "</ALLLEDGERENTRIES.LIST>"
    )


def _vouchers_xml(vouchers: Sequence[StubVoucher]) -> str:
    rows = "".join(
        f'<VOUCHER VCHTYPE="Journal">'
        f"<DATE>{v.date}</DATE>"
        f"<VOUCHERNUMBER>{v.master_id}</VOUCHERNUMBER>"
        f"<VOUCHERTYPENAME>Journal</VOUCHERTYPENAME>"
        f"<NARRATION>{v.narration}</NARRATION>"
        f"<MASTERID>{v.master_id}</MASTERID>"
        f"<PARTYLEDGERNAME>{v.party}</PARTYLEDGERNAME>"
        f"{_leg(v.debit, v.rupees, debit=True)}"
        f"{_leg(v.credit, v.rupees, debit=False)}"
        "</VOUCHER>"
        for v in vouchers
    )
    # The CMPINFO counter that cost a day on 2026-08-08: every real response
    # carries <VOUCHER>0</VOUCHER> as a COUNT outside the DATA block. It is here
    # so this stub reproduces the trap rather than a tidier Tally than exists.
    return _envelope(rows, desc="<CMPINFO><VOUCHER>0</VOUCHER></CMPINFO>")


# An Import must never happen during startup. If one does, the stub refuses it
# AND records it, so the test can name the write instead of inferring it.
_REFUSED_IMPORT = (
    "<ENVELOPE><BODY><DATA><IMPORTRESULT>"
    "<CREATED>0</CREATED><ALTERED>0</ALTERED><DELETED>0</DELETED>"
    "<IGNORED>0</IGNORED><ERRORS>1</ERRORS><EXCEPTIONS>0</EXCEPTIONS>"
    "<LINEERROR>the stub Tally refuses every write</LINEERROR>"
    "</IMPORTRESULT></DATA></BODY></ENVELOPE>"
)


class StubTally(HTTPServer):
    """TallyPrime's XML gateway, reduced to what startup asks it for.

    A mock of TALLY, not of `TallyClient`. `requests` keeps every raw envelope
    received, which is how "nothing was written" is measured at the boundary
    that matters rather than asserted about our own state.
    """

    def __init__(
        self,
        address: tuple[str, int],
        handler: type[BaseHTTPRequestHandler],
        *,
        companies: Sequence[str],
    ) -> None:
        self.companies = tuple(companies)
        self.requests: list[str] = []
        super().__init__(address, handler)


class _StubHandler(BaseHTTPRequestHandler):
    def do_POST(self) -> None:  # the stdlib spells the hook this way
        tally = cast("StubTally", self.server)
        length = int(self.headers.get("Content-Length", "0"))
        request = self.rfile.read(length).decode()
        tally.requests.append(request)

        collection = _collection_of(request)
        if _IMPORT in request:
            body = _REFUSED_IMPORT
        elif collection == real.COLLECTION_COMPANIES:
            body = _companies_xml(tally.companies)
        elif collection == real.COLLECTION_LEDGERS:
            body = _ledgers_xml(LEDGERS)
        elif collection == real.COLLECTION_BALANCES:
            body = _balances_xml()
        elif collection == real.COLLECTION_VOUCHERS:
            body = _vouchers_xml(HISTORY)
        else:
            # Recorded in `requests`, so the assertion on which collections were
            # asked for names it instead of a parse failure three layers up.
            body = "<ENVELOPE/>"

        raw = body.encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/xml; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def log_message(self, format: str, *args: object) -> None:  # quiet
        del format, args


@contextlib.contextmanager
def _stub_tally(*, companies: Sequence[str] = (app.COMPANY,)) -> Generator[StubTally]:
    tally = StubTally(("127.0.0.1", 0), _StubHandler, companies=companies)
    thread = threading.Thread(target=tally.serve_forever, daemon=True)
    thread.start()
    try:
        yield tally
    finally:
        tally.shutdown()
        tally.server_close()
        thread.join(timeout=HARD_TIMEOUT)


# ---------------------------------------------------------------------------
# ports, and running serve() where it cannot hang the suite
# ---------------------------------------------------------------------------


def _port_of(server: HTTPServer) -> int:
    return int(cast("tuple[str, int]", server.server_address)[1])


def _free_ports(count: int) -> tuple[int, ...]:
    """Ports nothing is listening on. Bound together so they cannot collide."""
    probes = [socket.socket() for _ in range(count)]
    try:
        for probe in probes:
            probe.bind(("127.0.0.1", 0))
        return tuple(int(probe.getsockname()[1]) for probe in probes)
    finally:
        for probe in probes:
            probe.close()


def _port_is_bindable(port: int) -> bool:
    """True when nothing holds the port. No SO_REUSEADDR: a listening socket
    must make this fail, which is the whole measurement."""
    with socket.socket() as probe:
        try:
            probe.bind(("127.0.0.1", port))
        except OSError:
            return False
    return True


@dataclass
class Startup:
    """One `serve()` call on a daemon thread, and whatever it raised."""

    thread: threading.Thread
    finished: threading.Event
    failures: list[BaseException]

    def failure(self, timeout: float = HARD_TIMEOUT) -> BaseException | None:
        assert self.finished.wait(timeout=timeout), (
            f"serve() neither raised nor returned within {timeout}s, so it is "
            "still serving. A startup that cannot reach Tally must refuse, not "
            "open for business."
        )
        return self.failures[0] if self.failures else None


def _start_serve(
    port: int, config: real.TallyConfig, company: str = app.COMPANY
) -> Startup:
    """`serve()` itself, on a daemon thread so `serve_forever()` cannot hang us."""
    finished = threading.Event()
    failures: list[BaseException] = []

    def run() -> None:
        try:
            app.serve("127.0.0.1", port, tally=config, company=company)
        except Exception as exc:  # recorded, then asserted on by type and text
            failures.append(exc)
        finally:
            finished.set()

    thread = threading.Thread(target=run, daemon=True)
    started = Startup(thread=thread, finished=finished, failures=failures)
    thread.start()
    return started


def _config_for(tally: StubTally) -> real.TallyConfig:
    return real.TallyConfig(
        host="127.0.0.1",
        port=_port_of(tally),
        timeout_seconds=5.0,
        retries=1,
        retry_backoff_seconds=0.0,
    )


def _closed_config(port: int) -> real.TallyConfig:
    """A real TallyConfig aimed at a port nothing answers on."""
    return real.TallyConfig(
        host="127.0.0.1",
        port=port,
        timeout_seconds=2.0,
        retries=1,
        retry_backoff_seconds=0.0,
    )


def _health(base: str) -> tuple[int, dict[str, object]]:
    """Status AND body: an unready service answers 503, and urlopen raises on it."""
    try:
        with urllib.request.urlopen(f"{base}/health", timeout=HARD_TIMEOUT) as r:  # noqa: S310
            return r.status, cast("dict[str, object]", json.loads(r.read().decode()))
    except urllib.error.HTTPError as exc:
        return exc.code, cast("dict[str, object]", json.loads(exc.read().decode()))


def _wait_until_serving(base: str) -> tuple[int, dict[str, object]]:
    """Poll until the app answers over HTTP, or fail on a deadline."""
    deadline = time.monotonic() + HARD_TIMEOUT
    last: OSError | None = None
    while time.monotonic() < deadline:
        try:
            return _health(base)
        except OSError as exc:  # connection refused while the thread starts up
            last = exc
            time.sleep(0.05)
    raise AssertionError(
        f"serve() never answered on {base} within {HARD_TIMEOUT}s: {last}"
    )


def _page(base: str) -> str:
    with urllib.request.urlopen(f"{base}/", timeout=HARD_TIMEOUT) as r:  # noqa: S310
        return r.read().decode()


@pytest.fixture
def watched_servers(monkeypatch: pytest.MonkeyPatch) -> Iterator[list[HTTPServer]]:
    """Every HTTPServer `serve()` builds — a real one, plus a handle to it.

    `serve()` does `HTTPServer(...).serve_forever()` and keeps no reference, so
    a test can neither stop it nor say it was never opened. The factory builds
    the genuine class; only the handle is new.
    """
    opened: list[HTTPServer] = []

    def build(
        address: tuple[str, int], handler: type[BaseHTTPRequestHandler]
    ) -> HTTPServer:
        server = HTTPServer(address, handler)
        opened.append(server)
        return server

    monkeypatch.setattr(app, "HTTPServer", build)
    yield opened

    for server in opened:
        # shutdown() waits on an event serve_forever() sets, so it deadlocks on a
        # server that never started serving. Bounded, then closed either way.
        stopper = threading.Thread(target=server.shutdown, daemon=True)
        stopper.start()
        stopper.join(timeout=HARD_TIMEOUT)
        server.server_close()


@pytest.fixture(autouse=True)
def no_runtime_leaks() -> Iterator[None]:
    """`_runtime_state` is module-global. No test here may inherit or leave one."""
    app.disconnect()
    app.DRAFTS.clear()
    yield
    app.disconnect()
    app.DRAFTS.clear()


# ---------------------------------------------------------------------------
# 1. the happy path, with nothing injected
# ---------------------------------------------------------------------------


def test_serve_connects_to_a_real_tally_over_http_and_then_answers_requests(
    watched_servers: list[HTTPServer],
) -> None:
    """The chain the product runs on, end to end, through a real socket.

    Every count asserted below equals what the stub actually served. That is
    what makes `ready` a MEASUREMENT: three, four and two are different numbers,
    so no constant satisfies them together.
    """
    with _stub_tally() as tally:
        (serve_port,) = _free_ports(1)
        started = _start_serve(serve_port, _config_for(tally))
        base = f"http://127.0.0.1:{serve_port}"

        status, body = _wait_until_serving(base)

        assert status == 200, "a connected, bootstrapped app answers /health with 200"
        assert app.connected() is True
        assert body["ready"] is True
        assert body["bootstrap_status"] == BootstrapStatus.READY.value
        assert body["failure_code"] is None
        assert body["backend"] == "RealTally"
        assert body["endpoint"] == f"http://127.0.0.1:{_port_of(tally)}"
        assert body["company_exists"] is True
        assert body["companies_visible"] == 1
        assert body["accounts_read"] == len(LEDGERS)
        assert body["vouchers_read"] == len(HISTORY)
        assert body["vendor_mappings_derived"] == MAPPINGS

        # The app is not merely up, it is usable: the home page renders off the
        # live runtime and names the backend it is really on.
        page = _page(base)
        assert "writing into <b>RealTally</b>" in page
        assert "Not real accounting software" not in page

        # The genuine transport really ran, and startup read only.
        #
        # An EXACT set, not a subset. It caught the licence read the moment that
        # landed - which is the whole point: any new traffic on the startup path
        # has to be added here deliberately, by somebody who has thought about
        # whether a customer's Tally should be answering it during boot.
        #
        # $$LicenseInfo:IsEducationalMode is the one added since. It fails
        # CLOSED to `unknown`, sends at most one round trip when the gateway
        # cannot answer, and never raises into startup. The stub does not
        # implement it, so this assertion also proves the read genuinely
        # tolerates a Tally that refuses it - which is what the live TallyPrime
        # here actually does.
        assert _collections(tally.requests) == {
            real.COLLECTION_COMPANIES,
            real.COLLECTION_LEDGERS,
            real.COLLECTION_VOUCHERS,
            real.COLLECTION_BALANCES,
            "$$LicenseInfo:IsEducationalMode",
        }
        assert _imports(tally.requests) == []

    assert started.thread.is_alive(), "serve() returned instead of serving"
    assert len(watched_servers) == 1, "serve() opened exactly one listening socket"


# ---------------------------------------------------------------------------
# 2. Tally unreachable
# ---------------------------------------------------------------------------


def test_serve_refuses_to_start_when_tally_is_not_listening_and_writes_nothing(
    watched_servers: list[HTTPServer],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """The raise is not the requirement. The state afterwards is.

    An app that raises and still reports ready, or raises and still leaves a
    file behind, has failed closed in the only sense that is easy to check and
    none of the senses that matter.
    """
    monkeypatch.chdir(tmp_path)
    serve_port, closed_port = _free_ports(2)

    started = _start_serve(serve_port, _closed_config(closed_port))
    failure = started.failure()

    assert isinstance(failure, RealTallyRequired), f"serve() raised {failure!r}"

    # The text has to tell a person what to go and look at. Quoted from the
    # messages in factory.real_tally and real.HttpTransport.send.
    message = str(failure)
    assert app.REFUSAL in message
    assert f"http://127.0.0.1:{closed_port}" in message, "the error must name the port"
    assert "Check that TallyPrime is running" in message
    assert "HTTP Server" in message

    assert app.connected() is False

    health = app.health()
    assert health["ready"] is False
    assert health["failure_code"] is not None
    assert health["failure_code"] == "NO_RUNTIME"
    assert health["backend"] is None

    # Nothing was written anywhere: no runtime, no draft, no listening socket,
    # and not one file in the directory the process was started from.
    assert app.DRAFTS == {}
    assert watched_servers == [], "a refused startup still opened a server"
    assert list(tmp_path.rglob("*")) == [], "a refused startup wrote a file"


# ---------------------------------------------------------------------------
# 3. nothing is left holding the port
# ---------------------------------------------------------------------------


def test_a_refused_startup_leaves_no_server_listening_on_the_port(
    watched_servers: list[HTTPServer],
) -> None:
    """A half-started process that holds a port is worse than one that exits.

    Asserted twice, because the two claims are different: the HTTPServer was
    never constructed, AND the port is genuinely free afterwards. The first
    could be true while something else held the socket; the second is what a
    person restarting the app actually runs into.
    """
    serve_port, closed_port = _free_ports(2)

    started = _start_serve(serve_port, _closed_config(closed_port))
    failure = started.failure()

    assert isinstance(failure, RealTallyRequired), f"serve() raised {failure!r}"
    assert watched_servers == [], "serve() constructed an HTTPServer despite refusing"
    assert _port_is_bindable(serve_port), (
        f"port {serve_port} is still held after a startup that refused"
    )


# ---------------------------------------------------------------------------
# 4. the README command is real
# ---------------------------------------------------------------------------

_RUN_COMMAND = re.compile(r"^\s*python -m ([A-Za-z_][A-Za-z0-9_.]*)", re.MULTILINE)


def _captured(stream: str | bytes | None) -> str:
    """Whatever a killed subprocess managed to emit, in whichever form."""
    if stream is None:
        return ""
    return stream if isinstance(stream, str) else stream.decode(errors="replace")


def test_the_readme_tells_a_person_to_run_a_module_that_python_can_find() -> None:
    """A README that documents a command nobody can run is the bug itself.

    The module name is READ OUT of README.md rather than written here, so the
    two cannot drift apart: renaming the module without editing the README
    fails this test.
    """
    named = sorted(set(_RUN_COMMAND.findall(README.read_text(encoding="utf-8"))))
    assert named, "README names no `python -m` command at all"
    # NOT `== ["accountant.web.app"]`. Hardcoding the answer here would defeat
    # the docstring above: the test would assert that the README says a
    # particular string, not that the string it says WORKS. Both
    # `accountant.web` and `accountant.web.app` are runnable, and pinning one
    # of them turned this red the moment the README was shortened to the form
    # a person actually types. What must hold is runnability, so that is what
    # is asserted, for EVERY command the README names.
    for module in named:
        spec = importlib.util.find_spec(module)
        assert spec is not None, f"README says to run {module}, which does not exist"
        assert spec.origin is not None and Path(spec.origin).is_file()
        if spec.submodule_search_locations is not None:
            # A package. `python -m pkg` needs pkg.__main__, and without it
            # Python answers "cannot be directly executed", which tells a
            # non-programmer nothing.
            assert importlib.util.find_spec(f"{module}.__main__") is not None, (
                f"README says `python -m {module}`, but {module} is a package "
                f"with no __main__ module, so that command cannot run"
            )

    module = named[0]

    # runpy is the machinery behind `-m`. A clean interpreter must resolve and
    # load the module under that exact name or the command cannot start.
    probe = subprocess.run(  # noqa: S603
        [
            sys.executable,
            "-c",
            f"import runpy; runpy.run_module({module!r}, "
            "run_name='__readme_probe__', alter_sys=True); print('LOADED')",
        ],
        capture_output=True,
        text=True,
        timeout=HARD_TIMEOUT,
        check=False,
        cwd=REPO,
    )
    assert probe.returncode == 0, probe.stderr
    assert "LOADED" in probe.stdout


def test_running_the_readme_command_reaches_the_apps_own_entry_point() -> None:
    """Resolving is not running. This runs it, exactly as written.

    With no Tally on the default port the command must refuse in the terminal —
    which is a working command doing its job, not a broken one. The failure this
    catches is the opposite: a command that dies before it reaches any code of
    ours.
    """
    argv = [sys.executable, "-m", "accountant.web.app"]
    try:
        finished = subprocess.run(  # noqa: S603
            argv,
            capture_output=True,
            text=True,
            timeout=HARD_TIMEOUT,
            check=False,
            cwd=REPO,
        )
    except subprocess.TimeoutExpired as still_running:
        # Still running means it is SERVING. That is only legitimate if it
        # connected first and said so; a server with no banner behind it is the
        # original bug, wearing the same silence.
        still_up = _captured(still_running.stdout) + _captured(still_running.stderr)
        assert "Accountant Dad -> http://" in still_up, (
            f"{' '.join(argv)} was still running after {HARD_TIMEOUT}s and never "
            f"printed the banner that says it connected. Captured: {still_up!r}"
        )
        return

    output = finished.stdout + finished.stderr
    assert "No module named" not in output, output
    assert app.REFUSAL in output, output
    assert finished.returncode != 0, "a startup that never started must exit non-zero"


# ---------------------------------------------------------------------------
# 5. the other way startup refuses, measured at the Tally boundary
# ---------------------------------------------------------------------------


def test_startup_touches_nobodys_books_when_the_company_is_not_open(
    watched_servers: list[HTTPServer],
) -> None:
    """Tally answers, and the answer is somebody else's company.

    This is the "nothing was written" claim measured where it counts — at Tally
    itself. The stub records every envelope it received, so the assertion is
    that no Import ever arrived, not that our own state looks unchanged.
    """
    with _stub_tally(companies=("Somebody Elses Books",)) as tally:
        (serve_port,) = _free_ports(1)
        started = _start_serve(serve_port, _config_for(tally))
        failure = started.failure()
        seen = list(tally.requests)

    assert isinstance(failure, RealTallyRequired), f"serve() raised {failure!r}"
    assert "is not open in Tally" in str(failure)
    assert "Somebody Elses Books" in str(failure), "the error must name what IS open"

    assert app.connected() is False
    assert app.health()["ready"] is False
    assert _collections(seen) == {real.COLLECTION_COMPANIES}, (
        "startup read past the identity check it had already failed"
    )
    assert _imports(seen) == []
    assert watched_servers == []
    assert _port_is_bindable(serve_port)
