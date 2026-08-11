"""Can somebody tell what this thing is doing, and join up one customer's entry.

WHAT THIS FILE PROVES
---------------------
1. correlation      every log line of ONE request carries the SAME request id,
                    two requests carry DIFFERENT ones, and the two or three
                    requests that make up ONE ENTRY carry the same entry id so
                    they can be joined
2. the counts       `/metrics` matches what the durable action log actually
                    holds after an entry driven over real HTTP — not a
                    process-local counter that would resurface as a smaller
                    number after a restart
3. the standing     an unmeasured value reads NOT_MEASURED and is NEVER 0, and
   owner rule       a MEASURED zero still reads 0, which is the control that
                    stops rule 3 being satisfied by writing NOT_MEASURED
                    everywhere
4. the credential   `/metrics` carries business counts and a company name, so
                    an unauthenticated caller gets 401 and no numbers
5. timing           a duration is recorded for the request AND for each Tally
                    call inside it, so a slow Tally is distinguishable from a
                    slow app
6. the log is safe  a session token never reaches a line, and a newline in a
                    field cannot forge a second line

WHAT THIS FILE DOES NOT PROVE
-----------------------------
Nothing here touches a real TallyPrime. The backend is `FakeTally` injected
through `app.configure()`, so every duration measured below is the cost of a
dictionary lookup and says NOTHING about what a licensed Tally costs. The
thresholds in `accountant/observability.py` are stated there as thresholds
rather than as measurements for exactly that reason.

It also does not prove that two HTTP requests in flight at once stay separate.
`HTTPServer` is single-threaded today, so there is no way to put two in flight.
What IS proved is the property that will matter when Task 11 makes it threaded:
the two ids live in ContextVars, and two threads setting them do not see each
other's values. The step from "two threads" to "two HTTP requests" is Task 11's
to take.
"""

from __future__ import annotations

import datetime
import io
import json
import logging
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest

from accountant import auth, observability
from accountant.auth import identity as ident
from accountant.memory.store import MemoryStore
from accountant.schema import ActionLog, Outcome
from accountant.web import app
from tests.test_web import demo_company, draft_id, fake_backend, operation, serving

#: An entry for a vendor the demo company has never seen, so it asks. Two
#: requests to answer it, which is what makes the entry id worth having.
UNSEEN = "paid Gupta Hardware 1500 for tools"
PURPOSE = "which_account"
FUNDING = "funding_is_named"


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


@pytest.fixture
def logged() -> Iterator[io.StringIO]:
    """Capture the app's own log into a string, and put logging back after.

    Installed through `observability.install_logging`, the SAME function
    `serve()` calls, rather than through a hand-built handler. A fixture that
    built its own formatter would be testing the fixture: the request id is put
    on every line by a filter that lives inside that function, so a test that
    bypasses it proves nothing about what a person running the product sees.
    """
    stream = io.StringIO()
    observability.install_logging(stream)
    try:
        yield stream
    finally:
        logger = logging.getLogger(observability.LOGGER_NAME)
        for handler in list(logger.handlers):
            logger.removeHandler(handler)
            handler.close()
        logger.propagate = True
        logger.setLevel(logging.NOTSET)


def wait_for(stream: io.StringIO, event: str, count: int = 1) -> list[str]:
    """The lines for `event`, once there are `count` of them.

    POLLED, NOT READ ONCE, and the race is real rather than theoretical. The
    server writes the body and only THEN falls into the `finally` that logs the
    request, so `urlopen` can return to this thread before the line exists. A
    bare read would fail perhaps one run in fifty, which is the worst kind of
    test: green often enough to be trusted and red often enough to be ignored.
    """
    deadline = time.monotonic() + 5.0
    while True:
        found = [
            line for line in stream.getvalue().splitlines() if f"event={event} " in line
        ]
        if len(found) >= count:
            return found
        if time.monotonic() > deadline:
            raise AssertionError(
                f"waited 5s for {count} {event!r} line(s); got {len(found)}:\n"
                f"{stream.getvalue()}"
            )
        time.sleep(0.01)


