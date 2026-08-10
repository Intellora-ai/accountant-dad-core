"""Place of supply, the arithmetic, the ledgers, and the decision that joins them.

The two tests that matter most in this file are the ones about things NOT
happening: an intra-State supply never producing IGST, and a supplier GSTIN
never standing in for a place of supply. Both are one line of well-meaning code
away, and both put a wrong statutory entry in somebody's books while the trial
balance still adds up.
"""

from __future__ import annotations

import datetime

import pytest

from accountant.rules.gst_rates import (
    RateLookup,
    RateOutcome,
    RuleCorpus,
    TaxType,
    official_corpus,
)
from accountant.rules.hsn_sac import Code, normalise
from accountant.rules.place_of_supply import (
    Jurisdiction,
    JurisdictionKind,
    PlaceOfSupplyOutcome,
    SupplyEvidence,
    SupplyKind,
    determine,
    gstin_state_code,
)
from accountant.tax.calculator import (
    BASIS_POINTS_PER_UNIT,
    ComputationOutcome,
    compute,
    line_amount_paise,
)
from accountant.tax.decision import (
    POSTING_ENABLED,
    TaxDecision,
    TaxOutcome,
    decide_tax,
)
from accountant.tax.ledger_mapper import (
    TAX_LEDGER_NAME,
    LedgerMappingOutcome,
    map_tax_lines,
)

DAY = datetime.date(2017, 7, 15)
CHART = ("Purchases", "Sundry Expenses", "Cash", "CGST", "SGST", "UTGST", "IGST")

CHANDIGARH = Jurisdiction("04", "Chandigarh", JurisdictionKind.UNION_TERRITORY)
LADAKH = Jurisdiction("38", "Ladakh", JurisdictionKind.UNION_TERRITORY)
MAHARASHTRA = Jurisdiction("27", "Maharashtra", JurisdictionKind.STATE)
KARNATAKA = Jurisdiction("29", "Karnataka", JurisdictionKind.STATE)


def decide(**overrides: object) -> TaxDecision:
    kwargs: dict[str, object] = {
        "corpus": official_corpus(),
        "raw_code": "2523",
        "taxable_paise": 100_000,
        "supply_date": DAY,
        "evidence": SupplyEvidence(CHANDIGARH, CHANDIGARH, True, None),
        "chart_of_accounts": CHART,
    }
    kwargs.update(overrides)
    return decide_tax(**kwargs)  # pyright: ignore[reportArgumentType]


class _CorpusThatFindsARateWithNoRule(RuleCorpus):
    """Answers FOUND and attaches no rule. The real corpus never does this.

    It exists so the engine's own refusal has something to refuse. Nothing here
    is evidence about any rate; it is evidence about the check.
    """

    def lookup(
        self, code: Code | None, tax_type: TaxType, on: datetime.date
    ) -> RateLookup:
        return RateLookup(
            outcome=RateOutcome.FOUND,
            code=code,
            tax_type=tax_type,
            on_date=on,
            reason="built by a test; the loaded corpus cannot produce this",
        )


def test_a_found_lookup_with_no_rule_attached_is_refused_at_runtime():
    """`python -O` strips `assert`, so the check could not be an assert.

    The engine used `assert look.rule is not None` here. Under `-O` that line
    disappears, the None travels into `compute`, and the failure surfaces as an
    arithmetic error naming a rate that was never there. This asserts the
    refusal happens at runtime instead, and that it names the corpus.
    """
    decision = decide(corpus=_CorpusThatFindsARateWithNoRule([], []))

    assert decision.outcome is TaxOutcome.UNCLEAR
    assert "FOUND with no rule attached" in decision.reason
    assert decision.lines == ()
    assert decision.total_tax_paise is None
    assert decision.computation is None


# ---- place of supply -------------------------------------------------------


def test_same_place_on_both_sides_is_an_intra_state_supply():
    decision = determine(SupplyEvidence(CHANDIGARH, CHANDIGARH, True))
    assert decision.supply_kind is SupplyKind.INTRA_STATE
    assert decision.second_intra_state_tax is TaxType.UTGST


def test_a_state_intra_supply_asks_for_sgst_and_a_union_territory_asks_for_utgst():
    """The kind comes off the evidence. The engine reads it; it never infers it."""
    assert (
        determine(SupplyEvidence(MAHARASHTRA, MAHARASHTRA, True)).second_intra_state_tax
        is TaxType.SGST
    )
    assert (
        determine(SupplyEvidence(LADAKH, LADAKH, True)).second_intra_state_tax
        is TaxType.UTGST
    )


def test_different_places_are_an_inter_state_supply():
    decision = determine(SupplyEvidence(MAHARASHTRA, KARNATAKA, True))
    assert decision.supply_kind is SupplyKind.INTER_STATE
    assert decision.second_intra_state_tax is None


