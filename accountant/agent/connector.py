"""One job at a time, from the cloud to a Tally that is on this machine.

THE SHAPE, AND WHY IT IS THIS SHAPE
-----------------------------------
The connector polls. It does not listen. `poll_once` asks the cloud for at most
one job, runs it, reports the outcome, and returns. `run_forever` is that in a
loop with a sleep. There is no socket bound anywhere in this module, and a
`grep` for `bind`, `listen` or `HTTPServer` in this package returns nothing.

One job at a time is deliberate. TallyPrime processes one XML request at a time
(`accountant/tallyio/real.py:2018`), so a connector that fetched a batch would
serialise them against Tally anyway, while making the failure story worse: a
crash mid-batch leaves several jobs in an unknown state instead of one.

THE TRANSPORT IS INJECTED
-------------------------
`CloudCall` is a function, exactly as `accountant.extract.service.ServiceCall`
is. The default is `https_cloud_call`, which is the only place in this package
that opens a socket. Every test supplies its own function and no test needs a
server. That also means the refusal logic is testable without TLS, without a
certificate, and without a cloud existing yet — which it does not.

WHAT AN UNKNOWN OUTCOME MEANS HERE
----------------------------------
If Tally accepts a write and the connector then fails to report it, the cloud
re-offers the job. That is the case `ExecutedOperations` exists for: the
operation id is recorded **before** the answer is sent, so the second offer is
answered `ALREADY_EXECUTED` from local disk rather than executed again.

The recording therefore happens after Tally answers and before the cloud does.
Getting that order wrong in either direction is a real defect: record too early
and a job that never ran is marked done; record too late and a dropped reply
duplicates a statutory entry.
"""

from __future__ import annotations

import json
import logging
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any, cast

from accountant import redact
from accountant.tallyio.client import (
    CompanyNotBackedUp,
    DuplicateOperation,
    TallyClient,
)

# ---------------------------------------------------------------------------
# Outcomes. Each one is a different fact, and none of them is a synonym.
# ---------------------------------------------------------------------------

#: Tally ran the job and answered.
EXECUTED = "EXECUTED"

#: Tally could not be reached. The job was NOT run and may be offered again.
#: Distinct from FAILED: nothing happened, so a retry is safe.
TALLY_UNAVAILABLE = "TALLY_UNAVAILABLE"

#: This connector has already run this operation id. Nothing was done now.
ALREADY_EXECUTED = "ALREADY_EXECUTED"

#: The job names a tenant this connector does not belong to.
REFUSED_WRONG_TENANT = "REFUSED_WRONG_TENANT"

#: The job names a company this connector was not paired to.
REFUSED_WRONG_COMPANY = "REFUSED_WRONG_COMPANY"

#: The job asks for something this connector does not know how to do.
REFUSED_UNKNOWN_KIND = "REFUSED_UNKNOWN_KIND"

#: Tally was reached, understood the job, and refused or errored on it. The job
#: was attempted. A retry is NOT automatically safe.
FAILED = "FAILED"

#: Job kinds the connector will run. Read-only by design in this task: the
#: write path reaches Tally through `pipeline.post` and its gate stack, and
#: handing the connector a bare write would put a second door beside the one
#: `tests/test_runtime_backend.py` exists to keep singular.
READ_KINDS: dict[str, str] = {
    "list_companies": "list_companies",
    "read_accounts": "read_accounts",
    "read_vouchers": "read_vouchers",
    "trial_balance": "trial_balance",
    "list_our_vouchers": "list_our_vouchers",
    "backed_up": "backed_up",
}


# ---------------------------------------------------------------------------
# The wire shapes
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ConnectorIdentity:
    """Who this connector is, and what it is allowed to touch.

    `secret` is the shared credential presented on every outbound call. It is
    never printed, and never written into a job result or a log line.

    Constructing one REGISTERS the secret with `accountant.redact`, 2026-08-11.
    Registration happens here, and not at each place a log line is written,
    because there is no way to have a connector secret without building one of
    these — so the redactor learns it whether or not anybody remembered. The
    alternative was a `redact.learn_secret(...)` call in `cli.main`, which is
    one line somebody deletes while refactoring startup, after which the guard
    is silently off and every test still passes.
    """

    connector_id: str
    secret: str
    tenant_id: str
    companies: frozenset[str]

    def __post_init__(self) -> None:
        for name, value in (
            ("connector_id", self.connector_id),
            ("secret", self.secret),
            ("tenant_id", self.tenant_id),
        ):
            if not value.strip():
                raise ValueError(f"connector identity needs a {name}")
        if not self.companies:
            raise ValueError(
                "connector identity names no company, so every job it is ever "
                "offered would be refused; pair it to at least one"
            )
        # After the refusals, so a blank secret is never learned. The return
        # value is deliberately ignored: a secret below `redact.MIN_LEARNABLE`
        # is still caught by the key-name patterns, and refusing to construct
        # an identity over it would be a new startup refusal introduced by a
        # logging change. The reason for the floor is on the constant.
        redact.learn_secret(self.secret)


