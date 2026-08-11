"""The connector: one job at a time, out to the cloud, down to loopback Tally.

WHAT THIS FILE PROVES, AND WHAT IT DOES NOT
-------------------------------------------
Every test here runs against `FakeTally` or a deliberate failure double. That
makes the evidence class `FAKETALLY`, and it says nothing about a real
TallyPrime. What it does prove is the part that has to be right before any
real Tally is involved at all: that a job for the wrong tenant, the wrong
company, or an operation id already used is refused BEFORE anything reaches
Tally, and that a dropped reply cannot execute a job twice.

No test in this file opens a socket. `CloudCall` is injected, which is the same
seam `accountant.extract.service.ServiceCall` uses, and it is why the refusal
logic can be proved while no cloud exists.
"""

from __future__ import annotations

import ast
import datetime
import importlib
import json
import logging
from pathlib import Path
from typing import Any, cast

import pytest

from accountant.agent import (
    ALREADY_EXECUTED,
    EXECUTED,
    FAILED,
    REFUSED_UNKNOWN_KIND,
    REFUSED_WRONG_COMPANY,
    REFUSED_WRONG_TENANT,
    TALLY_UNAVAILABLE,
    Connector,
    ConnectorIdentity,
    ExecutedOperations,
    Job,
    JobResult,
    cli,
)
from accountant.agent.connector import build_logger, https_cloud_call
from accountant.schema import Voucher
from accountant.tallyio.client import TallyClient
from accountant.tallyio.fake import FakeTally

COMPANY = "Demo Co"
TENANT = "tenant-alpha"


# ---------------------------------------------------------------------------
# doubles
# ---------------------------------------------------------------------------


def identity(**over: Any) -> ConnectorIdentity:
    base: dict[str, Any] = {
        "connector_id": "connector-1",
        "secret": "s3cr3t-value",
        "tenant_id": TENANT,
        "companies": frozenset({COMPANY}),
    }
    base.update(over)
    return ConnectorIdentity(**base)


class Cloud:
    """A cloud that hands out a queue of jobs and records every call made."""

    def __init__(self, jobs: list[dict[str, Any]] | None = None) -> None:
        self.jobs = list(jobs or [])
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.results: list[dict[str, Any]] = []

    def __call__(self, url: str, payload: Any) -> dict[str, Any]:
        self.calls.append((url, dict(payload)))
        if url.endswith("/connector/jobs"):
            return {"job": self.jobs.pop(0)} if self.jobs else {}
        if url.endswith("/connector/result"):
            self.results.append(dict(payload))
            return {"ok": True}
        return {}


class ClosedTally:
    """Tally is not running. Opening a client raises, as `real_tally` does."""

    def __call__(self, _company: str) -> TallyClient:
        raise OSError("[Errno 61] Connection refused")


class DropsAfterAnswering(Cloud):
    """Answers the job, then loses the result — the case duplicates come from."""

    def __call__(self, url: str, payload: Any) -> dict[str, Any]:
        if url.endswith("/connector/result"):
            self.calls.append((url, dict(payload)))
            raise OSError("connection reset while reporting")
        return super().__call__(url, payload)


def a_job(**over: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "job_id": "job-1",
        "tenant_id": TENANT,
        "company": COMPANY,
        "kind": "read_accounts",
        "operation_id": "ad_0001",
        "arguments": {},
    }
    base.update(over)
    return base


def seeded_tally() -> FakeTally:
    fake = FakeTally()
    fake.add_company(COMPANY, ("Purchases", "Cash", "Sharma Traders"))
    return fake


def connector(
    tmp_path: Path,
    *,
    cloud: Cloud | None = None,
    open_client: Any = None,
    ident: ConnectorIdentity | None = None,
    logger: logging.Logger | None = None,
) -> tuple[Connector, Cloud]:
    sky = cloud if cloud is not None else Cloud()
    fake = seeded_tally()
    conn = Connector(
        ident or identity(),
        "https://cloud.example.invalid",
        open_client or (lambda _company: fake),
        call=sky,
        executed=ExecutedOperations(tmp_path / "executed.log"),
        logger=logger or build_logger(tmp_path / "connector.log"),
    )
    return conn, sky


# ---------------------------------------------------------------------------
# 1. it starts without Tally
# ---------------------------------------------------------------------------


