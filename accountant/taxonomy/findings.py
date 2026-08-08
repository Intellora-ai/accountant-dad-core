"""Child 7 - the real error taxonomy, read out of published audit findings.

The four detectors in `accountant/detect/detectors.py` were written before
anybody checked what auditors actually find. This module is that check. Every
`Finding` below is one paragraph of one published report, with the amount the
report states and the entity it names. Every `Finding` is assigned to exactly
one `ErrorType`, and every `ErrorType` has at least one `Finding` behind it.

**What this module refuses to do.** It records what was found and where. It
never estimates how many times an error type occurs in the world, and it
carries no field in which such a number could be stored. Nothing in the
published record supports one, and a made-up number would quietly become the
justification for keeping or dropping a detector.

**Money is integer paise.** Audit reports state amounts in crore to two
decimal places, so `paise_from_crore(1719, 44)` reads
"`1,719.44 crore" and returns an exact integer. No float touches this file.

Each `ErrorType` carries a `signal`: what an entry of that type looks like in
a voucher stream. `coverage.py` maps the signal to a detector, or to
`UNCOVERED`. The signal is the whole reason the mapping is checkable rather
than a matter of opinion.
"""

from __future__ import annotations

from dataclasses import dataclass

from accountant.taxonomy.sources import (
    CAG_GST_2019,
    CAG_UNION_ACCOUNTS_2018_19,
    CAG_UNION_ACCOUNTS_2023_24,
    ICAI_FRRB_IND_AS_VOL_II,
    NFRA_MSKA_2023,
    Source,
)

# One crore rupees is 10,000,000 rupees, and one rupee is 100 paise.
PAISE_PER_CRORE = 10**9
PAISE_PER_HUNDREDTH_CRORE = PAISE_PER_CRORE // 100


def paise_from_crore(crore: int, hundredths: int = 0) -> int:
    """`paise_from_crore(1719, 44)` is `1,719.44 crore, exactly, in paise.

    Two integers in, one integer out. A report that prints two decimal places
    is printing hundredths of a crore, so no decimal type is needed and no
    float is permitted.
    """
    if crore < 0:
        raise ValueError(f"an audit finding cannot state {crore} crore")
    if not 0 <= hundredths < 100:
        raise ValueError(
            f"{hundredths} is not a two-digit hundredths part of a crore amount"
        )
    return crore * PAISE_PER_CRORE + hundredths * PAISE_PER_HUNDREDTH_CRORE


@dataclass(frozen=True)
class ErrorType:
    """One kind of error the published record actually contains.

    `signal` says what an entry of this type looks like in a voucher stream,
    in terms of the fields `accountant/schema.py` defines. It is the input to
    the coverage decision and nothing else.
    """

    name: str
    description: str
    signal: str


