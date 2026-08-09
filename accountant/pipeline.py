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
from accountant.extract.adapter import NOT_FOUND, ExtractedRecord, Extractor
from accountant.memory.company import CompanyMemory, propose_account
from accountant.memory.identity import normalise_company
from accountant.memory.index import normalise_vendor
from accountant.problems import FUNDING_PROBLEM, Problem
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


def funding_from_history(party: str, history: Sequence[Voucher]) -> str:
    """How THIS company has paid THIS vendor before. Unanimous or nothing.

    Public, not `_private`, because the rule it encodes - unanimous or nothing -
    is worth asserting directly rather than only through a draft. Its edge case
    is the empty vendor key, and reaching that through the pipeline needs a
    company whose books are full of blank-party vouchers.

    DELETED to make room for this: `_default_credit`, a hard-coded preference
    list - "Cash", then "Bank", then "Sundry Creditors", then whatever sorted
    first in the chart, then the literal "Cash" EVEN WHEN THE COMPANY HAD NO
    SUCH LEDGER. It ran on every entry and carried no provenance.

    The expense leg has always been proposed from the company's own history.
    The funding leg was guessed. Asking "how did you pay?" about a vendor whose
    last forty vouchers all say Cash is the same failure `bootstrap` exists to
    prevent: reading years of history and then behaving like a fresh install.

    Unanimous or nothing, exactly like a vendor match. Two different funding
    accounts in the history is a conflict, and a conflict is a question, never
    a majority vote.
    """
    key = normalise_vendor(party)
    if not key:
        # Guarding on `party` alone was not enough: "   " is truthy and
        # normalises to "", the same key every history row with no party has.
        # One blank-party row is a mistake; thirty of them would have made
        # "the vendor nobody named" the most consistently-paid supplier in the
        # books, and handed its funding account to any entry typed with a
        # whitespace vendor.
        return ""
    seen = {
        v.credit_account
        for v in history
        if v.credit_account and normalise_vendor(v.party) == key
    }
    return seen.pop() if len(seen) == 1 else ""


def _leg_source(proposed: str) -> str:
    """Where a ledger leg came from, so a reader can tell.

    `Voucher.provenance` is what makes the Hallucinate definition measurable:
    a field with no source is a hallucination by definition. Both legs now
    carry one.
    """
    return "company_history" if proposed else NOT_FOUND