def field_in(line: str, name: str) -> str:
    """One `key=value` off a log line. Raises rather than returning a default.

    A helper that returned "" for a missing field would let every assertion
    below pass against a line that carried nothing at all.
    """
    for part in line.split(" "):
        key, sep, value = part.partition("=")
        if sep and key == name:
            return value
    raise AssertionError(f"no {name}= on line: {line}")


def get(base: str, path: str = "/", token: str = "") -> tuple[int, str]:
    """GET, returning the status even when it is a refusal.

    `urlopen` raises on 4xx and here THE STATUS IS THE MEASUREMENT — "an
    unauthenticated caller got 401" cannot be asserted by a helper that turns
    the answer into an exception.
    """
    request = urllib.request.Request(base + path)  # noqa: S310 - loopback, http
    if token:
        request.add_header("Cookie", f"{app.COOKIE}={token}")
    try:
        with urllib.request.urlopen(request, timeout=5) as answer:  # noqa: S310
            return answer.status, answer.read().decode()
    except urllib.error.HTTPError as refused:
        return refused.status or 0, refused.read().decode()


def post(base: str, path: str, **fields: str) -> str:
    data = urllib.parse.urlencode(fields).encode()
    with urllib.request.urlopen(base + path, data=data, timeout=5) as answer:  # noqa: S310
        return answer.read().decode()


def an_entry_that_posts(base: str) -> str:
    """One entry through the surface, answered twice. Returns the draft id."""
    asked = post(base, "/entry", text=UNSEEN)
    draft = draft_id(asked)
    post(base, "/answer", draft=draft, value="Purchases", problem=PURPOSE)
    done = post(base, "/answer", draft=draft, value="Cash", problem=FUNDING)
    assert "posted" in done.lower(), done
    return draft


def parsed(body: str) -> dict[str, str]:
    """`/metrics` as a mapping. Comments and the trailing reason are dropped."""
    out: dict[str, str] = {}
    for line in body.splitlines():
        if not line.strip() or line.startswith("#"):
            continue
        name, sep, rest = line.partition(": ")
        assert sep, f"a metric line with no value: {line!r}"
        out[name] = rest.split("  #", 1)[0].strip()
    return out


# ---------------------------------------------------------------------------
# 1. correlation — which lines belong together
# ---------------------------------------------------------------------------


def test_every_log_line_of_one_request_carries_the_same_request_id(
    server: str, logged: io.StringIO
) -> None:
    """One request, several lines, one id.

    Asserted over EVERY line rather than over the request line alone. The
    request line is the easy one — it is written by the same function that
    holds the id. The lines that matter are the `tally_call` ones written deep
    inside the handler, because those are the ones somebody is reading when
    they are trying to work out why an entry was slow.
    """
    status, _ = get(server)
    assert status == 200
    wait_for(logged, "request")

    lines = [line for line in logged.getvalue().splitlines() if line.strip()]
    assert len(lines) >= 2, f"one request produced no detail:\n{lines}"

    ids = {field_in(line, "request") for line in lines}
    assert len(ids) == 1, f"one request wrote {len(ids)} different ids: {ids}"
    assert ids.pop().startswith("req_")


def test_two_requests_carry_two_different_request_ids(
    server: str, logged: io.StringIO
) -> None:
    """The half that makes the id worth having.

    An id that never changes is a constant with a misleading name — the same
    defect `/health` had when it returned a hardcoded `{"ok": true}` — and it
    would join every line in the file into one imaginary request.
    """
    get(server)
    get(server)
    requests = wait_for(logged, "request", count=2)

    first, second = field_in(requests[0], "request"), field_in(requests[1], "request")
    assert first != second, "two requests shared one correlation id"


