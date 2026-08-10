"""One entry, one tax answer, with the rules that produced it named.

WHY `TaxOutcome` IS NOT `schema.Outcome`
----------------------------------------
It uses the same three words — VALID, UNCLEAR, NOT_VALID — because that is the
vocabulary every report in this project already speaks, and inventing a fourth
would make the numbers incomparable. It is a DIFFERENT TYPE because a tax
computation is not a posting decision, and the day somebody wires one into the
other by accident the type checker has to be the thing that notices. A
`TaxOutcome.VALID` means "the tax was computed and every part of it can be
cited". It does not mean, and must never come to mean, "post this".

WHAT VALID DOES NOT AUTHORISE — owner decision Q3 = D
-----------------------------------------------------
Nothing. `POSTING_ENABLED` is False, `TaxDecision.posting_enabled` cannot be
constructed True, and the two guards that actually stop a GST bill are somewhere
else entirely and unchanged:

    accountant/checks.py::tax_lines_can_be_posted   before the decision
    accountant/tallyio/real.py::check_writable      at the wire

A bill carrying `gst_paise` is UNCLEAR in the product and refused by the
connector, whatever this module computes. `tests/test_gst_rules_safety.py` pins
that: the engine can return VALID for a bill on the very same day the pipeline
refuses to post it, and both are correct.

THE ORDER OF THE CHECKS
-----------------------
    place of supply -> code -> rate -> arithmetic -> ledger

Place of supply first, because it is the evidence gate: without it there is no
question of WHICH tax, so a rate lookup would be answering the wrong question.
Ledger last, because a company with no `IGST` ledger still deserves to be told
the rate it could not post.
"""

from __future__ import annotations

import datetime
from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum

from accountant.rules import hsn_sac
from accountant.rules.gst_rates import (
    RateLookup,
    RateOutcome,
    RateRule,
    RuleCorpus,
    TaxType,
)
from accountant.rules.place_of_supply import (
    PlaceOfSupplyDecision,
    PlaceOfSupplyOutcome,
    SupplyEvidence,
    SupplyKind,
    determine,
)
from accountant.rules.provenance import Citation
from accountant.tax.calculator import (
    ComputationOutcome,
    TaxComputation,
    TaxLine,
    compute,
)
from accountant.tax.ledger_mapper import (
    LedgerMapping,
    LedgerMappingOutcome,
    map_tax_lines,
)

#: Owner decision Q3 = D. Read by the safety tests, and by nothing that writes.
POSTING_ENABLED = False

POSTING_DISABLED_REASON = (
    "automatic GST posting is not enabled (owner decision Q3 = D). The rate is "
    "computed and cited so a person can enter it in Tally themselves."
)


class TaxOutcome(StrEnum):
    VALID = "valid"
    UNCLEAR = "unclear"
    NOT_VALID = "not_valid"


@dataclass(frozen=True)
class TaxDecision:
    outcome: TaxOutcome
    reason: str
    supply_kind: SupplyKind | None = None
    lines: tuple[TaxLine, ...] = ()
    ledgers: tuple[str, ...] = ()
    citations: tuple[Citation, ...] = ()
    place_of_supply: PlaceOfSupplyDecision | None = None
    rate_lookups: tuple[RateLookup, ...] = ()
    computation: TaxComputation | None = None
    ledger_mapping: LedgerMapping | None = None
    posting_enabled: bool = False

    def __post_init__(self) -> None:
        if self.posting_enabled:
            raise ValueError(POSTING_DISABLED_REASON)
        if self.outcome is TaxOutcome.VALID and not self.citations:
            raise ValueError(
                "a VALID tax decision must name the rules behind it; an uncited "
                "rate is a rumour"
            )
        if self.outcome is not TaxOutcome.VALID and self.lines:
            raise ValueError(
                f"a {self.outcome.value} tax decision must carry no tax lines, and "
                f"this one carries {len(self.lines)}"
            )

    @property
    def total_tax_paise(self) -> int | None:
        if self.outcome is not TaxOutcome.VALID:
            return None
        return sum(line.amount_paise for line in self.lines)

    def amount_for(self, tax_type: TaxType) -> int:
        """Paise of one tax, or zero when that tax does not apply.

        Zero here means "this tax is not part of this supply" — an inter-State
        supply has no CGST — and it is only ever reachable on a VALID decision,
        where every applicable line is present. On any other outcome `lines` is
        empty by construction, so this cannot be mistaken for a computed zero.
        """
        return sum(
            line.amount_paise for line in self.lines if line.tax_type is tax_type
        )


