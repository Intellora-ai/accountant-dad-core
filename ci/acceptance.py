"""The N = 10 acceptance run. One harness, three backends, one verdict.

OWNER DECISION, 2026-08-09
--------------------------
    N = 10

Not configurable for this gate, not lowered to make a failing run pass. The
value is here, in `docs/ARCHITECTURE.md` and in `docs/PROJECT_STATE.md`, so it
is findable by search rather than remembered from a conversation.

WHAT THIS IS
------------
Phase 5's exit condition, executable. Ten controlled vouchers posted, each read
back, one operation id retried, the ten selected by the ownership filter,
reversed as a batch, each reversal verified, and the trial balance back to its
exact prior value in paise.

It takes a `TallyClient` and nothing else, so the SAME code runs against:

    FakeTally           implementation evidence
    RealTally + TallySim  simulator evidence
    RealTally + a real TallyPrime, Educational mode, permitted date
                        Educational-mode compatibility evidence
    RealTally + a licensed TallyPrime, unchanged fixture
                        LIVE evidence — NOT YET RUN

The evidence class is passed in and printed beside every verdict. It is a
property of the run, never a label chosen afterwards when writing it up. The
harness cannot tell which Tally it is talking to; the caller can, and must say.

WHY THE CONDITIONS ARE A LIST AND NOT A CHAIN OF ASSERTS
---------------------------------------------------------
Fifteen conditions, each recorded with its actual value, its expected value and
its pass rule. A chain of asserts stops at the first failure and reports one
line; this reports all fifteen, which is what somebody needs when a run fails
at midnight against books they cannot see. "Phase 5 did not pass" is not a
result. "Condition 11 failed, actual 9, expected 10" is.

Any single condition failing means the run did not pass. Failures are not
averaged and there is no partial credit.
"""

from __future__ import annotations

import datetime
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

from accountant import pipeline, reversal
from accountant.reversal import BatchState, VoucherState
from accountant.schema import Voucher
from accountant.tallyio.client import (
    DuplicateOperation,
    TallyClient,
    new_operation_id,
    operation_id_in,
)

#: OWNER DECISION 2026-08-09. The Phase 5 acceptance batch size.
N = 10

#: Educational mode accepts the 1st, 2nd and 31st of a month and nothing else.
#: The default is a date every environment permits, so a run that has to move
#: to a real Tally does not also have to change its data.
DEFAULT_DATE = datetime.date(2026, 8, 31)

#: The evidence classes, spelled once. A run carries exactly one and cannot be
#: relabelled afterwards.
UNIT_TEST = "UNIT_TEST"
FAKETALLY = "FAKETALLY"
SIMULATOR = "SIMULATOR"
EDUCATIONAL_TALLY = "EDUCATIONAL_TALLY"
LICENSED_REALTALLY = "LICENSED_REALTALLY"

EVIDENCE_CLASSES = (
    UNIT_TEST,
    FAKETALLY,
    SIMULATOR,
    EDUCATIONAL_TALLY,
    LICENSED_REALTALLY,
)


@dataclass(frozen=True)
class Condition:
    """One pass condition, with everything needed to argue about it later."""

    name: str
    actual: object
    expected: object
    rule: str

    @property
    def passed(self) -> bool:
        return self.actual == self.expected

    def as_row(self) -> dict[str, object]:
        return {
            "metric": self.name,
            "actual": self.actual,
            "expected": self.expected,
            "pass_rule": self.rule,
            "passed": self.passed,
        }


@dataclass(frozen=True)
class AcceptanceRun:
    """One complete N = 10 run and its evidence bundle."""

    run_id: str
    company: str
    backend: str
    evidence_class: str
    backed_up: bool
    voucher_date: datetime.date
    baseline: Mapping[str, int]
    final: Mapping[str, int]
    operation_ids: tuple[str, ...]
    voucher_ids: tuple[str, ...]
    conditions: tuple[Condition, ...]
    batch_state: str
    voucher_states: tuple[str, ...]
    failed_early: str = ""
    log: tuple[str, ...] = field(default_factory=tuple[str, ...])

    @property
    def passed(self) -> bool:
        """Every condition, and no early failure. There is no partial credit."""
        return not self.failed_early and all(c.passed for c in self.conditions)

    @property
    def verdict(self) -> str:
        return "PASSED" if self.passed else "NOT_PASSED"

    def failures(self) -> tuple[Condition, ...]:
        return tuple(c for c in self.conditions if not c.passed)

    def bundle(self) -> dict[str, object]:
        """The evidence bundle. Every field the measurement contract names."""
        return {
            "run_id": self.run_id,
            "company": self.company,
            "backend": self.backend,
            "evidence_class": self.evidence_class,
            "backed_up": self.backed_up,
            "voucher_date": self.voucher_date.isoformat(),
            "n": N,
            "baseline_trial_balance": dict(self.baseline),
            "final_trial_balance": dict(self.final),
            "operation_ids": list(self.operation_ids),
            "voucher_ids": list(self.voucher_ids),
            "batch_state": self.batch_state,
            "voucher_states": list(self.voucher_states),
            "conditions": [c.as_row() for c in self.conditions],
            "failed_early": self.failed_early,
            "verdict": self.verdict,
            "log": list(self.log),
        }

    def to_json(self) -> str:
        return json.dumps(self.bundle(), indent=1, sort_keys=True)


