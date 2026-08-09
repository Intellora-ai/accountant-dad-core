"""`python -m ci.acceptance_cli` — the N = 10 run against a real TallyPrime.

G5.4. Everything that can be prepared without a licensed Tally, prepared. The
live run itself is `BLOCKED_ENVIRONMENT` and this file does not pretend
otherwise; what it removes is every reason the run could not happen the moment
the environment exists.

WHAT IT TAKES TO GO LIVE, ONCE THE OWNER HAS DONE THEIR PART
-------------------------------------------------------------
    python -m ci.acceptance_cli \\
        --host 192.168.64.2 --port 9000 \\
        --company "Demo Co" --backed-up \\
        --evidence-class EDUCATIONAL_TALLY \\
        --yes --out evidence/acceptance-2026-08-31.json

One command. No code change, no fixture edit, no new flag invented on the day.

THE PRE-FLIGHT IS NOT DECORATION
---------------------------------
The autonomy boundary requires that before any real Tally write the following
are SHOWN and confirmed: backend identity, company identity, backup identity,
mode, write-enabled status, the exact voucher set, the operation ids, the
cleanup plan, the expected trial-balance movement, and the reconciliation plan.
All of it is printed before `--yes` is honoured, and a run without `--yes` is a
pre-flight that touches nothing.

The operation ids are the one item that cannot be shown in advance: they are
minted per voucher, immediately before each write, and minting them early would
mean an id existed before the thing it identifies. What is shown instead is the
exact count, the exact amounts, the exact date, and the fact that every id is
recorded in the bundle.

WHY IT CANNOT CLAIM THE LIVE EVIDENCE CLASS
--------------------------------------------
`--evidence-class LICENSED_REALTALLY` is REFUSED unless the connector actually
measured `licence_mode == licensed`. Today that read is `UNKNOWN` by design —
A11 measured that this gateway will not answer `$$LicenseInfo`, and the TDL
workaround is what wedged a live Tally — so the tool cannot currently produce
the live class at all.

That is the point. The separation between compatibility evidence and live proof
is enforced by the machine and not by whoever writes the report afterwards. If
somebody wants the live label they have to make the licence read succeed, which
is the same thing as actually having a licence.
"""

from __future__ import annotations

import argparse
import datetime
import pathlib
import sys
from collections.abc import Sequence

from accountant.tallyio.factory import RealTallyRequired, real_tally
from accountant.tallyio.real import LicenceMode, RecordedBackups, TallyConfig
from ci import acceptance

EXIT_OK = 0
EXIT_REFUSED = 2
EXIT_NOT_PASSED = 3

#: Evidence classes this command may be asked for. `UNIT_TEST` and `FAKETALLY`
#: are excluded on purpose: this command only ever talks to a real connector,
#: so offering them would let a real run be filed under a class that means
#: "no Tally was involved".
LIVE_CLASSES = (
    acceptance.SIMULATOR,
    acceptance.EDUCATIONAL_TALLY,
    acceptance.LICENSED_REALTALLY,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m ci.acceptance_cli",
        description=f"The N = {acceptance.N} acceptance run against a real Tally.",
    )
    parser.add_argument("--company", required=True)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=9000)
    parser.add_argument(
        "--evidence-class",
        required=True,
        choices=LIVE_CLASSES,
        help="what this run may be used to claim. Checked against the connector.",
    )
    parser.add_argument(
        "--date",
        default=acceptance.DEFAULT_DATE.isoformat(),
        help=(
            "the voucher date. Educational mode accepts only the 1st, 2nd and "
            f"31st; the default {acceptance.DEFAULT_DATE.isoformat()} is one of them"
        ),
    )
    parser.add_argument("--backed-up", action="store_true")
    parser.add_argument(
        "--yes",
        action="store_true",
        help=f"write {acceptance.N} vouchers into this company and reverse them",
    )
    parser.add_argument("--out", default="", help="where to save the evidence bundle")
    return parser