def test_the_requests_that_make_up_one_entry_can_be_tied_together(
    server: str, logged: io.StringIO
) -> None:
    """THE POINT OF THE WHOLE EXERCISE.

    One entry is three requests here: the typing, and two answers. They have
    three different request ids on purpose — they ARE three requests — and the
    only thing that can join them is the entry id. Without it, "show me
    everything about the bill they typed at 14:32" has no answer.
    """
    draft = an_entry_that_posts(server)
    requests = wait_for(logged, "request", count=3)

    entries = [field_in(line, "entry") for line in requests]
    assert entries == [draft, draft, draft], (
        f"the three requests of one entry were filed under {set(entries)}"
    )
    assert len({field_in(line, "request") for line in requests}) == 3


def test_the_line_about_the_write_carries_the_entry_it_wrote(
    server: str, logged: io.StringIO
) -> None:
    """The request that touched somebody's books must be joinable, from the
    line that says it happened and not only from the summary at the end.

    Measured: with the entry id set at the END of `_run`, the `post_voucher`
    call and the decision row on the FIRST request both logged
    `entry=NOT_RECORDED`. Only the closing `request` line carried it — so the
    one moment worth investigating was the one moment nothing joined to.
    """
    post(server, "/entry", text="paid Sharma Traders 4200 for cement")
    wait_for(logged, "request")

    wrote = [
        line
        for line in logged.getvalue().splitlines()
        if "name=post_voucher" in line or f"event={app.AUDIT_ROW_EVENT} " in line
    ]
    assert wrote, "a straight-through entry logged neither its write nor its row"
    for line in wrote:
        assert field_in(line, "entry").startswith("draft"), line


def test_a_request_belonging_to_no_entry_says_so_rather_than_inventing_one(
    server: str, logged: io.StringIO
) -> None:
    """The home page is about no particular entry, and the line must say that.

    A blank there would render `entry=` and read as a bug in the formatter; a
    made-up id would join this line to an entry it has nothing to do with.
    """
    get(server)
    line = wait_for(logged, "request")[0]
    assert field_in(line, "entry") == "NOT_RECORDED"


def test_an_answer_naming_a_draft_that_expired_is_still_filed_under_it(
    server: str, logged: io.StringIO
) -> None:
    """The request most worth investigating is the one that failed.

    Written by a mutant: setting the entry id AFTER `draft_for` returns leaves
    exactly this request — the one where somebody's form stopped working — as
    the only one in the log with nothing to join it to.
    """
    post(server, "/answer", draft="no-such-draft", value="Cash", problem=FUNDING)
    line = wait_for(logged, "request")[0]
    assert field_in(line, "entry") == "no-such-draft"


def test_the_ids_belong_to_one_context_and_not_to_the_whole_process() -> None:
    """The reason both ids are ContextVars and not module globals.

    Task 11 replaces `HTTPServer` with a threading one. A module global would
    then be ONE CUSTOMER'S correlation id stamped on ANOTHER CUSTOMER'S lines,
    which makes a correlation id worse than useless: it joins together lines
    that have nothing to do with each other, and somebody investigating one
    company's entry reads another company's traffic.

    The barrier is the whole design of the test. Both threads SET before either
    READS, so a shared global is guaranteed to be caught rather than caught
    when the scheduler happens to cooperate — a racy test that passes most of
    the time is how a real leak survives a green suite.
    """
    seen: dict[str, tuple[str, str]] = {}
    both_have_set = threading.Barrier(2)

    def work(name: str) -> None:
        observability.begin_request(f"req_{name}")
        observability.set_entry_id(f"entry_{name}")
        both_have_set.wait(timeout=5)
        seen[name] = (
            observability.current_request_id(),
            observability.current_entry_id(),
        )

    threads = [
        threading.Thread(target=work, args=(name,)) for name in ("alpha", "beta")
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=5)

    assert seen == {
        "alpha": ("req_alpha", "entry_alpha"),
        "beta": ("req_beta", "entry_beta"),
    }


# ---------------------------------------------------------------------------
# 2. the counts come from the durable store
# ---------------------------------------------------------------------------


