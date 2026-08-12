"""One JSON line per operation, and the raw XML beside it.

WHY THE XML IS A FILE AND THE LINE IS A POINTER
------------------------------------------------
A Tally response is routinely tens of kilobytes. Inlining it turns the log into
something no `tail` can read and no `grep` can scan, and the log's job is to be
readable at 2am. So each line stays short and names two files.

APPEND, FLUSH, CLOSE. EVERY TIME.
----------------------------------
The file is opened in append mode, written, flushed and closed per operation
rather than held open. A held handle loses its buffer when the process is killed
- and the operations worth investigating are exactly the ones that ended in a
kill. Slower per write, and the write is 30ms of network anyway.

WHAT IS SANITISED, AND WHAT IS DELIBERATELY NOT
------------------------------------------------
`inputs` goes through `accountant.redact`, which is the same scrubber the
application logs use, so a secret that reaches here by accident does not land on
disk. The XML blobs are NOT scrubbed: they are the evidence, and a redacted
evidence file cannot answer "what exactly did we send". The owner's standing
decision is that redaction applies to application logs and the audit trail keeps
vendor and amount.

WHAT THIS FILE DOES NOT DO
---------------------------
It does not decide whether an operation may happen - that is
`accountant/tallyio/writedoor.py` - and it does not interpret the response. It
records what was sent, what came back, and how long it took.
"""

from __future__ import annotations

import contextlib
import datetime
import json
import os
import threading
import uuid
from collections.abc import Callable, Generator, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Final, cast

#: Where the log goes. Both configurable, neither hard-coded to a user's home.
ENV_LOG_DIR: Final = "ACCOUNTANT_LOG_DIR"
ENV_XML_DIR: Final = "ACCOUNTANT_XML_LOG_DIR"

_DEFAULT_LOG_DIR: Final = "logs"


#: Turns one value into what may be written down.
#:
#: INJECTED, not imported. `accountant/redact.py` is the scrubber the
#: application logs use, and importing it here would cross the connector
#: boundary that `tests/test_reverse_all_cli.py::
#: test_only_the_command_imports_above_the_connector_boundary` enforces - the
#: connector must not depend on the product above it. So a deployment that
#: wants scrubbing passes it in, and this package keeps its independence.
Scrubber = Callable[[str], str]


def _unchanged(value: str) -> str:
    """The default. The audit trail's job is to record what happened.

    The owner's standing decision is that redaction applies to APPLICATION
    logs and the audit trail KEEPS vendor and amount - a redacted evidence file
    cannot answer "what exactly did we send". A caller handling secrets passes
    a real scrubber.
    """
    return value


_LOCK: Final = threading.Lock()


def log_dir() -> Path:
    return Path(os.environ.get(ENV_LOG_DIR, _DEFAULT_LOG_DIR))


def xml_dir() -> Path:
    return Path(os.environ.get(ENV_XML_DIR, str(log_dir() / "xml")))


def _now() -> datetime.datetime:
    return datetime.datetime.now(datetime.UTC)


@dataclass
class Record:
    """One operation in flight. Filled in as it goes, written once at the end."""

    operation: str
    inputs: Mapping[str, object]
    request_xml: str = ""
    response_xml: str = ""
    status: str = "failure"
    error_summary: str | None = None
    extra: dict[str, object] = field(default_factory=dict[str, object])