def _taxes_for(place: PlaceOfSupplyDecision) -> tuple[TaxType, ...]:
    if place.supply_kind is SupplyKind.INTRA_STATE:
        second = place.second_intra_state_tax
        # `second` is set by `determine` on every intra-State path; the guard is
        # here so a future branch that forgets it fails loudly instead of
        # silently producing a half-split.
        if second is None:  # pragma: no cover - defended, not reachable today
            raise ValueError("an intra-State supply must name its second tax")
        return (TaxType.CGST, second)
    return (TaxType.IGST,)


def decide_tax(
    *,
    corpus: RuleCorpus,
    raw_code: str | None,
    taxable_paise: int,
    supply_date: datetime.date,
    evidence: SupplyEvidence,
    chart_of_accounts: Sequence[str],
) -> TaxDecision:
    """The whole engine, in one call, with every refusal named."""
    place = determine(evidence)
    if not place.determined:
        unanswerable = {
            PlaceOfSupplyOutcome.CONTRADICTED,
            PlaceOfSupplyOutcome.GSTIN_UNREADABLE,
        }
        outcome = (
            TaxOutcome.NOT_VALID
            if place.outcome in unanswerable
            else TaxOutcome.UNCLEAR
        )
        return TaxDecision(outcome=outcome, reason=place.reason, place_of_supply=place)

    code = hsn_sac.normalise(raw_code)
    wanted = _taxes_for(place)
    lookups = tuple(corpus.lookup(code, tax_type, supply_date) for tax_type in wanted)

    unresolved = [look for look in lookups if look.outcome is not RateOutcome.FOUND]
    if unresolved:
        first = unresolved[0]
        return TaxDecision(
            outcome=TaxOutcome.UNCLEAR,
            reason=f"{first.outcome.value}: {first.reason}",
            supply_kind=place.supply_kind,
            place_of_supply=place,
            rate_lookups=lookups,
        )

    rules: list[RateRule] = []
    for look in lookups:
        # FOUND is supposed to always carry a rule, and every lookup that
        # reaches here is FOUND. This is not an `assert`, because `python -O`
        # strips those: the None would then travel into `compute`, and the
        # failure would surface somewhere that cannot name its own cause. The
        # check stays at runtime and fails closed, so a corpus that ever
        # produces FOUND-with-no-rule is reported rather than computed around.
        if look.rule is None:
            return TaxDecision(
                outcome=TaxOutcome.UNCLEAR,
                reason=(
                    f"the {look.tax_type.value} lookup came back FOUND with no "
                    f"rule attached, so there is no rate to apply"
                ),
                supply_kind=place.supply_kind,
                place_of_supply=place,
                rate_lookups=lookups,
            )
        rules.append(look.rule)

    computation = compute(taxable_paise, rules)
    if computation.outcome is not ComputationOutcome.COMPUTED:
        outcome = (
            TaxOutcome.NOT_VALID
            if computation.outcome is ComputationOutcome.TAXABLE_NOT_POSITIVE
            else TaxOutcome.UNCLEAR
        )
        return TaxDecision(
            outcome=outcome,
            reason=computation.reason,
            supply_kind=place.supply_kind,
            place_of_supply=place,
            rate_lookups=lookups,
            computation=computation,
        )

    mapping = map_tax_lines(computation.lines, chart_of_accounts)
    if mapping.outcome is not LedgerMappingOutcome.MAPPED:
        return TaxDecision(
            outcome=TaxOutcome.UNCLEAR,
            reason=mapping.reason,
            supply_kind=place.supply_kind,
            place_of_supply=place,
            rate_lookups=lookups,
            computation=computation,
            ledger_mapping=mapping,
        )

    return TaxDecision(
        outcome=TaxOutcome.VALID,
        reason=(
            f"{place.reason}; "
            + "; ".join(
                f"{line.tax_type.value.upper()} at "
                f"{line.rate_basis_points / 100}% = {line.amount_paise} paise"
                for line in computation.lines
            )
        ),
        supply_kind=place.supply_kind,
        lines=computation.lines,
        ledgers=mapping.ledgers,
        citations=tuple(rule.citation for rule in rules),
        place_of_supply=place,
        rate_lookups=lookups,
        computation=computation,
        ledger_mapping=mapping,
    )