def test_metrics_match_what_the_store_actually_holds_after_a_posted_entry(
    tmp_path: Path,
) -> None:
    """Driven over HTTP, then checked against the database from outside.

    The store is a FILE and the counting is done AFTER the server is gone, on a
    connection this test opened itself. That is what makes it a check rather
    than a promise: a `/metrics` reading a counter it had been keeping in
    memory would agree with itself perfectly and disagree with the customer's
    audit trail, which is the only copy that survives a restart.

    Every expected number is computed here from the rows, by hand. Calling
    `render_metrics` to produce the expectation would compare the function with
    itself.
    """
    db = tmp_path / "app.db"
    with serving(demo_company(), fake_backend(), store_path=db) as base:
        an_entry_that_posts(base)
        status, body = get(base, app.METRICS_PATH)

    assert status == 200
    rows: tuple[ActionLog, ...] = MemoryStore(db).actions(app.COMPANY)
    assert rows, "the entry left no durable rows, so nothing below is a check"

    counts = parsed(body)
    decisions = [row for row in rows if row.outcome in {o.value for o in Outcome}]
    entries = {row.operation_id for row in decisions if row.operation_id}

    assert counts["action_log_rows"] == str(len(rows))
    assert counts["entries_seen"] == str(len(entries)) == "1"
    for outcome in Outcome:
        expected = sum(1 for row in decisions if row.outcome == outcome.value)
        assert counts[f"outcome_{outcome.value}"] == str(expected), outcome.value
    assert counts["writes_attempted"] == str(
        sum(1 for row in rows if row.action == "write_attempted")
    )
    assert counts["writes_outcome_unknown"] == str(
        sum(1 for row in rows if row.action == "write_outcome_unknown")
    )
    assert counts["company"] == app.COMPANY


def test_the_counts_survive_the_process_that_made_them(tmp_path: Path) -> None:
    """A restart must not reset them. This is the whole of rule 1.

    Two servers over ONE database file, an entry typed in each. A process-local
    counter reports 1 on the second run; the durable log reports 2, which is
    the number that is true.
    """
    db = tmp_path / "app.db"
    with serving(demo_company(), fake_backend(), store_path=db) as base:
        an_entry_that_posts(base)
        first = parsed(get(base, app.METRICS_PATH)[1])

    with serving(demo_company(), fake_backend(), store_path=db) as base:
        an_entry_that_posts(base)
        second = parsed(get(base, app.METRICS_PATH)[1])

    assert first["entries_seen"] == "1"
    assert second["entries_seen"] == "2", (
        "the second run forgot the first run's entry, so the count is a "
        "process-local counter wearing a durable name"
    )


def test_a_reversal_is_counted_from_the_row_the_undo_button_writes(
    tmp_path: Path,
) -> None:
    """The metric and the writer must spell the action the same way.

    Written as a test rather than trusted because the two ends are in different
    modules: `do_POST` writes the row and `render_metrics` counts it. A metric
    reading a word nobody writes counts zero forever and nothing complains.
    """
    db = tmp_path / "app.db"
    with serving(demo_company(), fake_backend(), store_path=db) as base:
        asked = post(base, "/entry", text=UNSEEN)
        draft = draft_id(asked)
        post(base, "/answer", draft=draft, value="Purchases", problem=PURPOSE)
        done = post(base, "/answer", draft=draft, value="Cash", problem=FUNDING)
        post(base, "/reverse", op=operation(done))
        counts = parsed(get(base, app.METRICS_PATH)[1])

    assert counts["reversals_single"] == "1"


