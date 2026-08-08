"""Child 7 - the coverage table, and the backlog of what it does not cover.

This is the answer to the question child #7 exists to ask: **are the four
detectors the right four?** Every real error type in `findings.py` gets one
row here, and every row carries all six of:

    route        exactly one of the four `Route` members - detector, rule,
                 question, or not detectable from available evidence
    status       COVERED, PARTIAL or UNCOVERED, as of today
    detector     the live detector name, or `UNCOVERED`
    evidence     the published documents the classification was read from,
                 taken from the findings themselves rather than typed again
    why          why the status is what it is
    next_step    what would move it, in one sentence

A row missing any of them does not load. That is the whole mechanism: a type
nobody has looked at cannot be added, and a type somebody has stopped looking
at cannot stay.

**The mapping rule, stated once and applied without exception.**

> A type maps to a detector when an entry of that type changes the one field
> that detector reads, in the direction that detector tests. It maps to
> `UNCOVERED` when an entry of that type can leave every field those four
> detectors read looking exactly as it always has.

That rule is why the table is checkable rather than a matter of taste. What
each detector reads:

| detector       | the field it reads and the direction it tests            |
|----------------|----------------------------------------------------------|
| `vendor_switch`| `debit_account` against the account this party always uses|
| `first_use`    | `debit_account` against every account ever used           |
| `magnitude`    | `amount_paise` against that account's own historical high |
| `gst_anomaly`  | `gst_paise` on an account that has never carried GST      |

All four are history-contradiction detectors. They fire when an entry
contradicts this company's own posted history, and they are silent when it
does not. Several types below are standing practices - the same wrong head,
entry after entry, on the entity's own sanction - and a standing practice
contradicts no history at all.

**Detector names are never written down here.** They are read from
`accountant.detect.detectors.ALL_DETECTORS`, through `detector_name`, which
refuses a function that has been dropped from that tuple. A detector renamed
or removed breaks this table loudly rather than leaving a stale string behind.

**Nothing is silently omitted.** Every `UNCOVERED` type carries a `Proposal`
in `PROPOSALS`, naming a detector that would fire on it and the input that
detector would need, and every `UNCOVERED` row's `next_step` names that same
proposal. A proposal is a backlog item, not a decision to build, and ten
proposals are not ten detectors anybody has agreed to write.

`tests/test_taxonomy.py` holds a second copy of the twelve type names, typed
by hand, and fails if the table and that list ever disagree. A type cannot
disappear from a report by disappearing from the table.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from accountant.detect import detectors
from accountant.taxonomy.findings import ERROR_TYPE_NAMES, findings_for

# The mapping target for a type no detector reads. Not a detector name, and a
# test asserts it can never collide with one.
UNCOVERED = "UNCOVERED"


class Route(StrEnum):
    """The FOUR ways an error type could ever be reached. Exactly one each.

    This is the classification the coverage table exists to force. A type with
    no route is a type nobody has thought about; a type with two is a type
    nobody has decided about.

        DETECTOR      a deterministic detector over the voucher stream. It
                      needs no input the connector does not already return.
        RULE          an accounting rule or an invariant check. It needs a
                      table of what is permitted, or two quantities that must
                      be equal. `accountant/rules/` is where that lives.
        QUESTION      a human review path. Only a person, or a document a
                      person supplies, holds the fact that decides it.
        NOT_DETECTABLE  the evidence needed is in nothing this system can read,
                      including from a person who is willing to answer.
    """

    DETECTOR = "deterministic detector"
    RULE = "accounting rule / invariant check"
    QUESTION = "question / human review path"
    NOT_DETECTABLE = "not detectable from available evidence"


class Status(StrEnum):
    """What is true TODAY about a type, as distinct from what could be.

        COVERED    something live fires on this type as the record shows it
        PARTIAL    something live fires on SOME instances, and provably not on
                   the shape the published finding actually has
        UNCOVERED  nothing live fires on it at all

    PARTIAL exists because the alternative is a lie in either direction.
    `vendor_switch` does fire on a misclassification that contradicts a party's
    own history, and it cannot fire on the same misclassification applied as a
    standing practice - and the published findings are mostly the second kind.
    Calling that COVERED overstates it; calling it UNCOVERED throws away a
    detector that works.
    """

    COVERED = "COVERED"
    PARTIAL = "PARTIAL"
    UNCOVERED = "UNCOVERED"


DETECTOR_NAMES: tuple[str, ...] = tuple(
    str(getattr(d, "__name__", d)) for d in detectors.ALL_DETECTORS
)

TARGETS: tuple[str, ...] = (*DETECTOR_NAMES, UNCOVERED)


def detector_name(detector: detectors.Detector) -> str:
    """The detector's own name, and proof it is still in `ALL_DETECTORS`."""
    if detector not in detectors.ALL_DETECTORS:
        raise ValueError(
            f"{getattr(detector, '__name__', detector)!r} is not in "
            f"ALL_DETECTORS, so the coverage table cannot map anything to it"
        )
    return str(getattr(detector, "__name__", detector))


