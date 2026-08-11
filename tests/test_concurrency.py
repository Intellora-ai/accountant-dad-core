"""Two people at once — Task 11, 2026-08-11.

THE DEFECT THIS FILE EXISTS FOR
-------------------------------
`accountant/web/app.py::serve()` ended with `HTTPServer(...).serve_forever()`.
`HTTPServer` handles ONE request at a time: it accepts a connection, runs the
whole handler, and only then looks at the next. So two customers, or one
customer with two tabs, queued behind each other, and a Tally call that hung
took the entire product down for everybody until it timed out.

Swapping in `ThreadingHTTPServer` fixes the queue and wakes up everything that
was safe only because requests were serialised. Those hazards are the real
subject of this file, and they are three:

    the connection   `sqlite3.connect` defaults to `check_same_thread=True`.
                     The store is opened on the startup thread and used by
                     whatever thread the next request lands on, so the FIRST
                     request on a new thread raised `ProgrammingError` before a
                     line of handler code ran.
    the caches       `DRAFTS`, `DRAFT_TENANT`, `BATCHES` and
                     `_recorded_mismatches` are module-level dictionaries with
                     check-then-act inside them: evict-oldest, get-then-pop,
                     record-once. Each fails differently and none of them errors
                     in a way a customer could report.
    the boundary     one dictionary shared by every request in the process is a
                     cache with no owner on it. Two colleagues in one company
                     sharing a half-finished entry is the design; two CUSTOMERS
                     sharing one is not.

WHAT IS PROVED BY MEASUREMENT, NOT BY HOPE
------------------------------------------
Nothing here asserts on elapsed time. "Two requests overlapped" is proved with a
`threading.Barrier`, which cannot be satisfied unless both requests are inside
the server at the same instant — on a single-threaded server the second never
arrives, the barrier breaks on its own deadline, and the test fails for the
right reason rather than on a slow machine.

WHAT THIS FILE DOES NOT PROVE
-----------------------------
Anything about real TallyPrime. The backend is `FakeTally` through
`tests/test_web.py::serving`, which is the ONE spin-up path in this suite — a
second threaded server here would be a second definition of what a running app
is, and the two would drift.

Anything about two PROCESSES. SQLite locks the whole file for a write, so a
second web worker is a different question and a deliberately deferred one:
`docs/OWNER_WORK.md`, "PostgreSQL migration".
"""

from __future__ import annotations

import ast
import concurrent.futures
import re
import sys
import threading
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest

from accountant import reversal
from accountant.extract.adapter import ExtractedRecord, TypedTextExtractor
from accountant.memory.store import MemoryStore
from accountant.web import app
from accountant.web.app import draft_for
from tests.test_auth import ALPHA, BETA, tenants
from tests.test_web import demo_company, fake_backend, serving

# Every wait in this file is bounded. A concurrency test that waits without a
# deadline does not fail when it is wrong, it hangs the suite.
DEADLINE = 10.0

REPO = Path(__file__).resolve().parent.parent


@pytest.fixture(autouse=True)
def production_auth(monkeypatch: pytest.MonkeyPatch) -> None:
    """This file runs with authentication REQUIRED, like `tests/test_auth.py`.

    `tests/conftest.py` sets LOCAL_DEV_MODE=1 for the whole suite, and in that
    mode EVERY request resolves to the one local-dev tenant. A tenant boundary
    cannot be measured when there is only one tenant, so the variable is deleted
    — and deleted rather than set to "0", because unset is the case that ships.

    Autouse, so a test added here later cannot silently inherit dev mode and
    pass by not distinguishing anybody from anybody.
    """
    monkeypatch.delenv(app.ENV_LOCAL_DEV_MODE, raising=False)
    # WHOSE books this server serves. Defect J1, 2026-08-11: the tenant check
    # had no caller, so any live session reached any company's books. It fails
    # closed now - a server that has not been told refuses everybody - so a
    # file running with authentication required has to say, exactly as a
    # deployment does.
    #
    # ALPHA, and that is what makes the tenant-isolation tests below measure
    # something rather than nothing: BETA is refused at the door, which is an
    # outer refusal in front of the draft-ownership check they are about.
    monkeypatch.setenv(app.ENV_TENANT, ALPHA)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
#
# `tenants` comes from `tests/test_auth.py` rather than being written again
# here. Two definitions of "two tenants, one user each" is how two files end up
# disagreeing about what isolation means.


