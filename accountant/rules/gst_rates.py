"""The rates themselves, each one read out of a CBIC notification by hand.

WHAT IS HERE, AND WHY IT IS SO SMALL
------------------------------------
Fifteen rules over four codes. Owner decision Q2 = C: the corpus holds the codes
the evaluation corpus actually uses and nothing else. A code is never added to
make a number look better.

Every rate below was read off a PDF served by `cbic-gst.gov.in`, whose first page
reads GOVERNMENT OF INDIA / MINISTRY OF FINANCE / (Department of Revenue). The
`document_reference` on each rule names the schedule and serial number it came
from, so any line here can be checked against the source in under a minute.

WHY THE RATES ARE 2017 RATES AND WHY THAT IS NOT A BUG
------------------------------------------------------
The current rate notifications live behind `taxinformation.cbic.gov.in`, which
fails TLS verification from here — the same failure this project already
recorded once. Rather than substitute a secondary source or reconstruct a rate
from memory, the corpus holds what could actually be retrieved and states how far
it checked: `amendments_checked_through` is 2017-08-17 on every rule, and a
lookup for a later supply date REFUSES rather than returning a rate that may have
moved. See `accountant/rules/effective_dates.py` and
`artifacts/ground_truth/rules/unverified_sources.json`.

WHY 4820 HAS TWO ENTRIES AND ALWAYS COMES BACK UNCLEAR
------------------------------------------------------
Because the notification does. Heading 4820 is printed twice — Schedule II at 6%
for exercise books and notebooks, Schedule III at 9% for registers and account
books "[other than note books and exercise books]". At four digits the code alone
cannot tell them apart, so the corpus reports a conflict and refuses. It was not
resolved by picking the commoner one, and the conflict was not invented for the
test suite; it is in the source document.

MONEY AND RATES ARE INTEGERS
----------------------------
Rates are basis points — 14% is 1400 — for the same reason money is paise. There
is no float anywhere in this file or the one that multiplies by it.
"""

from __future__ import annotations

import datetime
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from enum import StrEnum

from accountant.rules.effective_dates import EffectiveWindow, WindowVerdict
from accountant.rules.hsn_sac import Code, CodeKind
from accountant.rules.provenance import (
    AuthorityRank,
    Citation,
    Rejection,
    RuleStatus,
    Source,
    UnverifiedSource,
)


class TaxType(StrEnum):
    CGST = "cgst"
    SGST = "sgst"
    UTGST = "utgst"
    IGST = "igst"


class RateOutcome(StrEnum):
    """What a lookup found. Only FOUND carries a rate."""

    FOUND = "found"
    NOT_FOUND = "not_found"
    CONFLICT = "conflict"
    NOT_YET_IN_FORCE = "not_yet_in_force"
    ENDED = "ended"
    BEYOND_AMENDMENT_CHECK = "beyond_amendment_check"
    SOURCE_UNVERIFIED = "source_unverified"


@dataclass(frozen=True)
class RateRule:
    """One rate, for one code, for one tax, over one window, from one document."""

    rule_id: str
    code: Code
    description: str
    tax_type: TaxType
    rate_basis_points: int
    window: EffectiveWindow
    source: Source
    jurisdiction: str
    rule_version: str
    status: RuleStatus = RuleStatus.LOADED

    @property
    def citation(self) -> Citation:
        return Citation(
            rule_id=self.rule_id,
            notification_number=self.source.notification_number,
            source_url=self.source.url,
            document_reference=self.source.document_reference,
            retrieval_date=self.source.retrieval_date,
            effective_from=self.window.effective_from,
        )


@dataclass(frozen=True)
class RateLookup:
    """A rate, or a named reason there is not one. Never a silent zero."""

    outcome: RateOutcome
    code: Code | None
    tax_type: TaxType
    on_date: datetime.date
    reason: str
    rule: RateRule | None = None
    conflicting_rule_ids: tuple[str, ...] = ()

    @property
    def rate_basis_points(self) -> int | None:
        return self.rule.rate_basis_points if self.rule is not None else None


# ---------------------------------------------------------------------------
# The corpus
# ---------------------------------------------------------------------------


