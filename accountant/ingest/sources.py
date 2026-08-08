"""Child 5 - the sources, cited.

Every row this package loads came from a named file at a named URL, published
by a named UK central-government department under a named licence. That is
recorded here rather than in a comment, so a number produced downstream can
always be traced back to the bytes it came from.

**Central government only.** UK councils publish the same *concepts* under the
Local Government Transparency Code 2015, but the Code mandates concepts, not
column names, across roughly 2,600 publishers with no schema stability - a
fetched Rochdale file carries the live header typo `EFEFCTIVE`. That is an
unbounded parsing problem and it is deliberately out of scope.

Central government is not uniform either, and this table is the evidence:
seven departments, seven different header shapes, two text encodings and two
date formats for the same month of the same year. The difference is that the
publisher set is small and fixed, so an alias table is a finite thing that can
be written down and tested. `accountant/ingest/spend.py` holds that table.

`published_rows` is what the file contained when it was retrieved. It cannot be
re-checked without the network and is therefore marked as an observation, not a
test. `fixture_rows` IS checked by a test against the committed bytes.
"""

from __future__ import annotations

import datetime
from dataclasses import dataclass
from pathlib import Path

# https://www.nationalarchives.gov.uk/doc/open-government-licence/version/3/
LICENCE = "Open Government Licence v3.0"
LICENCE_URL = (
    "https://www.nationalarchives.gov.uk/doc/open-government-licence/version/3/"
)

# The day every URL below was fetched and every `published_rows` was counted.
RETRIEVED = datetime.date(2026, 8, 8)

# The one month every department below published, so a cross-department
# comparison is not quietly comparing different periods.
MONTH = "2025-11"

FIXTURE_DIR = Path(__file__).parent / "fixtures"


@dataclass(frozen=True)
class Source:
    """One published monthly spend file, and the slice of it committed here.

    `code` is the id prefix given to every voucher loaded from this department,
    so a voucher id says which organisation it came from without a lookup.
    """

    code: str
    department: str
    url: str
    encoding: str
    published_rows: int
    fixture_name: str
    fixture_rows: int

    @property
    def fixture_path(self) -> Path:
        return FIXTURE_DIR / self.fixture_name


MHCLG = Source(
    code="MHCLG",
    department="Ministry of Housing, Communities and Local Government",
    url=(
        "https://assets.publishing.service.gov.uk/media/695296452054b690f7cd3d13/"
        "MHCLG_Spend_over__25k_November_2025.csv"
    ),
    encoding="cp1252",
    published_rows=2595,
    fixture_name="mhclg-2025-11.csv",
    fixture_rows=57,
)

DHSC = Source(
    code="DHSC",
    department="Department of Health and Social Care",
    url=(
        "https://assets.publishing.service.gov.uk/media/6a1ffb3559fb7a60f827f6d9/"
        "DHSC-spending-over-25000-november-2025-revised.csv"
    ),
    encoding="utf-8-sig",
    published_rows=914,
    fixture_name="dhsc-2025-11.csv",
    fixture_rows=41,
)

DFT = Source(
    code="DFT",
    department="Department for Transport",
    url=(
        "https://assets.publishing.service.gov.uk/media/698da3d57da91680ad7f42e8/"
        "dft-spending-over-25000-november-2025.csv"
    ),
    encoding="cp1252",
    published_rows=2446,
    fixture_name="dft-2025-11.csv",
    fixture_rows=47,
)

DWP = Source(
    code="DWP",
    department="Department for Work and Pensions",
    url=(
        "https://assets.publishing.service.gov.uk/media/6984b79f2df808759a7bd74f/"
        "transparency-for-publication-november-2025.csv"
    ),
    encoding="cp1252",
    published_rows=8804,
    fixture_name="dwp-2025-11.csv",
    fixture_rows=54,
)

DEFRA = Source(
    code="DEFRA",
    department="Department for Environment, Food and Rural Affairs",
    url=(
        "https://assets.publishing.service.gov.uk/media/697a075233bc3750e7652fcd/"
        "November_Over__25K_Transparency.csv"
    ),
    encoding="cp1252",
    published_rows=953,
    fixture_name="defra-2025-11.csv",
    fixture_rows=38,
)

HMT = Source(
    code="HMT",
    department="HM Treasury",
    url=(
        "https://assets.publishing.service.gov.uk/media/697a080f005d288bf850dea7/"
        "HMT_spending_over_25_000_for_Nov_25.csv"
    ),
    encoding="cp1252",
    published_rows=100,
    fixture_name="hmt-2025-11.csv",
    fixture_rows=46,
)

# DBT publishes the narration column and leaves every cell in it empty - all 199
# rows of the real file, not just the slice committed here. It is kept precisely
# because it is real: it is what a department looks like when the column exists
# and the data does not, and it is the reason malformed rows are counted and
# named rather than dropped.
DBT = Source(
    code="DBT",
    department="Department for Business and Trade",
    url=(
        "https://assets.publishing.service.gov.uk/media/69a0223d532c9ad91ebbcd39/"
        "dbt-spending-over-25k-november-2025.csv"
    ),
    encoding="utf-8-sig",
    published_rows=199,
    fixture_name="dbt-2025-11.csv",
    fixture_rows=28,
)

# Every source, in a fixed order so a report never reorders itself.
ALL_SOURCES: tuple[Source, ...] = (MHCLG, DHSC, DFT, DWP, DEFRA, HMT, DBT)

# The departments that publish a usable narration, and therefore the ones a
# cross-organisation comparison can run over. DBT is excluded by measurement,
# not by opinion: every one of its rows fails the empty-narration rule.
COMPARABLE_SOURCES: tuple[Source, ...] = (MHCLG, DHSC, DFT, DWP, DEFRA, HMT)