def seeding(*tokens: tuple[str, str]) -> Callable[[MemoryStore], None]:
    def seed(store: MemoryStore) -> None:
        tenants(store, *tokens)

    return seed


def send(base: str, path: str, token: str, **fields: str) -> tuple[int, str]:
    """POST with a session cookie, returning the status even when it refuses."""
    body = urllib.parse.urlencode(fields).encode()
    request = urllib.request.Request(base + path, data=body)  # noqa: S310
    request.add_header("Cookie", f"{app.COOKIE}={token}")
    try:
        with urllib.request.urlopen(request, timeout=DEADLINE) as answer:  # noqa: S310
            return answer.status, answer.read().decode()
    except urllib.error.HTTPError as refused:
        return refused.status or 0, refused.read().decode()


def draft_on(page: str) -> str:
    found = re.search(r'name=draft value="([^"]+)"', page)
    assert found, f"no draft id in page:\n{page[:600]}"
    return found.group(1)


def problem_on(page: str) -> str:
    found = re.search(r'name=problem value="([^"]+)"', page)
    assert found, f"no problem id in page:\n{page[:600]}"
    return found.group(1)


class BarrierExtractor:
    """A reader that will not answer until `parties` requests are inside it.

    THE MEASUREMENT, AND WHY IT IS NOT A STOPWATCH. Timing says "the second
    request finished soon after the first", which is true of a fast serial
    server and false of a slow parallel one — it measures the machine, not the
    architecture. A barrier says something a serial server cannot make true at
    any speed: request two entered `extract` BEFORE request one left it.

    On a single-threaded server the second request never arrives, so the first
    waits out `DEADLINE` and `Barrier.wait` raises `BrokenBarrierError`. That is
    recorded in `broken` rather than raised onward, because
    `registry.guarded` catches everything a backend throws and turns it into an
    outage record — the test would otherwise see a tidy 200 and no explanation.

    The seam is `configure(extractor=...)`, which `serving` already exposes, so
    nothing about the shipped request path is replaced.
    """

    name = "barrier_over_typed_text"

    def __init__(self, parties: int, patience: float = DEADLINE) -> None:
        self.barrier = threading.Barrier(parties)
        self.overlapped = threading.Event()
        self.broken: list[str] = []
        # `patience` is SHORTER than the client's own timeout wherever the
        # barrier is meant to break. Equal deadlines mean the request and the
        # thing it is waiting for give up in the same instant, and the test then
        # fails on a dropped socket instead of reporting what it measured.
        self.patience = patience
        self._inner = TypedTextExtractor()

    def extract(self, data: bytes, mime: str, /) -> ExtractedRecord:
        try:
            self.barrier.wait(timeout=self.patience)
        except threading.BrokenBarrierError:
            self.broken.append(
                "the barrier never filled: a second request did not reach the "
                "reader while the first was still inside it, so the server "
                "served one request at a time"
            )
        else:
            self.overlapped.set()
        return self._inner.extract(data, mime)


# ---------------------------------------------------------------------------
# 1. two requests really are served at the same time
# ---------------------------------------------------------------------------


def test_two_simultaneous_requests_are_both_inside_the_server_at_once() -> None:
    """The defect, stated as the thing that could not happen before.

    Both requests must be inside `extract` together for the barrier to release.
    Under `HTTPServer` the second one is still sitting in the accept queue while
    the first blocks, so the barrier cannot fill, and the failure is the exact
    sentence `BarrierExtractor` records rather than a timing wobble.
    """
    token = app.new_token()
    reader = BarrierExtractor(parties=2)

    with (
        serving(
            demo_company(),
            fake_backend(),
            extractor=reader,
            seed=seeding((token, ALPHA)),
        ) as base,
        concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool,
    ):
        both = [
            pool.submit(
                send,
                base,
                "/entry",
                token,
                text=f"paid Gupta Hardware {n}00 for tools",
            )
            for n in (11, 12)
        ]
        answers = [f.result(timeout=DEADLINE * 2) for f in both]

    assert reader.broken == [], reader.broken
    assert reader.overlapped.is_set(), "neither request ever released the barrier"
    assert [status for status, _body in answers] == [200, 200]