ERROR_TYPES: tuple[ErrorType, ...] = (
    ErrorType(
        name="revenue_expenditure_as_capital",
        description=(
            "Expenditure of revenue nature - maintenance, repair, training, "
            "consultancy, employee dues - booked to a capital head."
        ),
        signal=(
            "the debit account is not the one this party's expenditure has "
            "always gone to"
        ),
    ),
    ErrorType(
        name="capital_expenditure_as_revenue",
        description=(
            "Expenditure that acquires or is directly attributable to a "
            "tangible asset booked to a revenue head."
        ),
        signal=(
            "the debit account is not the one this party's expenditure has "
            "always gone to"
        ),
    ),
    ErrorType(
        name="object_head_incompatible_with_major_head",
        description=(
            "The pair of heads is invalid under the rules: a revenue object "
            "head used against a capital major head. Either head on its own "
            "is in ordinary use."
        ),
        signal=(
            "the debit account is one the entity has used this way before and "
            "keeps using; only a rule table says the pair is wrong"
        ),
    ),
    ErrorType(
        name="wrong_expense_head_within_same_section",
        description=(
            "The entry lands in the right section but the wrong head inside "
            "it - grants for capital assets booked as grants-in-aid, as "
            "subsidies or as professional services; regular pension booked as "
            "incremental pension."
        ),
        signal=(
            "the debit account is one the entity posts to as a standing "
            "practice, on the authority of its own sanction"
        ),
    ),
    ErrorType(
        name="receipt_classified_as_wrong_type",
        description=(
            "A receipt is booked to the wrong side of the revenue/capital "
            "split, overstating one and understating the other."
        ),
        signal="the error is on the receipt side, not on a purchase entry",
    ),
    ErrorType(
        name="parked_in_suspense_head",
        description=(
            "Expenditure is booked to the final head and then moved out to a "
            "suspense head by a later transfer entry, so the head that should "
            "carry it does not."
        ),
        signal=(
            "the original entry is correct; the error is a later transfer "
            "entry against it"
        ),
    ),
    ErrorType(
        name="expenditure_netted_against_receipt",
        description=(
            "An outgoing is shown as a reduction of a receipt rather than as "
            "expenditure, so it never appears as expenditure at all."
        ),
        signal="no expenditure entry exists to inspect",
    ),
    ErrorType(
        name="expense_under_wrong_statement_head",
        description=(
            "An expense is presented under a statement head other than the "
            "one the standard requires - directors' sitting fees shown in "
            "other expenses rather than employee benefit expenses."
        ),
        signal=(
            "the account is the one this entity has always used for this "
            "expense; the standard, not the history, says it is wrong"
        ),
    ),
    ErrorType(
        name="balance_under_wrong_balance_sheet_head",
        description=(
            "A balance is grouped under the wrong balance sheet head - a rent "
            "deposit shown as a non-financial security deposit; statutory "
            "dues shown as provisions."
        ),
        signal=(
            "the account is the one this entity has always used for this "
            "balance; the standard, not the history, says it is wrong"
        ),
    ),
    ErrorType(
        name="related_party_not_identified",
        description=(
            "The counterparty is a related party and the entry is recorded "
            "and disclosed as though it were an ordinary one."
        ),
        signal=(
            "party, account, amount and tax can all look exactly as they "
            "always have; what is wrong is who the party is"
        ),
    ),
    ErrorType(
        name="expenditure_exceeds_sanctioned_provision",
        description=(
            "Expenditure exceeds the provision sanctioned for it and requires "
            "regularisation after the fact."
        ),
        signal=(
            "the amount breaches an external sanctioned limit, which may sit "
            "below or above anything this account has previously carried"
        ),
    ),
    ErrorType(
        name="tax_credit_claimed_where_not_admissible",
        description=(
            "Input tax credit is claimed on a head on which no credit is "
            "admissible, on an account that carries admissible tax the rest "
            "of the time."
        ),
        signal=(
            "tax appears on an account that already carries tax; only a rule "
            "table says this particular credit is not admissible"
        ),
    ),
)

ERROR_TYPE_NAMES: tuple[str, ...] = tuple(t.name for t in ERROR_TYPES)


@dataclass(frozen=True)
class Finding:
    """One extracted audit finding and the paragraph it was read from.

    `amount_paise` is `None` where the source states the finding without an
    amount. That is a stated absence, not a blank.
    """

    ref: str
    source: Source
    entity: str
    body: str
    error_type: str
    amount_paise: int | None = None

    def __post_init__(self) -> None:
        if not self.body.strip():
            raise ValueError(f"finding {self.ref!r} states nothing")
        if self.error_type not in ERROR_TYPE_NAMES:
            raise ValueError(
                f"finding {self.ref!r} is assigned to {self.error_type!r}, "
                f"which is not one of the {len(ERROR_TYPE_NAMES)} error types"
            )
        if self.amount_paise is not None and self.amount_paise < 0:
            raise ValueError(f"finding {self.ref!r} states a negative amount")