def preflight(args: argparse.Namespace, identity: object, when: datetime.date) -> str:
    """Everything the autonomy boundary requires, before a single write."""
    total = sum(
        acceptance.controlled_voucher(i, when).amount_paise for i in range(acceptance.N)
    )
    metrics = identity.as_metrics()  # type: ignore[attr-defined]
    lines = [
        "PRE-FLIGHT — nothing has been written yet",
        f"  backend identity     {metrics['backend']}",
        f"  endpoint             {metrics['endpoint']}",
        f"  company identity     {metrics['company_identifier']!r} "
        f"(exists: {metrics['company_exists']})",
        f"  companies visible    {metrics['companies_visible']}",
        f"  backup identity      recorded={bool(args.backed_up)}",
        f"  licence mode         {metrics['licence_mode']}",
        f"  licence detail       {metrics['licence_detail']}",
        f"  write enabled        {bool(args.yes)}",
        f"  evidence class       {args.evidence_class}",
        "",
        f"  voucher set          {acceptance.N} controlled vouchers dated {when}",
        f"                       Purchases / Cash, "
        f"{acceptance.controlled_voucher(0, when).amount_paise} to "
        f"{acceptance.controlled_voucher(acceptance.N - 1, when).amount_paise} paise",
        f"  expected movement    Purchases +{total}, Cash -{total} paise, then back",
        "  operation ids        minted one per voucher immediately before each",
        "                       write, and every one recorded in the bundle",
        "",
        "  cleanup plan         all of them reversed by operation id in one batch,",
        "                       each reversal verified against the trial balance",
        "  reconciliation plan  a batch that stops leaves a durable per-voucher",
        "                       state; reconcile with a read-only lookup, then",
        "                       resume explicitly. Vouchers already reversed are",
        "                       never re-reversed.",
    ]
    return "\n".join(lines)


def class_is_honest(declared: str, measured_mode: str) -> tuple[bool, str]:
    """Whether the connector's measurement supports the claim being made.

    Only one direction is dangerous. Filing a licensed run as compatibility
    evidence understates it and harms nobody; filing an Educational or unknown
    run as LIVE is the mislabelling that would let the last open question in
    this project be quietly closed.
    """
    if declared != acceptance.LICENSED_REALTALLY:
        return True, ""
    if measured_mode == LicenceMode.LICENSED.value:
        return True, ""
    return False, (
        f"refusing to label this run {acceptance.LICENSED_REALTALLY}: the "
        f"connector measured licence_mode={measured_mode!r}, not "
        f"{LicenceMode.LICENSED.value!r}. Live proof requires a licensed Tally "
        "that says so, and the licence read on this gateway currently returns "
        "UNKNOWN by design (A11). Use EDUCATIONAL_TALLY, or make the licence "
        "read succeed."
    )


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    when = datetime.date.fromisoformat(args.date)

    backups = RecordedBackups(
        frozenset({args.company}) if args.backed_up else frozenset()
    )
    try:
        client, identity = real_tally(
            TallyConfig(host=args.host, port=args.port), args.company, backups=backups
        )
    except RealTallyRequired as exc:
        print(f"{exc}", file=sys.stderr)
        return EXIT_REFUSED

    honest, why = class_is_honest(args.evidence_class, identity.licence_mode)
    if not honest:
        print(why, file=sys.stderr)
        return EXIT_REFUSED

    print(preflight(args, identity, when))

    if not args.yes:
        print()
        print("pre-flight only. nothing was written. pass --yes to run it.")
        return EXIT_OK

    result = acceptance.run_acceptance(
        client,
        args.company,
        run_id=identity.run_id,
        evidence_class=args.evidence_class,
        when=when,
    )
    print()
    print(acceptance.render(result))

    if args.out:
        path = pathlib.Path(args.out)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(result.to_json(), encoding="utf-8")
        print(f"\n  evidence bundle written to {path}")

    return EXIT_OK if result.passed else EXIT_NOT_PASSED


if __name__ == "__main__":  # pragma: no cover - exercised via main()
    raise SystemExit(main())
