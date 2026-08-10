"""Intra-State or inter-State, decided from evidence and never from a guess.

THE ONE RULE THIS MODULE EXISTS TO ENFORCE
------------------------------------------
    Never post from a GSTIN alone.

A GSTIN carries a state code in its first two digits, and that is exactly why it
is dangerous: it looks like an answer. It is evidence about WHERE THE SUPPLIER IS
REGISTERED, and nothing at all about where the supply took place. Reading the
place of supply off a supplier's registration number is how an inter-State bill
becomes an intra-State one, and the entry looks fine.

So a GSTIN here can do two things and no more: corroborate a stated supplier
state, or contradict it. It can never supply the place of supply, and it can
never make up for a place of supply that is missing.

WHAT IS DELIBERATELY NOT HERE
-----------------------------
Sections 10 and 12 of the IGST Act derive the place of supply from the nature of
the transaction — where goods were delivered, where an immovable property is,
where a service was performed. None of that is implemented, because the CBIC copy
of the IGST Act could not be retrieved (two 404s, recorded in
`gst_rates.UNVERIFIED_SOURCES`). Implementing it from memory would be inventing
statute. So the place of supply must be STATED on the document; if it is not, the
answer is UNCLEAR.

WHERE THE INTRA/INTER SPLIT ITSELF COMES FROM
---------------------------------------------
From the operative paragraph of each notification the corpus already cites, read
verbatim from the retrieved PDFs:

    1/2017-Central Tax (Rate)          "levied on intra-State supplies of goods"
    1/2017-Union Territory Tax (Rate)  "levied on intra-State supplies of goods"
    1/2017-Integrated Tax (Rate)       "levied on inter-State supplies of goods"
    11/2017-Central Tax (Rate)         "on the intra-State supply of services"
    11/2017-Union Territory Tax (Rate) "on the intra-State supply of services"
    8/2017-Integrated Tax (Rate)       "on the inter-State supply of services"

WHY `Jurisdiction.kind` ARRIVES AS EVIDENCE
-------------------------------------------
Whether a place is a State or a Union Territory decides whether the second half
of an intra-State supply is SGST or UTGST, and the corpus has no CBIC source for
that classification. Rather than ship an unsourced table of Indian territories,
the kind is carried on the evidence and the engine reads it. The engine never
infers it, and a missing kind is a missing field, not a default.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum

from accountant.rules.gst_rates import TaxType


class JurisdictionKind(StrEnum):
    STATE = "state"
    UNION_TERRITORY = "union_territory"


@dataclass(frozen=True)
class Jurisdiction:
    """A place, as the evidence names it. `code` is the two-character GST code."""

    code: str
    name: str
    kind: JurisdictionKind


class SupplyKind(StrEnum):
    INTRA_STATE = "intra_state"
    INTER_STATE = "inter_state"


class PlaceOfSupplyOutcome(StrEnum):
    DETERMINED = "determined"
    MISSING_SUPPLIER_STATE = "missing_supplier_state"
    MISSING_PLACE_OF_SUPPLY = "missing_place_of_supply"
    NOT_STATED_ON_DOCUMENT = "not_stated_on_document"
    CONTRADICTED = "contradicted"
    GSTIN_UNREADABLE = "gstin_unreadable"


#: The printed shape of a GSTIN: two digits of state code, a ten-character PAN,
#: an entity digit, the fixed letter Z, and a check character. This is a SHAPE
#: check and is described as nothing more. No checksum is claimed, because the
#: checksum algorithm is not in any document this corpus retrieved — and a shape
#: check can only ever reject, so being conservative here costs nothing.
_GSTIN_SHAPE = re.compile(r"^[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z][0-9A-Z]Z[0-9A-Z]$")


def gstin_state_code(gstin: str | None) -> str | None:
    """The first two digits, but only out of something shaped like a GSTIN.

    Returns None rather than raising: an unreadable registration number is a
    question for the person, and the caller turns it into one.
    """
    if not gstin:
        return None
    candidate = gstin.strip().upper()
    if not _GSTIN_SHAPE.match(candidate):
        return None
    return candidate[:2]


@dataclass(frozen=True)
class SupplyEvidence:
    """What the document actually said. Absence is represented, never filled in.

    `place_of_supply_stated_on_document` is separate from `place_of_supply`
    being present on purpose. A caller can populate the field from anywhere —
    memory, a default, an earlier bill — and the object would look complete. The
    flag is the difference between "the document says so" and "something says
    so", and only the first one is allowed to decide a tax.
    """

    supplier: Jurisdiction | None
    place_of_supply: Jurisdiction | None
    place_of_supply_stated_on_document: bool = False
    supplier_gstin: str | None = None


@dataclass(frozen=True)
class PlaceOfSupplyDecision:
    outcome: PlaceOfSupplyOutcome
    reason: str
    supply_kind: SupplyKind | None = None
    second_intra_state_tax: TaxType | None = None
    gstin_state_code: str | None = None

    @property
    def determined(self) -> bool:
        return self.outcome is PlaceOfSupplyOutcome.DETERMINED


def determine(evidence: SupplyEvidence) -> PlaceOfSupplyDecision:
    """The complete contract, in the order a wrong answer would be caught.

    The GSTIN is checked BEFORE the place of supply is looked at. That ordering
    is deliberate: a contradiction between a supplier's registration and the
    supplier state written on the bill means the document disagrees with itself,
    and no amount of correct place-of-supply data makes that safe to tax.
    """
    supplier = evidence.supplier
    if supplier is None or not supplier.code.strip():
        return PlaceOfSupplyDecision(
            outcome=PlaceOfSupplyOutcome.MISSING_SUPPLIER_STATE,
            reason="nothing says which State or Union Territory the supplier is in",
        )

    code_from_gstin: str | None = None
    if evidence.supplier_gstin:
        code_from_gstin = gstin_state_code(evidence.supplier_gstin)
        if code_from_gstin is None:
            return PlaceOfSupplyDecision(
                outcome=PlaceOfSupplyOutcome.GSTIN_UNREADABLE,
                reason=(
                    f"{evidence.supplier_gstin!r} is not shaped like a GSTIN, so "
                    "nothing can be taken from it"
                ),
            )
        if code_from_gstin != supplier.code:
            return PlaceOfSupplyDecision(
                outcome=PlaceOfSupplyOutcome.CONTRADICTED,
                reason=(
                    f"the supplier GSTIN begins {code_from_gstin} and the bill says "
                    f"the supplier is in {supplier.name} ({supplier.code}); the "
                    "document disagrees with itself"
                ),
                gstin_state_code=code_from_gstin,
            )

    place = evidence.place_of_supply
    if place is None or not place.code.strip():
        return PlaceOfSupplyDecision(
            outcome=PlaceOfSupplyOutcome.MISSING_PLACE_OF_SUPPLY,
            reason=(
                "the place of supply is missing. A supplier GSTIN says where the "
                "supplier is registered, not where the supply happened, so it is "
                "not used to fill this in"
            ),
            gstin_state_code=code_from_gstin,
        )

    if not evidence.place_of_supply_stated_on_document:
        return PlaceOfSupplyDecision(
            outcome=PlaceOfSupplyOutcome.NOT_STATED_ON_DOCUMENT,
            reason=(
                f"a place of supply of {place.name} was supplied, but the document "
                "does not state it; a place of supply is never inferred"
            ),
            gstin_state_code=code_from_gstin,
        )

    if place.code == supplier.code:
        second = (
            TaxType.UTGST
            if place.kind is JurisdictionKind.UNION_TERRITORY
            else TaxType.SGST
        )
        return PlaceOfSupplyDecision(
            outcome=PlaceOfSupplyOutcome.DETERMINED,
            reason=(
                f"supplier and place of supply are both {place.name} "
                f"({place.code}), so this is an intra-State supply"
            ),
            supply_kind=SupplyKind.INTRA_STATE,
            second_intra_state_tax=second,
            gstin_state_code=code_from_gstin,
        )

    return PlaceOfSupplyDecision(
        outcome=PlaceOfSupplyOutcome.DETERMINED,
        reason=(
            f"the supplier is in {supplier.name} ({supplier.code}) and the place of "
            f"supply is {place.name} ({place.code}), so this is an inter-State supply"
        ),
        supply_kind=SupplyKind.INTER_STATE,
        gstin_state_code=code_from_gstin,
    )