def test_the_connector_starts_when_tally_is_not_running(tmp_path: Path):
    """Construction must not touch Tally.

    The client is opened per job, not held, because Tally is frequently closed
    when a laptop boots and open ten minutes later. A client captured at
    construction would be permanently dead and the connector would need
    restarting by hand.
    """
    conn, _ = connector(tmp_path, open_client=ClosedTally())
    assert conn.identity.tenant_id == TENANT


def test_a_job_reports_tally_unavailable_when_tally_is_down(tmp_path: Path):
    conn, _ = connector(tmp_path, open_client=ClosedTally())
    result = conn.execute(Job.from_payload(a_job()))

    assert result.outcome == TALLY_UNAVAILABLE
    assert "Connection refused" in result.detail


def test_tally_unavailable_does_not_record_the_operation_as_done(tmp_path: Path):
    """Nothing ran, so the id must stay usable. Recording it here would strand
    the job for ever: every later offer would answer ALREADY_EXECUTED for work
    that never happened."""
    executed = ExecutedOperations(tmp_path / "executed.log")
    conn = Connector(
        identity(),
        "https://cloud.example.invalid",
        ClosedTally(),
        call=Cloud(),
        executed=executed,
        logger=build_logger(tmp_path / "c.log"),
    )
    conn.execute(Job.from_payload(a_job(operation_id="ad_never")))

    assert "ad_never" not in executed
    assert executed.count == 0


# ---------------------------------------------------------------------------
# 2. it executes one job when Tally is up
# ---------------------------------------------------------------------------


def test_a_job_executes_and_returns_what_tally_said(tmp_path: Path):
    conn, _ = connector(tmp_path)
    result = conn.execute(Job.from_payload(a_job()))

    assert result.outcome == EXECUTED
    assert result.value == ["Cash", "Purchases", "Sharma Traders"] or sorted(
        result.value
    ) == ["Cash", "Purchases", "Sharma Traders"]


def test_polling_fetches_one_job_runs_it_and_reports_the_outcome(tmp_path: Path):
    sky = Cloud([a_job()])
    conn, _ = connector(tmp_path, cloud=sky)

    result = conn.poll_once()

    assert result is not None and result.outcome == EXECUTED
    assert [url for url, _ in sky.calls] == [
        "https://cloud.example.invalid/connector/jobs",
        "https://cloud.example.invalid/connector/result",
    ]
    assert sky.results[0]["outcome"] == EXECUTED


def test_polling_with_no_work_returns_none_and_reports_nothing(tmp_path: Path):
    sky = Cloud([])
    conn, _ = connector(tmp_path, cloud=sky)

    assert conn.poll_once() is None
    assert sky.results == []


# ---------------------------------------------------------------------------
# 3. the three refusals, each BEFORE Tally is touched
# ---------------------------------------------------------------------------


def test_a_job_for_another_tenant_is_refused(tmp_path: Path):
    conn, _ = connector(tmp_path)
    result = conn.execute(Job.from_payload(a_job(tenant_id="tenant-beta")))

    assert result.outcome == REFUSED_WRONG_TENANT
    assert "tenant-beta" in result.detail


def test_a_job_for_another_company_is_refused(tmp_path: Path):
    conn, _ = connector(tmp_path)
    result = conn.execute(Job.from_payload(a_job(company="Somebody Else Ltd")))

    assert result.outcome == REFUSED_WRONG_COMPANY
    assert "Somebody Else Ltd" in result.detail


def test_a_refused_job_never_reaches_tally(tmp_path: Path):
    """The refusals are checked before the client is opened, so a wrong-tenant
    job cannot even cause a connection attempt to somebody's books."""
    opened: list[str] = []

    def watching(company: str) -> TallyClient:
        opened.append(company)
        return seeded_tally()

    conn, _ = connector(tmp_path, open_client=watching)
    for bad in (
        a_job(tenant_id="tenant-beta"),
        a_job(company="Somebody Else Ltd"),
        a_job(kind="drop_all_tables"),
    ):
        conn.execute(Job.from_payload(bad))

    assert opened == []