class RuleCorpus:
    """Rules that passed the load guards, plus the ones that did not, kept.

    A rejected rule is not dropped. It is held with the reason and the URL, so
    "codes rejected" is a number the report can print rather than a difference
    somebody has to reconstruct.
    """

    #: A rule must carry all four or it does not load. Missing any one of them
    #: makes the rate untraceable, undatable, or both.
    REQUIRED = (
        "source URL",
        "notification number",
        "retrieval date",
        "effective date",
    )

    def __init__(
        self,
        loaded: Sequence[RateRule],
        rejected: Sequence[Rejection],
        unverified: Sequence[UnverifiedSource] = (),
    ) -> None:
        self._loaded = tuple(loaded)
        self._rejected = tuple(rejected)
        self._unverified = tuple(unverified)

    @property
    def loaded(self) -> tuple[RateRule, ...]:
        return self._loaded

    @property
    def rejected(self) -> tuple[Rejection, ...]:
        return self._rejected

    @property
    def unverified(self) -> tuple[UnverifiedSource, ...]:
        return self._unverified

    @property
    def codes(self) -> tuple[str, ...]:
        return tuple(sorted({r.code.value for r in self._loaded}))

    def codes_of_kind(self, kind: CodeKind) -> tuple[str, ...]:
        return tuple(
            sorted({r.code.value for r in self._loaded if r.code.kind is kind})
        )

    @classmethod
    def build(
        cls,
        rules: Iterable[RateRule],
        unverified: Sequence[UnverifiedSource] = (),
    ) -> RuleCorpus:
        """Load what is citable, reject what is not, and say which is which.

        The four rejections are the owner's four, checked in the order they are
        written down. `SOURCE_UNVERIFIED` is rejected separately and last,
        because a rule can be perfectly well formed and still have no source
        standing behind it — that is the TLS failure case, and it must not read
        as a malformed rule.
        """
        loaded: list[RateRule] = []
        rejected: list[Rejection] = []
        for rule in rules:
            reason = cls._why_not(rule)
            if reason:
                rejected.append(
                    Rejection(rule_id=rule.rule_id, reason=reason, url=rule.source.url)
                )
                continue
            loaded.append(rule)
        return cls(loaded=loaded, rejected=rejected, unverified=unverified)

    @staticmethod
    def _why_not(rule: RateRule) -> str:
        if not rule.source.url.strip():
            return "no source URL"
        if not rule.source.notification_number.strip():
            return "no notification number"
        if rule.source.retrieval_date is None:
            return "no retrieval date"
        if rule.window.effective_from is None:
            return "no effective date"
        if not rule.source.may_stand_alone:
            return (
                f"authority rank {rule.source.authority_rank.value!r} may not be "
                "the sole authority for a production rule"
            )
        if rule.status is not RuleStatus.LOADED:
            return f"rule status is {rule.status.value}"
        if not rule.rule_version.strip():
            return "no rule version"
        if rule.rate_basis_points < 0:
            return f"negative rate {rule.rate_basis_points} basis points"
        return ""

    # -- lookup --------------------------------------------------------------

    def lookup(
        self, code: Code | None, tax_type: TaxType, on: datetime.date
    ) -> RateLookup:
        """The only way to get a rate out of this corpus.

        There is no second path, no cache warmed from somewhere else, and no
        fallback. If the code is not in `self._loaded` the answer is NOT_FOUND,
        whatever else is in there that looks like it.
        """
        if code is None:
            return RateLookup(
                outcome=RateOutcome.NOT_FOUND,
                code=None,
                tax_type=tax_type,
                on_date=on,
                reason="no usable HSN/SAC code was given",
            )

        candidates = [
            r
            for r in self._loaded
            if r.code.value == code.value and r.tax_type is tax_type
        ]
        if not candidates:
            if not any(r.tax_type is tax_type for r in self._loaded):
                # Not "we have not got round to this code" but "we have no
                # authority for this tax at all". SGST is the live example:
                # a State notifies its own rate, so no CBIC document carries it
                # and owner decision Q1 = A admits no other kind of source.
                return RateLookup(
                    outcome=RateOutcome.SOURCE_UNVERIFIED,
                    code=code,
                    tax_type=tax_type,
                    on_date=on,
                    reason=(
                        f"the corpus holds no {tax_type.value.upper()} rate for any "
                        "code, because no source that may stand alone under owner "
                        "decision Q1 = A publishes one"
                    ),
                )
            return RateLookup(
                outcome=RateOutcome.NOT_FOUND,
                code=code,
                tax_type=tax_type,
                on_date=on,
                reason=(
                    f"{code.value} is not in the rules corpus for "
                    f"{tax_type.value.upper()}; no similar code is used in its place"
                ),
            )

        in_force = [
            r for r in candidates if r.window.verdict(on) is WindowVerdict.IN_FORCE
        ]
        if len(in_force) > 1:
            rates = ", ".join(f"{r.rate_basis_points / 100}%" for r in in_force)
            return RateLookup(
                outcome=RateOutcome.CONFLICT,
                code=code,
                tax_type=tax_type,
                on_date=on,
                reason=(
                    f"{code.value} has {len(in_force)} different "
                    f"{tax_type.value.upper()} rates in force on {on} "
                    f"({rates}); "
                    "the corpus does not choose between them"
                ),
                conflicting_rule_ids=tuple(r.rule_id for r in in_force),
            )
        if len(in_force) == 1:
            rule = in_force[0]
            return RateLookup(
                outcome=RateOutcome.FOUND,
                code=code,
                tax_type=tax_type,
                on_date=on,
                reason=(
                    f"{rule.source.notification_number}, "
                    f"{rule.source.document_reference}"
                ),
                rule=rule,
            )

        # Candidates exist but none is in force. Report the most specific
        # reason rather than a bare NOT_FOUND, because "we have this code but
        # not for that date" is a different problem for the person reading it.
        verdicts = [c.window.verdict(on) for c in candidates]
        for verdict, outcome in (
            (WindowVerdict.BEYOND_AMENDMENT_CHECK, RateOutcome.BEYOND_AMENDMENT_CHECK),
            (WindowVerdict.ENDED, RateOutcome.ENDED),
            (WindowVerdict.NOT_YET_IN_FORCE, RateOutcome.NOT_YET_IN_FORCE),
        ):
            if verdict in verdicts:
                first = candidates[verdicts.index(verdict)]
                return RateLookup(
                    outcome=outcome,
                    code=code,
                    tax_type=tax_type,
                    on_date=on,
                    reason=first.window.explain(on),
                )
        return RateLookup(
            outcome=RateOutcome.NOT_FOUND,
            code=code,
            tax_type=tax_type,
            on_date=on,
            reason=f"{code.value} has no dated rule that applies on {on}",
        )