@pytest.mark.parametrize(
    ("evidence", "outcome"),
    [
        (
            SupplyEvidence(CHANDIGARH, None, False),
            PlaceOfSupplyOutcome.MISSING_PLACE_OF_SUPPLY,
        ),
        (
            SupplyEvidence(None, CHANDIGARH, True),
            PlaceOfSupplyOutcome.MISSING_SUPPLIER_STATE,
        ),
        (
            SupplyEvidence(CHANDIGARH, CHANDIGARH, False),
            PlaceOfSupplyOutcome.NOT_STATED_ON_DOCUMENT,
        ),
        (
            SupplyEvidence(
                Jurisdiction("", "", JurisdictionKind.STATE), CHANDIGARH, True
            ),
            PlaceOfSupplyOutcome.MISSING_SUPPLIER_STATE,
        ),
        (
            SupplyEvidence(
                CHANDIGARH, Jurisdiction("", "", JurisdictionKind.STATE), True
            ),
            PlaceOfSupplyOutcome.MISSING_PLACE_OF_SUPPLY,
        ),
    ],
)
def test_missing_evidence_is_named_and_never_filled_in(
    evidence: SupplyEvidence, outcome: PlaceOfSupplyOutcome
):
    decision = determine(evidence)
    assert decision.outcome is outcome
    assert not decision.determined
    assert decision.supply_kind is None


def test_a_supplier_gstin_alone_never_produces_a_place_of_supply():
    """THE RULE. A GSTIN says where the supplier is registered. Nothing more.

    If this ever returns DETERMINED, an inter-State bill can be posted as an
    intra-State one on the strength of a registration number, and the books will
    balance while the return does not.
    """
    decision = determine(
        SupplyEvidence(
            supplier=None,
            place_of_supply=None,
            place_of_supply_stated_on_document=False,
            supplier_gstin="04ABCDE1234F1Z5",
        )
    )
    assert not decision.determined
    assert decision.outcome is PlaceOfSupplyOutcome.MISSING_SUPPLIER_STATE


def test_a_gstin_beside_a_supplier_state_still_cannot_supply_the_place_of_supply():
    decision = determine(
        SupplyEvidence(CHANDIGARH, None, False, supplier_gstin="04ABCDE1234F1Z5")
    )
    assert decision.outcome is PlaceOfSupplyOutcome.MISSING_PLACE_OF_SUPPLY
    assert "not used to fill this in" in decision.reason
    assert decision.gstin_state_code == "04"


def test_a_gstin_that_contradicts_the_stated_supplier_state_stops_everything():
    decision = determine(
        SupplyEvidence(CHANDIGARH, CHANDIGARH, True, supplier_gstin="27ABCDE1234F1Z5")
    )
    assert decision.outcome is PlaceOfSupplyOutcome.CONTRADICTED
    assert "disagrees with itself" in decision.reason


def test_a_gstin_that_agrees_corroborates_and_changes_nothing_else():
    decision = determine(
        SupplyEvidence(CHANDIGARH, LADAKH, True, supplier_gstin="04ABCDE1234F1Z5")
    )
    assert decision.supply_kind is SupplyKind.INTER_STATE
    assert decision.gstin_state_code == "04"


@pytest.mark.parametrize(
    "text",
    ["", "04ABCDE1234", "04abcde1234f1z5X", "0AABCDE1234F1Z5", "04ABCDE1234F1A5"],
)
def test_a_gstin_that_is_not_shaped_like_one_yields_nothing(text: str):
    assert gstin_state_code(text) is None


def test_a_lowercase_gstin_is_read_because_case_is_typography():
    assert gstin_state_code("04abcde1234f1z5") == "04"


def test_an_unreadable_gstin_is_a_refusal_rather_than_a_shrug():
    decision = determine(
        SupplyEvidence(CHANDIGARH, CHANDIGARH, True, supplier_gstin="not-a-gstin")
    )
    assert decision.outcome is PlaceOfSupplyOutcome.GSTIN_UNREADABLE


def test_gstin_state_code_of_nothing_is_nothing():
    assert gstin_state_code(None) is None


# ---- the arithmetic --------------------------------------------------------


def test_a_rate_applied_to_paise_is_exact_or_it_is_nothing():
    assert line_amount_paise(100_000, 1400) == 14_000
    assert line_amount_paise(1, 900) is None
    assert BASIS_POINTS_PER_UNIT == 10_000