@dataclass(frozen=True)
class Job:
    """One unit of work, as the cloud describes it."""

    job_id: str
    tenant_id: str
    company: str
    kind: str
    operation_id: str = ""
    arguments: Mapping[str, Any] = field(default_factory=dict[str, Any])

    @staticmethod
    def from_payload(payload: Mapping[str, Any]) -> Job:
        """Build a job from whatever the cloud sent. Missing fields become empty
        strings rather than raising, because a malformed job must be REFUSED
        with a reason the cloud can read, not crash the connector."""
        return Job(
            job_id=str(payload.get("job_id", "")),
            tenant_id=str(payload.get("tenant_id", "")),
            company=str(payload.get("company", "")),
            kind=str(payload.get("kind", "")),
            operation_id=str(payload.get("operation_id", "")),
            arguments=dict(payload.get("arguments") or {}),
        )


@dataclass(frozen=True)
class JobResult:
    """What happened. `outcome` is one of the module constants above."""

    job_id: str
    outcome: str
    detail: str = ""
    value: Any = None

    def as_payload(self) -> dict[str, Any]:
        return {
            "job_id": self.job_id,
            "outcome": self.outcome,
            "detail": self.detail,
            "value": self.value,
        }


# ---------------------------------------------------------------------------
# Durable memory of what this connector has already done
# ---------------------------------------------------------------------------


class ExecutedOperations:
    """Operation ids this connector has run, on disk, one per line.

    A file rather than a set in memory, because the case this exists for is a
    connector that was restarted between doing the work and reporting it. An
    in-memory record would forget exactly when it matters.

    Append-only. There is no removal method: an operation id that has been used
    is used, and the reversal of a voucher does not release its identity — see
    the same rule in the cloud, where a reversed id is refused rather than
    recycled.
    """

    def __init__(self, path: Path | str) -> None:
        self._path = Path(path)
        self._seen: set[str] = set()
        if self._path.exists():
            self._seen = {
                line.strip()
                for line in self._path.read_text(encoding="utf-8").splitlines()
                if line.strip()
            }

    def __contains__(self, operation_id: str) -> bool:
        return operation_id in self._seen

    def record(self, operation_id: str) -> None:
        if not operation_id or operation_id in self._seen:
            return
        self._seen.add(operation_id)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with self._path.open("a", encoding="utf-8") as fh:
            fh.write(f"{operation_id}\n")
            fh.flush()

    @property
    def count(self) -> int:
        return len(self._seen)


# ---------------------------------------------------------------------------
# The transport
# ---------------------------------------------------------------------------

#: `(url, payload) -> response`. Injected so this module opens no socket in a
#: test, and so the refusal logic can be proved without a cloud existing.
CloudCall = Callable[[str, Mapping[str, Any]], Mapping[str, Any]]


def https_cloud_call(
    url: str, payload: Mapping[str, Any], *, timeout: float = 30.0
) -> Mapping[str, Any]:
    """The only place in this package that opens a socket.

    HTTPS is required and not merely preferred: the payload carries the
    connector secret, and a plaintext scheme would put a credential that can
    write to somebody's statutory books on the wire. A non-https URL is refused
    here rather than at review time.
    """
    if not url.lower().startswith("https://"):
        raise ValueError(
            f"the cloud URL must be https, and this one is {url!r}. The payload "
            "carries the connector secret; sending it in plaintext would hand "
            "write access to anyone on the path."
        )
    body = json.dumps(dict(payload)).encode("utf-8")
    # Two suppressions, one fact: the scheme was checked five lines up and
    # anything but https already raised. Both linters have to be told
    # separately - the ruff marker and the bandit marker are different words
    # and neither tool reads the other's.
    request = urllib.request.Request(  # nosec B310  # noqa: S310
        url,
        data=body,
        method="POST",
        headers={"Content-Type": "application/json", "Content-Length": str(len(body))},
    )
    with urllib.request.urlopen(  # nosec B310  # noqa: S310
        request, timeout=timeout
    ) as answer:
        raw = answer.read().decode("utf-8")
    parsed: object = json.loads(raw) if raw.strip() else {}
    if not isinstance(parsed, dict):
        raise ValueError(f"the cloud answered {type(parsed).__name__}, not an object")
    answered = cast("Mapping[Any, Any]", parsed)
    return {str(key): item for key, item in answered.items()}


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------