@dataclass(frozen=True)
class CoverageRow:
    """One real error type, classified, with its status and its next step.

    Every field is required, and every one of them is refused if it is blank.
    A row that omits its evidence, its reason or its next step would be a row
    that quietly says "we have not looked", which is the failure this table
    was built to make impossible.

        error_type     the published type, from `findings.py`
        route          exactly one of the four `Route` members
        status         COVERED, PARTIAL or UNCOVERED, as of today
        detector       the live detector name, or `UNCOVERED`
        evidence       the published paragraphs this classification was read
                       from, as source keys
        why            why the status is what it is
        next_step      what would move it, in one sentence
    """

    error_type: str
    route: Route
    status: Status
    detector: str
    evidence: tuple[str, ...]
    why: str
    next_step: str

    def __post_init__(self) -> None:
        if self.error_type not in ERROR_TYPE_NAMES:
            raise ValueError(f"{self.error_type!r} is not a real error type")
        if self.detector not in TARGETS:
            raise ValueError(
                f"{self.detector!r} is neither a detector in ALL_DETECTORS nor "
                f"{UNCOVERED}"
            )
        if (self.detector == UNCOVERED) is not (self.status is Status.UNCOVERED):
            raise ValueError(
                f"{self.error_type!r} says {self.status.value} and points at "
                f"{self.detector!r}; a type with no detector is {UNCOVERED} and "
                f"a type with one is not"
            )
        if not self.evidence:
            raise ValueError(
                f"{self.error_type!r} cites no published paragraph, so its "
                f"classification rests on nothing"
            )
        if not self.why.strip():
            raise ValueError(f"{self.error_type!r} gives no reason for its status")
        if not self.next_step.strip():
            raise ValueError(f"{self.error_type!r} names no next step")

    @property
    def covered(self) -> bool:
        """A detector is aimed at this type. Not a claim that it suffices."""
        return self.detector != UNCOVERED


def _cited(error_type: str) -> tuple[str, ...]:
    """The source keys behind one type, in extraction order, without repeats.

    Read from the findings rather than typed again here, so a row cannot cite
    a document no finding of that type came from.
    """
    keys: list[str] = []
    for finding in findings_for(error_type):
        if finding.source.key not in keys:
            keys.append(finding.source.key)
    return tuple(keys)