def controlled_voucher(n: int, when: datetime.date) -> Voucher:
    """Voucher n of N. Distinct amount, so ten of them are ten identities.

    Ten identical vouchers would be a legitimate thing for a business to post
    and a useless thing to test with: "did we get all ten back" is
    unanswerable when they cannot be told apart.
    """
    return Voucher(
        id=f"acceptance-{n:02d}",
        date=when,
        party="Sharma Traders",
        narration=f"acceptance run, controlled voucher {n} of {N}",
        debit_account="Purchases",
        credit_account="Cash",
        amount_paise=100_000 + n,
    )


def _distinct(values: Sequence[object]) -> int:
    return len({repr(v) for v in values})


def run_acceptance(
    client: TallyClient,
    company: str,
    *,
    run_id: str,
    evidence_class: str,
    when: datetime.date = DEFAULT_DATE,
    log: pipeline.ActionLogSink | None = None,
    company_key: str = "",
) -> AcceptanceRun:
    """The fifteen steps, in order, and the fifteen conditions they produce.

    Never raises for a Tally-shaped failure. An acceptance run that dies with a
    traceback has told you less than one that reports which condition failed,
    so every step that can fail records the failure and the run continues far
    enough to fill in the bundle.
    """
    if evidence_class not in EVIDENCE_CLASSES:
        raise ValueError(
            f"{evidence_class!r} is not one of {EVIDENCE_CLASSES}; an unlabelled "
            "run cannot be used as evidence for anything"
        )

    notes: list[str] = []
    failed_early = ""

    # 1-3. identity, before anything is written.
    #
    # `backed_up` is asked only once the company is known to be open. A backup
    # question about a company that is not there has no answer, and a connector
    # is entitled to raise rather than invent one.
    backend = type(client).__name__
    companies = client.list_companies()
    company_exists = company in companies
    backed_up = False
    if company_exists:
        backed_up = client.backed_up(company)
    else:
        failed_early = (
            f"{company!r} is not open in Tally; {len(companies)} company/companies "
            f"are: {list(companies)}. Company identity is uncertain, so nothing "
            "is read or written."
        )
        notes.append(failed_early)
    notes.append(
        f"backend {backend}; company_exists {company_exists}; backup {backed_up}"
    )

    # 4. the baseline, in exact paise.
    #
    # Guarded, because a company that is not open in Tally fails HERE and not
    # at the write. An unguarded read would end the run in a traceback naming a
    # KeyError, which tells an operator nothing about which of the fifteen
    # conditions is unmet.
    baseline: dict[str, int] = {}
    theirs_before = 0
    if not failed_early:
        try:
            baseline = dict(client.trial_balance(company))
            theirs_before = len(client.read_vouchers(company)) - len(
                client.list_our_vouchers(company)
            )
        except Exception as exc:
            failed_early = f"reading the baseline: {type(exc).__name__}: {exc}"
            notes.append(failed_early)

    # 5-6. ten distinct operation ids, ten distinct vouchers.
    ops: list[str] = []
    tally_ids: list[str] = []
    read_back_ok = 0
    for i in range(N if not failed_early else 0):
        op = new_operation_id()
        ops.append(op)
        try:
            client.write_voucher(company, controlled_voucher(i, when), op)
        except Exception as exc:  # a failed post is a result, not a crash
            failed_early = f"posting voucher {i}: {type(exc).__name__}: {exc}"
            notes.append(failed_early)
            break

        # 7. read back and verify EVERY voucher individually.
        back = client.read_by_operation_id(company, op)
        expected = controlled_voucher(i, when)
        if back is not None and all(
            getattr(back, f) == getattr(expected, f) for f in pipeline.VERIFIED_FIELDS
        ):
            read_back_ok += 1
            tally_ids.append(back.tally_id or "")
        else:
            notes.append(f"voucher {i} did not read back as written")

    # 8-9. retry one operation id, and prove it created nothing.
    duplicate_created = 0
    if ops and not failed_early:
        before_retry = len(client.list_our_vouchers(company))
        try:
            client.write_voucher(company, controlled_voucher(0, when), ops[0])
            notes.append("a duplicate operation id was ACCEPTED")
        except DuplicateOperation:
            notes.append("the duplicate operation id was refused, as it must be")
        except Exception as exc:
            notes.append(
                f"the duplicate retry failed oddly: {type(exc).__name__}: {exc}"
            )
        duplicate_created = len(client.list_our_vouchers(company)) - before_retry

    # 10. select only ours, through the approved ownership filter.
    ours: tuple[Voucher, ...] = ()
    theirs_selected = 0
    theirs_now = theirs_before
    try:
        ours = client.list_our_vouchers(company)
        theirs_selected = sum(1 for v in ours if operation_id_in(v.narration) is None)
        theirs_now = len(client.read_vouchers(company)) - len(ours)
    except Exception as exc:
        notes.append(f"selecting our vouchers: {type(exc).__name__}: {exc}")

    # 11-12. reverse all ten and verify each reversal.
    batch = None
    if not failed_early:
        try:
            batch = reversal.execute(
                reversal.confirm(reversal.preview(client, company)),
                client,
                log=log,
                company_key=company_key or company,
                run_id=run_id,
            )
        except Exception as exc:
            failed_early = f"the batch could not start: {type(exc).__name__}: {exc}"
            notes.append(failed_early)

    states = tuple(o.state.value for o in batch.outcomes) if batch else ()
    reversed_ok = sum(1 for s in states if s == VoucherState.REVERSED_VERIFIED)

    # 13. the final trial balance.
    final: dict[str, int] = {}
    try:
        final = dict(client.trial_balance(company))
    except Exception as exc:
        notes.append(f"reading the final trial balance: {type(exc).__name__}: {exc}")

    conditions = (
        Condition("vouchers_posted", len(ops), N, f"exactly {N} controlled vouchers"),
        Condition("operation_ids_distinct", _distinct(ops), N, "all distinct"),
        Condition(
            "voucher_identities_distinct", _distinct(tally_ids), N, "all distinct"
        ),
        Condition(
            "correct_company", company_exists, True, "the company is open in Tally"
        ),
        Condition(
            "postings_read_back",
            read_back_ok,
            N,
            "every voucher verified field by field",
        ),
        Condition(
            "duplicate_created_nothing",
            duplicate_created,
            0,
            "a retry creates zero vouchers",
        ),
        Condition(
            "no_user_voucher_selected",
            theirs_selected,
            0,
            "the ownership filter selects only ours",
        ),
        Condition(
            "user_vouchers_untouched",
            theirs_now,
            theirs_before,
            "their own entries are left alone",
        ),
        Condition("reversals_succeeded", reversed_ok, N, f"all {N} reversed"),
        Condition(
            "reversals_read_back",
            states.count(VoucherState.READBACK_FAILED.value),
            0,
            "no readback failure",
        ),
        Condition(
            "no_unknown_outcome",
            states.count(VoucherState.UNKNOWN_OUTCOME.value),
            0,
            "no unknown outcome",
        ),
        Condition(
            "no_wrong_movement",
            states.count(VoucherState.WRONG_MOVEMENT.value),
            0,
            "no wrong movement",
        ),
        Condition(
            "cleanup_completed",
            batch.state.value if batch else "not_run",
            BatchState.COMPLETED.value,
            "the batch completed",
        ),
        Condition(
            "trial_balance_restored", final, baseline, "exact paise, every ledger"
        ),
        Condition(
            "evidence_complete",
            bundle_complete(ops, tally_ids, states),
            True,
            "every field present",
        ),
    )

    return AcceptanceRun(
        run_id=run_id,
        company=company,
        backend=backend,
        evidence_class=evidence_class,
        backed_up=backed_up,
        voucher_date=when,
        baseline=baseline,
        final=final,
        operation_ids=tuple(ops),
        voucher_ids=tuple(tally_ids),
        conditions=conditions,
        batch_state=batch.state.value if batch else "not_run",
        voucher_states=states,
        failed_early=failed_early,
        log=tuple(notes),
    )