# ---------------------------------------------------------------------------
# Sources — retrieved 2026-08-10 from cbic-gst.gov.in
# ---------------------------------------------------------------------------

RETRIEVED = datetime.date(2026, 8, 10)
JURISDICTION = "IN"
RULE_VERSION = "1"

_AUTHORITY = (
    "Central Board of Indirect Taxes and Customs, Department of Revenue, "
    "Ministry of Finance, Government of India"
)

#: The whole corpus starts here. Each of the six notifications says so in its own
#: last operative paragraph: "This notification shall come into force with effect
#: from the 1st day of July, 2017."
IN_FORCE_FROM = datetime.date(2017, 7, 1)

#: The last day the amendment chain was checked, and the reason it stops there.
#:
#: CBIC's own listings of Central Tax (Rate), Integrated Tax (Rate) and Union
#: Territory Tax (Rate) notifications were read on 2026-08-10. Between 1 July
#: 2017 and this date the only amendments to notification 1/2017 in any of the
#: three series are the fertiliser one (18/2017-CTR and 18/2017-UTTR of
#: 30-06-2017, 16/2017-ITR of 30-06-2017) and the tractor-parts one (19/2017 of
#: 18-08-2017), and the first amendment to the services notifications is
#: 20/2017 of 22-08-2017. The earliest of those post-commencement dates is
#: 18-08-2017, so the corpus stops the day before it — uniformly, for every
#: rule, without judging which entries an amendment touched.
AMENDMENTS_CHECKED_THROUGH = datetime.date(2017, 8, 17)

_AMENDMENT_NOTE = (
    "CBIC notification listings read 2026-08-10: "
    "https://cbic-gst.gov.in/hindi/central-tax-rate.html, "
    "https://cbic-gst.gov.in/hindi/integrated-tax-rate.html, "
    "https://cbic-gst.gov.in/hindi/union-territory-tax-rate.html. "
    "The earliest amendment to any of the six source notifications dated after "
    "1 July 2017 is 19/2017 of 18-08-2017, so the check stops on 2017-08-17. "
    "Rate notifications issued after 2022 are published only on "
    "taxinformation.cbic.gov.in, which fails TLS verification from here."
)