COVERAGE: tuple[CoverageRow, ...] = (
    CoverageRow(
        error_type="revenue_expenditure_as_capital",
        route=Route.DETECTOR,
        status=Status.PARTIAL,
        detector=detector_name(detectors.vendor_switch),
        evidence=_cited("revenue_expenditure_as_capital"),
        why=(
            "PARTIAL, and the word is load-bearing. vendor_switch fires on an "
            "entry that lands on a different account from the one this party's "
            "expenditure has gone to before, and several of these findings are "
            "exactly that. It cannot fire on the same misclassification applied "
            "entry after entry, because a standing practice IS the history."
        ),
        next_step=(
            "the rule route: a table of which object heads belong to the "
            "revenue section, so the head can be judged against the entry "
            "rather than against this party's habits."
        ),
    ),
    CoverageRow(
        error_type="capital_expenditure_as_revenue",
        route=Route.DETECTOR,
        status=Status.PARTIAL,
        detector=detector_name(detectors.vendor_switch),
        evidence=_cited("capital_expenditure_as_revenue"),
        why=(
            "The same shape in the opposite direction, and PARTIAL for the same "
            "reason: caught when the account contradicts this party's history, "
            "missed when the wrong account is the party's history."
        ),
        next_step=(
            "the same rule table, read the other way: which object heads belong "
            "to the capital section."
        ),
    ),
    CoverageRow(
        error_type="object_head_incompatible_with_major_head",
        route=Route.RULE,
        status=Status.UNCOVERED,
        detector=UNCOVERED,
        evidence=_cited("object_head_incompatible_with_major_head"),
        why=(
            "The Department of Atomic Energy used the same invalid pair across "
            "two grants and defended it. A pair used entry after entry "
            "contradicts no history; only a rule table calls it invalid. Each "
            "head on its own is in ordinary use, so no detector reading one "
            "field at a time can see it."
        ),
        next_step="head_pair_invalid, once accountant/rules/ exists.",
    ),
    CoverageRow(
        error_type="wrong_expense_head_within_same_section",
        route=Route.QUESTION,
        status=Status.UNCOVERED,
        detector=UNCOVERED,
        evidence=_cited("wrong_expense_head_within_same_section"),
        why=(
            "The Ministry of Power stated it had adopted the head given in its "
            "own sanction, and the Ministry of Electronics and Information "
            "Technology that the correct head arrived after the booking. The "
            "fact that settles both is the sanction the entry was made under, "
            "and only a person or the document they hold can supply it."
        ),
        next_step=(
            "sanction_head_mismatch, once an entry can carry the sanction it "
            "cites. Voucher carries no such field today."
        ),
    ),
    CoverageRow(
        error_type="receipt_classified_as_wrong_type",
        route=Route.DETECTOR,
        status=Status.UNCOVERED,
        detector=UNCOVERED,
        evidence=_cited("receipt_classified_as_wrong_type"),
        why=(
            "Every live detector reads debit_account, amount_paise or gst_paise "
            "on a purchase entry. None reads the receipt side, so nothing looks "
            "at the field the error is in."
        ),
        next_step=(
            "receipt_side_switch, which needs no new input at all: it reads "
            "credit_account, a field the connector already returns."
        ),
    ),
    CoverageRow(
        error_type="parked_in_suspense_head",
        route=Route.DETECTOR,
        status=Status.UNCOVERED,
        detector=UNCOVERED,
        evidence=_cited("parked_in_suspense_head"),
        why=(
            "The original entry is correct and would pass every detector. The "
            "error is a later transfer entry against it, and no detector reads "
            "transfers."
        ),
        next_step=(
            "transfer_out_of_final_head, which needs transfer entries from the "
            "connector and the list of suspense accounts."
        ),
    ),
    CoverageRow(
        error_type="expenditure_netted_against_receipt",
        route=Route.NOT_DETECTABLE,
        status=Status.UNCOVERED,
        detector=UNCOVERED,
        evidence=_cited("expenditure_netted_against_receipt"),
        why=(
            "There is no expenditure entry to inspect. The interest was shown "
            "as a reduction of a receipt, so the ledger records the net figure "
            "and nothing else. No detector, no rule over the entries, and no "
            "question to the person who made them can recover a number that "
            "was never written down: what is missing is the gross figure, and "
            "the counterparty holds it."
        ),
        next_step=(
            "netting_against_receipt is proposed and would read the credit "
            "side, but it can only find the cases where an entry WAS raised "
            "against a receipt account. The netted-at-source case stays "
            "invisible until the gross figure is obtained from outside."
        ),
    ),
    CoverageRow(
        error_type="expense_under_wrong_statement_head",
        route=Route.RULE,
        status=Status.UNCOVERED,
        detector=UNCOVERED,
        evidence=_cited("expense_under_wrong_statement_head"),
        why=(
            "The account is the one the entity has used for this expense all "
            "along. The accounting standard says it is wrong; the history says "
            "it is ordinary. Only a table mapping a described expense to the "
            "head the standard assigns can separate the two."
        ),
        next_step=(
            "standard_head_mismatch, once accountant/rules/ carries the "
            "Schedule III heads and the plain-English phrasebook."
        ),
    ),
    CoverageRow(
        error_type="balance_under_wrong_balance_sheet_head",
        route=Route.RULE,
        status=Status.UNCOVERED,
        detector=UNCOVERED,
        evidence=_cited("balance_under_wrong_balance_sheet_head"),
        why=(
            "The statement-head case applied to a balance. Grouping is settled "
            "once and repeated, so nothing contradicts, and the judgement lives "
            "in the standard rather than in the ledger."
        ),
        next_step=(
            "balance_head_mismatch, from the same Schedule III table as the "
            "statement-head rule."
        ),
    ),
    CoverageRow(
        error_type="related_party_not_identified",
        route=Route.QUESTION,
        status=Status.UNCOVERED,
        detector=UNCOVERED,
        evidence=_cited("related_party_not_identified"),
        why=(
            "Party, account, amount and tax can every one of them look exactly "
            "as they always have. What is wrong is who the party is, and no "
            "history can infer that. The company knows, and can be asked."
        ),
        next_step=(
            "related_party, once the company supplies a related-party "
            "register. It is a question path because the register is a fact "
            "the person holds, not one the ledger implies."
        ),
    ),
    CoverageRow(
        error_type="expenditure_exceeds_sanctioned_provision",
        route=Route.RULE,
        status=Status.UNCOVERED,
        detector=UNCOVERED,
        evidence=_cited("expenditure_exceeds_sanctioned_provision"),
        why=(
            "magnitude is the nearest live detector and it does not fit: it "
            "bounds an amount by that account's own historical high, while the "
            "limit breached here is a sanctioned provision that can sit either "
            "side of it. The check is an invariant - a running total against a "
            "stated limit - not a surprise."
        ),
        next_step=(
            "sanction_breach, once the sanctioned amount per head is available. "
            "It sits outside the ledger."
        ),
    ),
    CoverageRow(
        error_type="tax_credit_claimed_where_not_admissible",
        route=Route.RULE,
        status=Status.UNCOVERED,
        detector=UNCOVERED,
        evidence=_cited("tax_credit_claimed_where_not_admissible"),
        why=(
            "gst_anomaly is the nearest live detector and it does not fit: it "
            "fires on an account that has never carried tax, while these "
            "accounts carry admissible tax the rest of the time. Only the "
            "blocked-credit list separates the claim from the ordinary ones."
        ),
        next_step=(
            "credit_not_admissible, once accountant/rules/ carries the "
            "blocked-credit list."
        ),
    ),
)