def build_logger(
    log_path: Path | str,
    *,
    max_bytes: int = 1_048_576,
    backups: int = 3,
    name: str = "accountant.agent",
) -> logging.Logger:
    """A rotating file log, so an unattended connector cannot fill a disk.

    `max_bytes` and `backups` bound the total at (backups + 1) * max_bytes,
    which is 4 MB by default. The numbers are stated here rather than left to a
    library default so the ceiling is a fact somebody can read.

    REDACTION IS INSTALLED ON THE HANDLER, 2026-08-11
    -------------------------------------------------
    Not on the logger. `Logger.handle` applies only its own filters and then
    walks its ancestors' HANDLERS, so a filter sitting on `accountant.agent`
    would be skipped entirely for a record made by a child logger. The handler
    is the object that writes bytes to somebody's disk, so the handler is where
    the guard goes — and every line, from every logger that ever reaches it, is
    scrubbed without one call site knowing this happened.

    This file lives on a customer's laptop. `docs/DATA_POLICY.md` Table B row 3
    says a connector key is "never logged, in any form"; until this filter
    existed that was a sentence in a document with nothing enforcing it, and
    `docs/DATA_POLICY.md` §4 listed "no secret is ever logged" as a test that
    could exist and did not.
    """
    logger = logging.getLogger(name)
    logger.setLevel(logging.INFO)
    logger.handlers.clear()
    path = Path(log_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(
        path, maxBytes=max_bytes, backupCount=backups, encoding="utf-8"
    )
    handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s")
    )
    redact.guard(handler)
    logger.addHandler(handler)
    logger.propagate = False
    return logger


# ---------------------------------------------------------------------------
# The connector
# ---------------------------------------------------------------------------


