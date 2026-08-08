"""Child 1 - the seeded synthetic book.

A fictional building-materials trader in Nagpur, generated from a seed. The
generator is a pure function of (seed, months): the same seed produces
byte-identical output, which is the only reason a score measured on this book
means anything at all.

Money is integer paise everywhere. Seasonality and GST are integer basis points
divided with `//`. There is no float in this package, in any field, at any step.

Randomness comes from one explicitly seeded `random.Random` instance. The
module-level `random` global is never touched, and nothing on the output path
iterates a set, a frozenset or an unordered mapping - either would make the
bytes depend on the interpreter's hash seed rather than on ours.

What this book is NOT: real. It is a fixture with known answers. GST presence
per account is a property of this fictional business, chosen so that every
error type the detectors know has somewhere to land.
"""

from __future__ import annotations

import calendar
import datetime
import random
from dataclasses import dataclass, replace

from accountant.schema import Voucher

COMPANY = "Nagpal Hardware & Timber"

# The first day of an Indian financial year, so month 0 is April.
START = datetime.date(2024, 4, 1)

# Acceptance criterion: at least 12 simulated months. Enforced, not hoped for.
MIN_MONTHS = 12
DEFAULT_MONTHS = 18

ID_PREFIX = "NHT-"

CASH = "Cash"
BANK = "Bank"

# Accounts this business actually posts to.
POSTED_ACCOUNTS: tuple[str, ...] = (
    "Advertising",
    "Bank Charges",
    "Electricity",
    "Freight & Cartage",
    "Insurance",
    "Printing & Stationery",
    "Professional Fees",
    "Purchases",
    "Rent",
    "Repairs & Maintenance",
    "Salaries",
    "Telephone & Internet",
)

# In the chart, never posted to in a clean book. This is what `first_use` needs:
# an account that genuinely has no history in this company.
UNPOSTED_ACCOUNTS: tuple[str, ...] = (
    "Capital Work in Progress",
    "Loan to Director",
    "Preliminary Expenses",
)

CHART: tuple[str, ...] = tuple(
    sorted((*POSTED_ACCOUNTS, *UNPOSTED_ACCOUNTS, CASH, BANK))
)

# Locale-independent on purpose. `%B` and `calendar.month_name` follow the
# machine's locale, and a book that reads differently on a different machine is
# not reproducible.
MONTH_NAMES: tuple[str, ...] = (
    "January",
    "February",
    "March",
    "April",
    "May",
    "June",
    "July",
    "August",
    "September",
    "October",
    "November",
    "December",
)

# Building materials in Nagpur: the trade dies in the monsoon and peaks after
# it. Basis points against a normal month, indexed by calendar month 1..12.
SEASON_BP: tuple[int, ...] = (
    11500,  # January
    12000,  # February
    13000,  # March
    11000,  # April
    10000,  # May
    7000,  # June
    6000,  # July
    6500,  # August
    8000,  # September
    13500,  # October
    14000,  # November
    12500,  # December
)

# Amounts wobble by up to 10% around their base, and never fall below Rs 100.
JITTER_BP = 1000
MIN_PAISE = 10_000


@dataclass(frozen=True)
class Vendor:
    """One counterparty, and the single account it is always posted to.

    `spellings` is the name noise. Every spelling must collapse to one key under
    `memory.index.normalise_vendor`, otherwise one vendor silently becomes two
    and `vendor_switch` can never fire.

    `gst_bp` is the GST rate in basis points, and 0 means this vendor's account
    never carries GST in this book.
    """

    spellings: tuple[str, ...]
    account: str
    credit_account: str
    gst_bp: int


@dataclass(frozen=True)
class Scheduled:
    """A dated item on a fixed cadence: monthly, quarterly or annual."""

    vendor: Vendor
    day: int
    base_paise: int
    every_months: int
    first_month: int
    narration: str


@dataclass(frozen=True)
class Irregular:
    """A stream of unscheduled purchases whose volume follows the season."""

    vendor: Vendor
    base_paise: int
    per_month: int
    narrations: tuple[str, ...]


@dataclass(frozen=True)
class Book:
    """A generated book and everything needed to reproduce it."""

    company: str
    chart: tuple[str, ...]
    vouchers: tuple[Voucher, ...]
    seed: int
    months: int