def test_a_slow_request_does_not_hold_up_an_unrelated_one() -> None:
    """The customer-visible half of the same fact.

    One request is parked inside the reader on a barrier that will never fill on
    its own. `/health` — which touches neither the reader nor the caches — must
    answer while it is parked. Serially it could not: the health check would sit
    behind the stuck entry for the whole of `DEADLINE`.
    """
    token = app.new_token()
    # Three parties, two of which never arrive, so the entry request stays
    # parked and the health check has to overtake it. It gives up well inside
    # its own client timeout, so the entry still returns a page at the end.
    parked = 3.0
    reader = BarrierExtractor(parties=3, patience=parked)

    with (
        serving(
            demo_company(),
            fake_backend(),
            extractor=reader,
            seed=seeding((token, ALPHA)),
        ) as base,
        concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool,
    ):
        stuck = pool.submit(
            send, base, "/entry", token, text="paid Gupta Hardware 1500 for tools"
        )
        health = pool.submit(
            lambda: (
                urllib.request.urlopen(  # noqa: S310
                    base + "/health", timeout=DEADLINE
                ).status
            )
        )
        # Answered inside the window the other request is still parked for.
        # A serial server cannot do that at any speed: the health check
        # would not be looked at until the entry had given up.
        assert health.result(timeout=parked) == 200, (
            "/health waited for a request that was blocked in the reader"
        )
        stuck.result(timeout=DEADLINE * 2)

    assert reader.broken, "the parked request was supposed to time out on the barrier"


# ---------------------------------------------------------------------------
# 2. SQLite, from a thread that did not open the connection
# ---------------------------------------------------------------------------


def test_a_write_from_a_thread_that_did_not_open_the_store_succeeds(
    tmp_path: Path,
) -> None:
    """The exact `ProgrammingError` a threaded server hits on its FIRST request.

    Opened here, written there. With `check_same_thread` left at its default the
    call below raises

        sqlite3.ProgrammingError: SQLite objects created in a thread can only be
        used in that same thread

    and the row never exists. Driven directly rather than over HTTP because the
    failure is a property of the CONNECTION, and a test that has to start a web
    server to see it is a test that names the wrong subject.
    """
    store = MemoryStore(tmp_path / "app.db")
    failures: list[BaseException] = []

    def write() -> None:
        try:
            store.create_tenant("tenant-from-another-thread", "Elsewhere", "2026-08-11")
        except BaseException as exc:  # recorded, then asserted on by type
            failures.append(exc)

    thread = threading.Thread(target=write)
    thread.start()
    thread.join(timeout=DEADLINE)

    assert failures == [], f"the write from a second thread raised {failures}"
    assert store.tenant("tenant-from-another-thread") is not None
    store.close()


def test_a_request_writes_the_audit_row_from_whichever_thread_served_it(
    tmp_path: Path,
) -> None:
    """The same fact through the shipped path, into a file a test can reopen.

    The store is opened on `serving`'s own thread; every request is served on a
    thread the server made afterwards. A durable row therefore proves the
    connection crossed a thread boundary and came back with the data.
    """
    token = app.new_token()
    db = tmp_path / "app.db"

    with serving(
        demo_company(),
        fake_backend(),
        seed=seeding((token, ALPHA)),
        store_path=db,
    ) as base:
        status, _body = send(
            base, "/entry", token, text="paid Gupta Hardware 1500 for tools"
        )
        assert status == 200

    after = MemoryStore(db)
    rows = after.actions(app.COMPANY)
    after.close()
    assert [r for r in rows if r.action == "asked"], (
        "the request was served but nothing reached the durable audit trail"
    )