class JsonLineAuditLogger:
    """Append-only. One line per operation, two XML files beside it."""

    def __init__(
        self,
        directory: Path | None = None,
        xml_directory: Path | None = None,
        scrub: Scrubber | None = None,
    ) -> None:
        self._dir = directory or log_dir()
        self._xml = xml_directory or xml_dir()
        self._scrub = scrub or _unchanged

    @property
    def path(self) -> Path:
        return self._dir / "audit.jsonl"

    def save_xml_blob(
        self, operation: str, xml: str, direction: str, token: str
    ) -> str:
        """One XML file. Returns its path, or "" when there was nothing to save.

        Dated directories, because the first question about an audit trail is
        always "what happened on the day of" and a single flat folder of a
        hundred thousand files answers it slowly.
        """
        if not xml:
            return ""
        day = _now().strftime("%Y-%m-%d")
        folder = self._xml / day
        folder.mkdir(parents=True, exist_ok=True)
        # The operation name reaches a filesystem path, so it is reduced to
        # characters that cannot climb out of the directory.
        safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in operation)
        target = folder / f"{safe}_{token}_{direction}.xml"
        target.write_text(xml, encoding="utf-8")
        return str(target)

    def write(self, record: Record, duration_ms: int, token: str) -> Path:
        """One line. Flushed and closed before returning."""
        self._dir.mkdir(parents=True, exist_ok=True)
        line = {
            "timestamp": _now().isoformat(),
            "operation": record.operation,
            "id": token,
            # `redact.scrub` takes text, not a mapping - it is the same
            # scrubber the application logs use. Each value is scrubbed
            # individually so the KEYS stay readable: "party" is not a secret,
            # and a log whose field names are redacted is a log nobody can grep.
            "inputs": {str(k): self._scrub(str(v)) for k, v in record.inputs.items()},
            "request_xml_path": self.save_xml_blob(
                record.operation, record.request_xml, "request", token
            ),
            "response_xml_path": self.save_xml_blob(
                record.operation, record.response_xml, "response", token
            ),
            "status": record.status,
            "error_summary": record.error_summary,
            "duration_ms": duration_ms,
            **record.extra,
        }
        with _LOCK, self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(line, default=str) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        return self.path

    @contextlib.contextmanager
    def record(self, operation: str, **inputs: object) -> Generator[Record]:
        """Wrap one operation. The line is written even when the body raises.

        `finally`, not `else`: an operation that raised is the one most worth
        having a line for, and a logger that only records successes is a logger
        that describes a system nobody is having trouble with.
        """
        import time

        entry = Record(operation=operation, inputs=inputs)
        token = uuid.uuid4().hex[:12]
        started = time.perf_counter()
        try:
            yield entry
        except BaseException as failure:
            entry.status = "failure"
            if entry.error_summary is None:
                entry.error_summary = f"{type(failure).__name__}: {failure}"[:300]
            raise
        finally:
            self.write(entry, int((time.perf_counter() - started) * 1000), token)


def recent(limit: int = 20, directory: Path | None = None) -> list[dict[str, object]]:
    """The last `limit` operations, newest last. Unreadable lines are skipped.

    A half-written final line - the process was killed mid-flush - must not stop
    a person reading the ninety-nine before it.
    """
    path = (directory or log_dir()) / "audit.jsonl"
    if not path.exists():
        return []
    out: list[dict[str, object]] = []
    for raw in path.read_text(encoding="utf-8").splitlines()[-limit:]:
        try:
            parsed: object = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            out.append(cast("dict[str, object]", parsed))
    return out


def summary(directory: Path | None = None) -> dict[str, object]:
    """Counts by operation and outcome, for a person asking how it is going."""
    lines = recent(limit=1_000_000, directory=directory)
    by_op: dict[str, int] = {}
    failures: dict[str, int] = {}
    durations: list[int] = []
    for line in lines:
        op = str(line.get("operation", "?"))
        by_op[op] = by_op.get(op, 0) + 1
        if line.get("status") != "success":
            failures[op] = failures.get(op, 0) + 1
        duration = line.get("duration_ms")
        if isinstance(duration, int):
            durations.append(duration)
    total = len(lines)
    return {
        "operations_count": total,
        "by_operation": by_op,
        "failures_by_operation": failures,
        "success_rate": (total - sum(failures.values())) / total if total else 1.0,
        "avg_duration_ms": sum(durations) // len(durations) if durations else 0,
    }