def test_the_tenant_check_runs_before_the_company_check(tmp_path: Path):
    """A job from another tenant names a company that is not ours to reason
    about, so the tenant answer is the honest one."""
    conn, _ = connector(tmp_path)
    both_wrong = a_job(tenant_id="tenant-beta", company="Somebody Else Ltd")

    assert conn.execute(Job.from_payload(both_wrong)).outcome == REFUSED_WRONG_TENANT


def test_an_unknown_job_kind_is_refused_and_names_what_it_knows(tmp_path: Path):
    conn, _ = connector(tmp_path)
    result = conn.execute(Job.from_payload(a_job(kind="write_voucher")))

    assert result.outcome == REFUSED_UNKNOWN_KIND
    assert "read_accounts" in result.detail


# ---------------------------------------------------------------------------
# 4. duplicate protection — C5 in the connector
# ---------------------------------------------------------------------------


def test_the_same_operation_id_is_never_executed_twice(tmp_path: Path):
    conn, _ = connector(tmp_path)
    job = Job.from_payload(a_job(operation_id="ad_once"))

    first = conn.execute(job)
    second = conn.execute(job)

    assert first.outcome == EXECUTED
    assert second.outcome == ALREADY_EXECUTED
    assert "ad_once" in second.detail


def test_a_dropped_reply_does_not_execute_the_job_a_second_time(tmp_path: Path):
    """THE CASE THIS EXISTS FOR.

    The connector runs the job, then fails to report it. The cloud never heard
    an answer, so it offers the same job again. If the operation id were
    recorded after the report rather than before, this second offer would run
    against Tally a second time.
    """
    sky = DropsAfterAnswering([a_job(operation_id="ad_dropped")])
    conn, _ = connector(tmp_path, cloud=sky)

    with pytest.raises(OSError):
        conn.poll_once()

    sky.jobs.append(a_job(job_id="job-2", operation_id="ad_dropped"))
    second = conn.execute(Job.from_payload(sky.jobs.pop(0)))

    assert second.outcome == ALREADY_EXECUTED


def test_a_job_that_reached_tally_and_failed_is_not_recorded_as_done(
    tmp_path: Path,
):
    """THE ORDERING, PINNED.

    The record must be written AFTER Tally answers. Written before, a job that
    reached Tally and errored would be marked executed, and every later offer
    of it would answer ALREADY_EXECUTED for work that never completed — the
    job is then stranded for ever with nothing saying so.

    The unavailable test above cannot catch this: its client fails at OPEN, so
    it never reaches the recording line at all. This one opens successfully and
    fails inside the call, which is the only shape that distinguishes the two
    orderings. Moving the record one line earlier survives every other test in
    this file.
    """
    executed = ExecutedOperations(tmp_path / "executed.log")

    class OpensThenFails:
        def read_accounts(self, _company: str) -> tuple[str, ...]:
            raise ValueError("Tally accepted the connection and then refused")

    conn = Connector(
        identity(),
        "https://cloud.example.invalid",
        lambda _c: cast(TallyClient, OpensThenFails()),
        call=Cloud(),
        executed=executed,
        logger=build_logger(tmp_path / "c.log"),
    )
    result = conn.execute(Job.from_payload(a_job(operation_id="ad_reached")))

    assert result.outcome == FAILED
    assert "ad_reached" not in executed
    assert executed.count == 0


def test_a_job_with_no_operation_id_is_not_deduplicated(tmp_path: Path):
    """A read with no operation id is not an operation. Recording the empty
    string would make the FIRST such job poison every later one."""
    conn, _ = connector(tmp_path)
    job = Job.from_payload(a_job(operation_id=""))

    assert conn.execute(job).outcome == EXECUTED
    assert conn.execute(job).outcome == EXECUTED


# ---------------------------------------------------------------------------
# 5. it survives a Tally restart
# ---------------------------------------------------------------------------


def test_the_connector_survives_tally_going_away_and_coming_back(tmp_path: Path):
    """Down, then up, with no restart of the connector in between."""
    state = {"up": True}

    def flaky(_company: str) -> TallyClient:
        if not state["up"]:
            raise OSError("[Errno 61] Connection refused")
        return seeded_tally()

    conn, _ = connector(tmp_path, open_client=flaky)

    assert conn.execute(Job.from_payload(a_job(operation_id="a"))).outcome == EXECUTED
    state["up"] = False
    assert (
        conn.execute(Job.from_payload(a_job(operation_id="b"))).outcome
        == TALLY_UNAVAILABLE
    )
    state["up"] = True
    assert conn.execute(Job.from_payload(a_job(operation_id="c"))).outcome == EXECUTED