def rows_by_type() -> dict[str, CoverageRow]:
    """The coverage table keyed by error type."""
    return {row.error_type: row for row in COVERAGE}


def uncovered_types() -> tuple[str, ...]:
    """Every error type no detector is aimed at, in table order."""
    return tuple(row.error_type for row in COVERAGE if not row.covered)


def uncovered_count() -> int:
    """The `UNCOVERED` count, as a single number."""
    return len(uncovered_types())


def covered_types() -> tuple[str, ...]:
    """Every error type a detector is aimed at, in table order."""
    return tuple(row.error_type for row in COVERAGE if row.covered)


def partial_types() -> tuple[str, ...]:
    """Every type something live fires on for only some of its instances."""
    return tuple(row.error_type for row in COVERAGE if row.status is Status.PARTIAL)


def types_by_route(route: Route) -> tuple[str, ...]:
    """Every type classified into one route, in table order."""
    return tuple(row.error_type for row in COVERAGE if row.route is route)


def route_counts() -> tuple[tuple[Route, int], ...]:
    """How many types each of the four routes holds, in Route order.

    Reported as a table rather than a total, because a route holding nothing
    is the interesting case: it means either the route is unnecessary or the
    table has stopped looking.
    """
    return tuple((route, len(types_by_route(route))) for route in Route)


def status_counts() -> tuple[tuple[Status, int], ...]:
    """How many types carry each status, in Status order."""
    return tuple(
        (status, len([r for r in COVERAGE if r.status is status])) for status in Status
    )


