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
from typing import Protocol

from accountant import checks, problems
from accountant.decide import decide_problems
from accountant.detect import detectors
from accountant.extract.adapter import ExtractedRecord, Extractor
from accountant.memory.company import CompanyMemory, propose_account
from accountant.memory.identity import normalise_company
from accountant.problems import Problem
from accountant.schema import ActionLog, CheckResult, Decision, Flag, Outcome, Voucher
from accountant.tallyio.client import TallyClient, new_operation_id


class ActionLogSink(Protocol):
    """Somewhere durable to append a decision.

    A Protocol rather than `MemoryStore` so the pipeline does not depend on
    SQLite. `accountant/memory/store.py` satisfies it today; anything that can
    append and never mutate would.
    """

    def record_action(self, entry: ActionLog) -> None: ...


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
    memory: CompanyMemory,
    *,
    today: datetime.date | None = None,
) -> Draft:
    """Extract, then propose an account from THIS COMPANY'S OWN memory.

    `memory` is company-scoped and carries its own identity. That identity is
    checked against `company` here, so a caller cannot hand company A's memory
    to a draft being built for company B - the one mistake that would make
    every isolation guarantee in `accountant/memory/` decorative.

    Measured, `accountant/ingest/crossorg.py`, 16,011 real rows over 30 ordered
    department pairs: within-organisation account prediction reaches 53.08%,
    cross-organisation is 0.00% on 29 of the 30. A mapping borrowed from
    another company is not a weaker answer, it is a wrong one.

    `propose_account` RAISES `MemoryNotReady` when this company has had no
    successful bootstrap. That is deliberate and is not caught here: "we have
    not read your books yet" must never arrive at the decision as "no match",
    because no-match asks the person a question and not-ready means we are not
    entitled to ask one yet.
    """
    expected = normalise_company(company)
    if memory.identity.key != expected:
        raise ValueError(
            f"memory for company {memory.identity.name!r} was passed to a "
            f"draft for {company!r}; company-scoped memory is never shared"
        )

    record = extractor.extract(data, mime)

    proposed_debit = propose_account(memory, record.party) if record.party else None
    proposed_debit = proposed_debit or ""

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
    memory: CompanyMemory,
    *,
    detector_set: Sequence[detectors.Detector] = detectors.SLICE_4_DETECTORS,
    flag_cap: int | None = None,
) -> Draft:
    """Run checks, memory and detectors, then apply the decision order.

    The detectors read history through THIS COMPANY'S index and no other.
    `memory.index()` is derived from the scoped store, so a detector cannot
    reach a vendor mapping that belongs to somebody else's books.

    `as_match_result()` RAISES on MEMORY_NOT_READY rather than converting it to
    NO_MATCH. Those two look alike and mean opposite things: no-match means ask
    the person, not-ready means we have not read their books and have no
    standing to ask anything yet.
    """
    if memory.identity.key != normalise_company(draft.company):
        raise ValueError(
            f"memory for company {memory.identity.name!r} was passed to a "
            f"draft for {draft.company!r}; company-scoped memory is never shared"
        )

    index = memory.index()
    draft.checks = checks.run(draft.voucher, accounts)
    draft.flags, draft.dropped_flags = detectors.run(
        draft.voucher, history, index, detectors=detector_set, cap=flag_cap
    )
    match = memory.lookup(draft.voucher.party).as_match_result()
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
    memory: CompanyMemory,
    *,
    detector_set: Sequence[detectors.Detector] = detectors.SLICE_4_DETECTORS,
    flag_cap: int | None = None,
    today: datetime.date | None = None,
    log: ActionLogSink | None = None,
    run_id: str = "",
) -> Draft:
    """One entry, all the way through. Posts if Valid, stops otherwise.

    `memory` is REQUIRED and must already be bootstrapped from this company's
    own Tally. It used to be built here as
    `MemoryIndex.from_vouchers(client.read_vouchers(company))`, which was the
    product failure: an index rebuilt from scratch on every call, carrying no
    company key, no bootstrap record, and no way to express
    MEMORY_NOT_READY. An existing customer whose history had not been read
    looked exactly like a customer with no history, and the difference between
    those two is the difference between asking a question and guessing.

    Bootstrap it with `accountant.memory.bootstrap.bootstrap(client, company,
    store)` - or `resume(store, company)` when it was done earlier - and pass
    the result in. A caller that cannot produce one has not read the person's
    books, and must not be proposing accounts.
    """
    accounts = client.read_accounts(company)
    history = client.read_vouchers(company)

    draft = build_draft(company, data, mime, extractor, accounts, memory, today=today)
    draft = evaluate(
        draft, accounts, history, memory, detector_set=detector_set, flag_cap=flag_cap
    )

    if draft.decision and draft.decision.outcome is Outcome.VALID:
        draft = post(draft, client)
        record_decision(log, draft, memory, client, "posted", run_id)
    else:
        record_decision(log, draft, memory, client, "blocked", run_id)

    return draft


def record_decision(
    log: ActionLogSink | None,
    draft: Draft,
    memory: CompanyMemory,
    client: TallyClient,
    action: str,
    run_id: str,
) -> None:
    """One durable row per decision, written HERE rather than by a caller.

    Public because the web app does not go through `run` - it builds a draft,
    shows it, asks a question, and re-evaluates across several HTTP requests.
    It therefore reaches the same decisions by a different route, and must
    produce the same rows. Sharing this function is what keeps the two paths
    from drifting into two different definitions of what a decision is.

    The web app used to keep its own forty-row list, which meant the audit
    trail existed only while somebody had a browser open and vanished on
    restart. A decision is made here, so the record of it belongs here: every
    caller of `run` gets the same trail without having to remember to write
    one.

    The reason comes from the decision itself on EVERY path, including the
    posted one. "Why did you refuse" is the obvious question; "why did you
    post" is the one asked six months later by somebody looking at the voucher
    in their books.
    """
    if log is None or draft.decision is None:
        return

    log.record_action(
        ActionLog(
            ts=datetime.datetime.now(datetime.UTC),
            action=action,
            company_key=memory.identity.key,
            outcome=draft.decision.outcome.value,
            reason=draft.decision.reason,
            run_id=run_id,
            backend=type(client).__name__,
            operation_id=draft.operation_id,
            voucher_id=draft.posted_tally_id or "",
            vendor_id=draft.voucher.party,
            detail=f"{draft.voucher.debit_account or '(none proposed)'} "
            f"{draft.voucher.amount_paise} paise",
        )
    )