def test_a_durable_audit_row_can_be_joined_to_the_log_lines_that_made_it(
    tmp_path: Path, logged: io.StringIO
) -> None:
    """THE REASON THE REQUEST ID IS NOT A COLUMN ON `action_log`.

    `MemoryStore._migrate` would take one: it is additive-only and every
    existing row would be left NULL, which reads back as NOT_RECORDED. It was
    still refused. A request id is a key into a LOG FILE, and log files rotate;
    six months later the column would name a line that no longer exists — a
    foreign key to nothing, inside an append-only statutory record that cannot
    be corrected.

    The join is needed in the other direction anyway: "given this voucher, what
    happened", not "given this line, which row". So the OPERATION ID — already
    on every durable row, and outliving any log — is put on the log line, and
    this test is the proof that the two ends really do meet.
    """
    db = tmp_path / "app.db"
    with serving(demo_company(), fake_backend(), store_path=db) as base:
        an_entry_that_posts(base)
        wait_for(logged, "request", count=3)

    rows = MemoryStore(db).actions(app.COMPANY)
    posted = [row for row in rows if row.action == "posted"]
    assert len(posted) == 1, "no posted row, so there is nothing to join to"

    joined = [
        line
        for line in logged.getvalue().splitlines()
        if f"event={app.AUDIT_ROW_EVENT} " in line
        and field_in(line, "operation") == posted[0].operation_id
    ]
    assert joined, (
        f"no log line names operation {posted[0].operation_id!r}, so a person "
        f"holding the voucher cannot find the requests that wrote it"
    )
    assert field_in(joined[-1], "action") == "posted"
    assert field_in(joined[-1], "request").startswith("req_")


def test_the_request_id_is_not_a_column_on_the_audit_table() -> None:
    """Stated as a test so the decision cannot be reversed by accident.

    Adding a column here is one line in `MemoryStore._migrate`, and the whole
    argument against it lives in a comment. A comment does not fail.
    """
    columns = MemoryStore(":memory:").columns_of("action_log")

    assert "request_id" not in columns
    # The two durable joins that DO exist, and that outlive any log file.
    assert "run_id" in columns
    assert "operation_id" in columns


def test_metrics_refuses_when_nothing_is_connected(server: str) -> None:
    """Fail closed. Serving zeros with no runtime would read as a quiet day."""
    app.disconnect()
    status, body = get(server, app.METRICS_PATH)

    assert status == 503
    assert "entries_seen" not in body
    assert "REAL TALLY REQUIRED" in body


# ---------------------------------------------------------------------------
# 3. NOT_MEASURED, never 0 — and the control that keeps it honest
# ---------------------------------------------------------------------------


def test_an_unmeasured_value_reads_not_measured_and_never_zero() -> None:
    """The standing owner rule, over an empty log, on the pure function.

    Every one of these is genuinely unmeasurable today and each says why in the
    body: a rate over no entries is undefined, and an auth refusal writes no
    durable row on purpose.

    `refused_replays` WAS on this list, 2026-08-11, and is not any more. The
    stated reason was defect I2 — a refused write replay recorded as
    `write_outcome_unknown`, so the log could not tell a replay from an unknown
    outcome. I2 was fixed while this file was being written: there are now two
    named rows, `refused_replay` and `write_refused_duplicate`, so the count is
    real and NOT_MEASURED would be the false statement.
    `test_a_refused_replay_is_counted_now_that_it_has_a_row_of_its_own` below
    is what replaced it.
    """
    body = observability.render_metrics((), company="Some Co", uptime=1.0)
    counts = parsed(body)

    for name in (
        "question_rate",
        "auth_refusals_401",
        "auth_refusals_403",
    ):
        assert counts[name] == observability.NOT_MEASURED, name
        assert counts[name] != "0", f"{name} reported a zero it never measured"

    for name in ("question_rate", "auth_refusals_401"):
        line = next(row for row in body.splitlines() if row.startswith(f"{name}: "))
        assert "  # " in line, f"{name} says NOT_MEASURED and does not say why"


def test_a_measured_zero_is_still_written_as_zero(tmp_path: Path) -> None:
    """THE CONTROL, and without it the rule above is satisfied by writing
    NOT_MEASURED on every line and measuring nothing at all.

    `not_valid` is currently unreachable from a typed entry — the only
    unanswerable check is `amount_is_integer_paise` and the extractor cannot
    produce a non-integer. So after a real posted entry this is a zero we
    COUNTED, and reporting it as unmeasured would be its own kind of lie.
    """
    db = tmp_path / "app.db"
    with serving(demo_company(), fake_backend(), store_path=db) as base:
        an_entry_that_posts(base)
        counts = parsed(get(base, app.METRICS_PATH)[1])

    assert counts["outcome_not_valid"] == "0"
    assert counts["writes_outcome_unknown"] == "0"
    assert counts["question_rate"] == "1.0", (
        "one entry, asked about, so the rate is measured and is not NOT_MEASURED"
    )


