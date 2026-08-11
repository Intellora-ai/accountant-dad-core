"""`python -m accountant.agent` — run the connector on the user's machine.

WHY EVERY VALUE IS PRINTED AT STARTUP
-------------------------------------
The same reason the web app prints its resolved configuration
(`accountant/web/app.py:2104-2106`): "which Tally am I talking to" and "which
tenant am I acting for" must never be a guess. On a connector the question is
sharper, because this program runs unattended on somebody else's laptop and the
only way anyone will ever diagnose it is from what it said when it started.

The secret is the one value that is never printed. It is read from the
environment or a file and goes nowhere but the outbound request body.

WHY THE SECRET IS NOT A COMMAND-LINE ARGUMENT
---------------------------------------------
Arguments are visible in the process table to every user on the machine, and
they land in shell history. `ACCOUNTANT_CONNECTOR_SECRET`, or a file whose
path is given, keeps it out of both.

WHY NOTHING HERE CALLS `print` DIRECTLY
---------------------------------------
Added 2026-08-11. The banner deliberately prints every resolved value, and
"every resolved value" is a promise that will one day include something it
should not — `--cloud-url https://user:hunter2@cloud.example` is a credential
in a value this program is explicitly proud of printing. So stdout has a seam
too: `_say` scrubs, and `tests/test_redaction.py` reads this module's AST and
fails on any `print(` call outside it. A rule enforced by remembering is a rule
that lasts until the next person.
"""

from __future__ import annotations

import argparse
import os
import signal
import sys
from collections.abc import Sequence
from pathlib import Path
from types import FrameType

from accountant import redact
from accountant.agent.connector import (
    Connector,
    ConnectorIdentity,
    ExecutedOperations,
    build_logger,
)
from accountant.tallyio.client import TallyClient
from accountant.tallyio.factory import real_tally
from accountant.tallyio.real import RecordedBackups, TallyConfig

#: The variable NAME, not a secret. noqa because the linter matches on the
#: word rather than on whether a value was assigned.
ENV_SECRET = "ACCOUNTANT_CONNECTOR_SECRET"  # nosec B105  # noqa: S105


def _say(line: str) -> None:
    """Everything this program says to a person, redacted on the way out.

    One function so there is one place to change, and so the AST test in
    `tests/test_redaction.py` has something to point at. See the module
    docstring for why a bare `print` is a defect here.
    """
    print(redact.scrub(line))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m accountant.agent",
        description=(
            "Run the Accountant Dad connector. It dials out to the cloud, takes "
            "one job at a time, and runs it against TallyPrime on this machine. "
            "It never listens on a port."
        ),
        # No abbreviations. argparse accepts any unambiguous prefix by default,
        # so `--secret hunter2` was silently accepted as `--secret-file hunter2`
        # - the exact argument this program refuses to have, quietly reinstated
        # as a typo. A test found it. An unknown flag must be an error.
        allow_abbrev=False,
    )
    parser.add_argument("--connector-id", required=True)
    parser.add_argument("--tenant-id", required=True)
    parser.add_argument(
        "--company",
        action="append",
        required=True,
        metavar="NAME",
        help="a company this connector may act on. Repeat for more than one. "
        "A job naming anything else is refused before Tally is touched.",
    )
    parser.add_argument(
        "--cloud-url",
        required=True,
        help="https:// base URL. Plaintext is refused: the request body "
        "carries the connector secret.",
    )
    parser.add_argument(
        "--secret-file",
        type=Path,
        default=None,
        help=f"file holding the connector secret. Defaults to reading {ENV_SECRET}.",
    )
    parser.add_argument("--tally-host", default="localhost")
    parser.add_argument("--tally-port", type=int, default=9000)
    parser.add_argument(
        "--state-dir",
        type=Path,
        default=Path("data"),
        help="where the executed-operations record and the log are kept. The "
        "record is what stops a dropped reply executing a job twice, so it "
        "must be on disk that survives a restart.",
    )
    parser.add_argument("--interval-seconds", type=float, default=2.0)
    parser.add_argument(
        "--once",
        action="store_true",
        help="take at most one job and exit. For checking a new pairing.",
    )
    return parser


def read_secret(secret_file: Path | None) -> str:
    """From a file, or the environment. Never from an argument."""
    if secret_file is not None:
        return secret_file.read_text(encoding="utf-8").strip()
    value = os.environ.get(ENV_SECRET, "").strip()
    if not value:
        raise SystemExit(
            f"no connector secret. Set {ENV_SECRET} or pass --secret-file PATH. "
            "It is deliberately not a command-line argument: arguments are "
            "visible in the process table and land in shell history."
        )
    return value


def open_tally(host: str, port: int) -> object:
    """Return a factory that opens a client for one company, per job.

    Per job, not once: Tally is often closed when a laptop boots and open later,
    and a client captured at startup would be permanently dead.

    `RecordedBackups` is EMPTY here, so this connector cannot write. Task 1
    forwards reads only; the write path reaches Tally through `pipeline.post`
    and its gate stack, and a connector that could write would be a second door
    beside the one `tests/test_runtime_backend.py` exists to keep singular.
    """

    def factory(company: str) -> TallyClient:
        client, _identity = real_tally(
            TallyConfig(host=host, port=port), company, backups=RecordedBackups()
        )
        return client

    return factory


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    secret = read_secret(args.secret_file)

    identity = ConnectorIdentity(
        connector_id=args.connector_id,
        secret=secret,
        tenant_id=args.tenant_id,
        companies=frozenset(args.company),
    )
    state = Path(args.state_dir)
    log = build_logger(state / "connector.log")

    _say("Accountant Dad connector, resolved configuration:")
    _say(f"  connector id  {identity.connector_id}")
    _say(f"  tenant        {identity.tenant_id}")
    _say(f"  companies     {sorted(identity.companies)}")
    _say(f"  cloud         {args.cloud_url}")
    _say(f"  tally         http://{args.tally_host}:{args.tally_port} (loopback)")
    _say(f"  state         {state.resolve()}")
    _say("  secret        read, not shown")
    _say("  writes        REFUSED - this connector forwards reads only")

    connector = Connector(
        identity,
        args.cloud_url,
        open_tally(args.tally_host, args.tally_port),  # type: ignore[arg-type]
        executed=ExecutedOperations(state / "executed.log"),
        logger=log,
    )

    def stop(_signum: int, _frame: FrameType | None) -> None:
        _say("\nstopping after the job in hand")
        connector.stop()

    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, stop)

    connector.register()
    if args.once:
        result = connector.poll_once()
        _say(f"  result        {result.outcome if result else 'NO WORK'}")
        return 0
    done = connector.run_forever(interval_seconds=args.interval_seconds)
    _say(f"  executed      {done} job(s)")
    return 0


if __name__ == "__main__":  # pragma: no cover - exercised by __main__.py
    sys.exit(main())
