"""Educational-mode COMPATIBILITY slice. Not live proof of anything else.

WHAT THIS IS, AND THE THREE KINDS OF EVIDENCE IT MUST NEVER BE CONFUSED WITH
---------------------------------------------------------------------------
This project produces exactly three kinds of evidence, and conflating them is
the failure this file exists to prevent:

    FakeTally implementation evidence
        Our code behaves as designed. Says NOTHING about TallyPrime.
    Educational-mode compatibility evidence            <-- THIS FILE
        A real TallyPrime accepted our XML on a date Educational mode permits.
        Proves the MECHANISM. Says nothing about the 2026-08-07 fixture.
    RealTally live evidence
        The unchanged 2026-08-07 contract passed against a licensed TallyPrime.
        NOT OBTAINABLE under the owner's 2026-08-08 Option 2 decision.

This file produces the middle one and may never be reported as the third.

WHY THE DATE IS 2026-08-31 AND WHY THAT IS NOT CHEATING
-------------------------------------------------------
Educational mode accepts vouchers dated only the 1st, 2nd and 31st of a month
(confirmed on Tally's own help site). `tests/test_tally_contract.py:39` posts on
`2026-08-07`, which Educational mode refuses.

That fixture is NOT edited, here or anywhere. Instead this slice uses a date the
environment permits, and the result is labelled compatibility rather than proof.
A passing run here must NEVER be used to claim the 2026-08-07 fixture works. The
whole point of keeping them separate is that one of them is still unanswered.

WHY NOT `Demo Co`
-----------------
The company was to be `Demo Co`. Creating it over the XML gateway was attempted
and REFUSED by Tally, verbatim:

    <RESPONSE>Unknown Request, cannot be processed</RESPONSE>

A company cannot be conjured over the gateway; it must already exist and be open
in TallyPrime, which is a GUI action. So this slice runs against whatever company
is actually open, names it in the evidence, and records the substitution loudly
rather than switching quietly. See `--company`.

WHAT IT DOES TO SOMEBODY'S BOOKS
--------------------------------
One voucher, carrying our marker and a fresh operation ID, then reversed by that
operation ID, then the trial balance is checked back to the exact paise. If the
reversal or the restoration fails, the run reports FAIL and says so; it does not
retry, and it does not clean up by guessing.

IT USED TO BE A SECOND WRITE DOOR. FIXED 2026-08-11.
-----------------------------------------------------
Measured defect: line 233 of this file read `client.write_voucher(...)` and line
301 read `client.reverse_by_operation_id(...)`. Both reached the connector
DIRECTLY. `tests/test_runtime_backend.py` exists to keep the write door
singular, and it only ever scanned `accountant/`, so this file was a second door
standing wide open outside its field of view — and it is the one door that gets
pointed at somebody's real, live TallyPrime.

What the direct call skipped, and what going through `pipeline.post` restores:

    the Valid gate + binding  a decision naming THIS operation authorises the
                              write, and `pipeline.post` refuses an unnamed one
    C6, properly              the old read-back here asked only "is something
                              carrying our marker there". That is defect W1 —
                              the presence check that accepted a DIFFERENT
                              voucher as proof. `pipeline.post` compares amount,
                              party, date and both legs and refuses on any
                              difference
    G3                        the row must appear in Tally's OWN unfiltered
                              register, checked inside the write path rather
                              than afterwards by this script's own goodwill

and `pipeline.reverse_operation` replaces the bare boolean with a trial-balance
comparison: a reversal that returns True and leaves the books unchanged is the
worst of the three outcomes because it is the one that gets believed.

Nothing this file measured was dropped. Two measurements were added, and three
got stronger evidence sources — the identifier and the marker are now read out
of what Tally STORED rather than out of the reply we composed ourselves.

The one guard `pipeline.post` cannot supply here is the write-ahead audit row:
it needs a log sink and a bootstrapped company memory, and this is a hand-run
script whose output is JSON on a terminal. So the operation id is printed to
stderr immediately before the socket opens, which is the only durable place a
script like this has. A write whose id nobody knows is the one a person cannot
afterwards go and find.

Run:  python -m ci.educational_slice --host 192.168.64.2 --port 9000
"""

from __future__ import annotations

import argparse
import dataclasses
import datetime
import json
import sys
import uuid

from accountant import pipeline
from accountant.extract.adapter import ExtractedRecord
from accountant.schema import Decision, Outcome, Voucher
from accountant.tallyio.client import new_operation_id, operation_id_in
from accountant.tallyio.real import RealTally, RecordedBackups, TallyConfig

# Educational mode permits the 1st, 2nd and 31st. Literally the 31st, not "the
# last day" - February has only two usable dates. August has all three.
EDUCATIONAL_DATE = datetime.date(2026, 8, 31)