def detectors_targeting_no_error_type() -> tuple[str, ...]:
    """Detectors in `ALL_DETECTORS` that no row in this table maps to.

    The uncomfortable half of the result, and the reason child #7 was worth
    building: a detector nothing maps to was written against an error nobody
    has published.
    """
    aimed = {row.detector for row in COVERAGE if row.covered}
    return tuple(name for name in DETECTOR_NAMES if name not in aimed)


@dataclass(frozen=True)
class Proposal:
    """A detector that would fire on an `UNCOVERED` type.

    A backlog item, not a decision to build. `needs` states the input the
    detector would require, because a proposal that hides its input cost is
    not a proposal.
    """

    error_type: str
    detector: str
    fires_when: str
    needs: str

    def __post_init__(self) -> None:
        if self.error_type not in uncovered_types():
            raise ValueError(
                f"{self.error_type!r} is already covered; a proposal exists "
                f"only for an {UNCOVERED} type"
            )
        if self.detector in DETECTOR_NAMES:
            raise ValueError(
                f"{self.detector!r} is already a detector in ALL_DETECTORS"
            )


PROPOSALS: tuple[Proposal, ...] = (
    Proposal(
        error_type="object_head_incompatible_with_major_head",
        detector="head_pair_invalid",
        fires_when=(
            "the account posted and the section it sits in are a pair the "
            "chart's own rules do not permit"
        ),
        needs="accountant/rules/ - which account categories may pair (Phase 8)",
    ),
    Proposal(
        error_type="wrong_expense_head_within_same_section",
        detector="sanction_head_mismatch",
        fires_when=(
            "the account posted differs from the account named in the sanction "
            "or order the entry cites"
        ),
        needs="a sanction or order reference on the entry; Voucher carries none",
    ),
    Proposal(
        error_type="receipt_classified_as_wrong_type",
        detector="receipt_side_switch",
        fires_when=(
            "the credit account for a receipt from this party is not the one "
            "this party's receipts have gone to before"
        ),
        needs="nothing new - it reads credit_account, which no detector reads",
    ),
    Proposal(
        error_type="parked_in_suspense_head",
        detector="transfer_out_of_final_head",
        fires_when=(
            "a later entry moves an amount out of a final head into a suspense "
            "or holding account"
        ),
        needs="transfer entries from the connector, and the suspense accounts",
    ),
    Proposal(
        error_type="expenditure_netted_against_receipt",
        detector="netting_against_receipt",
        fires_when=(
            "a receipt account is debited where an expense account should have been"
        ),
        needs="nothing new - it reads the credit side of the entry",
    ),
    Proposal(
        error_type="expense_under_wrong_statement_head",
        detector="standard_head_mismatch",
        fires_when=(
            "the account chosen for a described expense is not the account the "
            "reporting standard assigns to that description"
        ),
        needs="accountant/rules/ - Schedule III heads and the phrasebook",
    ),
    Proposal(
        error_type="balance_under_wrong_balance_sheet_head",
        detector="balance_head_mismatch",
        fires_when=(
            "a balance is grouped under a head the reporting standard assigns elsewhere"
        ),
        needs="accountant/rules/ - Schedule III heads and the phrasebook",
    ),
    Proposal(
        error_type="related_party_not_identified",
        detector="related_party",
        fires_when="the party matches the company's own related-party register",
        needs=(
            "a related-party register the company supplies; no history can infer one"
        ),
    ),
    Proposal(
        error_type="expenditure_exceeds_sanctioned_provision",
        detector="sanction_breach",
        fires_when=(
            "the running total posted against a sanction passes the sanctioned amount"
        ),
        needs="the sanctioned amount per head, which sits outside Tally history",
    ),
    Proposal(
        error_type="tax_credit_claimed_where_not_admissible",
        detector="credit_not_admissible",
        fires_when="tax is claimed on a supply the rules block credit on",
        needs="accountant/rules/ - the blocked-credit list (Phase 8)",
    ),
)


def proposal_for(error_type: str) -> Proposal | None:
    """The backlog proposal for one type, or None where the type is covered."""
    for proposal in PROPOSALS:
        if proposal.error_type == error_type:
            return proposal
    return None