def bundle_complete(
    ops: Sequence[str], tally_ids: Sequence[str], states: Sequence[str]
) -> bool:
    """Whether the bundle can actually be used as evidence.

    Not "did the run pass". A run can fail and still produce a complete record;
    what is refused is a record with holes in it, because a missing field is
    indistinguishable from a field nobody looked at.
    """
    return (
        len(ops) == N
        and all(ops)
        and len(tally_ids) == N
        and all(tally_ids)
        and len(states) == N
    )


def render(run: AcceptanceRun) -> str:
    """The human-readable form. One line per condition, verdict last."""
    lines = [
        f"N = {N} acceptance run",
        f"  run id          {run.run_id}",
        f"  company         {run.company!r}",
        f"  backend         {run.backend}",
        f"  evidence class  {run.evidence_class}",
        f"  backup recorded {run.backed_up}",
        f"  voucher date    {run.voucher_date}",
        "",
    ]
    if run.failed_early:
        lines.append(f"  STOPPED EARLY: {run.failed_early}")
        lines.append("")
    for c in run.conditions:
        mark = "ok  " if c.passed else "FAIL"
        lines.append(f"  {mark} {c.name}: actual {c.actual!r}, expected {c.expected!r}")
    lines.append("")
    lines.append(f"  VERDICT: {run.verdict}")
    if not run.passed:
        lines.append(f"  {len(run.failures())} condition(s) failed")
    return "\n".join(lines)