WINDOW = EffectiveWindow(
    effective_from=IN_FORCE_FROM,
    effective_to=None,
    amendments_checked_through=AMENDMENTS_CHECKED_THROUGH,
    amendment_check_note=_AMENDMENT_NOTE,
)


def _source(url: str, title: str, number: str, reference: str) -> Source:
    return Source(
        url=url,
        title=title,
        issuing_authority=_AUTHORITY,
        notification_number=number,
        retrieval_date=RETRIEVED,
        authority_rank=AuthorityRank.CBIC_NOTIFICATION,
        document_reference=reference,
    )


_CGST_GOODS_URL = (
    "https://cbic-gst.gov.in/hindi/pdf/central-tax-rate/"
    "Notification-for-CGST-rate-Schedule.pdf"
)
_IGST_GOODS_URL = (
    "https://cbic-gst.gov.in/hindi/pdf/integrated-tax-rate/"
    "Notification%20for%20IGST%20rate%20Schedule-1.pdf"
)
_UTGST_GOODS_URL = (
    "https://cbic-gst.gov.in/hindi/pdf/ut-tax/notfctn-1-UTGST-rate-english.pdf"
)
_CGST_SERVICES_URL = (
    "https://cbic-gst.gov.in/hindi/pdf/central-tax-rate/Notification11-CGST.pdf"
)
_IGST_SERVICES_URL = (
    "https://cbic-gst.gov.in/hindi/pdf/integrated-tax-rate/Notification8-IGST.pdf"
)
_UTGST_SERVICES_URL = (
    "https://cbic-gst.gov.in/hindi/pdf/ut-tax/Rate%20Notification%20-%20UTGST.pdf"
)

_GOODS = {
    TaxType.CGST: (
        _CGST_GOODS_URL,
        "Notification No.1/2017-Central Tax (Rate) — CGST Rate Schedule notified "
        "under section 9 (1)",
        "1/2017-Central Tax (Rate)",
    ),
    TaxType.UTGST: (
        _UTGST_GOODS_URL,
        "Notification No.1/2017-Union Territory Tax (Rate) — UTGST Rate Schedule "
        "notified under section 7 (1)",
        "1/2017-Union Territory Tax (Rate)",
    ),
    TaxType.IGST: (
        _IGST_GOODS_URL,
        "Notification No.1/2017-Integrated Tax (Rate) — IGST Rate Schedule "
        "notified under section 5 (1)",
        "1/2017-Integrated Tax (Rate)",
    ),
}

_SERVICES = {
    TaxType.CGST: (
        _CGST_SERVICES_URL,
        "Notification No. 11/2017-Central Tax (Rate) — rates for supply of "
        "services under the CGST Act",
        "11/2017-Central Tax (Rate)",
    ),
    TaxType.UTGST: (
        _UTGST_SERVICES_URL,
        "Notification No. 11/2017-Union Territory Tax (Rate) — rates for supply "
        "of services under the UTGST Act",
        "11/2017-Union Territory Tax (Rate)",
    ),
    TaxType.IGST: (
        _IGST_SERVICES_URL,
        "Notification No. 8/2017-Integrated Tax (Rate) — rates for supply of "
        "services under the IGST Act",
        "8/2017-Integrated Tax (Rate)",
    ),
}


def _goods_rule(
    slug: str,
    code_value: str,
    description: str,
    tax_type: TaxType,
    basis_points: int,
    reference: str,
) -> RateRule:
    url, title, number = _GOODS[tax_type]
    return RateRule(
        rule_id=f"gst.goods.{code_value}.{slug}.{tax_type.value}.v{RULE_VERSION}",
        code=Code(value=code_value, kind=CodeKind.HSN),
        description=description,
        tax_type=tax_type,
        rate_basis_points=basis_points,
        window=WINDOW,
        source=_source(url, title, number, reference),
        jurisdiction=JURISDICTION,
        rule_version=RULE_VERSION,
    )


def _service_rule(
    code_value: str,
    description: str,
    tax_type: TaxType,
    basis_points: int,
    reference: str,
) -> RateRule:
    url, title, number = _SERVICES[tax_type]
    return RateRule(
        rule_id=f"gst.services.{code_value}.{tax_type.value}.v{RULE_VERSION}",
        code=Code(value=code_value, kind=CodeKind.SAC),
        description=description,
        tax_type=tax_type,
        rate_basis_points=basis_points,
        window=WINDOW,
        source=_source(url, title, number, reference),
        jurisdiction=JURISDICTION,
        rule_version=RULE_VERSION,
    )