class Connector:
    """Dials out, takes one job, runs it against local Tally, reports back.

    `open_client` is a callable rather than a client, because Tally may be
    closed when the connector starts and open later. Calling it per job is what
    makes "start without Tally, work once Tally appears" true rather than
    aspirational — a client captured at construction would be permanently dead.
    """

    def __init__(
        self,
        identity: ConnectorIdentity,
        cloud_url: str,
        open_client: Callable[[str], TallyClient],
        *,
        call: CloudCall = https_cloud_call,
        executed: ExecutedOperations | None = None,
        logger: logging.Logger | None = None,
    ) -> None:
        self.identity = identity
        self.cloud_url = cloud_url.rstrip("/")
        self._open_client = open_client
        self._call = call
        self._executed = executed or ExecutedOperations(Path("data/executed.log"))
        self._log = logger or logging.getLogger("accountant.agent")
        self._stopping = False

    # -- registration -------------------------------------------------------

    def register(self) -> Mapping[str, Any]:
        """Announce this connector to the cloud. Idempotent by connector id."""
        answer = self._call(
            f"{self.cloud_url}/connector/register",
            {
                "connector_id": self.identity.connector_id,
                "secret": self.identity.secret,
                "tenant_id": self.identity.tenant_id,
                "companies": sorted(self.identity.companies),
            },
        )
        self._log.info("registered connector_id=%s", self.identity.connector_id)
        return answer

    # -- the refusals -------------------------------------------------------

    def refusal_for(self, job: Job) -> JobResult | None:
        """The reason this job will not run, or None if it may.

        Checked in this order on purpose: tenant first, because a job from
        another tenant should never have been offered and the company name in
        it is not ours to reason about.
        """
        if job.tenant_id != self.identity.tenant_id:
            return JobResult(
                job.job_id,
                REFUSED_WRONG_TENANT,
                f"this connector belongs to tenant "
                f"{self.identity.tenant_id!r} and the job names "
                f"{job.tenant_id!r}",
            )
        if job.company not in self.identity.companies:
            return JobResult(
                job.job_id,
                REFUSED_WRONG_COMPANY,
                f"this connector is paired to "
                f"{sorted(self.identity.companies)} and the job names "
                f"{job.company!r}",
            )
        if job.operation_id and job.operation_id in self._executed:
            return JobResult(
                job.job_id,
                ALREADY_EXECUTED,
                f"operation {job.operation_id!r} has already been executed by "
                f"this connector; nothing was done now",
            )
        if job.kind not in READ_KINDS:
            return JobResult(
                job.job_id,
                REFUSED_UNKNOWN_KIND,
                f"this connector runs {sorted(READ_KINDS)} and the job asks "
                f"for {job.kind!r}",
            )
        return None

    # -- execution ----------------------------------------------------------

    def execute(self, job: Job) -> JobResult:
        """Run one job. Never raises: every failure becomes an outcome.

        A connector that raised would stop polling, and a stopped connector is
        indistinguishable from a disconnected one at the cloud end. Turning
        every failure into a reported outcome is what keeps the two apart.
        """
        refusal = self.refusal_for(job)
        if refusal is not None:
            self._log.info("job=%s outcome=%s", job.job_id, refusal.outcome)
            return refusal

        try:
            client = self._open_client(job.company)
        except Exception as exc:
            return self._unavailable(job, exc)

        method = getattr(client, READ_KINDS[job.kind])
        try:
            value = method() if job.kind == "list_companies" else method(job.company)
        except (DuplicateOperation, CompanyNotBackedUp) as exc:
            return self._failed(job, exc)
        except OSError as exc:
            return self._unavailable(job, exc)
        except Exception as exc:
            return self._failed(job, exc)

        # Recorded AFTER Tally answered and BEFORE the cloud is told. A dropped
        # reply then comes back as ALREADY_EXECUTED rather than running twice.
        if job.operation_id:
            self._executed.record(job.operation_id)

        self._log.info("job=%s outcome=%s", job.job_id, EXECUTED)
        return JobResult(job.job_id, EXECUTED, "", _plain(value))

    def _unavailable(self, job: Job, exc: BaseException) -> JobResult:
        detail = f"{type(exc).__name__}: {exc}"
        self._log.warning("job=%s outcome=%s %s", job.job_id, TALLY_UNAVAILABLE, detail)
        return JobResult(job.job_id, TALLY_UNAVAILABLE, detail)

    def _failed(self, job: Job, exc: BaseException) -> JobResult:
        detail = f"{type(exc).__name__}: {exc}"
        self._log.error("job=%s outcome=%s %s", job.job_id, FAILED, detail)
        return JobResult(job.job_id, FAILED, detail)

    # -- the loop -----------------------------------------------------------

    def poll_once(self) -> JobResult | None:
        """Ask for one job, run it, report it. None when there was no work."""
        answer = self._call(
            f"{self.cloud_url}/connector/jobs",
            {
                "connector_id": self.identity.connector_id,
                "secret": self.identity.secret,
                "tenant_id": self.identity.tenant_id,
            },
        )
        payload = answer.get("job")
        if not payload:
            return None
        result = self.execute(Job.from_payload(payload))
        self._call(
            f"{self.cloud_url}/connector/result",
            {
                "connector_id": self.identity.connector_id,
                "secret": self.identity.secret,
                **result.as_payload(),
            },
        )
        return result

    def stop(self) -> None:
        """Ask `run_forever` to finish the job in hand and return."""
        self._stopping = True

    def run_forever(
        self,
        *,
        interval_seconds: float = 2.0,
        sleep: Callable[[float], None] = time.sleep,
    ) -> int:
        """Poll until stopped. Returns the number of jobs executed.

        A transport failure is logged and slept through rather than raised: the
        cloud being briefly unreachable is a normal condition for a program
        running on somebody's laptop, and exiting on it would need a human to
        start the connector again.
        """
        done = 0
        while not self._stopping:
            try:
                if self.poll_once() is not None:
                    done += 1
                    continue
            except (urllib.error.URLError, OSError, ValueError) as exc:
                self._log.warning("poll failed %s: %s", type(exc).__name__, exc)
            sleep(interval_seconds)
        self._log.info("stopped after %d job(s)", done)
        return done


def _plain(value: Any) -> Any:
    """Make a Tally answer JSON-safe without inventing structure.

    Tuples become lists and dataclasses become dicts; anything already plain is
    returned untouched. Nothing is dropped or renamed, so a reader of the cloud
    side sees what Tally said.
    """
    if isinstance(value, Mapping):
        mapping = cast("Mapping[Any, Any]", value)
        return {str(key): _plain(item) for key, item in mapping.items()}
    if isinstance(value, str | bytes) or not isinstance(value, Sequence):
        return _scalar(value)
    items = cast("Sequence[Any]", value)
    return [_plain(item) for item in items]


def _scalar(value: Any) -> Any:
    if isinstance(value, str | int | float | bool) or value is None:
        return value
    if hasattr(value, "__dataclass_fields__"):
        return {
            name: _plain(getattr(value, name))
            for name in value.__dataclass_fields__  # type: ignore[attr-defined]
        }
    return str(value)