def test_the_two_intra_state_halves_add_up_to_the_whole():
    """CGST at 14% plus UTGST at 14% is IGST at 28%, to the paise, or the split lies."""
    corpus = official_corpus()
    code = normalise("2523")
    assert code is not None
    cgst = corpus.lookup(code, TaxType.CGST, DAY).rule
    utgst = corpus.lookup(code, TaxType.UTGST, DAY).rule
    igst = corpus.lookup(code, TaxType.IGST, DAY).rule
    assert cgst and utgst and igst
    for taxable in (100, 100_000, 123_400, 999_900):
        split = compute(taxable, [cgst, utgst])
        whole = compute(taxable, [igst])
        assert split.total_tax_paise == whole.total_tax_paise, taxable


def test_a_tax_that_is_not_whole_paise_is_refused_rather_than_rounded():
    corpus = official_corpus()
    code = normalise("9987")
    assert code is not None
    rule = corpus.lookup(code, TaxType.IGST, DAY).rule
    assert rule is not None
    result = compute(1, [rule])
    assert result.outcome is ComputationOutcome.NOT_EXACT_IN_PAISE
    assert result.lines == ()
    assert result.total_tax_paise is None
    assert "no official rule for rounding it" in result.reason


def test_every_computed_amount_is_an_int_and_never_a_float():
    corpus = official_corpus()
    code = normalise("2523")
    assert code is not None
    rule = corpus.lookup(code, TaxType.CGST, DAY).rule
    assert rule is not None
    result = compute(250_000, [rule])
    for line in result.lines:
        assert isinstance(line.amount_paise, int)
        assert not isinstance(line.amount_paise, bool)
    assert isinstance(result.total_tax_paise, int)
    assert result.total_including_tax_paise == 250_000 + 35_000


@pytest.mark.parametrize("taxable", [0, -1, -100_000])
def test_a_non_positive_taxable_amount_produces_no_tax_line(taxable: int):
    result = compute(taxable, list(official_corpus().loaded[:1]))
    assert result.outcome is ComputationOutcome.TAXABLE_NOT_POSITIVE
    assert result.lines == ()


def test_a_float_or_a_bool_in_the_money_field_is_refused_at_the_arithmetic():
    """`bool` is an `int`, and a float has reached a money field in this repo."""
    rules = list(official_corpus().loaded[:1])
    for bad in (100.0, True):
        result = compute(bad, rules)  # pyright: ignore[reportArgumentType]
        assert result.outcome is ComputationOutcome.TAXABLE_NOT_POSITIVE
        assert "money is integer paise" in result.reason


def test_no_rate_rules_means_no_tax_lines_and_a_reason():
    result = compute(100_000, [])
    assert result.outcome is ComputationOutcome.NO_RATES
    assert result.total_including_tax_paise is None


# ---- ledgers ---------------------------------------------------------------


def test_every_tax_line_maps_to_a_ledger_that_already_exists():
    lines = compute(100_000, list(official_corpus().loaded[:1])).lines
    mapping = map_tax_lines(lines, CHART)
    assert mapping.outcome is LedgerMappingOutcome.MAPPED
    assert mapping.ledgers == ("CGST",)


def test_a_missing_ledger_is_never_created_and_nothing_partial_is_mapped():
    corpus = official_corpus()
    code = normalise("2523")
    assert code is not None
    rules = [
        corpus.lookup(code, TaxType.CGST, DAY).rule,
        corpus.lookup(code, TaxType.UTGST, DAY).rule,
    ]
    lines = compute(100_000, [r for r in rules if r]).lines
    mapping = map_tax_lines(lines, ("Purchases", "Cash", "CGST"))
    assert mapping.outcome is LedgerMappingOutcome.LEDGER_MISSING
    assert mapping.missing_ledgers == ("UTGST",)
    assert mapping.mapped == ()
    assert "does not create ledgers" in mapping.reason


def test_mapping_nothing_says_so_rather_than_reporting_success():
    assert map_tax_lines([], CHART).outcome is LedgerMappingOutcome.NOTHING_TO_MAP


def test_every_tax_type_has_exactly_one_ledger_name():
    assert set(TAX_LEDGER_NAME) == set(TaxType)
    assert len(set(TAX_LEDGER_NAME.values())) == len(TaxType)


# ---- the decision ----------------------------------------------------------


def test_an_intra_state_supply_splits_into_cgst_and_utgst_and_carries_no_igst():
    decision = decide()
    assert decision.outcome is TaxOutcome.VALID
    assert decision.supply_kind is SupplyKind.INTRA_STATE
    assert decision.amount_for(TaxType.CGST) == 14_000
    assert decision.amount_for(TaxType.UTGST) == 14_000
    assert decision.amount_for(TaxType.IGST) == 0
    assert decision.total_tax_paise == 28_000
    assert decision.ledgers == ("CGST", "UTGST")