_CEMENT = (
    "Portland cement, aluminous cement, slag cement, super sulphate cement and "
    "similar hydraulic cements, whether or not coloured or in the form of clinkers"
)
_EXERCISE_BOOKS = "Exercise book, graph book, & laboratory note book and notebooks"
_REGISTERS = (
    "Registers, account books, order books, receipt books, letter pads, "
    "memorandum pads, diaries and similar articles, blotting-pads, binders "
    "(loose-leaf or other), folders, file covers, manifold business forms, "
    "interleaved carbon sets and other articles of stationary, of paper or "
    "paperboard; and book covers, of paper or paperboard [other than note books "
    "and exercise books]"
)
_REAL_ESTATE = "Real estate services."
_MAINTENANCE = "Maintenance, repair and installation (except construction) services."


OFFICIAL_RULES: tuple[RateRule, ...] = (
    # 2523 — Schedule IV. CGST 14%, UTGST 14%, IGST 28%.
    _goods_rule(
        "sch4-18", "2523", _CEMENT, TaxType.CGST, 1400, "Schedule IV - 14%, S. No. 18"
    ),
    _goods_rule(
        "sch4-18", "2523", _CEMENT, TaxType.UTGST, 1400, "Schedule IV - 14%, S. No. 18"
    ),
    _goods_rule(
        "sch4-18", "2523", _CEMENT, TaxType.IGST, 2800, "Schedule IV - 28%, S. No. 18"
    ),
    # 4820, first of two. Schedule II. CGST 6%, UTGST 6%, IGST 12%.
    _goods_rule(
        "sch2-123",
        "4820",
        _EXERCISE_BOOKS,
        TaxType.CGST,
        600,
        "Schedule II - 6%, S. No. 123",
    ),
    _goods_rule(
        "sch2-123",
        "4820",
        _EXERCISE_BOOKS,
        TaxType.UTGST,
        600,
        "Schedule II - 6%, S. No. 123",
    ),
    _goods_rule(
        "sch2-123",
        "4820",
        _EXERCISE_BOOKS,
        TaxType.IGST,
        1200,
        "Schedule II - 12%, S. No. 123",
    ),
    # 4820, second of two. Schedule III. CGST 9%, UTGST 9%, IGST 18%.
    _goods_rule(
        "sch3-154",
        "4820",
        _REGISTERS,
        TaxType.CGST,
        900,
        "Schedule III - 9%, S. No. 154",
    ),
    _goods_rule(
        "sch3-154",
        "4820",
        _REGISTERS,
        TaxType.UTGST,
        900,
        "Schedule III - 9%, S. No. 154",
    ),
    _goods_rule(
        "sch3-154",
        "4820",
        _REGISTERS,
        TaxType.IGST,
        1800,
        "Schedule III - 18%, S. No. 154",
    ),
    # 9972 — real estate services. CGST 9%, UTGST 9%, IGST 18%.
    _service_rule("9972", _REAL_ESTATE, TaxType.CGST, 900, "Sl. No. 16, Heading 9972"),
    _service_rule("9972", _REAL_ESTATE, TaxType.UTGST, 900, "Sl. No. 16, Heading 9972"),
    _service_rule("9972", _REAL_ESTATE, TaxType.IGST, 1800, "Sl. No. 16, Heading 9972"),
    # 9987 — maintenance and repair. CGST 9%, UTGST 9%, IGST 18%.
    _service_rule("9987", _MAINTENANCE, TaxType.CGST, 900, "Sl. No. 25, Heading 9987"),
    _service_rule("9987", _MAINTENANCE, TaxType.UTGST, 900, "Sl. No. 25, Heading 9987"),
    _service_rule("9987", _MAINTENANCE, TaxType.IGST, 1800, "Sl. No. 25, Heading 9987"),
)


