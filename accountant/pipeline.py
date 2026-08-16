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
from typing import TYPE_CHECKING, Protocol, runtime_checkable

from accountant import checks, problems
from accountant import questions as Q
from accountant.cage import gate as cage_gate
from accountant.cage.decision import Action, Decided, DocumentType, Moment
from accountant.decide import decide_problems
from accountant.detect import detectors
from accountant.extract.adapter import NOT_FOUND, ExtractedRecord, Extractor
from accountant.memory.company import (
    FROM_HUMAN_ANSWER,
    CompanyMemory,
    LiveDisagreement,
    disagrees_with_live_history,
    propose_account,
)
from accountant.memory.identity import normalise_company
from accountant.memory.index import normalise_vendor
from accountant.problems import FUNDING_PROBLEM, Problem
from accountant.rules.gst_rates import official_corpus
from accountant.rules.place_of_supply import SupplyEvidence
from accountant.schema import (
    NOT_RECORDED,
    ActionLog,
    CheckResult,
    Decision,
    Flag,
    Outcome,
    Voucher,
)
from accountant.tallyio.client import (
    DuplicateOperation,
    TallyClient,
    new_operation_id,
)
from accountant.tax.decision import TaxDecision, decide_tax

if TYPE_CHECKING:  # pragma: no cover - a type, never a runtime dependency
    # THIS IMPORT CANNOT BE MADE AT MODULE SCOPE, and the reason is measured
    # rather than stylistic. `accountant.period` imports
    # `accountant.observability`, which imports THIS module by name at its line
    # 54. So a top-level `from accountant.tallyio.period import PeriodReader`
    # here is fine, but `from accountant.period import check_period` is a
    # genuine cycle that raises at import time, not a lint. The type is taken
    # here under `from __future__ import annotations`, where only the checker
    # reads it, and the function is imported inside `_period_open` below.
    from accountant.tallyio.period import PeriodReader

#: What `provenance` says about a leg that came out of this company's own books.
#: A literal in three places until 2026-08-10, and one of them is now read as a
#: gate rather than only written, so it is named once.
FROM_COMPANY_HISTORY = "company_history"

#: What the voucher's provenance says about a party name that was ESTIMATED.
#:
#: `NOT_FOUND`-prefixed on purpose, because that prefix is already the one rule
#: two readers share: `cage/gate.py::_was_read` and `web/app.py` both treat a
#: source beginning `not_found` as an absence whatever value came with it. The
#: leg IS absent - `voucher.party` is blank - so the line beside it has to say
#: absent, or the evidence record contradicts the thing it is evidence about.
#:
#: It names the tier, because "no party" and "a party we would not act on" are
#: different facts and the reviewer reading this voucher afterwards needs to
#: know which one they are looking at. The reading itself is NOT deleted: it
#: stays on `Draft.record`, with its own source and its own score.
ESTIMATED_NOT_AN_IDENTITY = (
    f"{NOT_FOUND}: {{backend}} estimated this name rather than reading it, and "
    "an estimated name is never used as a supplier's identity - so this one is "
    "being asked about instead"
)

#: What the voucher's provenance says about a total that was ESTIMATED.
#:
#: `NOT_FOUND`-prefixed for exactly the reason the entry above is: that prefix is
#: the one rule `cage/gate.py::_was_read` and `web/app.py` already share, so a
#: reader of this voucher sees an ABSENCE rather than a bill for ₹0.00. The
#: amount IS absent - it is the zero `build_draft` writes when nothing was read -
#: and a source line still naming the tier that guessed would leave the evidence
#: record contradicting the voucher it is evidence about.
#:
#: A SECOND SENTENCE RATHER THAN ONE WITH A FIELD PLACEHOLDER IN IT, because the
#: two refusals are not the same refusal. A name is refused for being an
#: IDENTITY; a total is refused for being MONEY, and the person is then asked how
#: much rather than who. One sentence covering both would name neither.
ESTIMATED_NOT_AN_AMOUNT = (
    f"{NOT_FOUND}: {{backend}} estimated this amount rather than reading it, and "
    "an estimated amount is never posted as money - so this one is being asked "
    "about instead"
)

#: D-06. The vendor whose remembered account the CURRENT ledger contradicts.
#:
#: A distinct problem id, not `which_account`. The two are asked in opposite
#: situations - `which_account` fires when memory has no single answer, this one
#: fires when memory has one and the live ledger no longer backs it - and
#: `decide_problems` retires an answered problem by id. Sharing an id would let
#: an answer to one silently retire the other.
LIVE_HISTORY_DISAGREES = "live_history_disagrees_with_memory"


class ActionLogSink(Protocol):
    """Somewhere durable to append a decision.

    A Protocol rather than `MemoryStore` so the pipeline does not depend on
    SQLite. `accountant/memory/store.py` satisfies it today; anything that can
    append and never mutate would.
    """

    def record_action(self, entry: ActionLog) -> None: ...


@runtime_checkable
class OperationRegister(Protocol):
    """A durable record of every operation id this company has ever written.

    Separate from `ActionLogSink` rather than folded into it, because they
    answer different questions and a sink that can only append rows is still a
    useful thing to pass. `MemoryStore` satisfies both.

    `runtime_checkable` so `post` can ask. A `Protocol` check at runtime tests
    for the METHODS and not for their behaviour, which is all that is wanted
    here: the question is "can this store refuse a reused identity", and a
    store that cannot must not silently look like one that can.
    """

    def claim_operation(self, company_key: str, operation_id: str, at: str) -> bool: ...

    def mark_operation_reversed(
        self, company_key: str, operation_id: str, at: str
    ) -> bool: ...