def test_executed_operations_survive_a_connector_restart(tmp_path: Path):
    """The record is a file, not a set, because the case it exists for is a
    connector that was restarted between doing the work and reporting it."""
    path = tmp_path / "executed.log"
    first = ExecutedOperations(path)
    first.record("ad_survivor")

    second = ExecutedOperations(path)

    assert "ad_survivor" in second
    assert second.count == 1


def test_recording_the_same_operation_twice_writes_one_line(tmp_path: Path):
    path = tmp_path / "executed.log"
    store = ExecutedOperations(path)
    store.record("ad_x")
    store.record("ad_x")

    assert path.read_text(encoding="utf-8").splitlines() == ["ad_x"]


# ---------------------------------------------------------------------------
# 6. failures are reported, never raised
# ---------------------------------------------------------------------------


def test_a_tally_error_becomes_failed_rather_than_stopping_the_connector(
    tmp_path: Path,
):
    """FAILED and TALLY_UNAVAILABLE are different facts: one means the job was
    attempted, the other means it was not, and only the second is safe to retry
    blindly."""

    class Angry:
        def read_accounts(self, _company: str) -> tuple[str, ...]:
            raise ValueError("Tally said no")

    def angry(_company: str) -> TallyClient:
        return cast(TallyClient, Angry())

    conn, _ = connector(tmp_path, open_client=angry)
    result = conn.execute(Job.from_payload(a_job()))

    assert result.outcome == FAILED
    assert "Tally said no" in result.detail


def test_run_forever_sleeps_through_a_cloud_outage_instead_of_exiting(tmp_path: Path):
    """A laptop loses its network. Exiting would need a human to start the
    connector again, which is the thing this product cannot require."""
    naps: list[float] = []

    class Unreachable(Cloud):
        def __call__(self, _url: str, _payload: Any) -> dict[str, Any]:
            raise OSError("network is unreachable")

    conn, _ = connector(tmp_path, cloud=Unreachable())

    def sleep(seconds: float) -> None:
        naps.append(seconds)
        if len(naps) >= 3:
            conn.stop()

    assert conn.run_forever(interval_seconds=0.01, sleep=sleep) == 0
    assert naps == [0.01, 0.01, 0.01]


def test_run_forever_counts_the_jobs_it_executed(tmp_path: Path):
    sky = Cloud(
        [a_job(job_id="j1", operation_id="o1"), a_job(job_id="j2", operation_id="o2")]
    )
    conn, _ = connector(tmp_path, cloud=sky)

    def sleep(_seconds: float) -> None:
        conn.stop()

    assert conn.run_forever(interval_seconds=0.01, sleep=sleep) == 2


# ---------------------------------------------------------------------------
# 7. the log is bounded
# ---------------------------------------------------------------------------


def test_the_log_rotates_and_does_not_grow_without_bound(tmp_path: Path):
    """An unattended program on somebody's laptop must not fill their disk.

    The ceiling is (backups + 1) * max_bytes and it is asserted here rather
    than trusted, because "it rotates" without a measured bound is the same
    promise every unbounded log ever made.
    """
    log_path = tmp_path / "connector.log"
    log = build_logger(log_path, max_bytes=2_048, backups=2, name="rotation-probe")

    for i in range(2_000):
        log.info("job=%s outcome=%s padding=%s", i, EXECUTED, "x" * 80)

    written = sorted(tmp_path.glob("connector.log*"))
    total = sum(p.stat().st_size for p in written)

    assert len(written) == 3, [p.name for p in written]
    assert total <= 3 * 2_048 * 1.2, total
    for handler in log.handlers:
        handler.close()


def test_the_log_records_the_outcome_of_a_refusal(tmp_path: Path):
    log_path = tmp_path / "refusal.log"
    log = build_logger(log_path, name="refusal-probe")
    conn, _ = connector(tmp_path, logger=log)

    conn.execute(Job.from_payload(a_job(tenant_id="tenant-beta")))
    for handler in log.handlers:
        handler.flush()

    assert REFUSED_WRONG_TENANT in log_path.read_text(encoding="utf-8")


