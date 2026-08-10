"""Which Tally ledger each tax line would land in — if a ledger already exists.

WE NEVER CREATE A LEDGER THE ACCOUNTANT DID NOT CREATE. That rule is already
written into `accountant/checks.py::accounts_exist` for the two ordinary legs,
and a tax leg is not an exception to it. A company whose chart has no `IGST`
ledger does not get one invented; the mapping comes back unmapped and the entry
is handed over.

The names are a MAPPING CHOICE, not a tax fact, and they are stated in one place
so a company that calls its ledger something else changes one dictionary rather
than hunting through the calculator. `RealTally`'s own contract test already uses
the bare form — `<LEDGERNAME>CGST</LEDGERNAME>` at
`tests/test_real_tally.py:1528` — so these match what the connector has always
expected to see.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum

from accountant.rules.gst_rates import TaxType
from accountant.tax.calculator import TaxLine

#: The ledger this application expects to find for each tax. Not created, found.
TAX_LEDGER_NAME: dict[TaxType, str] = {
    TaxType.CGST: "CGST",
    TaxType.SGST: "SGST",
    TaxType.UTGST: "UTGST",
    TaxType.IGST: "IGST",
}


class LedgerMappingOutcome(StrEnum):
    MAPPED = "mapped"
    LEDGER_MISSING = "ledger_missing"
    NOTHING_TO_MAP = "nothing_to_map"


@dataclass(frozen=True)
class MappedTaxLine:
    line: TaxLine
    ledger: str


@dataclass(frozen=True)
class LedgerMapping:
    outcome: LedgerMappingOutcome
    reason: str
    mapped: tuple[MappedTaxLine, ...] = ()
    missing_ledgers: tuple[str, ...] = ()

    @property
    def ledgers(self) -> tuple[str, ...]:
        return tuple(m.ledger for m in self.mapped)


def map_tax_lines(
    lines: Sequence[TaxLine], chart_of_accounts: Sequence[str]
) -> LedgerMapping:
    """Every line mapped, or none of them.

    Partial mapping is refused deliberately. Half a tax posted is worse than no
    tax posted, because the trial balance still adds up and nothing looks wrong.
    """
    if not lines:
        return LedgerMapping(
            outcome=LedgerMappingOutcome.NOTHING_TO_MAP,
            reason="there are no tax lines to map",
        )

    known = set(chart_of_accounts)
    mapped: list[MappedTaxLine] = []
    missing: list[str] = []
    for line in lines:
        ledger = TAX_LEDGER_NAME[line.tax_type]
        if ledger in known:
            mapped.append(MappedTaxLine(line=line, ledger=ledger))
        else:
            missing.append(ledger)

    if missing:
        return LedgerMapping(
            outcome=LedgerMappingOutcome.LEDGER_MISSING,
            reason=(
                "this company's chart of accounts has no ledger called "
                f"{', '.join(sorted(set(missing)))}, and Accountant Dad does not "
                "create ledgers"
            ),
            missing_ledgers=tuple(sorted(set(missing))),
        )
    return LedgerMapping(
        outcome=LedgerMappingOutcome.MAPPED,
        reason="every tax line has a ledger that already exists in this company",
        mapped=tuple(mapped),
    )