def test_the_question_rate_is_never_zero_on_a_server_that_has_done_nothing(
    server: str,
) -> None:
    """Over HTTP, on the surface a person actually scrapes.

    `question rate: 0` is the sentence the standing rule names. On a fresh
    server it would say this system never has to ask a question, on the day it
    has done nothing at all.
    """
    counts = parsed(get(server, app.METRICS_PATH)[1])

    assert counts["entries_seen"] == "0", "a measured zero, because we counted"
    assert counts["question_rate"] == observability.NOT_MEASURED
    assert "question_rate: 0" not in get(server, app.METRICS_PATH)[1]


def test_metrics_is_plain_text_and_needs_no_dependency_to_read(server: str) -> None:
    """Deliberately NOT the Prometheus exposition format — that format has one
    value type, a float, and no way to write NOT_MEASURED in it."""
    request = urllib.request.Request(server + app.METRICS_PATH)  # noqa: S310
    with urllib.request.urlopen(request, timeout=5) as answer:  # noqa: S310
        assert answer.headers.get_content_type() == "text/plain"
        body = answer.read().decode()

    assert body.endswith("\n")
    assert parsed(body)["uptime_seconds"]


# ---------------------------------------------------------------------------
# 4. the credential. These counts are a customer's trading volume.
# ---------------------------------------------------------------------------