def test_an_intra_state_supply_is_never_calculated_as_igst():
    """The mutation this guards: swapping the intra and inter branches.

    An intra-State bill posted as IGST puts the whole tax in the wrong statutory
    head. It reconciles, it balances, and the return is wrong.
    """
    for place in (CHANDIGARH, LADAKH, MAHARASHTRA):
        decision = decide(evidence=SupplyEvidence(place, place, True))
        assert TaxType.IGST not in {line.tax_type for line in decision.lines}
        assert decision.amount_for(TaxType.IGST) == 0


def test_an_inter_state_supply_carries_igst_only():
    decision = decide(evidence=SupplyEvidence(MAHARASHTRA, KARNATAKA, True))
    assert decision.outcome is TaxOutcome.VALID
    assert decision.amount_for(TaxType.IGST) == 28_000
    assert decision.amount_for(TaxType.CGST) == 0
    assert decision.amount_for(TaxType.SGST) == 0
    assert decision.amount_for(TaxType.UTGST) == 0
    assert decision.ledgers == ("IGST",)


def test_an_intra_state_supply_in_a_state_is_unclear_because_sgst_has_no_source():
    decision = decide(evidence=SupplyEvidence(MAHARASHTRA, MAHARASHTRA, True))
    assert decision.outcome is TaxOutcome.UNCLEAR
    assert "holds no SGST rate for any code" in decision.reason
    assert decision.lines == ()


@pytest.mark.parametrize(
    ("overrides", "outcome", "needle"),
    [
        ({"raw_code": "8471"}, TaxOutcome.UNCLEAR, "not in the rules corpus"),
        ({"raw_code": "4820"}, TaxOutcome.UNCLEAR, "does not choose between them"),
        ({"raw_code": "nonsense"}, TaxOutcome.UNCLEAR, "no usable HSN/SAC code"),
        (
            {"supply_date": datetime.date(2026, 8, 10)},
            TaxOutcome.UNCLEAR,
            "may be stale",
        ),
        (
            {"supply_date": datetime.date(2017, 6, 1)},
            TaxOutcome.UNCLEAR,
            "takes effect on 2017-07-01",
        ),
        ({"taxable_paise": 0}, TaxOutcome.NOT_VALID, "nothing to tax"),
        (
            {"taxable_paise": 1, "raw_code": "9987"},
            TaxOutcome.UNCLEAR,
            "not a whole number of paise",
        ),
        (
            {"chart_of_accounts": ("Purchases", "Cash")},
            TaxOutcome.UNCLEAR,
            "no ledger called",
        ),
        (
            {"evidence": SupplyEvidence(CHANDIGARH, None, False)},
            TaxOutcome.UNCLEAR,
            "the place of supply is missing",
        ),
        (
            {
                "evidence": SupplyEvidence(
                    CHANDIGARH, CHANDIGARH, True, "27ABCDE1234F1Z5"
                )
            },
            TaxOutcome.NOT_VALID,
            "disagrees with itself",
        ),
    ],
)
def test_every_refusal_names_itself_and_carries_no_tax_lines(
    overrides: dict[str, object], outcome: TaxOutcome, needle: str
):
    decision = decide(**overrides)
    assert decision.outcome is outcome
    assert needle in decision.reason
    assert decision.lines == ()
    assert decision.ledgers == ()
    assert decision.total_tax_paise is None
    assert decision.citations == ()


def test_a_valid_decision_cites_every_rule_it_used():
    decision = decide()
    assert len(decision.citations) == 2
    for citation in decision.citations:
        assert citation.source_url.startswith("https://cbic")
        assert citation.notification_number
        assert citation.document_reference
        assert citation.retrieval_date is not None
        assert citation.effective_from == datetime.date(2017, 7, 1)


def test_a_valid_decision_cannot_be_built_without_a_citation():
    with pytest.raises(ValueError, match="uncited rate is a rumour"):
        TaxDecision(outcome=TaxOutcome.VALID, reason="no citation")


def test_a_refusal_cannot_be_built_carrying_tax_lines():
    lines = compute(100_000, list(official_corpus().loaded[:1])).lines
    with pytest.raises(ValueError, match="must carry no tax lines"):
        TaxDecision(outcome=TaxOutcome.UNCLEAR, reason="refused", lines=lines)


def test_the_decision_keeps_the_lookups_so_a_refusal_can_be_read_back():
    decision = decide(raw_code="8471")
    assert len(decision.rate_lookups) == 2
    assert all(look.outcome is RateOutcome.NOT_FOUND for look in decision.rate_lookups)
    assert decision.place_of_supply is not None
    assert decision.place_of_supply.determined


def test_posting_is_off_and_a_decision_cannot_turn_it_on():
    assert POSTING_ENABLED is False
    assert decide().posting_enabled is False
    with pytest.raises(ValueError, match="not enabled"):
        TaxDecision(outcome=TaxOutcome.UNCLEAR, reason="probe", posting_enabled=True)
