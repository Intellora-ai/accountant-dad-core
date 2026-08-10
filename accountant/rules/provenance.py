"""Where a rule came from, and whether that is good enough to run it.

A rate with no citation is a rumour. This module holds the citation, the rank of
the authority behind it, and the record of a source that could not be retrieved
at all — because the honest outcome of a failed fetch is a named gap, not a
number remembered from somewhere else.
"""

from __future__ import annotations

import datetime
from dataclasses import dataclass
from enum import StrEnum


class AuthorityRank(StrEnum):
    """Owner decision Q1 = A, in order. Nothing else is a source.

    A commercial rate API is deliberately absent rather than ranked last: it is
    not a weak source, it is not a source, and it is not a dependency either.
    """

    CBIC_NOTIFICATION = "cbic_notification"
    INCOME_TAX_NOTIFICATION = "income_tax_notification"
    GST_COUNCIL_SUPPLEMENTARY = "gst_council_supplementary"


#: Ranks that may stand alone behind a rule that runs. GST Council material may
#: be recorded beside a rule as context and can never be the only thing there.
SOLE_AUTHORITY_RANKS = frozenset(
    {AuthorityRank.CBIC_NOTIFICATION, AuthorityRank.INCOME_TAX_NOTIFICATION}
)


class RuleStatus(StrEnum):
    """LOADED runs. SOURCE_UNVERIFIED is recorded and never runs.

    `SOURCE_UNVERIFIED` is one of the labels the owner fixed for this project,
    spelled exactly as `docs/OWNER_DECISIONS.md` spells it.
    """

    LOADED = "loaded"
    SOURCE_UNVERIFIED = "source_unverified"


@dataclass(frozen=True)
class Source:
    """One official document, named precisely enough to be re-fetched.

    `document_reference` is the part of the document the rule was read from —
    "Schedule IV - 14%, S. No. 18", not "the rate schedule". Without it a
    citation points at a hundred pages and nobody checks it.

    Nothing is validated here on purpose. A `Source` with a blank URL must be
    constructible, because the guard that rejects it lives in
    `gst_rates.RuleCorpus.build` and a guard nothing can violate is a guard
    nobody can test.
    """

    url: str
    title: str
    issuing_authority: str
    notification_number: str
    retrieval_date: datetime.date | None
    authority_rank: AuthorityRank
    document_reference: str = ""

    @property
    def may_stand_alone(self) -> bool:
        return self.authority_rank in SOLE_AUTHORITY_RANKS


@dataclass(frozen=True)
class UnverifiedSource:
    """A source that was asked for and did not arrive.

    Recorded with the exact error rather than summarised, because "the fetch
    failed" is not reproducible and "unable to verify the first certificate" is.
    `would_have_supported` says what the corpus is missing as a result, so the
    gap is legible without reading the code that would have used it.
    """

    url: str
    attempted_on: datetime.date
    error: str
    would_have_supported: str
    status: RuleStatus = RuleStatus.SOURCE_UNVERIFIED


@dataclass(frozen=True)
class Rejection:
    """A rule that was offered to the corpus and refused, and why."""

    rule_id: str
    reason: str
    url: str


@dataclass(frozen=True)
class Citation:
    """What a computed result points back at. One line per rule that was used."""

    rule_id: str
    notification_number: str
    source_url: str
    document_reference: str
    retrieval_date: datetime.date | None
    effective_from: datetime.date | None
