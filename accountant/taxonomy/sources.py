"""Child 7 - the published documents this taxonomy was read out of.

Every error type in this package traces back to a paragraph of one of these
documents. Nothing here was reasoned from what an accounting error *ought* to
look like, because that reasoning is exactly what child #7 exists to check.

**A source with no URL or no retrieval date does not load.** The rule is the
same one `accountant/rules/` will carry in Phase 8, and it is enforced in
`__post_init__` rather than trusted: an uncited entry cannot be constructed, so
it cannot reach the coverage table.

`retrieved` is an ISO 8601 date string and is parsed on construction, so a
retrieval date that is not a date is refused at load time too.
"""

from __future__ import annotations

import datetime
from dataclasses import dataclass

# Every document below was fetched and read on this day.
RETRIEVED = "2026-08-08"


@dataclass(frozen=True)
class Source:
    """One published audit document.

    `url` and `retrieved` default to empty so that the refusal is reachable:
    the failure mode this class exists to prevent is an entry that simply
    forgot its citation, not one that tried to pass a malformed URL.
    """

    key: str
    publisher: str
    title: str
    url: str = ""
    retrieved: str = ""

    def __post_init__(self) -> None:
        if not self.url.strip():
            raise ValueError(
                f"source {self.key!r} has no URL; a taxonomy entry without a "
                f"citation does not load"
            )
        if not self.retrieved.strip():
            raise ValueError(
                f"source {self.key!r} has no retrieval date; a taxonomy entry "
                f"without a citation does not load"
            )
        # Refuses a retrieval date that is not a date, at load time.
        datetime.date.fromisoformat(self.retrieved)

    def retrieved_date(self) -> datetime.date:
        """The retrieval date as a date. Parsed already in `__post_init__`."""
        return datetime.date.fromisoformat(self.retrieved)


CAG_UNION_ACCOUNTS_2018_19 = Source(
    key="CAG-4-2020",
    publisher="Comptroller and Auditor General of India",
    title=(
        "Report No. 4 of 2020 (Financial Audit) - Union Government, "
        "Accounts of the Union Government, for the year 2018-19"
    ),
    url=(
        "https://cag.gov.in/webroot/uploads/download_audit_report/2020/"
        "Report%20No.%204%20of%202020_Eng-05f808ecd3a8165.55898472.pdf"
    ),
    retrieved=RETRIEVED,
)

CAG_UNION_ACCOUNTS_2023_24 = Source(
    key="CAG-PR-2025-08-12",
    publisher="Comptroller and Auditor General of India",
    title=(
        "Press Release, 12 August 2025 - CAG Audit Report on Accounts of the "
        "Union Government tabled in Parliament (FY 2023-24)"
    ),
    url=(
        "https://saiindia.gov.in/uploads/PressRelease/"
        "PR-Press-Release-on-report-no-16-in-English-0689b2628353901-78136985.pdf"
    ),
    retrieved=RETRIEVED,
)

CAG_GST_2019 = Source(
    key="CAG-11-2019-ch4",
    publisher="Comptroller and Auditor General of India",
    title=(
        "Report No. 11 of 2019 (Indirect Taxes - Goods and Services Tax), "
        "Chapter IV: Compliance Audit of GST"
    ),
    url=(
        "https://cag.gov.in/uploads/download_audit_report/2019/"
        "Chapter_4_Compliance_Audit_of_GST_of_Report_No_11_of_2019_"
        "Compliance_Audit_of_Union_Government_Department_of_Revenue_"
        "Indirect_Taxes_Goods_and_Services_Tax.pdf"
    ),
    retrieved=RETRIEVED,
)

ICAI_FRRB_IND_AS_VOL_II = Source(
    key="ICAI-FRRB-IndAS-II",
    publisher="The Institute of Chartered Accountants of India",
    title=(
        "Study on Compliance of Financial Reporting Requirements "
        "(Ind AS Framework), Volume II, Financial Reporting Review Board, "
        "October 2022"
    ),
    url="http://frrb.icai.org/wp-content/uploads/2022/11/Vol-II.pdf",
    retrieved=RETRIEVED,
)

NFRA_MSKA_2023 = Source(
    key="NFRA-132.2-2023-03",
    publisher="National Financial Reporting Authority",
    title=(
        "Inspection Report 2023 - M/s MSKA & Associates, "
        "Inspection Report No. 132.2-2023-03, 02 January 2025"
    ),
    url="https://nfra.gov.in/mska-associates-inspection-report-no-132-2-2023-03/",
    retrieved=RETRIEVED,
)

SOURCES: tuple[Source, ...] = (
    CAG_UNION_ACCOUNTS_2018_19,
    CAG_UNION_ACCOUNTS_2023_24,
    CAG_GST_2019,
    ICAI_FRRB_IND_AS_VOL_II,
    NFRA_MSKA_2023,
)