def build_draft(
    company: str,
    data: bytes,
    mime: str,
    extractor: Extractor,
    accounts: tuple[str, ...],  # noqa: ARG001 - see below
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

    `accounts` IS UNUSED here as of 2026-08-09, and kept on purpose. Its only
    reader was `_default_credit`, the deleted funding guess. It is not removed
    yet because it is positional in roughly thirty call sites including two
    test files owned by other work in flight, and a mechanical signature change
    across those is a merge conflict, not an improvement. Recorded as an open
    item in `docs/PROJECT_STATE.md` rather than left as a silent oddity.

    Nothing here validates a proposed ledger against `accounts`, and that is
    also deliberate: emptying a leg that memory proposed but the chart no
    longer contains would DELETE the evidence. `checks.accounts_exist` names
    the missing ledger instead, which is what the person needs to see.
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
        # Absent on purpose. The funding leg is proposed in `evaluate`, which
        # is the one function that both sees this company's history AND is the
        # only way a draft can ever acquire a decision. Proposing it here as
        # well would give a second, unguarded route to a ledger leg.
        credit_account="",
        amount_paise=record.total_paise or 0,
        gst_paise=record.tax_paise,
        provenance=dict(record.per_field_source)
        | {
            "credit_account": NOT_FOUND,
            "debit_account": _leg_source(proposed_debit),
        },
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

    # The funding leg, proposed HERE and nowhere else.
    #
    # `evaluate` is the only function that gives a draft a decision, and `post`
    # refuses a draft without one. So siting the proposal here makes it
    # structurally impossible to post a voucher whose credit leg was not either
    # read from this company's own history or answered by a person. Putting it
    # in `build_draft` would have been symmetric with the expense leg and
    # weaker: a caller could skip straight to `evaluate` with an empty leg.
    #
    # Only ever fills an EMPTY leg, so a human answer is never overwritten.
    if not draft.voucher.credit_account:
        proposed = funding_from_history(draft.voucher.party, history)
        if proposed:
            draft.voucher = replace(
                draft.voucher,
                credit_account=proposed,
                provenance=dict(draft.voucher.provenance or {})
                | {"credit_account": "company_history"},
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
    draft.decision = decide_problems(
        draft.problems,
        asked=len(draft.answers),
        # The SAME answered-ids `next_question` filters on, so the decision
        # and the question can never disagree about what is outstanding.
        answered=[pid for pid, _ in draft.answers],
        # G5.1. The decision is born carrying the identity of the operation it
        # decides. `answer` clears the decision and this rebuilds it, so the
        # id survives every question without ever being minted again.
        operation_id=draft.operation_id,
    )
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

    The answer is NOT permission to post. It is new information, and the entry
    can still come out Not valid.

    Phase 4 exit 2, made STRUCTURAL 2026-08-09. This used to say "the caller
    must re-run evaluate()" and leave `draft.decision` alone — a comment, not a
    guarantee. Since this function rewrites `debit_account`, a surviving
    decision would be an approval granted to a DIFFERENT voucher from the one
    the draft now carries.

    It was safe only by accident: `answer` is reached only from an UNCLEAR
    draft, and `post` refuses anything that is not VALID. Change either and a
    mutated voucher posts against a stale approval.

    So the decision is CLEARED. `post` then fails closed with "draft has not
    been evaluated", which makes re-entering the decision order mandatory
    rather than requested. Every existing caller already re-evaluated on the
    next line, so nothing had to change around it.
    """
    draft.answers.append((problem_id, account))

    # WHICH leg the answer sets is decided by the question that was asked.
    # Every answer used to go to `debit_account`, because the funding leg was
    # never asked about - `_default_credit` guessed it. Now that "how did you
    # pay?" is a real question, its answer must land on the credit side or the
    # person silently overwrites the expense account they already chose.
    leg = "credit_account" if problem_id == FUNDING_PROBLEM else "debit_account"
    draft.voucher = replace(draft.voucher, **{leg: account})
    prov = dict(draft.voucher.provenance or {})
    prov[leg] = "human_answer"
    draft.voucher = replace(draft.voucher, provenance=prov)
    draft.decision = None
    return draft


# What must come back UNCHANGED for a write to count as ours. Money, party,
# date and both legs - every field a person would be harmed by if Tally stored
# something else. `narration` is deliberately absent: we stamp the marker into
# it, so it is expected to differ from what the draft carried.
VERIFIED_FIELDS = (
    "amount_paise",
    "party",
    "date",
    "debit_account",
    "credit_account",
)


def _identity_mismatches(sent: Voucher, back: Voucher) -> list[str]:
    """Every field Tally returned differently, named one per line."""
    return [
        f"{field}: sent {getattr(sent, field)!r}, Tally has {getattr(back, field)!r}"
        for field in VERIFIED_FIELDS
        if getattr(sent, field) != getattr(back, field)
    ]


def post(
    draft: Draft,
    client: TallyClient,
    *,
    log: ActionLogSink | None = None,
    memory: CompanyMemory | None = None,
    run_id: str = "",
) -> Draft:
    """Write to Tally, but only if the outcome is Valid, then read it back.

    Raises if called on a draft that is not Valid. The gate lives here, server
    side, so no caller can bypass it.

    W1, FIXED 2026-08-09. The read-back used to be a PRESENCE check: it asked
    Tally "is there a voucher with my operation id", and on any answer that was
    not None it reported success and THREW THE VOUCHER AWAY. It checked the
    label on the box and never opened it.

    Two agents found the same hole independently. A voucher carrying our marker
    with a different amount, party or date was accepted as proof, and so was
    one that existed in the marker view but was ABSENT from `read_vouchers` and
    the trial balance. That defeated G3 - the register guarantee this phase
    exists to deliver - in the one code path that actually posts. The guarantee
    was only ever enforced by a standalone script.

    So the box is opened. Every field in `VERIFIED_FIELDS` must come back
    unchanged, and `posted_tally_id` is taken from what TALLY returned rather
    than from our own write result, because Tally's answer is the evidence and
    ours is only a claim.

    This can only ever refuse MORE, never post more. There is no input for
    which the old code refused and this one accepts.

    W2 / A6, FIXED 2026-08-09. THE ATTEMPT IS RECORDED BEFORE THE SOCKET OPENS.

    Until now the only ActionLog row on this path was written by the CALLER,
    after `post` returned. So every way this function can fail - a refused
    read-back, a mismatch, a register absence, a dropped connection, the
    process being killed mid-write - left ZERO durable rows. The operation id
    survived in a traceback and nowhere else, which means a voucher that may
    exist in somebody's books could not afterwards be found, reconciled or
    reversed.

    An exception handler would have closed most of that and none of the worst
    of it: no `except` clause runs when the machine loses power between the
    request and the response, and that is precisely the case where a voucher
    exists and nobody knows. So the row is WRITTEN AHEAD. A
    `write_attempted` row with no terminal row after it is the durable
    signature of "we started a write and cannot say how it ended" - the state
    the brief calls POSTING and the code previously had no way to represent.

    `log` and `memory` are optional so that the many tests calling
    `post(draft, client)` keep working, and both real callers - `run` below and
    `web/app.py` - pass them. `test_write_ahead_log.py` asserts that they do,
    because an optional audit trail nobody passes is not an audit trail.
    """
    if draft.decision is None:
        raise ValueError("draft has not been evaluated")
    if draft.decision.outcome is not Outcome.VALID:
        raise ValueError(f"refusing to post: outcome is {draft.decision.outcome.value}")

    # G5.1. The approval must belong to THIS operation. Both halves matter and
    # they fail for different reasons:
    #
    #   empty     a decision that names no operation authorises no write. The
    #             write-ahead row would then be the only thing carrying the id,
    #             and nothing would tie the voucher back to the reasoning.
    #   mismatch  a decision built for a DIFFERENT draft. `DRAFTS` holds up to
    #             200 at once, so two live drafts is the ordinary case; an
    #             approval granted to one of them must not post the other.
    #
    # Checked before the write-ahead row, because neither case is a write that
    # started — it is a write that was never entitled to start.
    if not draft.decision.operation_id:
        raise ValueError(
            f"refusing to post {draft.operation_id!r}: the decision carries no "
            "operation id, so nothing ties this approval to this write"
        )
    if draft.decision.operation_id != draft.operation_id:
        raise ValueError(
            f"refusing to post {draft.operation_id!r}: the decision authorised a "
            f"different operation, {draft.decision.operation_id!r}"
        )

    _record_write(log, draft, memory, client, run_id, WRITE_ATTEMPTED, "")

    try:
        client.write_voucher(draft.company, draft.voucher, draft.operation_id)

        # C6: read back. HTTP 200 is not proof the voucher exists.
        back = client.read_by_operation_id(draft.company, draft.operation_id)
        if back is None:
            raise RuntimeError(
                f"wrote operation {draft.operation_id} but could not read it back"
            )

        mismatches = _identity_mismatches(draft.voucher, back)
        if mismatches:
            raise RuntimeError(
                f"wrote operation {draft.operation_id} and read back a DIFFERENT "
                f"voucher: " + "; ".join(mismatches) + ". Nothing is recorded as "
                "posted. The entry may exist in Tally and must be checked by hand."
            )

        # G3: our own marker filter found it. Tally's UNFILTERED register is
        # what a person would see, and the two can disagree - a voucher can
        # carry the marker and still be absent from the register and the trial
        # balance.
        register = client.read_vouchers(draft.company)
        if not any(v.tally_id == back.tally_id for v in register):
            raise RuntimeError(
                f"operation {draft.operation_id} was readable by its marker but "
                f"is NOT in Tally's own register (tally_id {back.tally_id!r}). "
                "Nothing is recorded as posted."
            )
    except BaseException as exc:
        # BaseException, not Exception. A KeyboardInterrupt or a SystemExit
        # arriving between the write and the read-back leaves exactly the
        # uncertainty this row exists to record, and catching only `Exception`
        # would let the two cases most likely to happen during a manual run go
        # unrecorded.
        _record_write(
            log,
            draft,
            memory,
            client,
            run_id,
            WRITE_OUTCOME_UNKNOWN,
            f"{type(exc).__name__}: {exc}",
        )
        raise

    # Tally's identifier, not ours. What Tally says it stored is the evidence.
    draft.posted_tally_id = back.tally_id
    return draft


@dataclass(frozen=True)
class Reversal:
    """What a reversal actually did, measured against Tally's own trial balance."""

    operation_id: str
    reversed_: bool
    #: Ledger -> paise, AFTER minus BEFORE, for every ledger that moved.
    moved: dict[str, int]
    detail: str


def reverse_operation(client: TallyClient, company: str, operation_id: str) -> Reversal:
    """Undo exactly one voucher by operation id, and PROVE the books moved back.

    The single doorway for undoing. `POST /reverse` used to call
    `client.reverse_by_operation_id` directly with whatever string the form
    carried, check nothing, and report "reversed" on the strength of a boolean.
    `pipeline.reverse` did the same thing one layer up. Neither looked at the
    trial balance, although criterion #6.5 - "post N vouchers, run bulk
    reverse, and Tally's trial balance returns to its exact prior value in
    paise" - is the rollback the whole project rests on, and it was only ever
    checked inside tests.

    So it is checked here, on the live path:

        read the voucher back      what should move, and by how much
        trial balance BEFORE
        reverse
        trial balance AFTER
        compare                    exact paise, both legs, nothing else moved

    A reversal that returns True and leaves the books unchanged is the worst of
    the three possible outcomes, because it is the one that gets believed.
    """
    voucher = client.read_by_operation_id(company, operation_id)
    if voucher is None:
        return Reversal(
            operation_id=operation_id,
            reversed_=False,
            moved={},
            detail=f"no voucher of ours in {company!r} carries {operation_id!r}",
        )

    before = client.trial_balance(company)
    ok = client.reverse_by_operation_id(company, operation_id)
    after = client.trial_balance(company)
    moved = {
        ledger: after.get(ledger, 0) - before.get(ledger, 0)
        for ledger in set(before) | set(after)
        if after.get(ledger, 0) != before.get(ledger, 0)
    }

    if not ok:
        if moved:
            raise RuntimeError(
                f"reversing {operation_id!r} in {company!r} reported failure but "
                f"the trial balance moved by {moved}. The books and the answer "
                "disagree; nothing here can be trusted until a person looks."
            )
        return Reversal(
            operation_id=operation_id,
            reversed_=False,
            moved={},
            detail=f"Tally refused to reverse {operation_id!r} and nothing moved",
        )

    expected = {
        voucher.debit_account: -voucher.amount_paise,
        voucher.credit_account: voucher.amount_paise,
    }
    if moved != expected:
        raise RuntimeError(
            f"reversing {operation_id!r} in {company!r} reported success, but "
            f"the trial balance moved by {moved} and undoing this voucher "
            f"should have moved it by {expected}. Nothing is recorded as "
            "reversed. The books must be checked by hand."
        )

    return Reversal(
        operation_id=operation_id,
        reversed_=True,
        moved=moved,
        detail=(
            f"undid {voucher.amount_paise} paise: "
            f"{voucher.debit_account} and {voucher.credit_account} are back "
            "to their exact prior value"
        ),
    )


def reverse(draft: Draft, client: TallyClient) -> bool:
    """Undo exactly this voucher, by operation ID. Never by amount.

    Thin on purpose: everything it does is `reverse_operation`, which is also
    what the web app calls, so the two paths cannot drift into two different
    definitions of what "reversed" means.
    """
    return reverse_operation(client, draft.company, draft.operation_id).reversed_


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
    # A5. BEFORE the two reads, not after them. Both of these can fail on a
    # flaky connector, and when they do on a company we have never successfully
    # read, the exception a caller sees should say "we have not read your
    # books", not "connection refused". Same outcome - nothing is written -
    # different diagnosis, and the two lead to completely different actions.
    memory.require_usable("propose an account")

    accounts = client.read_accounts(company)
    history = client.read_vouchers(company)

    draft = build_draft(company, data, mime, extractor, accounts, memory, today=today)
    draft = evaluate(
        draft, accounts, history, memory, detector_set=detector_set, flag_cap=flag_cap
    )

    if draft.decision and draft.decision.outcome is Outcome.VALID:
        draft = post(draft, client, log=log, memory=memory, run_id=run_id)
        record_decision(log, draft, memory, client, "posted", run_id)
    else:
        record_decision(log, draft, memory, client, "blocked", run_id)

    return draft


#: The two rows that bracket a write. A `write_attempted` with no partner is
#: the durable signature of an unknown outcome, whether the process survived
#: to say so or not.
WRITE_ATTEMPTED = "write_attempted"
WRITE_OUTCOME_UNKNOWN = "write_outcome_unknown"


def _record_write(
    log: ActionLogSink | None,
    draft: Draft,
    memory: CompanyMemory | None,
    client: TallyClient,
    run_id: str,
    action: str,
    detail: str,
) -> None:
    """One row about a write, independent of any decision.

    Separate from `record_decision` because that function returns early when
    `draft.decision is None` and reports the DECISION'S outcome. Neither fits
    here: this row is about the write, and its honest outcome is that there
    isn't one yet.
    """
    if log is None or memory is None:
        return

    log.record_action(
        ActionLog(
            ts=datetime.datetime.now(datetime.UTC),
            action=action,
            company_key=memory.identity.key,
            # Not the decision's VALID. This row says a write is in flight or
            # ended unknown, and calling either of those "valid" would let a
            # reader mistake it for a posted voucher.
            outcome=action,
            reason=detail
            or (
                "about to write to Tally; if no later row names this operation "
                "id, a voucher may exist and must be checked by hand"
            ),
            run_id=run_id,
            backend=type(client).__name__,
            operation_id=draft.operation_id,
            voucher_id="",
            vendor_id=draft.voucher.party,
            detail=f"{draft.voucher.debit_account or '(none proposed)'} / "
            f"{draft.voucher.credit_account or '(none proposed)'} "
            f"{draft.voucher.amount_paise} paise",
        )
    )


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