FINDINGS: tuple[Finding, ...] = (
    Finding(
        ref="Para 3.11(a), Annexure 3.5, Sl. 1",
        source=CAG_UNION_ACCOUNTS_2018_19,
        entity="Department of Atomic Energy",
        body=(
            "Revenue object heads 21 and 27 used against capital major head "
            "4861. The Department stated the expenditure was of capital "
            "nature; audit held the reply not tenable because the object head "
            "used was of the revenue category."
        ),
        error_type="object_head_incompatible_with_major_head",
        amount_paise=paise_from_crore(1719, 44),
    ),
    Finding(
        ref="Para 3.11(a), Annexure 3.5, Sl. 2",
        source=CAG_UNION_ACCOUNTS_2018_19,
        entity="Department of Atomic Energy",
        body="Revenue object head 21 used against capital major head 5401.",
        error_type="object_head_incompatible_with_major_head",
        amount_paise=paise_from_crore(262, 6),
    ),
    Finding(
        ref="Para 3.11(a), Annexure 3.5, Sl. 3",
        source=CAG_UNION_ACCOUNTS_2018_19,
        entity="Ministry of Road Transport and Highways",
        body=(
            "Revenue object heads 11, 13 and 28 used against capital major head 5054."
        ),
        error_type="object_head_incompatible_with_major_head",
        amount_paise=paise_from_crore(63, 81),
    ),
    Finding(
        ref="Para 3.11(a), Annexure 3.5, Sl. 4",
        source=CAG_UNION_ACCOUNTS_2018_19,
        entity="Ministry of Shipping",
        body="Revenue object head 13 used against capital major head 5052.",
        error_type="object_head_incompatible_with_major_head",
        amount_paise=paise_from_crore(4, 74),
    ),
    Finding(
        ref="Para 3.11(b), Annexure 3.6, Sl. 1",
        source=CAG_UNION_ACCOUNTS_2018_19,
        entity="Ministry of Road Transport and Highways",
        body=(
            "Training courses on road safety and a consultancy fee for IT "
            "specialists booked under object head 53-Major Works in the "
            "capital section instead of 28-Professional Services in the "
            "revenue section."
        ),
        error_type="revenue_expenditure_as_capital",
        amount_paise=paise_from_crore(2, 4),
    ),
    Finding(
        ref="Para 3.11(b), Annexure 3.6, Sl. 2",
        source=CAG_UNION_ACCOUNTS_2018_19,
        entity="Department of Space",
        body=(
            "Procurement of BN tubes booked under a capital machinery and "
            "equipment head instead of object head 21-Supplies and Materials "
            "in the revenue section."
        ),
        error_type="revenue_expenditure_as_capital",
        amount_paise=paise_from_crore(1, 67),
    ),
    Finding(
        ref="Para 3.11(b), Annexure 3.6, Sl. 3",
        source=CAG_UNION_ACCOUNTS_2018_19,
        entity="Department of Space",
        body=(
            "Gratuity and leave encashment of 131 employees booked under a "
            "capital machinery and equipment head instead of major head 2071 "
            "Pensions and other retirement benefits in the revenue section."
        ),
        error_type="revenue_expenditure_as_capital",
        amount_paise=paise_from_crore(14, 41),
    ),
    Finding(
        ref="Para 3.11(b), Annexure 3.6, Sl. 4",
        source=CAG_UNION_ACCOUNTS_2018_19,
        entity="Ministry of Railways",
        body=(
            "Deep screening, a tamping machine and repair and maintenance of "
            "permanent way booked under the capital section instead of the "
            "revenue section."
        ),
        error_type="revenue_expenditure_as_capital",
        amount_paise=paise_from_crore(1, 62),
    ),
    Finding(
        ref="Para 3.11(b), Annexure 3.6, Sl. 5",
        source=CAG_UNION_ACCOUNTS_2018_19,
        entity="Ministry of Railways",
        body=(
            "Work on VHF communication booked under New Line instead of the "
            "revenue section."
        ),
        error_type="revenue_expenditure_as_capital",
        amount_paise=paise_from_crore(2, 67),
    ),
    Finding(
        ref="Para 3.11(b), Annexure 3.6, Sl. 6",
        source=CAG_UNION_ACCOUNTS_2018_19,
        entity="Department of Space",
        body=(
            "In 16 cases, consumables directly attributable to a tangible "
            "asset booked under object head 21-Supplies and Materials in the "
            "revenue section instead of 60-Other Capital Expenditure in the "
            "capital section."
        ),
        error_type="capital_expenditure_as_revenue",
        amount_paise=paise_from_crore(149, 73),
    ),
    Finding(
        ref="Para 3.11(b), Annexure 3.6, Sl. 7",
        source=CAG_UNION_ACCOUNTS_2018_19,
        entity="Department of Space",
        body=(
            "Procurement of a hand-held explosive vapour detector and VHF "
            "base station equipment booked under 28-Professional Services in "
            "the revenue section instead of 52-Machinery and Equipment in the "
            "capital section."
        ),
        error_type="capital_expenditure_as_revenue",
        amount_paise=paise_from_crore(1, 10),
    ),
    Finding(
        ref="Para 3.11(b), Annexure 3.6, Sl. 8",
        source=CAG_UNION_ACCOUNTS_2018_19,
        entity="Department of Space",
        body=(
            "Procurement of simulation software booked under 21-Supplies and "
            "Material in the revenue section instead of 52-IT Machinery and "
            "Equipment in the capital section."
        ),
        error_type="capital_expenditure_as_revenue",
        amount_paise=paise_from_crore(3, 38),
    ),
    Finding(
        ref="Para 3.11(c)",
        source=CAG_UNION_ACCOUNTS_2018_19,
        entity="Ministry of Tourism",
        body=(
            "35-Grants for creation of capital assets booked as "
            "31-Grants-in-aid-general. The Ministry accepted the "
            "misclassification and assured booking under the correct head."
        ),
        error_type="wrong_expense_head_within_same_section",
        amount_paise=paise_from_crore(1145, 89),
    ),
    Finding(
        ref="Para 3.11(c)",
        source=CAG_UNION_ACCOUNTS_2018_19,
        entity="Ministry of Power",
        body=(
            "35-Grants for creation of capital assets booked as "
            "33-Subsidies. The Ministry stated it had adopted the "
            "classification given in the sanction of its finance division."
        ),
        error_type="wrong_expense_head_within_same_section",
        amount_paise=paise_from_crore(445, 38),
    ),
    Finding(
        ref="Para 3.11(c)",
        source=CAG_UNION_ACCOUNTS_2018_19,
        entity="Ministry of Electronics and Information Technology",
        body=(
            "Claims of the Reserve Bank booked under object head "
            "32-Contributions instead of 50-Other Charges. The Ministry "
            "stated the correct head was communicated only after the "
            "expenditure had been booked."
        ),
        error_type="wrong_expense_head_within_same_section",
        amount_paise=paise_from_crore(71),
    ),
    Finding(
        ref="Para 3.11(c)",
        source=CAG_UNION_ACCOUNTS_2018_19,
        entity="Ministry of Power",
        body=(
            "35-Grants for creation of capital assets booked under "
            "28-Professional services."
        ),
        error_type="wrong_expense_head_within_same_section",
        amount_paise=paise_from_crore(50),
    ),
    Finding(
        ref="Para 2.4.2.8",
        source=CAG_UNION_ACCOUNTS_2018_19,
        entity="Specified Undertaking of the Unit Trust of India",
        body=(
            "Proceeds of a sale of shares accounted under major head 4000 "
            "Miscellaneous Capital Receipts. Audit held that receipts from "
            "sale of shares can only be treated as non-tax revenue, so "
            "capital receipts were overstated and revenue receipts "
            "understated."
        ),
        error_type="receipt_classified_as_wrong_type",
        amount_paise=paise_from_crore(12426),
    ),
    Finding(
        ref="Para 3.13",
        source=CAG_UNION_ACCOUNTS_2018_19,
        entity="Ministry of Defence, Grant No. 22 Defence Pensions",
        body=(
            "Expenditure booked in March 2019 under head 2071.02.101.01 was "
            "then transferred to a suspense head by transfer entry in the "
            "same month. Audit recommended action against the accounting "
            "authorities who approved it."
        ),
        error_type="parked_in_suspense_head",
        amount_paise=paise_from_crore(5000),
    ),
    Finding(
        ref="Para 3.14",
        source=CAG_UNION_ACCOUNTS_2018_19,
        entity="Central Board of Direct Taxes",
        body=(
            "Interest on refunds of excess tax classified as a reduction in "
            "revenue rather than as expenditure, so it was withdrawn from the "
            "Consolidated Fund without appropriation. The source states no "
            "amount for the year at this paragraph."
        ),
        error_type="expenditure_netted_against_receipt",
    ),
    Finding(
        ref="Para 3.5 (FY 2023-24)",
        source=CAG_UNION_ACCOUNTS_2023_24,
        entity="Union Government",
        body=(
            "Of misclassification in accounting of `4,214.07 crore, the "
            "portion relating to receipts."
        ),
        error_type="receipt_classified_as_wrong_type",
        amount_paise=paise_from_crore(3283, 50),
    ),
    Finding(
        ref="Para 3.5.2.1 (FY 2023-24)",
        source=CAG_UNION_ACCOUNTS_2023_24,
        entity="Department of Telecommunications",
        body=("Regular pension payments misclassified as Incremental Pension Payment."),
        error_type="wrong_expense_head_within_same_section",
        amount_paise=paise_from_crore(3443, 8),
    ),
    Finding(
        ref="Para 4.2.1.1 (FY 2023-24)",
        source=CAG_UNION_ACCOUNTS_2023_24,
        entity="Transfers to States (Capital Charged)",
        body=(
            "Excess expenditure over the grant, requiring regularisation "
            "under Article 115(1)(b) of the Constitution."
        ),
        error_type="expenditure_exceeds_sanctioned_provision",
        amount_paise=paise_from_crore(31308, 41),
    ),
    Finding(
        ref="Chapter 4, Observation 4",
        source=ICAI_FRRB_IND_AS_VOL_II,
        entity="Reviewed enterprise",
        body=(
            "Directors' sitting fees disclosed under Other Expenses. The "
            "Board viewed that it should have been disclosed under the head "
            "employee benefit expenses in line with Ind AS 19."
        ),
        error_type="expense_under_wrong_statement_head",
    ),
    Finding(
        ref="Chapter 1, Observation 11",
        source=ICAI_FRRB_IND_AS_VOL_II,
        entity="Reviewed enterprise",
        body=(
            "A rent deposit disclosed as a security deposit under Other "
            "Non-current Assets. The Board viewed that a rent deposit is a "
            "financial asset under Ind AS 32 and should have been disclosed "
            "under Other Financial Assets."
        ),
        error_type="balance_under_wrong_balance_sheet_head",
    ),
    Finding(
        ref="Chapter 3, Observations 5-6",
        source=ICAI_FRRB_IND_AS_VOL_II,
        entity="Reviewed enterprise",
        body=(
            "Provident fund payable and professional tax payable included "
            "under Provisions. The Board viewed they should sit under "
            "statutory dues payable within Other Current Liabilities."
        ),
        error_type="balance_under_wrong_balance_sheet_head",
    ),
    Finding(
        ref="Executive Summary (f), Paras 43 to 63",
        source=NFRA_MSKA_2023,
        entity="Four of five inspected audit engagements",
        body=(
            "Related party disclosures found incomplete, inaccurate and "
            "non-compliant with Ind AS 24 and the Companies Act 2013, "
            "including failure to disclose terms and conditions, incorrect "
            "interest rates and misrepresentation of balances."
        ),
        error_type="related_party_not_identified",
    ),
    Finding(
        ref="Para 4.7.5",
        source=CAG_GST_2019,
        entity="21 taxpayers across six Commissionerates",
        body=(
            "Input tax credit availed in Tran-1 of Education Cess, Secondary "
            "and Higher Education Cess, Swachh Bharat Cess and Krishi Kalyan "
            "Cess, which had been abolished and were ineligible to be carried "
            "forward, and which was therefore inadmissible."
        ),
        error_type="tax_credit_claimed_where_not_admissible",
        amount_paise=paise_from_crore(9, 74),
    ),
)


def findings_for(error_type: str) -> tuple[Finding, ...]:
    """Every extracted finding assigned to one type, in extraction order."""
    return tuple(f for f in FINDINGS if f.error_type == error_type)