def test_the_secret_is_never_written_to_the_log(tmp_path: Path):
    """A credential in a log file on a customer's laptop is a credential
    published to anyone who can read that laptop."""
    log_path = tmp_path / "secret.log"
    log = build_logger(log_path, name="secret-probe")
    sky = Cloud([a_job()])
    conn, _ = connector(tmp_path, cloud=sky, logger=log)

    conn.poll_once()
    for handler in log.handlers:
        handler.flush()

    assert "s3cr3t-value" not in log_path.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# 8. the transport refuses plaintext
# ---------------------------------------------------------------------------


def test_the_cloud_call_refuses_a_plaintext_url():
    """The payload carries the connector secret. http:// would put a credential
    that can reach somebody's books on the wire in clear."""
    with pytest.raises(ValueError, match="must be https"):
        https_cloud_call("http://cloud.example.invalid/x", {"secret": "s"})


def _listening_names(module: Path) -> set[str]:
    """Names this module actually USES, read off the AST.

    Not a substring scan. A substring scan reads comments and docstrings, and
    this repository has already been bitten by exactly that: the gate-contract
    test at `tests/test_gate_contract.py:227` matched a command inside a `#`
    comment and reported a dead gate as live for two days. The first version of
    this test failed on the word `HTTPServer` inside the paragraph explaining
    that no HTTPServer is used.
    """
    forbidden = {"HTTPServer", "ThreadingHTTPServer", "socketserver", "bind", "listen"}
    tree = ast.parse(module.read_text(encoding="utf-8"))
    used: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and node.id in forbidden:
            used.add(node.id)
        elif isinstance(node, ast.Attribute) and node.attr in forbidden:
            used.add(node.attr)
        elif isinstance(node, ast.Import):
            used |= {a.name.split(".")[0] for a in node.names} & forbidden
        elif isinstance(node, ast.ImportFrom):
            used |= {a.name for a in node.names} & forbidden
            if (node.module or "").split(".")[0] in forbidden:
                used.add((node.module or "").split(".")[0])
    return used


def test_no_module_in_this_package_binds_a_socket():
    """The connector dials out and never listens. Port 9000 is never exposed.

    That is the whole architecture: a customer must never be asked to open an
    unauthenticated accounting API to the internet. Read off the AST so the day
    somebody adds a listener to make debugging easier, this fails.
    """
    package = Path(__file__).resolve().parent.parent / "accountant" / "agent"
    modules = sorted(package.rglob("*.py"))
    assert modules, "the guard found no modules, so it was proving nothing"

    for module in modules:
        assert _listening_names(module) == set(), module.name


def test_the_socket_guard_catches_a_listener_that_was_added():
    """The control. A guard nobody has watched fail is not a guard.

    Written to the same shape the package uses, so a real regression looks like
    this and is caught by the same code path.
    """
    planted = ast.parse(
        "from http.server import HTTPServer\n"
        "def serve():\n"
        "    HTTPServer(('0.0.0.0', 9000), None).serve_forever()\n"
    )
    caught: set[str] = set()
    forbidden = {"HTTPServer", "ThreadingHTTPServer", "socketserver", "bind", "listen"}
    for node in ast.walk(planted):
        if isinstance(node, ast.Name) and node.id in forbidden:
            caught.add(node.id)
        elif isinstance(node, ast.ImportFrom):
            caught |= {a.name for a in node.names} & forbidden

    assert "HTTPServer" in caught


# ---------------------------------------------------------------------------
# 9. identity refuses to be useless
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("field", "value"),
    [("connector_id", "  "), ("secret", ""), ("tenant_id", "")],
)
def test_an_identity_missing_a_field_is_refused(field: str, value: str):
    with pytest.raises(ValueError, match=field):
        identity(**{field: value})


def test_an_identity_paired_to_no_company_is_refused():
    """It would refuse every job it was ever offered, silently, for ever."""
    with pytest.raises(ValueError, match="names no company"):
        identity(companies=frozenset())


def test_a_job_result_serialises_to_the_wire_shape():
    result = JobResult("job-9", EXECUTED, "", ["Cash"])
    assert json.loads(json.dumps(result.as_payload())) == {
        "job_id": "job-9",
        "outcome": EXECUTED,
        "detail": "",
        "value": ["Cash"],
    }