#: What the cage's answer does to an outcome that survived the decision order.
#:
#: A TABLE BECAUSE IT IS ONE, and because the safety property is then readable
#: in the data rather than spread over three branches: no value here is wider
#: than the VALID it is allowed to replace. `tests/test_cage_on_the_live_path.py`
#: asserts it is total over `Action`, so a fourth action fails at build time
#: instead of raising `KeyError` in front of somebody's bill.
CAGE_NARROWS: dict[Action, Outcome] = {
    Action.POST: Outcome.VALID,
    Action.ASK: Outcome.UNCLEAR,
    Action.BLOCK: Outcome.NOT_VALID,
}


def narrowed_by_the_cage(decision: Decision, said: Decided) -> Decision:
    """Apply the cage's answer to a decision. It may NARROW and never WIDEN.

        existing VALID + cage POST   -> VALID
        existing VALID + cage ASK    -> UNCLEAR
        existing VALID + cage BLOCK  -> NOT_VALID
        existing UNCLEAR             -> UNCHANGED, whatever the cage says
        existing NOT_VALID           -> UNCHANGED, whatever the cage says

    `docs/ARCHITECTURE.md` 19.4: an advisory layer is ADDED to a deterministic
    one and never SUBTRACTS from it. The cage weighs confidence, conservation
    and four world facts; `decide_problems` weighs checks, memory and
    detectors. Neither can see what the other sees, so agreement is not
    required - but a cage that could overrule a refusal would be a second,
    weaker decision order with the last word, and the failure it produces is a
    bill somebody already refused arriving in their books.

    THE PROPERTY IS STRUCTURAL AND NOT A CONSEQUENCE OF THE TABLE. The early
    return is what holds it: the only decision this function can replace is one
    that was VALID, so no cage answer of any kind can reach an UNCLEAR or a
    NOT_VALID. Deleting the table's `POST -> VALID` row, or adding a row that
    widened, would still not let a refusal become a post. That is deliberate -
    a safety property that depends on the contents of a dict is one dict edit
    away from being gone, and the edit looks like a configuration change.

    THE SENTENCE COMES FROM THE CAGE WHEN THE CAGE IS WHAT REFUSED. A decision
    that survived the deterministic order carries "nothing unclear and nothing
    surprising", and leaving that on a screen which now says "needs an answer"
    tells the person we do not know why we stopped. `said.said` is every reason
    the cage had, already joined into whole sentences that name no ledger
    account.

    WHAT IT DOES NOT CARRY OVER, SAID SO NOBODY RELIES ON IT: a cage ASK
    produces an UNCLEAR with NO `question_options` and NO `question_problem_id`,
    because the cage has no `Problem` to attach and no reader in this repository
    can yet offer the answers. So `next_question` returns `None`, the review
    page renders the cage's sentence with no buttons, and
    `Decision.refuse_answer` correctly says there is nothing to answer. That is
    a hand-over wearing an UNCLEAR badge, and it is the honest state until a
    reader can ask - not a defect to be papered over by inventing a question
    nobody can answer.
    """
    if decision.outcome is not Outcome.VALID:
        return decision
    narrowed = CAGE_NARROWS[said.action]
    if narrowed is Outcome.VALID:
        return decision
    return replace(decision, outcome=narrowed, reason=said.said)


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
    #: The concerns the display cap kept off the screen. NOT discarded.
    #:
    #: `detectors.run` returns the capped list and a count, and used to be the
    #: only thing that saw the rest - so a DISPLAY decision was deleting
    #: findings from the evidence record. The owner's rule when setting
    #: `flag_cap = 3` was explicit: "never lose concerns from the audit/evidence
    #: record" and "the cap affects display only, not detection, storage,
    #: safety, or posting". `flags` is what a person sees; `flags +
    #: suppressed_flags` is what the system found.
    suppressed_flags: list[Flag] = field(default_factory=list[Flag])
    problems: list[Problem] = field(default_factory=list[Problem])
    decision: Decision | None = None
    #: What the GST engine made of this bill, or None when it carries no tax.
    #:
    #: EVIDENCE, NEVER AUTHORITY. A `TaxOutcome.VALID` here means the tax was
    #: computed and every part of it can be cited. It does not mean, and must
    #: never come to mean, "post this" - `accountant/tax/decision.py` says the
    #: same thing at greater length and refuses to construct a decision that
    #: claims otherwise.
    tax: TaxDecision | None = None
    posted_tally_id: str | None = None
    answers: list[tuple[str, str]] = field(default_factory=list[tuple[str, str]])
    #: Detector names the person has looked at and chosen not to act on.
    #:
    #: A dismissal is NOT a validation and deliberately changes nothing else:
    #: the flag stays, the problem stays, the decision stays. It exists so the
    #: screen can stop asking and so criterion #3.7 has something to log. One
    #: line away from here is a version where dismissing a surprise approves
    #: the entry, and that is the version that posts a wrong voucher because
    #: somebody clicked to make a warning go away.
    dismissed: list[str] = field(default_factory=list[str])
    #: D-06. Set by `evaluate` whenever memory and the live ledger disagree
    #: about this vendor, carrying BOTH sources and BOTH counts.
    #:
    #: Recorded even after a person has answered and the question has stopped
    #: being asked, because "your books used to say one thing and now say
    #: another" is a fact about the entry that a reader needs afterwards, and an
    #: evidence record that erases itself on resolution is not one.
    memory_conflict: LiveDisagreement | None = None

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
    return FROM_COMPANY_HISTORY if proposed else NOT_FOUND