def test_n_concurrent_posts_leave_exactly_n_rows_and_no_duplicates(
    tmp_path: Path,
) -> None:
    """No lost write, no double write. The conservation law of an audit trail.

    Eight entries, eight threads, one row each — because each entry names an
    unseen vendor and therefore ends UNCLEAR, which writes exactly one `asked`
    row and posts nothing to Tally. Seven rows means a write was lost; nine
    means one was applied twice; eight operation ids that are not all distinct
    means two threads built one entry between them.

    Distinct amounts, so the operation ids are distinct: an idempotency refusal
    would be a different measurement wearing this one's clothes.
    """
    token = app.new_token()
    db = tmp_path / "app.db"
    entries = [f"paid Gupta Hardware {1000 + n}00 for tools" for n in range(8)]

    with serving(
        demo_company(),
        fake_backend(),
        seed=seeding((token, ALPHA)),
        store_path=db,
    ) as base:
        ready = threading.Barrier(len(entries))

        def post(text: str) -> int:
            # Aligned on a barrier so the eight requests are genuinely
            # simultaneous rather than eight sequential ones a pool happened to
            # spread out.
            ready.wait(timeout=DEADLINE)
            status, _body = send(base, "/entry", token, text=text)
            return status

        with concurrent.futures.ThreadPoolExecutor(max_workers=len(entries)) as pool:
            statuses = [
                f.result(timeout=DEADLINE * 2)
                for f in [pool.submit(post, text) for text in entries]
            ]

    assert statuses == [200] * len(entries)

    after = MemoryStore(db)
    asked = [r for r in after.actions(app.COMPANY) if r.action == "asked"]
    after.close()

    assert len(asked) == len(entries), (
        f"{len(entries)} concurrent entries left {len(asked)} audit rows"
    )
    ids = [r.operation_id for r in asked]
    assert len(set(ids)) == len(entries), f"operation ids were not distinct: {ids}"


def test_two_threads_recording_the_same_vendor_lose_no_observation() -> None:
    """The one read-modify-write in the store, driven into its own race.

    `MemoryStore._record` reads the existing count, adds one, and writes it
    back. Unguarded, two threads both read `times=n`, both write `n+1`, and one
    observation is gone — no exception, no log line, just a memory that has seen
    less than it has seen. Two hundred calls across two threads must leave
    `times=200`.

    The switch interval is turned down for the same reason, and with the same
    honesty, as the bulk-confirmation test below. MEASURED, 2026-08-11, with the
    lock removed: at the default 5ms this failed on two runs in three, which is
    a flaky test rather than a guard. At `1e-6` it fails every time, because the
    interleaving it is looking for is the one CPython was declining to schedule.
    Restored in `finally`, so nothing else in the suite inherits it.
    """
    store = MemoryStore()
    rounds = 100
    start = threading.Barrier(2)

    def record() -> None:
        start.wait(timeout=DEADLINE)
        for _ in range(rounds):
            store.record_vendor(
                "acme", "sharma traders", "Purchases", provenance="a test"
            )

    previous = sys.getswitchinterval()
    sys.setswitchinterval(1e-6)
    try:
        threads = [threading.Thread(target=record) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=DEADLINE * 2)
    finally:
        sys.setswitchinterval(previous)

    (seen,) = store.vendor("acme", "sharma traders")
    store.close()
    assert seen.times == rounds * 2, (
        f"{rounds * 2} observations were recorded and the row remembers {seen.times}"
    )


# ---------------------------------------------------------------------------
# 3. the tenant boundary on the shared caches
# ---------------------------------------------------------------------------