KOHLI = Vendor(("Kohli Estates", "KOHLI ESTATES"), "Rent", BANK, 0)
PAYROLL = Vendor(("Staff Payroll",), "Salaries", BANK, 0)
MSEDCL = Vendor(
    (
        "Maharashtra State Electricity Distribution",
        "MAHARASHTRA STATE ELECTRICITY DISTRIBUTION",
        "Maharashtra State Electricity Distribution Ltd",
    ),
    "Electricity",
    BANK,
    0,
)
BHARAT = Vendor(
    ("Bharat Telecom Services", "BHARAT TELECOM SERVICES"),
    "Telephone & Internet",
    BANK,
    1800,
)
SBI = Vendor(("State Bank of India",), "Bank Charges", BANK, 1800)
DESHMUKH = Vendor(
    ("Deshmukh & Associates", "DESHMUKH & ASSOCIATES"),
    "Professional Fees",
    BANK,
    1800,
)
ASHIRWAD = Vendor(
    ("Ashirwad Printers", "ASHIRWAD PRINTERS"), "Printing & Stationery", CASH, 1200
)
UNITED = Vendor(("United India Insurance",), "Insurance", BANK, 0)

# Four spellings, and every one of them normalises to `sharma_traders`. This is
# the vendor that satisfies "at least one vendor under three or more spellings".
SHARMA = Vendor(
    (
        "Sharma Traders",
        "M/s Sharma Traders",
        "SHARMA TRADERS",
        "Sharma Traders Pvt Ltd",
    ),
    "Purchases",
    CASH,
    1800,
)
VERMA = Vendor(
    ("Verma Cement Depot", "VERMA CEMENT DEPOT", "M/s Verma Cement Depot"),
    "Purchases",
    BANK,
    1800,
)
GUPTA = Vendor(
    ("Gupta Hardware Mart", "Gupta Hardware Mart Pvt Ltd", "GUPTA HARDWARE MART"),
    "Purchases",
    CASH,
    1800,
)
BALAJI = Vendor(("Balaji Timber Works", "BALAJI TIMBER WORKS"), "Purchases", BANK, 1800)
RAVI = Vendor(
    ("Ravi Electricals", "RAVI ELECTRICALS"), "Repairs & Maintenance", CASH, 1800
)
ADMEDIA = Vendor(("Nagpur Ad Media",), "Advertising", BANK, 500)
DIESEL = Vendor(
    ("Nagpur Diesel & Freight", "NAGPUR DIESEL & FREIGHT"),
    "Freight & Cartage",
    CASH,
    500,
)

SCHEDULED: tuple[Scheduled, ...] = (
    Scheduled(PAYROLL, 1, 6_200_000, 1, 0, "Staff wages"),
    Scheduled(KOHLI, 5, 1_800_000, 1, 0, "Godown rent"),
    Scheduled(MSEDCL, 12, 740_000, 1, 0, "Electricity supply"),
    Scheduled(BHARAT, 18, 236_000, 1, 0, "Broadband and landline"),
    Scheduled(SBI, 28, 59_000, 1, 0, "Account maintenance charges"),
    Scheduled(DESHMUKH, 20, 1_500_000, 3, 0, "Quarterly return filing"),
    Scheduled(ASHIRWAD, 22, 480_000, 3, 1, "Invoice books and letterheads"),
    Scheduled(UNITED, 15, 2_250_000, 12, 2, "Godown fire policy"),
)

IRREGULAR: tuple[Irregular, ...] = (
    Irregular(SHARMA, 4_200_000, 3, ("Cement bags", "OPC cement", "Cement and sand")),
    Irregular(VERMA, 2_800_000, 2, ("PPC cement", "Cement bags")),
    Irregular(GUPTA, 950_000, 2, ("Fasteners and fittings", "Hand tools")),
    Irregular(BALAJI, 6_500_000, 1, ("Sal wood planks", "Plywood sheets")),
    Irregular(RAVI, 320_000, 1, ("Godown wiring repair", "Lighting replacement")),
    Irregular(ADMEDIA, 1_200_000, 1, ("Hoarding at Wardha Road", "Festive print ad")),
    Irregular(DIESEL, 560_000, 2, ("Tempo hire", "Diesel and cartage")),
)


def add_months(start: datetime.date, offset: int) -> tuple[int, int]:
    """Return (year, month) `offset` whole months after `start`."""
    index = (start.year * 12 + start.month - 1) + offset
    return index // 12, index % 12 + 1


def gst_of(amount_paise: int, gst_bp: int) -> int | None:
    """The GST component contained in a tax-inclusive total.

    Integer arithmetic only: `total * rate / (1 + rate)` in basis points.
    """
    if gst_bp == 0:
        return None
    return amount_paise * gst_bp // (10000 + gst_bp)


def _round_to_rupee(paise: int) -> int:
    return paise // 100 * 100