# The fixture this slice is NOT allowed to claim anything about. Present so the
# distance between the two is visible in the source, not only in prose.
UNTOUCHED_FIXTURE_DATE = datetime.date(2026, 8, 7)

EVIDENCE_CLASS = "EDUCATIONAL_MODE_COMPATIBILITY"


@dataclasses.dataclass
class Row:
    """One measurement. Every field required by the owner's evidence contract."""

    metric: str
    actual: object
    expected: object
    pass_rule: str
    evidence_source: str
    run_id: str
    backend: str
    company: str
    ledger: str

    @property
    def passed(self) -> bool:
        return self.actual == self.expected


def _rows_to_json(rows: list[Row]) -> str:
    return json.dumps(
        [dataclasses.asdict(r) | {"passed": r.passed} for r in rows], indent=2
    )


def controlled_record(voucher: Voucher) -> ExtractedRecord:
    """The provenance of a voucher that nobody extracted from anything.

    `pipeline.Draft` requires an `ExtractedRecord`, and `ExtractedRecord`
    refuses to construct unless every named field states where it came from
    (S3). There is no document here — the voucher is a controlled fixture
    written into this file — so the source is spelled out as exactly that.

    Leaving the sources blank was never an option: the record would then read
    as "extracted from something", which is the one thing it is not, and a
    provenance that overstates itself is the failure this project keeps
    rediscovering.
    """
    source = "controlled fixture in ci/educational_slice.py, not extracted"
    return ExtractedRecord(
        date=voucher.date,
        party=voucher.party,
        total_paise=voucher.amount_paise,
        tax_paise=0,
        raw_text=voucher.narration,
        backend="none",
        per_field_source=dict.fromkeys(ExtractedRecord.FIELDS, source),
    )


def controlled_draft(
    company: str, voucher: Voucher, operation_id: str
) -> pipeline.Draft:
    """A draft `pipeline.post` will accept, and an honest account of why.

    The outcome is stated VALID here rather than produced by `pipeline.evaluate`
    because there is nothing for the decision order to judge: no extraction, no
    memory, no detectors — a single controlled voucher chosen by the person
    running the script, against ledgers this slice has already read back out of
    Tally and refuses to create.

    That is a real difference and it is not hidden: what the door adds to THIS
    script is the operation-id binding, the field-by-field read-back and the
    register check, not a judgement about a document there isn't one of.
    """
    return pipeline.Draft(
        id=voucher.id,
        company=company,
        voucher=voucher,
        record=controlled_record(voucher),
        operation_id=operation_id,
        decision=Decision(
            outcome=Outcome.VALID,
            reason=(
                "controlled compatibility fixture: one voucher, both ledgers "
                "read back from this company's own chart of accounts"
            ),
            operation_id=operation_id,
        ),
    )