class TestMetricsNeedsACredential:
    """This class, and only this class, runs with authentication REQUIRED.

    `tests/conftest.py` sets LOCAL_DEV_MODE=1 for the whole suite so the HTTP
    tests above keep measuring what they were written for. Here the credential
    IS the subject, so the variable is deleted — and deleted rather than set to
    "0", because an unset variable is the case that ships.

    Autouse and class-scoped, so a test added here later cannot silently
    inherit dev mode and pass by checking nothing, while the rest of the file
    is untouched.
    """

    @pytest.fixture(autouse=True)
    def production_auth(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv(ident.ENV_LOCAL_DEV_MODE, raising=False)

    @staticmethod
    def seeding(token: str) -> Callable[[MemoryStore], None]:
        def seed(store: MemoryStore) -> None:
            now = datetime.datetime.now(datetime.UTC)
            store.create_tenant("tenant-obs", "Observability Ltd", now.isoformat())
            digest, salt = auth.hash_password("correct horse battery staple")
            store.create_user(
                "user-obs",
                "tenant-obs",
                "watcher@obs.test",
                digest,
                salt,
                now.isoformat(),
            )
            store.open_session(
                auth.token_fingerprint(token),
                "user-obs",
                "tenant-obs",
                now.isoformat(),
                (now + datetime.timedelta(days=7)).isoformat(),
            )

        return seed

    def test_an_unauthenticated_caller_gets_no_counts_at_all(self) -> None:
        """401, and not one number in the body.

        The status alone would not be enough. A route that answered 401 and
        still wrote the counts underneath the refusal page would have given
        them away, which is why the body is checked for the numbers themselves.
        """
        token = auth.new_token()
        with serving(demo_company(), fake_backend(), seed=self.seeding(token)) as base:
            status, body = get(base, app.METRICS_PATH)

        assert status == 401
        assert "entries_seen" not in body
        assert "action_log_rows" not in body
        assert "uptime_seconds" not in body
        # Not `app.COMPANY not in body`: the refusal is drawn by `page()`, and
        # every page in this app carries the company in its header — which is
        # a pre-existing property of the refusal page and not something this
        # route decides. What must not leak is the COUNTS, and they do not.

    def test_a_signed_in_caller_gets_them(self) -> None:
        """THE CONTROL. A route that refuses everybody proves nothing."""
        token = auth.new_token()
        with serving(demo_company(), fake_backend(), seed=self.seeding(token)) as base:
            status, body = get(base, app.METRICS_PATH, token=token)

        assert status == 200
        assert parsed(body)["entries_seen"] == "0"

    def test_health_is_still_open_and_metrics_is_not(self) -> None:
        """The two endpoints answer different questions and are gated
        differently on purpose.

        `/health` says whether the service can receive work, which a load
        balancer needs before anybody has a credential. `/metrics` says how much
        business a named company did.
        """
        token = auth.new_token()
        with serving(demo_company(), fake_backend(), seed=self.seeding(token)) as base:
            health_status, health_body = get(base, "/health")
            metrics_status, _ = get(base, app.METRICS_PATH)

        assert (health_status, metrics_status) == (200, 401)
        assert json.loads(health_body)["ready"] is True


# ---------------------------------------------------------------------------
# 5. timing — is it us or is it Tally
# ---------------------------------------------------------------------------


def test_a_duration_is_recorded_for_the_request_and_for_each_tally_call(
    server: str, logged: io.StringIO
) -> None:
    """Both halves, because only one of them is not an answer.

    A total with no split makes a slow Tally and a slow app indistinguishable,
    and the two need completely different people to fix them. `app_ms` is the
    total minus the Tally time, so the two numbers on one line say which.
    """
    get(server)
    calls = wait_for(logged, "tally_call")
    line = wait_for(logged, "request")[0]

    assert {field_in(c, "name") for c in calls} >= {
        "list_companies",
        "list_our_vouchers",
        "trial_balance",
    }
    for call in calls:
        assert float(field_in(call, "ms")) >= 0.0

    assert int(field_in(line, "tally_calls")) == len(calls)
    assert float(field_in(line, "ms")) >= float(field_in(line, "tally_ms"))
    assert float(field_in(line, "app_ms")) >= 0.0
    assert field_in(line, "status") == "200"
    assert field_in(line, "method") == "GET"


def test_a_tally_call_that_raises_is_still_timed() -> None:
    """The failure is the case worth timing.

    A Tally that has stopped answering is slow first and absent second, and how
    long the failure took is the evidence for which of the two happened. Driven
    directly rather than over HTTP because the point is the `finally`, and a
    fake that raises on a route would be testing the route.
    """
    observability.begin_request(observability.new_request_id())
    with pytest.raises(RuntimeError), observability.tally_call("read_accounts"):
        raise RuntimeError("Tally went away")

    assert observability.tally_calls_this_request() == 1
    assert observability.tally_ms_this_request() >= 0.0


def test_each_request_starts_the_tally_clock_again(
    server: str, logged: io.StringIO
) -> None:
    """The shipped server serves every request on one thread, so one context
    is reused. A clock that was only ever defaulted would make request two
    report request one's Tally time on top of its own, and the number would
    climb forever."""
    get(server)
    get(server)
    first, second = wait_for(logged, "request", count=2)

    assert int(field_in(second, "tally_calls")) == int(field_in(first, "tally_calls"))


def test_what_slow_means_is_a_number_the_documentation_carries() -> None:
    """A threshold nobody can find is a threshold nobody can argue with.

    Asserted against `docs/OBSERVABILITY.md` so the prose and the constant
    cannot drift: changing one without the other fails here.
    """
    doc = (
        Path(__file__).resolve().parent.parent / "docs" / "OBSERVABILITY.md"
    ).read_text()

    assert str(int(observability.SLOW_TALLY_MS)) in doc
    assert str(int(observability.SLOW_REQUEST_MS)) in doc
    assert observability.SLOW_REQUEST_MS > observability.SLOW_TALLY_MS


# ---------------------------------------------------------------------------
# 6. what must never reach a log line
# ---------------------------------------------------------------------------


def test_a_session_token_never_reaches_the_log(logged: io.StringIO) -> None:
    """A log is the copy of your data that ends up in the widest number of
    places. It gets identifiers and durations and nothing a thief could use."""
    token = auth.new_token()
    with serving(demo_company(), fake_backend()) as base:
        get(base, "/", token=token)
        wait_for(logged, "request")

    written = logged.getvalue()
    assert token not in written
    assert app.COOKIE not in written
    assert "password" not in written.lower()


def test_a_query_string_is_cut_off_before_the_path_is_logged(
    server: str, logged: io.StringIO
) -> None:
    """This app puts nothing in a query string, so anything found in one came
    from outside and must not be copied into a file that gets mailed around."""
    get(server, "/?token=hunter2&note=secret")
    line = wait_for(logged, "request")[0]

    assert field_in(line, "path") == "/"
    assert "hunter2" not in logged.getvalue()


def test_a_newline_in_a_field_cannot_forge_a_second_log_line(
    logged: io.StringIO,
) -> None:
    """Log injection, and it is reachable: a path, a company name and an
    exception message all reach the log and all are influenced from outside. A
    newline would end the line early and let the rest be read as a second one,
    with whatever request id the forger chose."""
    observability.begin_request("req_real")
    observability.log("probe", reason="one\nevent=request request=req_forged")

    lines = [line for line in logged.getvalue().splitlines() if line.strip()]
    assert len(lines) == 1, f"one call wrote {len(lines)} lines: {lines}"
    assert "req_forged" in lines[0], "the text is kept; only the line break goes"
    # The forged id survives as TEXT inside a quoted field, which is harmless.
    # What must not survive is a second LINE: the correlation id of the one
    # line written is still ours, so nothing can be filed under req_forged.
    assert field_in(lines[0], "request") == "req_real"


def test_two_installs_do_not_write_every_line_twice() -> None:
    """A second handler doubles every line, which quietly doubles every count
    a person makes by grepping the log."""
    stream = io.StringIO()
    observability.install_logging(stream)
    observability.install_logging(stream)
    try:
        observability.begin_request("req_once")
        observability.log("probe")
        written = [row for row in stream.getvalue().splitlines() if row.strip()]
        assert len(written) == 1
    finally:
        logger = logging.getLogger(observability.LOGGER_NAME)
        for handler in list(logger.handlers):
            logger.removeHandler(handler)
            handler.close()
        logger.propagate = True
        logger.setLevel(logging.NOTSET)


def test_a_refused_replay_is_counted_now_that_it_has_a_row_of_its_own(
    tmp_path: Path,
) -> None:
    """What replaced `refused_replays` on the NOT_MEASURED list, 2026-08-11.

    Post an entry, undo it, then re-submit the form that posted it. Until defect
    I1 was fixed that wrote a SECOND voucher wearing the same operation id.
    It is now refused before the socket opens, with a `refused_replay` row of
    its own, and this is the metric reading that row.

    Driven over HTTP rather than by hand-building the row, because the point is
    that the word the handler WRITES and the word the metric READS are the same
    word. Two literals is how a counter reads zero for ever while the thing it
    counts keeps happening — which is why both ends import the name from one
    place, and why this test would fail if they stopped.
    """
    db = tmp_path / "app.db"
    with serving(demo_company(), fake_backend(), store_path=db) as base:
        draft = an_entry_that_posts(base)
        before = parsed(get(base, app.METRICS_PATH)[1])
        assert before["refused_replays"] == "0", "a measured zero, before anything"

        operation = app.DRAFTS[draft].operation_id
        post(base, "/reverse", op=operation)

        # 409 Conflict IS the refusal, so this cannot go through `post`, which
        # raises on anything that is not 2xx. The status is asserted rather
        # than swallowed: a replay that answered 200 would be the defect back.
        replay = urllib.request.Request(  # noqa: S310
            base + "/answer",
            data=urllib.parse.urlencode(
                {"draft": draft, "value": "Cash", "problem": FUNDING}
            ).encode(),
        )
        with pytest.raises(urllib.error.HTTPError) as refused:
            urllib.request.urlopen(replay, timeout=5)  # noqa: S310
        assert refused.value.status == 409

        after = parsed(get(base, app.METRICS_PATH)[1])

    assert after["refused_replays"] == "1"
    assert after["refused_replays"] != observability.NOT_MEASURED