def _jitter(rng: random.Random, base_paise: int) -> int:
    factor = rng.randrange(10000 - JITTER_BP, 10000 + JITTER_BP + 1)
    return max(_round_to_rupee(base_paise * factor // 10000), MIN_PAISE)


def _seasoned(base_paise: int, month: int) -> int:
    return base_paise * SEASON_BP[month - 1] // 10000


def contributions(voucher: Voucher) -> tuple[tuple[str, int], ...]:
    """What this voucher does to the trial balance: debit up, credit down.

    The same rule the Tally connector applies, stated here so a book can be
    checked without a connector.
    """
    return (
        (voucher.debit_account, voucher.amount_paise),
        (voucher.credit_account, -voucher.amount_paise),
    )


def balances(voucher: Voucher) -> bool:
    """True when this voucher's two sides cancel to exactly zero paise.

    GST is a component *inside* the total, not an extra line, so it must be
    smaller than the total rather than added to it.
    """
    if voucher.amount_paise <= 0:
        return False
    if voucher.debit_account == voucher.credit_account:
        return False
    gst = voucher.gst_paise
    if gst is not None and not 0 <= gst < voucher.amount_paise:
        return False
    return sum(paise for _, paise in contributions(voucher)) == 0


def _entry(
    rng: random.Random,
    vendor: Vendor,
    when: datetime.date,
    narration: str,
    amount_paise: int,
) -> Voucher:
    """One unidentified voucher. Ids are assigned after the whole book is sorted.

    `provenance` and `tally_id` stay None on every generated voucher. This book
    is the company's own posted history, not something we extracted or wrote,
    and an empty field cannot become a corruption marker later.
    """
    return Voucher(
        id="",
        date=when,
        party=rng.choice(vendor.spellings),
        narration=narration,
        debit_account=vendor.account,
        credit_account=vendor.credit_account,
        amount_paise=amount_paise,
        gst_paise=gst_of(amount_paise, vendor.gst_bp),
    )


def _scheduled_for_month(
    rng: random.Random, month_index: int, year: int, month: int
) -> list[Voucher]:
    out: list[Voucher] = []
    days = calendar.monthrange(year, month)[1]
    for item in SCHEDULED:
        if month_index < item.first_month:
            continue
        if (month_index - item.first_month) % item.every_months != 0:
            continue
        when = datetime.date(year, month, min(item.day, days))
        narration = f"{item.narration} for {MONTH_NAMES[month - 1]} {year}"
        amount = _jitter(rng, item.base_paise)
        out.append(_entry(rng, item.vendor, when, narration, amount))
    return out


def _irregular_for_month(rng: random.Random, year: int, month: int) -> list[Voucher]:
    out: list[Voucher] = []
    days = calendar.monthrange(year, month)[1]
    for item in IRREGULAR:
        count = max(_seasoned(item.per_month, month) + rng.randrange(-1, 2), 0)
        for _ in range(count):
            when = datetime.date(year, month, rng.randrange(1, days + 1))
            amount = _jitter(rng, _seasoned(item.base_paise, month))
            narration = rng.choice(item.narrations)
            out.append(_entry(rng, item.vendor, when, narration, amount))
    return out


def generate_book(
    *, seed: int, months: int = DEFAULT_MONTHS, start: datetime.date = START
) -> Book:
    """Generate one book. A pure function of its arguments.

    Raises ValueError below MIN_MONTHS, because "at least 12 simulated months"
    is an acceptance criterion and a generator that can quietly produce three is
    not one.
    """
    if months < MIN_MONTHS:
        raise ValueError(
            f"a book must cover at least {MIN_MONTHS} months; asked for {months}"
        )

    # A reproducible generator is the whole requirement here, and a
    # cryptographic one cannot be seeded to give byte-identical output. This
    # produces a test fixture, never a key, a token or an operation ID.
    rng = random.Random(seed)  # noqa: S311  # nosec B311
    entries: list[Voucher] = []
    for month_index in range(months):
        year, month = add_months(start, month_index)
        entries.extend(_scheduled_for_month(rng, month_index, year, month))
        entries.extend(_irregular_for_month(rng, year, month))

    # Stable sort: same date keeps emission order, so the sequence is a function
    # of the seed and nothing else.
    entries.sort(key=lambda v: v.date)
    vouchers = tuple(
        replace(v, id=f"{ID_PREFIX}{n:05d}") for n, v in enumerate(entries, start=1)
    )
    return Book(
        company=COMPANY, chart=CHART, vouchers=vouchers, seed=seed, months=months
    )