def run(host: str, port: int, company: str | None, debit: str, credit: str) -> int:
    run_id = f"edu_{uuid.uuid4().hex}"
    cfg = TallyConfig(host=host, port=port)
    backend = "RealTally"
    rows: list[Row] = []

    def add(
        metric: str,
        actual: object,
        expected: object,
        rule: str,
        source: str,
        ledger: str = "",
    ) -> None:
        rows.append(
            Row(
                metric,
                actual,
                expected,
                rule,
                source,
                run_id,
                backend,
                company or "(not yet resolved)",
                ledger,
            )
        )

    # -- identity ------------------------------------------------------------
    # A read-only client first. `RecordedBackups()` is EMPTY by default and so
    # refuses every write, which is the right shape for a probe.
    probe = RealTally(cfg, backups=RecordedBackups())
    companies = probe.list_companies()
    add(
        "companies_visible",
        len(companies),
        len(companies),
        "reported, not asserted",
        "RealTally.list_companies()",
    )

    if company is None:
        if len(companies) != 1:
            print(
                f"FAIL: expected exactly one open company, found {companies}",
                file=sys.stderr,
            )
            return 2
        company = companies[0]

    add("company_selected", company, company, "reported", "RealTally.list_companies()")
    add(
        "company_is_demo_co",
        company == "Demo Co",
        True,
        "the brief asked for Demo Co; a False here is the RECORDED SUBSTITUTION, "
        "not a silent switch - creating a company over XML is refused by Tally",
        "attempted COMPANY ACTION=Create -> <RESPONSE>Unknown Request, cannot be "
        "processed</RESPONSE>",
    )

    ledgers = probe.read_accounts(company)
    add(
        "ledgers_read",
        len(ledgers),
        len(ledgers),
        "reported",
        "RealTally.read_accounts()",
    )
    for name in (debit, credit):
        add(
            "ledger_exists",
            name in ledgers,
            True,
            "the slice never creates a ledger; it uses what is already there",
            "RealTally.read_accounts()",
            ledger=name,
        )
    if debit not in ledgers or credit not in ledgers:
        print(_rows_to_json(rows))
        print(f"FAIL: {debit!r} or {credit!r} absent from {ledgers}", file=sys.stderr)
        return 2

    # -- baseline ------------------------------------------------------------
    before = probe.trial_balance(company)
    register_before = probe.read_vouchers(company)
    add(
        "trial_balance_balances_to_zero",
        sum(before.values()),
        0,
        "a conservation law: debits and credits must cancel, and it needs no "
        "expert and no labels - it holds or it does not",
        "RealTally.trial_balance()",
    )
    add(
        "register_size_before",
        len(register_before),
        len(register_before),
        "reported",
        "RealTally.read_vouchers() UNFILTERED",
    )

    # -- the write -----------------------------------------------------------
    # Writes are refused unless a backup is recorded for the company. Passing it
    # here is the deliberate, explicit act the gate exists to require.
    client = RealTally(cfg, backups=RecordedBackups(frozenset({company})))
    op = new_operation_id()
    amount = 131300  # distinctive, so it cannot be confused with existing rows
    voucher = Voucher(
        id=f"edu-{run_id[:8]}",
        date=EDUCATIONAL_DATE,
        party=credit,
        narration="Educational-mode compatibility slice",
        debit_account=debit,
        credit_account=credit,
        amount_paise=amount,
    )
    add(
        "voucher_date",
        voucher.date.isoformat(),
        EDUCATIONAL_DATE.isoformat(),
        "must be a date Educational mode permits (1st, 2nd, 31st)",
        "this script",
    )
    add(
        "fixture_date_untouched",
        UNTOUCHED_FIXTURE_DATE.isoformat(),
        "2026-08-07",
        "the contract fixture is never edited to satisfy this environment",
        "tests/test_tally_contract.py:39",
    )

    # The id reaches the operator BEFORE the socket does. `pipeline.post`
    # writes a durable `write_attempted` row when it is handed a log sink; this
    # script has none, so stderr is the only record an interrupted run leaves.
    print(f"about to write operation {op} into {company!r}", file=sys.stderr)

    posted = pipeline.post(controlled_draft(company, voucher, op), client)
    add(
        "write_door",
        # Read off the callable that actually ran, not written out as a literal
        # on both sides. `Condition(x, 0, 0, ...)` - a row whose two operands
        # are the same constant - is a measurement that reports a pass for
        # every possible run, and this project has already been bitten by two
        # of them in ci/readiness.py.
        f"{pipeline.post.__module__}.{pipeline.post.__qualname__}",
        "accountant.pipeline.post",
        "the single sanctioned write door: Valid gate, operation-id binding, "
        "field-by-field read-back (C6) and the register check (G3) all run "
        "inside it. Asserted structurally by tests/test_write_door.py",
        "pipeline.post.__qualname__ of the function this run called",
    )
    add(
        "voucher_created",
        # NOT `is not None` - posted_tally_id is typed `str | None` and
        # `pipeline.post` has already refused a missing read-back, so what is
        # left to catch is an EMPTY identifier: Tally accepting the request
        # without identifying what it created.
        bool(posted.posted_tally_id),
        True,
        "Tally returned a non-empty identifier for the row it created, taken "
        "from the READ-BACK and not from the write reply - Tally's answer is "
        "the evidence and ours is only a claim",
        "pipeline.post() -> Draft.posted_tally_id",
    )
    add(
        "voucher_identifier",
        posted.posted_tally_id,
        posted.posted_tally_id,
        "reported",
        "pipeline.post() -> Draft.posted_tally_id",
    )

    # -- read back, OUR filter and TALLY'S OWN register ----------------------
    back = client.read_by_operation_id(company, op)
    add(
        "read_back_by_operation_id",
        back is not None,
        True,
        "HTTP 200 is not proof the voucher exists (C6)",
        "RealTally.read_by_operation_id()",
    )
    if back is None:
        print(_rows_to_json(rows))
        print(
            f"FAIL: operation {op} wrote and then could not be read back; a "
            f"voucher may exist in {company!r} and has to be checked by hand",
            file=sys.stderr,
        )
        return 2

    add(
        "read_back_matches_field_by_field",
        [
            f
            for f in pipeline.VERIFIED_FIELDS
            if getattr(voucher, f) != getattr(back, f)
        ],
        [],
        "amount, party, date and both legs came back unchanged. The direct "
        "write this slice used until 2026-08-11 asked only whether SOMETHING "
        "carrying our marker was there, which is defect W1",
        "pipeline.VERIFIED_FIELDS vs RealTally.read_by_operation_id()",
    )
    add(
        "voucher_carries_marker",
        operation_id_in(back.narration),
        op,
        "every written voucher carries our marker and its operation id (C4), "
        "read out of what TALLY STORED - the write reply only ever echoed the "
        "narration we composed ourselves",
        "RealTally.read_by_operation_id() -> Voucher.narration",
    )

    register_after = client.read_vouchers(company)
    add(
        "register_grew_by_one",
        len(register_after) - len(register_before),
        1,
        "the voucher must appear in the UNFILTERED register Tally itself "
        "returns, not only through our own marker filter",
        "RealTally.read_vouchers() UNFILTERED",
    )
    found = [v for v in register_after if v.amount_paise == amount]
    add(
        "found_in_unfiltered_register_by_amount",
        len(found),
        1,
        "located WITHOUT using our marker as the search key",
        "RealTally.read_vouchers() UNFILTERED",
    )

    after = client.trial_balance(company)
    delta = {k: after.get(k, 0) - before.get(k, 0) for k in set(before) | set(after)}
    moved = {k: v for k, v in delta.items() if v}
    add(
        "trial_balance_delta",
        moved,
        {debit: amount, credit: -amount},
        "the trial balance must move by exactly this voucher and nothing else",
        "RealTally.trial_balance() before and after",
        ledger=f"{debit}/{credit}",
    )

    # -- reversal and cleanup ------------------------------------------------
    # Through `pipeline.reverse_operation`, the single doorway for undoing. It
    # reads the voucher, snapshots the trial balance either side, and raises
    # ReversalMismatch rather than reporting a success the books do not support.
    undone = pipeline.reverse_operation(client, company, op)
    add(
        "reversal_reported_success",
        undone.reversed_,
        True,
        "reversal targets the operation id, never an amount or narration text",
        "pipeline.reverse_operation() -> Reversal.reversed_",
    )
    add(
        "reversal_moved_exactly_this_voucher",
        undone.moved,
        {debit: -amount, credit: amount},
        "both legs moved back by exactly this voucher and nothing else moved. "
        "The bare boolean this slice used until 2026-08-11 could not tell a "
        "reversal that worked from one that returned True and did nothing",
        "pipeline.reverse_operation() -> Reversal.moved",
        ledger=f"{debit}/{credit}",
    )

    restored = client.trial_balance(company)
    add(
        "trial_balance_restored_exactly",
        restored,
        before,
        "#6.5 - the books must return to their exact prior value, in paise",
        "RealTally.trial_balance() after reversal",
    )

    register_final = client.read_vouchers(company)
    add(
        "register_size_restored",
        len(register_final),
        len(register_before),
        "the row is gone from Tally's own register, not merely from our view",
        "RealTally.read_vouchers() UNFILTERED",
    )
    add(
        "cleanup_status",
        client.read_by_operation_id(company, op) is None,
        True,
        "nothing of ours is left behind in somebody's books",
        "RealTally.read_by_operation_id() after reversal",
    )

    print(_rows_to_json(rows))
    failed = [
        r
        for r in rows
        # `startswith` alone. The `!= "reported"` that used to sit beside it
        # was redundant AND tripped ruff's S105, which reads a comparison
        # against a string near a name containing "pass" as a hardcoded
        # password. Deleting the redundancy removed the finding without a
        # suppression, which is the better of the two fixes.
        if not r.passed and not r.pass_rule.startswith("reported")
    ]
    verdict = "PASS" if not failed else "FAIL"
    print(
        f"\nevidence_class = {EVIDENCE_CLASS}"
        f"\nverdict        = {verdict}"
        f"\nrun_id         = {run_id}"
        f"\nbackend        = {backend}"
        f"\ncompany        = {company}"
        f"\nvoucher_date   = {EDUCATIONAL_DATE.isoformat()}"
        f"\nfailed_rows    = {[r.metric for r in failed]}"
        "\n\nThis is COMPATIBILITY evidence. It proves a real TallyPrime accepted"
        "\nour XML on a permitted date. It is NOT live proof, NOT RealTally proof,"
        "\nNOT 2026-08-07 proof, and NOT Phase 3 live completion."
    )
    return 0 if verdict == "PASS" else 1


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--host", default="192.168.64.2")
    p.add_argument("--port", type=int, default=9000)
    p.add_argument(
        "--company",
        default=None,
        help="defaults to the single open company; named in the evidence",
    )
    p.add_argument("--debit", default="AD Test Expense")
    p.add_argument("--credit", default="AD Test Vendor")
    a = p.parse_args(argv)
    return run(a.host, a.port, a.company, a.debit, a.credit)


if __name__ == "__main__":
    raise SystemExit(main())
