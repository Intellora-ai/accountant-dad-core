"""`python -m accountant.tallyio --reverse-all --company "Demo Co"`

The command the frozen plan's "Verification, end to end" section documents. It
is the operator's way to undo everything Accountant Dad wrote into one company,
and it is the only surface besides the web app that can start a bulk reversal.

WHY THIS FILE IMPORTS UPWARD, AND WHY THAT IS NOT A LAYERING BREAK
------------------------------------------------------------------
Every module inside `accountant/tallyio/` imports only `accountant.schema` and
its own package. That is correction C3 — nothing outside the connector touches
XML or port 9000, and nothing inside it depends on the product above it — and it
is what makes the whole system testable against a double.

`__main__.py` is not part of that graph. Nothing in `accountant/tallyio/*.py`
imports it; Python's `-m` machinery runs it, and it exists only to be a command.
`tests/test_bulk_reversal.py` asserts exactly that: every OTHER module in the
package still imports nothing above the boundary, so the guarantee is checked
rather than promised.

The alternative was putting the command somewhere else and quietly not
implementing the one the frozen plan names. That is a worse trade: a documented
command that does not exist is a lie in the documentation.

WHY IT TAKES FLAGS AND NOT ENVIRONMENT VARIABLES
------------------------------------------------
`accountant/web/app.py` resolves its Tally from `ACCOUNTANT_TALLY_HOST` and
friends, because a packaged app has no comfortable place to put a command line.
This one is run by a person at a terminal who is about to remove vouchers from
somebody's books, and every value that decides WHICH books is better typed than
inherited. Duplicating the env parser here would have been the reuse that
matters least and the drift that matters most: two spellings of "which Tally",
diverging silently.

Principle 9 — defaults are explicit. Every resolved value is printed before
anything is touched.

WHAT IT WILL NOT DO
-------------------
It will not reverse anything without `--yes`. It prints the exact count and the
exact operation ids first, and a run without `--yes` is a preview that exits 0
having touched nothing.

It will not reverse anything without `--audit-log` either, and that gate is as
load-bearing as `--backed-up`. This command deletes vouchers out of somebody's
books; owner decision Q8 = A requires every state transition it causes to be
recorded durably with seven fields. Until 2026-08-10 it passed no log to
`reversal.confirm` or `reversal.execute` at all, so **the identical operation
that records a full audit trail through the web app recorded nothing here**. The
web path was the only one the seven-field measurement ever covered.

There is deliberately NO DEFAULT PATH. Where a customer's audit trail lives is
not a decision this command may make on its own, and a default would put the
file somewhere nobody chose while looking like it had been thought about.
Refusing costs one flag.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence

from accountant import reversal
from accountant.memory.store import MemoryStore
from accountant.reversal import BatchState, VoucherState
from accountant.tallyio.factory import RealTallyRequired, real_tally
from accountant.tallyio.real import RecordedBackups, TallyConfig

#: Exit codes. A batch that did not complete must not look like success to a
#: shell, a CI step or a person reading `$?`.
EXIT_OK = 0
EXIT_REFUSED = 2
EXIT_NOT_COMPLETED = 3

AUDIT_LOG_REQUIRED = (
    "refused: no operation performed. --yes removes vouchers from a company's "
    "books, and every state transition that causes must be recorded durably "
    "(owner decision Q8 = A). Pass --audit-log PATH naming the SQLite file to "
    "append the history to. There is no default, because where a customer's "
    "audit trail lives is not a decision this command may make on its own."
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m accountant.tallyio",
        description="Reverse every voucher Accountant Dad wrote into one company.",
    )
    parser.add_argument(
        "--reverse-all",
        action="store_true",
        required=True,
        help="the only operation this command performs",
    )
    parser.add_argument(
        "--company", required=True, help="the company, exactly as Tally names it"
    )
    parser.add_argument(
        "--host", default="127.0.0.1", help="where TallyPrime is listening"
    )
    parser.add_argument("--port", type=int, default=9000, help="Tally's HTTP port")
    parser.add_argument(
        "--backed-up",
        action="store_true",
        help=(
            "assert that this company has been backed up. Without it every "
            "reversal is refused, which is the safe default"
        ),
    )
    parser.add_argument(
        "--yes",
        action="store_true",
        help="confirm the exact list printed by the preview and reverse it",
    )
    parser.add_argument(
        "--audit-log",
        default="",
        metavar="PATH",
        help=(
            "the SQLite file to append the reversal history to. REQUIRED with "
            "--yes; there is no default"
        ),
    )
    return parser


def _print_resolved(args: argparse.Namespace) -> None:
    print("resolved configuration")
    print(f"  company    {args.company!r}")
    print(f"  endpoint   http://{args.host}:{args.port}")
    print(f"  backed up  {bool(args.backed_up)}")
    print(f"  confirmed  {bool(args.yes)}")
    # Principle 9 — every resolved value is printed before anything is touched,
    # and "nothing is being recorded" is exactly the value a person about to
    # delete vouchers should see rather than infer.
    print(f"  audit log  {args.audit_log or '(none)'}")


def _print_preview(batch: reversal.Batch) -> None:
    print()
    print(f"batch {batch.batch_id}: {len(batch.outcomes)} voucher(s) of ours")
    for outcome in batch.outcomes:
        print(f"  {outcome.operation_id}")
    if not batch.outcomes:
        print("  (none)")


def _print_result(batch: reversal.Batch) -> None:
    print()
    print(f"batch {batch.batch_id}: {batch.state.value.upper()}")
    print(f"  {batch.detail}")
    for outcome in batch.outcomes:
        line = f"  {outcome.operation_id}  {outcome.state.value}"
        if outcome.state is not VoucherState.REVERSED_VERIFIED and outcome.detail:
            line += f"  — {outcome.detail}"
        print(line)
    print(f"  every paise accounted for: {batch.accounted}")


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    _print_resolved(args)

    # Checked BEFORE the connection is opened, so a run that could not have been
    # recorded is refused before it can touch anything at all. Same shape as the
    # backup gate below it: the cheap refusal comes first.
    if args.yes and not args.audit_log:
        print(f"\n{AUDIT_LOG_REQUIRED}", file=sys.stderr)
        return EXIT_REFUSED

    backups = RecordedBackups(
        frozenset({args.company}) if args.backed_up else frozenset()
    )
    try:
        client, identity = real_tally(
            TallyConfig(host=args.host, port=args.port), args.company, backups=backups
        )
    except RealTallyRequired as exc:
        print(f"\n{exc}", file=sys.stderr)
        return EXIT_REFUSED

    print(f"  backend    {identity.backend}")

    try:
        batch = reversal.preview(client, args.company)
    except Exception as exc:  # see below
        # Broad on purpose. `ValueError` is the unmarked-candidate refusal;
        # `CompanyNotBackedUp` and every connector error arrive as their own
        # types. All of them mean the same thing at this point — nothing was
        # touched — and all of them must exit non-zero with a sentence rather
        # than a traceback at somebody who is mid-cleanup on live books.
        print(f"\nrefused: {type(exc).__name__}: {exc}", file=sys.stderr)
        return EXIT_REFUSED

    _print_preview(batch)

    if not args.yes:
        print("\npreview only. nothing was reversed. pass --yes to confirm this list.")
        return EXIT_OK

    # `--yes` got us here, and `--yes` without `--audit-log` was refused above,
    # so this path always has somewhere durable to write. Closed in `finally`,
    # because a batch whose rows are still in a buffer when the process exits
    # has recorded nothing — which is the whole defect this replaced.
    log = MemoryStore(args.audit_log)
    try:
        result = reversal.execute(
            # `--yes` is the operator confirming the exact list just printed,
            # and that is a state transition the history has to carry. No
            # backend is named: confirming touches no Tally.
            reversal.confirm(
                batch,
                log=log,
                company_key=args.company,
                run_id=identity.run_id,
            ),
            client,
            log=log,
            company_key=args.company,
            run_id=identity.run_id,
        )
    finally:
        log.close()
    _print_result(result)
    print(f"  history appended to {args.audit_log}")
    return EXIT_OK if result.state is BatchState.COMPLETED else EXIT_NOT_COMPLETED


if __name__ == "__main__":  # pragma: no cover - exercised via main()
    raise SystemExit(main())
