"""The vertical slice, end to end.

    input bytes
      -> #15 extract
      -> build a draft voucher
      -> checks + #2 memory + #3 detectors
      -> decision order
      -> Not valid: notify, do not post
         Unclear:   ask a closed question, then re-evaluate
         Valid:     post to Tally, read it back, notify

There is no confirmation gate. The system's own validity judgement decides.
"""

from __future__ import annotations

import datetime
import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field, replace

from accountant import checks, problems
from accountant.decide import decide_problems
from accountant.detect import detectors
from accountant.extract.adapter import ExtractedRecord, Extractor
from accountant.memory.index import MemoryIndex
from accountant.problems import Problem
from accountant.schema import CheckResult, Decision, Flag, Outcome, Voucher
from accountant.tallyio.client import TallyClient, new_operation_id


@dataclass
class Draft:
    """One entry moving through the pipeline. Carries its own history."""

    id: str
    company: str
    voucher: Voucher
    record: ExtractedRecord
    operation_id: str
    checks: list[CheckResult] = field(default_factory=list[CheckResult])
    flags: list[Flag] = field(default_factory=list[Flag])
    dropped_flags: int = 0
    problems: list[Problem] = field(default_factory=list[Problem])
    decision: Decision | None = None
    posted_tally_id: str | None = None
    answers: list[tuple[str, str]] = field(default_factory=list[tuple[str, str]])

    @property
    def outcome(self) -> Outcome:
        """The decided outcome. Raises if the draft was never evaluated."""
        if self.decision is None:
            raise ValueError("draft has not been evaluated")
        return self.decision.outcome

    @property
    def reason(self) -> str:
        """Why that outcome. Raises if the draft was never evaluated."""
        if self.decision is None:
            raise ValueError("draft has not been evaluated")
        return self.decision.reason

    @property
    def provenance(self) -> dict[str, str]:
        return dict(self.record.per_field_source)


def _default_credit(accounts: tuple[str, ...]) -> str:
    for preferred in ("Cash", "Bank", "Sundry Creditors"):
        if preferred in accounts:
            return preferred
    return accounts[0] if accounts else "Cash"


def build_draft(
    company: str,
    data: bytes,
    mime: str,
    extractor: Extractor,
    accounts: tuple[str, ...],
    index: MemoryIndex,
    *,
    today: datetime.date | None = None,
) -> Draft:
    """Extract, then propose an account from memory. Never guesses an account."""
    record = extractor.extract(data, mime)

    proposed_debit = ""
    if record.party:
        m = index.lookup(record.party)
        if m.status.value == "match":
            proposed_debit = m.accounts[0]

    voucher = Voucher(
        id=f"draft-{uuid.uuid4().hex[:8]}",
        date=record.date or (today or datetime.date.today()),
        party=record.party or "",
        narration=record.raw_text.strip(),
        debit_account=proposed_debit,
        credit_account=_default_credit(accounts),
        amount_paise=record.total_paise or 0,
        gst_paise=record.tax_paise,
        provenance=dict(record.per_field_source),
    )

    return Draft(
        id=voucher.id,
        company=company,
        voucher=voucher,
        record=record,
        operation_id=new_operation_id(),
    )


def evaluate(
    draft: Draft,
    accounts: tuple[str, ...],
    history: tuple[Voucher, ...],
    index: MemoryIndex,
    *,
    detector_set: Sequence[detectors.Detector] = detectors.SLICE_4_DETECTORS,
    flag_cap: int | None = None,
) -> Draft:
    """Run checks, memory and detectors, then apply the decision order."""
    draft.checks = checks.run(draft.voucher, accounts)
    draft.flags, draft.dropped_flags = detectors.run(
        draft.voucher, history, index, detectors=detector_set, cap=flag_cap
    )
    match = index.lookup(draft.voucher.party)
    draft.problems = problems.find(
        draft.voucher, draft.checks, match, draft.flags, accounts, history, index
    )
    draft.decision = decide_problems(draft.problems, asked=len(draft.answers))
    return draft


def next_question(draft: Draft):
    """The one question to put to the person now, or None.

    Never repeats a problem already answered — that is the non-overlapping rule.
    """
    answered = {pid for pid, _ in draft.answers}
    for p in draft.problems:
        if p.answerable and p.id not in answered:
            return p.question
    return None


def answer(draft: Draft, account: str, problem_id: str = "which_account") -> Draft:
    """Record an answer to a clarifying question.

    The answer is NOT permission to post. It is new information. The caller must
    re-run evaluate(), and the entry can still come out Not valid.
    """
    draft.answers.append((problem_id, account))
    draft.voucher = replace(draft.voucher, debit_account=account)
    prov = dict(draft.voucher.provenance or {})
    prov["debit_account"] = "human_answer"
    draft.voucher = replace(draft.voucher, provenance=prov)
    return draft


def post(draft: Draft, client: TallyClient) -> Draft:
    """Write to Tally, but only if the outcome is Valid, then read it back.

    Raises if called on a draft that is not Valid. The gate lives here, server
    side, so no caller can bypass it.
    """
    if draft.decision is None:
        raise ValueError("draft has not been evaluated")
    if draft.decision.outcome is not Outcome.VALID:
        raise ValueError(f"refusing to post: outcome is {draft.decision.outcome.value}")

    result = client.write_voucher(draft.company, draft.voucher, draft.operation_id)

    # C6: read back. HTTP 200 is not proof the voucher exists.
    back = client.read_by_operation_id(draft.company, draft.operation_id)
    if back is None:
        raise RuntimeError(
            f"wrote operation {draft.operation_id} but could not read it back"
        )

    draft.posted_tally_id = result.tally_id
    return draft


def reverse(draft: Draft, client: TallyClient) -> bool:
    """Undo exactly this voucher, by operation ID. Never by amount."""
    return client.reverse_by_operation_id(draft.company, draft.operation_id)


def run(
    company: str,
    data: bytes,
    mime: str,
    extractor: Extractor,
    client: TallyClient,
    *,
    detector_set: Sequence[detectors.Detector] = detectors.SLICE_4_DETECTORS,
    flag_cap: int | None = None,
    today: datetime.date | None = None,
) -> Draft:
    """One entry, all the way through. Posts if Valid, stops otherwise."""
    accounts = client.read_accounts(company)
    history = client.read_vouchers(company)
    index = MemoryIndex.from_vouchers(history)

    draft = build_draft(company, data, mime, extractor, accounts, index, today=today)
    draft = evaluate(
        draft, accounts, history, index, detector_set=detector_set, flag_cap=flag_cap
    )

    if draft.decision and draft.decision.outcome is Outcome.VALID:
        draft = post(draft, client)

    return draft