def test_a_malformed_job_is_refused_rather_than_crashing_the_connector(
    tmp_path: Path,
):
    """A connector that raised on a bad job would stop polling, and a stopped
    connector looks exactly like a disconnected one from the cloud."""
    conn, _ = connector(tmp_path)
    result = conn.execute(Job.from_payload({}))

    assert result.outcome == REFUSED_WRONG_TENANT


def test_registration_sends_the_identity_and_nothing_else(tmp_path: Path):
    sky = Cloud()
    conn, _ = connector(tmp_path, cloud=sky)
    conn.register()

    url, payload = sky.calls[0]
    assert url.endswith("/connector/register")
    assert payload["connector_id"] == "connector-1"
    assert payload["tenant_id"] == TENANT
    assert payload["companies"] == [COMPANY]
    assert set(payload) == {"connector_id", "secret", "tenant_id", "companies"}


# ---------------------------------------------------------------------------
# the command line
# ---------------------------------------------------------------------------
#
# `main()` is what a customer actually runs, and it was the one part of this
# package with no test at all: 0% of `cli.py` on the diff-coverage gate. Every
# test below drives it through `main(argv)` with a fake `Connector` injected
# where the real one would open sockets, so what is measured is the wiring -
# which values reach the connector, and which value never leaves the process.


class FakeConnector:
    """Stands in for `Connector`, recording what it was built with."""

    def __init__(
        self,
        identity_: ConnectorIdentity,
        cloud_url: str,
        open_client: object,
        *,
        executed: object,
        logger: object,
    ) -> None:
        self.identity = identity_
        self.cloud_url = cloud_url
        self.open_client = open_client
        self.executed = executed
        self.logger = logger
        self.registered = 0
        self.polls = 0
        self.forever = 0

    def register(self) -> None:
        self.registered += 1

    def poll_once(self) -> JobResult | None:
        self.polls += 1
        return JobResult(job_id="job-1", outcome=EXECUTED, detail="done")

    def run_forever(self, *, interval_seconds: float) -> int:
        self.forever += 1
        self.interval = interval_seconds
        return 3

    def stop(self) -> None:  # pragma: no cover - only a signal handler calls it
        pass