#: Sources that were asked for and did not arrive. Each one is a named gap, with
#: the exact error, and each one costs the engine a capability rather than being
#: papered over with a remembered number.
UNVERIFIED_SOURCES: tuple[UnverifiedSource, ...] = (
    UnverifiedSource(
        url="https://taxinformation.cbic.gov.in/view-pdf/1010211/ENG/Notifications",
        attempted_on=RETRIEVED,
        error="unable to verify the first certificate",
        would_have_supported=(
            "the current GST rate schedule. Rate notifications issued after 2022 "
            "are published only on this host, so the corpus cannot see any rate "
            "change after 2022 and refuses every supply dated later than "
            "2017-08-17 rather than returning a rate that may have moved."
        ),
    ),
    UnverifiedSource(
        url="https://taxinformation.cbic.gov.in/view-pdf/1010100/ENG/Notifications",
        attempted_on=RETRIEVED,
        error="unable to verify the first certificate",
        would_have_supported=(
            "notification 9/2025-Central Tax (Rate), which the CBIC listing pages "
            "record as superseding notification 1/2017-Central Tax (Rate)"
        ),
    ),
    UnverifiedSource(
        url=(
            "https://www.cbic.gov.in/resources/htdocs-cbec/gst/"
            "notfctn-9-2025-cgst-rate-english.pdf"
        ),
        attempted_on=RETRIEVED,
        error="HTTP 500 Internal Server Error",
        would_have_supported=(
            "notification 9/2025-Central Tax (Rate) from the main CBIC host"
        ),
    ),
    UnverifiedSource(
        url=(
            "https://www.cbic.gov.in/resources/htdocs-cbec/gst/"
            "notfctn-1-2017-cgst-rate-english.pdf"
        ),
        attempted_on=RETRIEVED,
        error="HTTP 500 Internal Server Error",
        would_have_supported=(
            "a second independent copy of notification 1/2017-Central Tax (Rate). "
            "The cbic-gst.gov.in copy was retrieved instead and is what the rules "
            "above cite."
        ),
    ),
    UnverifiedSource(
        url="https://cbic-gst.gov.in/pdf/IGST-Act-Updated-30092020.pdf",
        attempted_on=RETRIEVED,
        error="HTTP 404 Not Found",
        would_have_supported=(
            "sections 7, 8, 10 and 12 of the IGST Act, 2017 — the statutory "
            "definition of inter-State and intra-State supply, and the derivation "
            "of the place of supply from the nature of the transaction. Without "
            "it the engine requires the place of supply to be STATED and never "
            "derives it; see accountant/rules/place_of_supply.py."
        ),
    ),
    UnverifiedSource(
        url="https://cbic-gst.gov.in/hindi/igst-act.html",
        attempted_on=RETRIEVED,
        error="HTTP 404 Not Found",
        would_have_supported="the same IGST Act sections, from the CBIC Acts index",
    ),
    UnverifiedSource(
        url="STATE GOVERNMENT SGST RATE NOTIFICATIONS — no CBIC source exists",
        attempted_on=RETRIEVED,
        error=(
            "not attempted: an SGST rate is notified by a State Government, not by "
            "CBIC or the Income Tax Department, so it falls outside the authority "
            "hierarchy fixed by owner decision Q1 = A"
        ),
        would_have_supported=(
            "the SGST half of an intra-State supply made in a State. The corpus "
            "therefore carries no SGST rate at all, and an intra-State supply in a "
            "State comes back UNCLEAR. Intra-State supplies in a Union Territory "
            "are computable, because UTGST is notified by the Central Government "
            "and 1/2017-Union Territory Tax (Rate) is a CBIC document."
        ),
    ),
    UnverifiedSource(
        url=(
            "https://courier.cbic.gov.in/ECCS/advisory/2025/"
            "NOTIFICATION%20NO.%209_2025-INTEGRATED%20TAX%20(RATE)%20-1759486719.pdf"
        ),
        attempted_on=RETRIEVED,
        error=(
            "retrieved, then REJECTED: the file is served from a cbic.gov.in host "
            "but its pages are headed 'The Institute of Chartered Accountants of "
            "India — GST & Indirect Taxes Committee' and the document is marked "
            "'[UPDATED] [As corrected by corrigendum, dated 18-9-2025]'. It is a "
            "third-party consolidation of the notification, not the notification "
            "as issued, so it is not rank-1 authority and no rate was taken from it."
        ),
        would_have_supported="the post-2025 IGST rate schedule",
    ),
)


def official_corpus() -> RuleCorpus:
    """The production corpus. Built the same way in tests, scripts and the app."""
    return RuleCorpus.build(OFFICIAL_RULES, unverified=UNVERIFIED_SOURCES)
