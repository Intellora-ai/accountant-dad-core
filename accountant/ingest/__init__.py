"""Child 5 - public real-data ingest, UK central government.

Everything measured before this package was measured on a book this project
generated, against errors this project injected. That proves the detectors do
what they were written to do. It proves nothing about whether the product's
central idea works on a real ledger.

UK central government publishes transaction-level spend over 25,000 pounds:
monthly, free, no login, Open Government Licence, and with the **narration and
the account on the same row**. That pair is exactly what this product predicts,
recorded by the organisation that actually made the decision.

    Narrative      ->  Voucher.narration
    Expense Type   ->  Voucher.debit_account

WHY CENTRAL GOVERNMENT AND NOT COUNCILS
---------------------------------------
The Local Government Transparency Code 2015 mandates the concepts, not the
column names, across roughly 2,600 publishers. A real fetched Rochdale file
carries the live header typo `EFEFCTIVE`. That is an unbounded parsing problem
with no schema to hold onto. Central government is not uniform either - seven
departments produced seven header shapes for the same month - but the publisher
set is small and fixed, so the alias table in `spend.py` is a finite thing that
can be written down, cited and tested.

WHY INDIA IS NOT USED
---------------------
It publishes nothing at this level. data.gov.in returns no transaction-level
voucher or ledger datasets, and the Union Budget XLSX bottoms out at the
four-digit major head. There is no Indian equivalent to load.

WHAT THIS PACKAGE IS FOR
------------------------
    load        real rows into the frozen `Voucher` type, stating the row
                count and the source URL, and counting every row it could not
                read rather than dropping it
    score       hand a department to `accountant/score` unmodified
    compare     answer the design-validity question: does an account mapping
                learned at one organisation transfer to another?

MONEY
-----
UK amounts are pounds and are carried as integer **pence**, never a float. The
one place the name changes is `spend.as_voucher`, which puts pence into
`Voucher.amount_paise` and says so in its own docstring, because that field is
named for the Indian product and this data is not Indian.

NETWORK
-------
`fetch.py` is the only module that could open a connection, it is called
separately, and every test in this repository reads committed bytes instead.
"""

from __future__ import annotations

from accountant.ingest.crossorg import (
    MIN_PAIRS,
    Accuracy,
    CrossOrgReport,
    PairResult,
    Split,
    compare,
    measure,
    split,
)
from accountant.ingest.fetch import fetch_source, read_url
from accountant.ingest.report import render_cross, render_load
from accountant.ingest.sources import (
    ALL_SOURCES,
    COMPARABLE_SOURCES,
    LICENCE,
    LICENCE_URL,
    MONTH,
    RETRIEVED,
    Source,
)
from accountant.ingest.spend import (
    CREDIT_NOT_IN_SOURCE,
    MINOR_UNIT,
    Column,
    Columns,
    LoadResult,
    RejectedRow,
    SpendRow,
    as_score_book,
    as_voucher,
    load_all,
    load_bytes,
    load_source,
    vouchers,
)

__all__ = [
    "ALL_SOURCES",
    "COMPARABLE_SOURCES",
    "CREDIT_NOT_IN_SOURCE",
    "LICENCE",
    "LICENCE_URL",
    "MINOR_UNIT",
    "MIN_PAIRS",
    "MONTH",
    "RETRIEVED",
    "Accuracy",
    "Column",
    "Columns",
    "CrossOrgReport",
    "LoadResult",
    "PairResult",
    "RejectedRow",
    "Source",
    "SpendRow",
    "Split",
    "as_score_book",
    "as_voucher",
    "compare",
    "fetch_source",
    "load_all",
    "load_bytes",
    "load_source",
    "measure",
    "read_url",
    "render_cross",
    "render_load",
    "split",
    "vouchers",
]