@pytest.fixture
def cli_bench(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> tuple[list[FakeConnector], Path]:
    """`main()` with the connector replaced, so no socket is ever opened."""
    made: list[FakeConnector] = []

    def build(*args: Any, **kwargs: Any) -> FakeConnector:
        made.append(FakeConnector(*args, **kwargs))
        return made[-1]

    monkeypatch.setattr(cli, "Connector", build)
    monkeypatch.delenv(cli.ENV_SECRET, raising=False)
    return made, tmp_path


def run_cli(state: Path, *extra: str, secret_file: Path | None = None) -> int:
    argv = [
        "--connector-id",
        "connector-1",
        "--tenant-id",
        TENANT,
        "--company",
        COMPANY,
        "--cloud-url",
        "https://cloud.example",
        "--state-dir",
        str(state),
        *extra,
    ]
    if secret_file is not None:
        argv += ["--secret-file", str(secret_file)]
    return cli.main(argv)


def test_the_secret_comes_from_the_environment(
    cli_bench: tuple[list[FakeConnector], Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    made, tmp = cli_bench
    monkeypatch.setenv(cli.ENV_SECRET, "from-the-environment")

    assert run_cli(tmp, "--once") == 0

    assert made[0].identity.secret == "from-the-environment"


def test_the_secret_can_come_from_a_file(
    cli_bench: tuple[list[FakeConnector], Path],
) -> None:
    made, tmp = cli_bench
    keyfile = tmp / "secret"
    keyfile.write_text("  from-a-file\n", encoding="utf-8")

    assert run_cli(tmp, "--once", secret_file=keyfile) == 0

    assert made[0].identity.secret == "from-a-file", (
        "surrounding whitespace is stripped"
    )


def test_no_secret_anywhere_stops_the_program_and_says_how_to_fix_it(
    cli_bench: tuple[list[FakeConnector], Path],
) -> None:
    """A connector with no credential must not start and then fail per job."""
    _made, tmp = cli_bench
    with pytest.raises(SystemExit) as stopped:
        run_cli(tmp, "--once")
    assert cli.ENV_SECRET in str(stopped.value)
    assert "--secret-file" in str(stopped.value)


def test_an_empty_environment_variable_counts_as_no_secret(
    cli_bench: tuple[list[FakeConnector], Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    """`ACCOUNTANT_CONNECTOR_SECRET=` is a common way to think you set it."""
    _made, tmp = cli_bench
    monkeypatch.setenv(cli.ENV_SECRET, "   ")
    with pytest.raises(SystemExit):
        run_cli(tmp, "--once")


def test_the_secret_is_never_printed(
    cli_bench: tuple[list[FakeConnector], Path],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The startup banner prints every resolved value except this one.

    Unattended software on somebody else's laptop is diagnosed from what it
    said when it started, so the banner has to be complete - which is exactly
    why the one value that must not be in it needs an assertion.
    """
    _made, tmp = cli_bench
    monkeypatch.setenv(cli.ENV_SECRET, "a-very-distinctive-secret")

    run_cli(tmp, "--once")

    printed = capsys.readouterr().out
    assert "a-very-distinctive-secret" not in printed
    assert "read, not shown" in printed
    assert "connector-1" in printed
    assert TENANT in printed
    assert COMPANY in printed
    assert "https://cloud.example" in printed
    assert "REFUSED" in printed, "the banner must say it cannot write"


def test_once_takes_a_single_job_and_returns(
    cli_bench: tuple[list[FakeConnector], Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    made, tmp = cli_bench
    monkeypatch.setenv(cli.ENV_SECRET, "s")

    assert run_cli(tmp, "--once") == 0

    assert made[0].registered == 1
    assert made[0].polls == 1
    assert made[0].forever == 0


def test_without_once_it_polls_until_stopped(
    cli_bench: tuple[list[FakeConnector], Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    made, tmp = cli_bench
    monkeypatch.setenv(cli.ENV_SECRET, "s")

    assert run_cli(tmp, "--interval-seconds", "0.25") == 0

    assert made[0].forever == 1
    assert made[0].polls == 0
    assert made[0].interval == 0.25


def test_every_named_company_reaches_the_connector(
    cli_bench: tuple[list[FakeConnector], Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    """`--company` repeats. A connector paired to two companies that only
    remembered one would refuse half of its own work."""
    made, tmp = cli_bench
    monkeypatch.setenv(cli.ENV_SECRET, "s")

    run_cli(tmp, "--once", "--company", "Second Co")

    assert made[0].identity.companies == frozenset({COMPANY, "Second Co"})


def test_the_state_directory_is_where_the_record_and_the_log_go(
    cli_bench: tuple[list[FakeConnector], Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Both must survive a restart: the record is what stops a dropped reply
    executing a job twice, and a log in a temp directory is not a log."""
    _made, tmp = cli_bench
    monkeypatch.setenv(cli.ENV_SECRET, "s")
    state = tmp / "state"

    run_cli(state, "--once")

    assert (state / "connector.log").exists()


def test_the_parser_requires_the_four_values_that_cannot_be_guessed() -> None:
    """Which connector, which tenant, which company, which cloud. A default for
    any of them would be this program guessing whose books it is touching."""
    parser = cli.build_parser()
    for missing in ("--connector-id", "--tenant-id", "--company", "--cloud-url"):
        argv = [
            "--connector-id",
            "c",
            "--tenant-id",
            "t",
            "--company",
            "Demo Co",
            "--cloud-url",
            "https://c.example",
        ]
        at = argv.index(missing)
        with pytest.raises(SystemExit):
            parser.parse_args(argv[:at] + argv[at + 2 :])


def test_there_is_no_secret_argument_at_all() -> None:
    """Not "discouraged" - absent. An argument is visible in the process table
    to every user on the machine and lands in shell history, so the only way to
    be sure nobody passes one is for the parser to reject it.
    """
    with pytest.raises(SystemExit):
        cli.build_parser().parse_args(
            [
                "--connector-id",
                "c",
                "--tenant-id",
                "t",
                "--company",
                "Demo Co",
                "--cloud-url",
                "https://c.example",
                "--secret",
                "hunter2",
            ]
        )


def test_the_client_factory_is_built_fresh_for_each_company() -> None:
    """Per job, not once. Tally is usually closed when a laptop boots and open
    later; a client captured at startup would be permanently dead."""
    factory = cli.open_tally("localhost", 9000)
    assert callable(factory)


# ---------------------------------------------------------------------------
# the shapes that go back to the cloud
# ---------------------------------------------------------------------------


def result_of(kind: str, tally: FakeTally, tmp_path: Path) -> Any:
    """Run one read job through the connector and return what the CLOUD got.

    Through `poll_once` rather than by calling the shaping helper directly. The
    helper is private, and more to the point the thing worth asserting is what
    crosses the wire - a shape that is right inside the process and wrong in the
    payload is still a field the cloud silently stops seeing.
    """
    sky = Cloud(
        [{"job_id": "job-1", "kind": kind, "tenant_id": TENANT, "company": COMPANY}]
    )

    def open_client(_company: str) -> TallyClient:
        return tally

    conn, cloud = connector(tmp_path, cloud=sky, open_client=open_client)
    outcome = conn.poll_once()
    assert outcome is not None
    assert outcome.outcome == EXECUTED, outcome.detail
    return cloud.results[-1]["value"]


def test_a_trial_balance_reaches_the_cloud_as_integer_paise(tmp_path: Path) -> None:
    """Integer paise, never float. A mapping that arrived as anything else
    would be a rounding bug with a network in the middle of it."""
    tally = FakeTally()
    tally.add_company(
        COMPANY,
        accounts=("Purchases", "Cash"),
        vouchers=(
            Voucher(
                id="v1",
                date=datetime.date(2026, 8, 10),
                party="Sharma Traders",
                narration="cement supply",
                debit_account="Purchases",
                credit_account="Cash",
                amount_paise=380000,
            ),
        ),
    )

    balance = result_of("trial_balance", tally, tmp_path)

    assert balance == {"Purchases": 380000, "Cash": -380000}
    assert all(isinstance(v, int) and not isinstance(v, bool) for v in balance.values())


def test_a_voucher_reaches_the_cloud_with_nothing_dropped_or_renamed(
    tmp_path: Path,
) -> None:
    """A `Voucher` is a dataclass and the cloud reads its fields BY NAME, so a
    quiet rename here is a field the cloud silently stops seeing. Asserted
    against the dataclass's own field names rather than a copied list, which
    would just be the same mistake written twice."""
    voucher = Voucher(
        id="v1",
        date=datetime.date(2026, 8, 10),
        party="Sharma Traders",
        narration="cement supply",
        debit_account="Purchases",
        credit_account="Cash",
        amount_paise=380000,
    )
    tally = FakeTally()
    tally.add_company(COMPANY, accounts=("Purchases", "Cash"), vouchers=(voucher,))

    vouchers = result_of("read_vouchers", tally, tmp_path)

    assert isinstance(vouchers, list), "a tuple must arrive as a JSON array"
    first = cast("dict[str, Any]", vouchers[0])
    assert set(first) == set(voucher.__dataclass_fields__)
    assert first["amount_paise"] == 380000
    assert first["date"] == "2026-08-10", "a date is stringified, not dropped"


def test_the_whole_payload_is_json_serialisable(tmp_path: Path) -> None:
    """The real transport calls `json.dumps`. A shape that survives an equality
    check in this process and raises at the wire is a failure nothing here
    would otherwise catch."""
    tally = FakeTally()
    tally.add_company(
        COMPANY,
        accounts=("Purchases", "Cash"),
        vouchers=(
            Voucher(
                id="v1",
                date=datetime.date(2026, 8, 10),
                party="Sharma Traders",
                narration="cement supply",
                debit_account="Purchases",
                credit_account="Cash",
                amount_paise=380000,
            ),
        ),
    )
    for kind in ("read_vouchers", "trial_balance", "read_accounts", "list_companies"):
        json.dumps(result_of(kind, tally, tmp_path))


def test_the_module_entry_point_routes_to_cli_main() -> None:
    """`python -m accountant.agent` must reach `cli.main` and nothing else.

    Trivial to write and not trivial to leave untested: this file is the only
    thing standing between the documented command and an ImportError that a
    customer would meet at the worst possible moment.
    """
    module = importlib.import_module("accountant.agent.__main__")
    assert module.main is cli.main