def test_a_draft_typed_by_one_tenant_is_invisible_to_another(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`DRAFTS` is one dictionary for the whole process. This is its edge.

    TWO GUARDS, AND THIS TEST NOW MEASURES BOTH, 2026-08-11. Until the
    cross-tenant fix landed there was only the inner one: Beta reached
    `/answer`, `draft_for` refused her Alpha's draft, and she was told it had
    expired. Beta is now refused 403 at the door and never reaches the route at
    all.

    The inner guard is therefore asserted directly rather than over HTTP. It is
    not redundant and must not be deleted: one process serves one company
    TODAY, so the door check is the whole boundary — the day one process serves
    several, `draft_for` is what stops a draft crossing between them, and a
    guard that is only reachable in a future arrangement still has to be
    correct when it arrives.
    """
    alpha_token, beta_token = app.new_token(), app.new_token()
    db = tmp_path / "app.db"

    with serving(
        demo_company(),
        fake_backend(),
        seed=seeding((alpha_token, ALPHA), (beta_token, BETA)),
        store_path=db,
    ) as base:
        status, page = send(
            base, "/entry", alpha_token, text="paid Gupta Hardware 1500 for tools"
        )
        assert status == 200
        draft = draft_on(page)
        problem = problem_on(page)

        # THE OUTER GUARD. Beta never reaches the route.
        status, _refused = send(
            base, "/answer", beta_token, draft=draft, problem=problem, value="Purchases"
        )
        assert status == 403, "a foreign tenant reached a route on Alpha's server"
        assert app.DRAFTS[draft].answers == [], "Beta's answer reached Alpha's draft"

        # THE INNER GUARD, asked directly, because the outer one now stands in
        # front of every HTTP path to it.
        live = app.runtime()

        def from_another_customer() -> app.Principal:
            return app.Principal("somebody-else", BETA)

        # Through `current_principal`, which is the public name `draft_for`
        # actually reads. Reaching into the ContextVar itself would be testing
        # the storage rather than the seam, and pyright refuses the private
        # access for the same reason.
        #
        # `monkeypatch.context()` and NOT `monkeypatch.undo()`. undo() rolls
        # back everything this test's monkeypatch has done, which includes the
        # autouse fixture's LOCAL_DEV_MODE and ACCOUNTANT_TENANT - so the rest
        # of the test then ran in dev mode and Alpha was refused her own draft.
        # The context restores exactly the one thing it set.
        with monkeypatch.context() as swapped:
            swapped.setattr(app, "current_principal", from_another_customer)
            assert draft_for(draft, live) is None, (
                "draft_for handed a draft to a principal from another tenant"
            )

        # And the boundary is a boundary, not a wall: Alpha still owns it.
        status, mine = send(
            base,
            "/answer",
            alpha_token,
            draft=draft,
            problem=problem,
            value="Purchases",
        )
        assert status == 200
        assert "draft expired" not in mine
        assert app.DRAFTS[draft].answers == [(problem, "Purchases")]


def test_two_people_in_one_tenant_still_share_a_draft() -> None:
    """The other half, and the reason the check is at TENANT and not at user.

    Two colleagues in one accounts department are meant to be able to pick up
    each other's half-finished entry — that is the same argument `batch_for`
    makes in reverse for bulk reversals, where the guarantee being protected is
    "the person who confirms saw the list" and the key is therefore the USER.
    Copying the stricter key here would break a company that employs two people.
    """
    first, second = app.new_token(), app.new_token()

    with serving(
        demo_company(),
        fake_backend(),
        # Two sessions, both for ALPHA. `tenants` gives one user per tenant, so
        # these are two browsers of one tenant, which is the case being drawn.
        seed=seeding((first, ALPHA), (second, ALPHA)),
    ) as base:
        status, page = send(
            base, "/entry", first, text="paid Gupta Hardware 1500 for tools"
        )
        assert status == 200
        draft, problem = draft_on(page), problem_on(page)

        status, answered = send(
            base, "/answer", second, draft=draft, problem=problem, value="Purchases"
        )

    assert status == 200
    assert "draft expired" not in answered


def test_only_one_of_two_simultaneous_bulk_confirmations_is_honoured() -> None:
    """The get-then-pop race, and the only one on this list that WRITES.

    `batch_for` reads a previewed batch and then removes it, because a
    confirmation may be honoured exactly once. Serialised, the gap between those
    two lines did not exist. Threaded, two confirmations arriving together both
    found the batch present, both passed the owner check, and both went on to
    reverse every voucher in it.

    WHY THE SWITCH INTERVAL IS TURNED DOWN, WITH THE NUMBERS. The window
    between the read and the pop is a handful of bytecodes, and CPython only
    considers switching threads every 5ms by default — so at the default the
    unguarded version passes this test. MEASURED, 2026-08-11: with the lock
    removed and the default interval, 300 rounds gave 300 single confirmations
    and the defect was invisible. At `1e-6` the same 300 rounds gave 29 double
    confirmations. The interval is a scheduling knob, not a behaviour change:
    it makes an interleaving that a real machine can produce likely enough to
    observe, and it is restored in `finally` so no other test inherits it.

    Driven at the function rather than over HTTP because the two calls have to
    be aligned to the instruction, and two HTTP requests cannot be.

    The structural guard
    `test_every_shared_cache_in_the_web_app_is_written_under_the_lock` is the
    other half. This one says the behaviour is right; that one says it is right
    for the reason we think, which is what stops it regressing quietly.
    """
    rounds = 300
    previous = sys.getswitchinterval()
    sys.setswitchinterval(1e-6)
    try:
        with serving(demo_company(), fake_backend(), seed=seeding()) as _base:
            live = app.runtime()
            honoured = [
                _race_two_confirmations(f"batch-{n}", live) for n in range(rounds)
            ]
    finally:
        sys.setswitchinterval(previous)

    assert honoured == [1] * rounds, (
        "a batch was confirmed twice, so every voucher in it would have been "
        f"reversed twice: {sorted(set(honoured))}"
    )


def _race_two_confirmations(batch_id: str, live: app.Runtime) -> int:
    """Two aligned threads confirm one batch. How many were honoured."""
    batch = _a_batch(batch_id)
    app.BATCHES[batch.batch_id] = (batch, app.NOT_RECORDED)

    start = threading.Barrier(2)
    counted = threading.Lock()
    got: list[reversal.Batch] = []

    def confirm() -> None:
        start.wait(timeout=DEADLINE)
        answer = app.batch_for(batch_id, live)
        if answer is not None:
            with counted:
                got.append(answer)

    threads = [threading.Thread(target=confirm) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=DEADLINE)
    return len(got)


def _a_batch(batch_id: str) -> reversal.Batch:
    """A previewed batch owned by nobody, which is what an out-of-request
    caller is. `batch_for` compares that marker against the current principal,
    and outside a request both are `NOT_RECORDED`, so the owner check passes and
    the ONCE-ONLY check is the only thing left being measured."""
    return reversal.Batch(
        batch_id=batch_id,
        company=app.COMPANY,
        state=reversal.BatchState.PREVIEW,
        baseline={},
    )


# ---------------------------------------------------------------------------
# 4. the structural guards — what a behavioural test cannot reach
# ---------------------------------------------------------------------------


def _tree(path: str) -> ast.Module:
    return ast.parse((REPO / path).read_text(encoding="utf-8"))


def test_serve_builds_a_threading_server_and_says_its_threads_are_daemons() -> None:
    """AST, not a substring scan.

    Every structural test in this repository that matched text has eventually
    matched its own explanatory comment — `tests/test_connector.py` records the
    time it found the word `HTTPServer` inside a paragraph saying no HTTPServer
    is used. So this walks the tree of `serve()` itself.

    Two claims, because they are two different failures: a plain `HTTPServer`
    is the original defect back again, and a non-daemon thread pool means one
    request stuck on a Tally socket turns "stop the server" into "wait for
    Tally".
    """
    tree = _tree("accountant/web/app.py")
    serve = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name == "serve"
    )

    built = {
        node.func.id
        for node in ast.walk(serve)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }
    assert "ThreadingHTTPServer" in built, "serve() does not build a threading server"
    assert "HTTPServer" not in built, (
        "serve() builds a plain HTTPServer, which handles one request at a time"
    )

    daemons = [
        node
        for node in ast.walk(serve)
        if isinstance(node, ast.Assign)
        and any(
            isinstance(t, ast.Attribute) and t.attr == "daemon_threads"
            for t in node.targets
        )
    ]
    assert daemons, "serve() never states whether its request threads are daemons"
    assert all(
        isinstance(a.value, ast.Constant) and a.value.value is True for a in daemons
    ), "serve() sets daemon_threads to something other than True"


def test_every_store_method_that_touches_the_database_holds_the_lock() -> None:
    """One connection is only safe while ONE thing may use it at a time.

    Derived from the AST rather than hand-listed, so a method added tomorrow is
    covered without anybody remembering to add it here. `__init__` is the one
    exemption and it is a real one: nothing else holds a reference to the object
    while its constructor runs, so there is no second thread to exclude — and it
    takes the lock anyway, which this test does not require but does not mind.
    """
    tree = _tree("accountant/memory/store.py")
    store = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.ClassDef) and node.name == "MemoryStore"
    )

    unguarded: list[str] = []
    for method in store.body:
        if not isinstance(method, ast.FunctionDef) or method.name == "__init__":
            continue
        touches = any(
            isinstance(node, ast.Attribute) and node.attr == "_db"
            for node in ast.walk(method)
        )
        if not touches:
            continue
        guarded = any(
            isinstance(item.context_expr, ast.Attribute)
            and item.context_expr.attr == "_lock"
            for node in ast.walk(method)
            if isinstance(node, ast.With)
            for item in node.items
        )
        if not guarded:
            unguarded.append(method.name)

    assert unguarded == [], (
        f"these MemoryStore methods use the shared connection without holding "
        f"the lock: {unguarded}"
    )


def test_the_store_connection_is_opened_for_more_than_one_thread() -> None:
    """`check_same_thread=False` is what makes the lock the only guard needed.

    Asserted structurally as well as behaviourally, because the behavioural test
    above passes on a single-threaded run of a threaded product: the value of
    this flag is a decision, and a decision that is not written down is one
    somebody reverts while reading the diff.
    """
    tree = _tree("accountant/memory/store.py")
    connects = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "connect"
    ]
    assert connects, "the store no longer opens a sqlite3 connection at all"
    for call in connects:
        stated = {
            kw.arg: kw.value
            for kw in call.keywords
            if isinstance(kw.value, ast.Constant)
        }
        flag = stated.get("check_same_thread")
        assert flag is not None, (
            "sqlite3.connect does not say check_same_thread, so it defaults to "
            "True and the first request on a new thread raises"
        )
        assert isinstance(flag, ast.Constant) and flag.value is False


def test_no_request_handler_installs_or_drops_the_runtime() -> None:
    """`_runtime_state` is shared by every thread, so nothing per-request may
    write it.

    `install()` and `disconnect()` are the two functions carrying
    `global _runtime_state`. They are startup and teardown entry points; a call
    to either from inside `Handler` would mean one customer's request could
    change what company every OTHER in-flight request is working in — which is
    the same class of defect as the cross-tenant draft above, one layer up.
    """
    tree = _tree("accountant/web/app.py")
    handler = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.ClassDef) and node.name == "Handler"
    )
    called = {
        node.func.id
        for node in ast.walk(handler)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }
    assert not called & {"install", "disconnect", "configure", "connect"}, (
        f"a request handler rebinds the process-wide runtime: "
        f"{sorted(called & {'install', 'disconnect', 'configure', 'connect'})}"
    )


def test_every_shared_cache_in_the_web_app_is_written_under_the_lock() -> None:
    """The four module-level mutables, and the guard that keeps them in step.

    Derived from the module rather than listed by hand: any module-level `dict`
    or `set` in `app.py` is a cache shared by every request thread, and every
    function that MUTATES one must hold `_CACHE_LOCK` while it does. A new cache
    added without a lock fails here rather than in production at 4pm.

    Constant lookup tables are excluded by measurement, not by name — a mapping
    nothing mutates cannot be raced, and the scan below only looks at functions
    that actually mutate one.
    """
    tree = _tree("accountant/web/app.py")
    shared: set[str] = set()
    for node in tree.body:
        if not isinstance(node, ast.AnnAssign) or not isinstance(node.target, ast.Name):
            continue
        # A module-level name bound to a dict or set literal is in-process state
        # that outlives a request. `set()` is a Call, so it is included too.
        if isinstance(node.value, (ast.Dict, ast.Set)) or (
            isinstance(node.value, ast.Call)
            and isinstance(node.value.func, ast.Name)
            and node.value.func.id in {"set", "dict"}
        ):
            shared.add(node.target.id)
    assert {"DRAFTS", "DRAFT_TENANT", "BATCHES", "_recorded_mismatches"} <= shared, (
        f"the scan stopped finding the caches it was written for: {sorted(shared)}"
    )

    mutators = {"pop", "clear", "add", "discard", "update", "setdefault"}
    unguarded: list[str] = []
    for function in ast.walk(tree):
        if not isinstance(function, ast.FunctionDef):
            continue
        mutates = any(
            (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr in mutators
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id in shared
            )
            or (
                isinstance(node, ast.Assign)
                and any(
                    isinstance(t, ast.Subscript)
                    and isinstance(t.value, ast.Name)
                    and t.value.id in shared
                    for t in node.targets
                )
            )
            for node in ast.walk(function)
        )
        if not mutates:
            continue
        holds = any(
            isinstance(item.context_expr, ast.Name)
            and item.context_expr.id == "_CACHE_LOCK"
            for node in ast.walk(function)
            if isinstance(node, ast.With)
            for item in node.items
        )
        if not holds:
            unguarded.append(function.name)

    assert unguarded == [], (
        f"these functions mutate a cache shared by every request thread without "
        f"holding _CACHE_LOCK: {unguarded}"
    )


@pytest.fixture(autouse=True)
def a_clean_cache() -> Iterator[None]:
    """No test here may inherit or leave a draft, an owner row or a batch."""
    for cache in (app.DRAFTS, app.DRAFT_TENANT, app.BATCHES):
        cache.clear()
    yield
    for cache in (app.DRAFTS, app.DRAFT_TENANT, app.BATCHES):
        cache.clear()