def _party_not_taken(record: ExtractedRecord, party: str | None) -> dict[str, str]:
    """The one provenance line to overwrite when a read name was not taken.

    Empty in every other case, and that is deliberate rather than tidy. The
    record's own source line is the truth about what the reader did, and the
    only situation it stops describing the VOUCHER is this one: the reader
    stated a name and a real backend beside it, and the leg is blank anyway.

    A field the reader genuinely did not read already carries `not_found` and
    the reader's own sentence, which is better than anything this could write.
    """
    if party is not None or record.party is None:
        return {}
    return {"party": ESTIMATED_NOT_AN_IDENTITY.format(backend=record.backend)}


def _amount_not_taken(
    record: ExtractedRecord, total_paise: int | None
) -> dict[str, str]:
    """The same line, one field over, when a read total was not taken.

    Empty in every other case, for the reason `_party_not_taken` gives above:
    the record's own source line is the truth about what the reader did, and the
    only situation it stops describing the VOUCHER is this one - the reader
    stated a figure with a real backend beside it, and the voucher's amount is
    zero anyway.
    """
    if total_paise is not None or record.total_paise is None:
        return {}
    return {"total_paise": ESTIMATED_NOT_AN_AMOUNT.format(backend=record.backend)}


def build_draft(
    company: str,
    data: bytes,
    mime: str,
    extractor: Extractor,
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

    `accounts` IS GONE, 2026-08-09. It was a positional parameter this function
    never read - its only reader was `_default_credit`, the deleted funding
    guess - carried since then behind a `# noqa: ARG001` and a paragraph
    explaining that it did nothing. Deferring it was justified as "thirty
    positional call sites, a merge conflict rather than an improvement", and
    the count was right: 32 sites, 2 in the package and 30 across 11 test
    files, all passing it 5th of 6 positional arguments.

    Removed anyway, because the cost was not the noqa. A signature that names
    the chart of accounts says this function consults the chart, and the
    docstring then had to spend a paragraph saying it does not - a reader who
    skims the signature and not the paragraph concludes the proposed ledger is
    validated here. It is not, and it must not be: emptying a leg that memory
    proposed but the chart no longer contains would DELETE the evidence.
    `checks.accounts_exist` names the missing ledger instead, in `evaluate`,
    which is what the person needs to see.

    The change is a pure deletion with no behaviour in it, and pyright in
    strict mode plus the whole suite check every call site, so "did I miss one"
    is measured rather than reviewed.
    """
    expected = normalise_company(company)
    if memory.identity.key != expected:
        raise ValueError(
            f"memory for company {memory.identity.name!r} was passed to a "
            f"draft for {company!r}; company-scoped memory is never shared"
        )

    record = extractor.extract(data, mime)

    # AN ESTIMATED NAME IS NOT AN IDENTITY. This is the whole of the F-03 fix
    # and it is one line, because this is the one place a read party name turns
    # into one. Everything downstream keys off `Voucher.party` - `evaluate`
    # hands it to `funding_from_history`, to `memory.lookup`, to the detectors
    # and to `disagrees_with_live_history` - so a name refused here is refused
    # in all five without five separate guards to keep in step.
    party = record.party if record.read_exactly("party") else None

    # AND AN ESTIMATED AMOUNT IS NOT MONEY, 2026-08-13, owner-ordered. The line
    # above guarded IDENTITY and the line at `amount_paise=` below did not guard
    # MONEY: an estimated name was refused and asked about, and an estimated
    # TOTAL was taken at face value. `Voucher.amount_paise` is what `checks`
    # runs on, what the magnitude and duplicate detectors compare against
    # history, and what `pipeline.post` writes to Tally - so a guessed figure
    # reaching it is a wrong number in somebody's books, not a wrong screen.
    #
    # `read_exactly`, NOT A THRESHOLD, and the SAME `ENTITLED_TO_EXACT` list the
    # party guard uses. The owner's words were "a tier entitled to be believed",
    # that list already is one, no number is chosen here, and `ASK_FLOOR` and
    # `AUTO_POST_FLOOR` stay the only two bands in the product.
    total_paise = record.total_paise if record.read_exactly("total_paise") else None

    proposed_debit = propose_account(memory, party) if party else None
    proposed_debit = proposed_debit or ""

    voucher = Voucher(
        id=f"draft-{uuid.uuid4().hex[:8]}",
        date=record.date or (today or datetime.date.today()),
        party=party or "",
        narration=record.raw_text.strip(),
        debit_account=proposed_debit,
        # Absent on purpose. The funding leg is proposed in `evaluate`, which
        # is the one function that both sees this company's history AND is the
        # only way a draft can ever acquire a decision. Proposing it here as
        # well would give a second, unguarded route to a ledger leg.
        credit_account="",
        # ZERO IS THE UNREAD AMOUNT, and it is a decision rather than a fallback.
        # `Voucher.amount_paise` is `int` and cannot hold `None`, so the choice
        # was zero here or `int | None` through `schema`, `tallyio` and every
        # reader of a posting. Zero is safe because it does not sit still: it is
        # the exact value `checks.amount_is_positive` exists to catch, that check
        # runs first in `ALL_CHECKS`, `problems.QUESTION_FOR` already maps it to
        # `questions.how_much`, and the person reads "How much did you pay X? I
        # couldn't work it out." No new rule, no new sentence, and the outcome is
        # UNCLEAR - so nothing auto-posts and nothing is refused outright either.
        #
        # A ZERO CANNOT REACH THE CAGE'S ARITHMETIC AND PASS IT, which is the
        # first thing to check about a number like this. `cage/gate.py::observed`
        # builds the observation from `draft.record`, never from this voucher, so
        # `conservation` runs on the figure the reader actually stated and no
        # 0-against-0 comparison exists to be satisfied.
        amount_paise=total_paise or 0,
        gst_paise=record.tax_paise,
        provenance=dict(record.per_field_source)
        | _party_not_taken(record, party)
        | _amount_not_taken(record, total_paise)
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


def memory_still_owns_the_leg(voucher: Voucher, conflict: LiveDisagreement) -> bool:
    """Is the debit leg still memory's unreviewed answer? D-06's ask-or-not gate.

    The conflict is RECORDED whenever the two sources disagree. Whether it is
    also ASKED about turns on this, and only this: is the account this voucher
    is about to be posted to the one memory remembered, and did it get there
    without a person looking at it.

    Both halves are the point of the feature. `answer` stamps
    `human_answer` onto the leg it rewrites, so a person who has been shown both
    numbers and has chosen is not asked the same question again — which is what
    makes the answer able to resolve the conflict at all. And a leg carrying
    anything other than the remembered account is not memory winning, so there
    is nothing here to stop.

    Public rather than `_private` because it is the exact boundary between
    "recorded" and "asked", and a boundary worth stating is worth asserting
    directly.
    """
    if (voucher.provenance or {}).get("debit_account", "") == FROM_HUMAN_ANSWER:
        return False
    return voucher.debit_account == conflict.remembered_account


def which_is_it_now(
    conflict: LiveDisagreement, party: str, accounts: Sequence[str]
) -> Q.Question:
    """The D-06 question. Both counts, no ledger name, S7.

    Built here rather than in `accountant/questions.py` for one reason and it is
    not a good one: the phrasebook module is owned elsewhere this cycle. The
    S7 rule it enforces is obeyed all the same, and
    `tests/test_stale_memory_conflict.py` runs this question through the same
    `Question.mentions_any` guard the phrasebook's own questions are run
    through, so the rule is checked rather than promised.

    The two numbers are the whole content. "Your books changed" is not
    actionable; "you did this 40 times and then that 60 times" is the sentence
    a person can recognise or reject in a second.
    """
    who = party.strip() or "them"
    options = [
        Q.Answer(label=Q.PLAIN[account], value=account)
        # Live first, most-used first, because the live ledger is the current
        # truth and D-06 is that it wins. Then the remembered one, if the live
        # ledger has stopped naming it at all.
        for account in (*conflict.live_accounts, conflict.remembered_account)
        # An option is a promise that the thing exists — the lesson `how_paid`
        # is written around. An account the chart no longer holds, or one we
        # have no plain words for, is not offered.
        if account in accounts and account in Q.PLAIN
    ]
    seen: set[str] = set()
    unique = [o for o in options if not (o.value in seen or seen.add(o.value))]
    unique.append(Q.Answer(label="something else", value=Q.HANDOVER))
    return Q.Question(
        problem_id=LIVE_HISTORY_DISAGREES,
        text=(
            f"With {who}, your books used to say one thing — "
            f"{conflict.remembered_times} times. They now say something else "
            f"{conflict.changed_times} times. Which is this one?"
        ),
        answers=tuple(unique),
    )


def _with_conflict_question(
    found: list[Problem],
    conflict: LiveDisagreement,
    party: str,
    accounts: Sequence[str],
) -> list[Problem]:
    """Add the D-06 problem, keeping 'how did you pay?' last.

    `problems.find` deliberately asks the purpose of the entry before the
    funding of it, and this question is about the purpose. Appending would put
    it after the funding question and quietly invert that order, so it goes in
    front of the funding problem when there is one.
    """
    if any(p.id == LIVE_HISTORY_DISAGREES for p in found):
        return found
    problem = Problem(
        id=LIVE_HISTORY_DISAGREES,
        answerable=True,
        detail=conflict.detail,
        question=which_is_it_now(conflict, party, accounts),
    )
    at = next((i for i, p in enumerate(found) if p.id == FUNDING_PROBLEM), len(found))
    return [*found[:at], problem, *found[at:]]


def tax_for(draft: Draft, accounts: Sequence[str]) -> TaxDecision:
    """Run the GST engine over one bill and return what it made of it.

    WHAT IT CAN ANSWER WITH TODAY, STATED PLAINLY. `ExtractedRecord` carries no
    HSN or SAC code and no GSTINs, so `SupplyEvidence` is empty and the honest
    answer is almost always UNCLEAR with the reason named - "the document does
    not state a place of supply" rather than a computed rate.

    That is the point rather than a shortfall. The person is currently told only
    that Accountant Dad cannot post a tax line; this tells them WHY the tax
    could not be worked out either. And the day a reader that extracts a GSTIN
    and an HSN code arrives, this call starts producing real rates with nothing
    further to wire - the engine was always able to, and nothing was asking it.

    `taxable_paise` is the amount NET of tax. `Voucher.amount_paise` is the
    total and `gst_paise` is the tax inside it, so the base is the difference. A
    rate applied to the gross would overstate the tax by the tax.
    """
    voucher = draft.voucher
    gst = voucher.gst_paise or 0
    return decide_tax(
        corpus=official_corpus(),
        # No HSN or SAC is extracted yet, and None is the honest value. Passing
        # a guessed code would produce a rate with a citation behind it, which
        # is the most convincing possible way to be wrong.
        raw_code=None,
        taxable_paise=max(voucher.amount_paise - gst, 0),
        supply_date=voucher.date,
        # Empty for the same reason. `place_of_supply_stated_on_document` is
        # False because no document has stated one to us: the flag is the
        # difference between "the document says so" and "something says so",
        # and only the first may decide a tax.
        evidence=SupplyEvidence(supplier=None, place_of_supply=None),
        chart_of_accounts=tuple(accounts),
    )


def evaluate(
    draft: Draft,
    accounts: tuple[str, ...],
    history: tuple[Voucher, ...],
    memory: CompanyMemory,
    *,
    detector_set: Sequence[detectors.Detector] = detectors.SLICE_4_DETECTORS,
    flag_cap: int | None = None,
    period_open: bool | None,
    pdf_repaired: bool | None,
) -> Draft:
    """Run checks, memory and detectors, apply the decision order, then the cage.

    The detectors read history through THIS COMPANY'S index and no other.
    `memory.index()` is derived from the scoped store, so a detector cannot
    reach a vendor mapping that belongs to somebody else's books.

    `as_match_result()` RAISES on MEMORY_NOT_READY rather than converting it to
    NO_MATCH. Those two look alike and mean opposite things: no-match means ask
    the person, not-ready means we have not read their books and have no
    standing to ask anything yet.

    D-06, 2026-08-10. MEMORY IS CHECKED AGAINST THE LIVE LEDGER, HERE.

    `history` is what the connector returned moments ago. `memory` was built
    once, by `bootstrap`, when the company was opened, and nothing re-reads it.
    Until now the two never met: `build_draft` proposed the debit leg out of the
    snapshot, `evaluate` judged the proposal against the snapshot's own index,
    and the live rows sitting in this function's own arguments were used for the
    funding leg and the detectors and never for the one question that matters —
    does the ledger still say this?

    The reproduced case, in the owner's words: memory learned Purchases 40
    times, the live ledger later contains Repairs 60 times, and the next entry
    still posts Purchases without a flag or a question. `vendor_switch` cannot
    catch it, and it is worth saying why rather than assuming: that detector
    compares the proposed leg against the SAME snapshot index the leg was
    proposed from, so it agrees with itself by construction and stays silent.

    So the live evidence is compared against the remembered answer, at the exact
    point the proposal is judged, which is the smallest place this could be
    fixed. A disagreement is UNCLEAR and not NOT_VALID, because an answer fixes
    it — and the answer is new information, not authorisation: `answer` clears
    the decision and the entry re-enters this function at step 1.

    THE CAGE IS ASKED LAST, 2026-08-13, AND IT MAY ONLY NARROW.

    This is the seam rather than `run` because `run` is not where the decision
    is made. `web/app.py` calls THIS function twice and `run` never — so a gate
    sited in `run` would guard the demo path and leave the shipped one open,
    which is the same defect as a guard that is unit-tested and not installed.

    `period_open` and `pdf_repaired` HAVE NO DEFAULTS. The other two facts the
    cage needs are derived here, from the draft, because they are already in
    hand and asking the caller would invite an answer that disagrees with the
    evidence: `party_known` is whether this company's chart of accounts names
    the party, and `carries_gst` is the same question `Voucher.needs_tax_lines`
    already answers on the write side. `reading_tiers` is NOT passed - `gate`
    derives it from the record, which is where the reading tier is stamped.

    `period_open=None` blocks, and blocking is the correct answer to "nobody
    looked". It is NOT the only value this parameter can carry, and the comment
    that used to stand here saying so - "nothing in this repository reads
    whether a company's books are open for a date" - was false when it was
    written. `accountant/period.py::check_period` reads it, over
    `accountant/tallyio/period.py`, and hands back three states rather than two:
    `True` open, `False` closed, `None` we could not check. All three arrive
    here, from `web/app.py::Runtime.period_open` on the shipped path and from
    `pipeline._period_open` on this module's own `run`.

    A default of `True` is the one value that would be silently supplied at
    every call site that forgot, and would grant exactly the permission the
    field exists to withhold. So there is no default at all.

    MEASURED CONSEQUENCE, 2026-08-13, AND IT IS NOT SMALL. Across this
    repository's whole suite, 993 drafts reach this function and the cage
    BLOCKS every one of them - 280 that the decision order called VALID, 624
    UNCLEAR and 89 NOT_VALID. Only the 280 change outcome, all of them from
    VALID to NOT_VALID, because the cage may not widen the other two. So the
    live path posts NOTHING. Four independent hard blocks are responsible and
    supplying `period_open=True` clears only one of them: `net_paise` has no
    source, so `net_plus_tax_equals_gross` is INDETERMINATE on every bill;
    `party_known` is False for every supplier that is not itself a ledger; and
    270 of the 280 read below `ASK_FLOOR`. That is a real measurement of how
    much this product actually knows, not a tuning problem.
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

    # THE RULES ENGINE IS ACTUALLY RUN, 2026-08-10.
    #
    # `accountant/rules/` and `accountant/tax/` were built, tested and merged,
    # and then imported by nothing outside their own package: `decide_tax` had
    # no caller anywhere on the live path. A corpus that is never evaluated is a
    # corpus nobody can be wrong about, which is the same reason a check that
    # cannot fail is not a check.
    #
    # IT DOES NOT AUTHORISE A POSTING, AND CANNOT. Owner decision Q3 = D stands
    # untouched: `checks.tax_lines_can_be_posted` still fails a bill carrying
    # `gst_paise`, `tallyio.real.check_writable` still refuses it at the wire,
    # and `TaxDecision.posting_enabled` cannot be constructed True. What this
    # buys is that the hand-over stops saying only "enter this one yourself" and
    # starts saying WHY the tax could not be computed - which is the question
    # the person is left holding.
    #
    # Only for bills that carry tax. Running it on every entry would compute an
    # answer to a question nobody asked and put an UNCLEAR tax verdict beside a
    # plain cash purchase.
    draft.tax = tax_for(draft, accounts) if draft.voucher.needs_tax_lines else None
    # Detect everything, THEN decide what fits on the screen. Running with the
    # cap would give the same `flags` and the same count, and would throw the
    # overflow away - which is the one thing the owner's decision forbids. The
    # slice below is the same arithmetic `detectors.run` applies, kept honest by
    # `check_cap` refusing the negative cap that would silently invert it.
    detectors.check_cap(flag_cap)
    found, _ = detectors.run(
        draft.voucher, history, index, detectors=detector_set, cap=None
    )
    if flag_cap is None or len(found) <= flag_cap:
        draft.flags, draft.suppressed_flags, draft.dropped_flags = found, [], 0
    else:
        draft.flags = found[:flag_cap]
        draft.suppressed_flags = found[flag_cap:]
        draft.dropped_flags = len(found) - flag_cap
    match = memory.lookup(draft.voucher.party).as_match_result()
    draft.problems = problems.find(
        draft.voucher, draft.checks, match, draft.flags, accounts, history, index
    )

    # D-06. Recorded on every evaluation, asked about only while memory is
    # still the one deciding. The two are separated on purpose: see
    # `memory_still_owns_the_leg` and `Draft.memory_conflict`.
    draft.memory_conflict = disagrees_with_live_history(
        memory, draft.voucher.party, history
    )
    if draft.memory_conflict is not None and memory_still_owns_the_leg(
        draft.voucher, draft.memory_conflict
    ):
        draft.problems = _with_conflict_question(
            draft.problems, draft.memory_conflict, draft.voucher.party, accounts
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

    # The cage, immediately after the decision order and before the draft is
    # handed back. Asked on EVERY draft and not only on the VALID ones: the
    # property that it cannot widen belongs in `narrowed_by_the_cage`, in one
    # place, and short-circuiting here would put a second copy of it at the
    # call site where the two could drift. `decide` is pure and does no IO, and
    # it was run over all 993 drafts in this suite without raising.
    draft.decision = narrowed_by_the_cage(
        draft.decision,
        cage_gate.gate(
            draft,
            moment=Moment.BEFORE_THE_WRITE,
            # Derived here, not asked for. Both are already on the draft, and a
            # caller asked for them could answer differently from the evidence.
            # THE PARTY WAS BEING LOOKED UP IN THE WRONG PLACE. `accounts` is
            # the CHART OF ACCOUNTS - Purchases, Cash, Rent. A supplier is not
            # one of those, so this asked "is Sharma Traders a ledger head" and
            # answered no.
            #
            # MEASURED over the 173 failing tests: `party_known` was False on
            # 299 of 299 gate calls, while 288 of those records carried a party
            # and the fixtures give that party FORTY past vouchers. The cage
            # said "I have never seen Sharma Traders in your books" about a
            # vendor with forty entries in the books. After this: 169 of 299.
            #
            # `history` IS THE BOOKS. The owner's hard rule 7 is untouched and
            # is why the `or` is this way round: a party in NEITHER the chart
            # nor the history is still unknown, and still blocks.
            # THE KIND OF DOCUMENT, DERIVED FROM THE TIER THAT READ IT, and
            # not asked of the caller. `typed_text` is a person typing a
            # sentence - there is no document, so there are no line items and
            # no invoice date, and the invoice conservation laws describe
            # nothing on it. Every other tier read an actual file.
            #
            # MEASURED: this is what refused `paid Sharma Traders 4200 for
            # cement` with three reasons, none of which were true of it.
            # OWNER RULING, 2026-08-16: the kind turns on WHERE THE CHARACTERS
            # CAME FROM. `typed_text` means a person typed the sentence, so
            # nothing was read and nothing could be misread - that is
            # `TYPED_EXPENSE_NOTE`, and it may reach VALID on the ordinary
            # checks. Anything else was lifted off a document by a reader and
            # is judged as an invoice here; a reader that some day reports an
            # expense note off a PAGE passes `NON_INVOICE_EXPENSE_NOTE`, which
            # `decision._needs_a_person` caps at a question.
            document_type=(
                DocumentType.TYPED_EXPENSE_NOTE
                if draft.record.per_field_source.get("total_paise", "").startswith(
                    "typed_text"
                )
                else DocumentType.INVOICE
            ),
            party_known=(
                draft.voucher.party in accounts
                or any(one.party == draft.voucher.party for one in history)
            ),
            carries_gst=draft.voucher.needs_tax_lines,
            questions_asked=len(draft.answers),
            # The caller's, because neither is on the draft. `period_open` has
            # no source anywhere; `pdf_repaired=None` means "not a PDF, nothing
            # repaired", and `gate._repaired` lets the RECORD overrule it when
            # the reader says it mended the bytes.
            period_open=period_open,
            pdf_repaired=pdf_repaired,
            # THE NET, HANDED OVER. Added 2026-08-15, and its absence was not a
            # missing feature - it was evidence that had been read, carried the
            # whole way here, and then dropped one line short of the check that
            # needed it.
            #
            # MEASURED before this line existed: of 956 blocks, 955 cited "there
            # is something on this bill I could not check at all". That sentence
            # is `net_plus_tax_equals_gross` returning INDETERMINATE, and it
            # returned INDETERMINATE because `net_paise` took its default of
            # `None` here while `draft.record.net_paise` held the figure the
            # reader had already found. Measured on the text-layer bill in
            # `tests/test_textlayer.py`: the record carried 104624 and the law
            # said "the net amount was not read".
            #
            # `record` AND NOT A DERIVATION. `gate.gate`'s docstring is explicit
            # that the net is a parameter and is never worked out as total minus
            # tax: a number derived from the law's own inputs is checked against
            # itself and passes for ever. So this is the READ figure or nothing,
            # and nothing still blocks - the correct direction for a bill whose
            # pre-tax figure genuinely was not printed on it.
            net_paise=draft.record.net_paise,
        ),
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

    # DEFECT I1, FIXED 2026-08-10. THE IDENTITY IS CLAIMED BEFORE THE WRITE.
    #
    # Reversing a voucher DELETES it, so after a reversal Tally's answer to "is
    # there a voucher carrying operation X" is no, and the id looked free. It
    # was not free. `docs/ARCHITECTURE.md` section 7 and
    # `accountant/tallyio/client.py` both say the operation id IS the identity
    # of a write: reads, duplicate detection and reversal match on it and on
    # nothing else. An identity that can be reused after a delete is not an
    # identity, it is a slot, and two `posted` rows naming one operation id and
    # two different Tally ids cannot afterwards be reconciled by the one thing
    # they have in common.
    #
    # It was reachable from a browser. Post an entry, press undo, re-submit the
    # form that posted it: same id, different voucher, two `posted` rows.
    #
    # BEFORE the write-ahead row, deliberately. A refused replay is not a write
    # that started and ended unknown — nothing went out — and recording it as
    # `write_attempted` would put a row meaning "a voucher may exist and must be
    # checked by hand" against an attempt we know wrote nothing.
    company_key = memory.identity.key if memory is not None else draft.company
    if isinstance(log, OperationRegister) and not log.claim_operation(
        company_key, draft.operation_id, datetime.datetime.now(datetime.UTC).isoformat()
    ):
        raise DuplicateOperation(
            f"refusing to post {draft.operation_id!r}: that operation id has "
            f"already been written in {draft.company!r}. Reversing a voucher "
            f"does not free its identity - two vouchers carrying one operation "
            f"id could never be told apart afterwards. Nothing was written. "
            f"Start the entry again and it will get an identity of its own."
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
    except DuplicateOperation as exc:
        # DEFECT I2, FIXED 2026-08-10, and reachable now only by one route.
        #
        # `WRITE_OUTCOME_UNKNOWN` means "a voucher may exist and must be checked
        # by hand". A duplicate refusal is the one write failure where that is
        # KNOWABLY FALSE: both backends raise it BEFORE any import goes out, so
        # at this instant there is positive evidence that this attempt wrote
        # nothing. Recording the opposite sends somebody to look in Tally for a
        # voucher we already know we did not write, and it dilutes the row that
        # means a person really is needed.
        #
        # `accountant/tallyio/real.py` argues the other direction at length: an
        # UNKNOWN must never be flattened into a failure, because a retry after
        # a write that DID land makes two entries. That argument is about
        # uncertainty. This arm is about the case where there is none.
        #
        # The claim above catches the ordinary replay before the socket opens,
        # so what reaches here is the disagreement case: our register says the
        # id is free and TALLY says it is not - a database restored from a
        # backup older than the books, or a write that predates this table. The
        # voucher in Tally is the older one, and this row says so.
        _record_write(
            log,
            draft,
            memory,
            client,
            run_id,
            WRITE_REFUSED_DUPLICATE,
            f"Tally already holds operation {draft.operation_id!r} and refused "
            f"this write before sending anything. Nothing was written now; the "
            f"voucher that carries this id was written earlier. {exc}",
        )
        raise
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


class ReversalMismatch(RuntimeError):
    """Tally's answer and Tally's own books disagree about a reversal.

    A `RuntimeError` subclass rather than a new exception hierarchy, so every
    existing `pytest.raises(RuntimeError)` around this path keeps working and
    nothing that catches broadly changes behaviour.

    It exists because `accountant/reversal.py` must tell two failures apart and
    the message text is not a safe discriminator. This one is WRONG_MOVEMENT —
    the books moved by something other than what undoing the voucher should
    have moved them by, in either direction. Any OTHER exception out of the
    same call is UNKNOWN_OUTCOME: a dropped connection or a malformed response
    says nothing about whether the books moved, and the two demand opposite
    responses. WRONG_MOVEMENT stops the batch dead; UNKNOWN_OUTCOME stops it
    pending a read-only reconciliation.
    """


@dataclass(frozen=True)
class Reversal:
    """What a reversal actually did, measured against Tally's own trial balance."""

    operation_id: str
    reversed_: bool
    #: Ledger -> paise, AFTER minus BEFORE, for every ledger that moved.
    moved: dict[str, int]
    detail: str


def reverse_operation(
    client: TallyClient,
    company: str,
    operation_id: str,
    *,
    log: object | None = None,
) -> Reversal:
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

    `log` MARKS the operation id reversed; it never releases it. The id stays
    spent, because the reason a reversed id must not be written again — two
    vouchers sharing one identity — is as true after the reversal is recorded as
    it was before. What the mark buys is a trail that can say "used, then
    undone" rather than only "used".
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
            raise ReversalMismatch(
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
    if isinstance(log, OperationRegister):
        log.mark_operation_reversed(
            company, operation_id, datetime.datetime.now(datetime.UTC).isoformat()
        )

    if moved != expected:
        raise ReversalMismatch(
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


def _period_open(
    company: str,
    on: datetime.date,
    reader: PeriodReader | None,
) -> bool | None:
    """Are this company's books open on `on`? Three answers, two of which block.

    THE THIRD STATE IS THE WHOLE POINT, and collapsing it was a measured defect
    rather than a hypothetical one. `PeriodCheck.open_for_posting` is a bare
    `bool`, so an unreachable Tally comes back `False`; handed to the cage,
    `False` is read by `decision._period_closed` as *"The books for 12 March
    2026 are closed, so nothing can be added to them"* - a confident, specific
    statement about the customer's Tally that OUR dropped connection invented.
    It sends somebody into their accounting software to fix a setting that was
    never wrong. So this returns `PeriodCheck.for_cage`, which keeps them apart:

        OPEN        -> True    may post
        CLOSED      -> False   blocks, and says the books are closed
        UNVERIFIED  -> None    blocks, and says WE could not check

    NO READER IS `None`, AND THAT IS NOT A FALLBACK. `TallyClient` has no period
    method of any kind - `accountant/tallyio/client.py` defines none - so a
    caller holding only a client genuinely has not looked, and "nobody looked"
    must never be reportable as "the books are open". `None` blocks, which is
    the correct answer to a question nobody asked.

    NEVER RAISES. `check_period` turns an unreachable gateway, a timeout, an
    unparseable body and a company that is not open into UNVERIFIED, and logs
    one `event=period_check` line whichever way it went. A traceback in front of
    somebody's bill is the failure this whole layer is written against.

    THE IMPORT IS LAZY AND HAS TO BE. `accountant.period` imports
    `accountant.observability`, which imports `accountant.pipeline` - so the
    obvious module-scope import raises `ImportError` before a single test runs.
    The TYPE_CHECKING block at the top of this file carries the type.
    """
    if reader is None:
        return None
    from accountant.period import check_period

    return check_period(company=company, on=on, reader=reader).for_cage


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
    period_reader: PeriodReader | None = None,
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

    `period_reader` IS OPTIONAL AND DEFAULTS TO NOBODY LOOKING. Supplied, it is
    one bounded probe of the company's own Tally - five second timeout, one
    attempt - and its three-way answer reaches `cage.gate` as `True`, `False` or
    `None`. Omitted, `period_open` is `None`, which blocks, and is the same
    value this call site passed unconditionally until 2026-08-15. The default is
    NOT `PeriodReader()`: that would build an `HttpTransport` aimed at port 9000
    of whatever machine is running, so a suite would go green on one laptop and
    red on another, and a doubled client would be answered by a real socket.

    `accountant/web/app.py` is the shipped caller and does not come through
    here - it calls `evaluate` directly, with `Runtime.period_open(on=...)`.
    """
    # A5. BEFORE the two reads, not after them. Both of these can fail on a
    # flaky connector, and when they do on a company we have never successfully
    # read, the exception a caller sees should say "we have not read your
    # books", not "connection refused". Same outcome - nothing is written -
    # different diagnosis, and the two lead to completely different actions.
    memory.require_usable("propose an account")

    accounts = client.read_accounts(company)
    history = client.read_vouchers(company)

    draft = build_draft(company, data, mime, extractor, memory, today=today)
    draft = evaluate(
        draft,
        accounts,
        history,
        memory,
        detector_set=detector_set,
        flag_cap=flag_cap,
        # THE PERIOD IS READ, NOT INVENTED, AND NOT ASSUMED EITHER.
        #
        # This was `period_open=None` unconditionally, under a comment saying
        # "nothing in this repository reads whether a company's books are open
        # for a date". That comment was FALSE when it was written:
        # `accountant/period.py::check_period` reads exactly that, over
        # `accountant/tallyio/period.py::PeriodReader`, and both shipped web
        # call sites already passed a real value through
        # `web/app.py::Runtime.period_open`. Only this call site declined to
        # fetch a fact the codebase could read.
        #
        # `draft.voucher.date` AND NEVER TODAY. Whether books are open is a
        # question about a date, and a bill entered today for a purchase made in
        # March is the exact case this check exists for. `build_draft` has
        # already run, so the bill's own date is in hand.
        #
        # With no reader this is still `None` and still blocks - unchanged
        # behaviour for every caller that has not looked. See `_period_open` for
        # why "nobody looked" is `None` and not `False`.
        #
        # `pdf_repaired=None` says this caller repaired nothing, which is true:
        # the reader is what mends bytes, and `cage/gate._repaired` reads that
        # off the record and overrules this.
        period_open=_period_open(company, draft.voucher.date, period_reader),
        pdf_repaired=None,
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
#: A write Tally refused as a duplicate. NOT an unknown outcome: the refusal
#: happens before any import goes out, so this row states that nothing was
#: written rather than that nobody can say.
WRITE_REFUSED_DUPLICATE = "write_refused_duplicate"


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
    tenant_id: str = NOT_RECORDED,
    user_id: str = NOT_RECORDED,
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

    `tenant_id` and `user_id` default to `NOT_RECORDED` rather than to a blank
    or to a made-up "system" identity. A caller with no session behind it -
    `run`, a script, a test - genuinely has neither, and the row says so. Only
    a caller that HELD a session passes them.
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
            tenant_id=tenant_id,
            user_id=user_id,
        )
    )
