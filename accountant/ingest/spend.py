"""Child 5 - the loader. Real UK central-government spend, into `Voucher`.

Everything measured so far was measured on a book this project wrote itself,
which teaches nothing about whether the idea works. UK central government
publishes transaction-level spend over 25,000 pounds, monthly, free, with no
login, and - the part that matters - it puts a **narration** and an **account**
on the same row:

    Narrative      "Broadband and landline"          what it was for
    Expense Type   "IT - Service Contracts"          which account it went to

That pair is exactly what this product predicts, written down by the
organisation that actually made the decision. It is the cheapest real test of
the whole premise that exists.

WHAT THE DATA IS NOT
--------------------
Uniform. Seven departments published the same month under the same statutory
duty and produced seven different header shapes, two text encodings and two
date formats. The narration column alone is called `Narrative`, `Description`,
`Item Text`, `Publication Description`, `Invoice Cost Centre Description` and
`PO Catergory Description ` - that spelling and that trailing space are both in
the published file. So this loader does not assume column names; it resolves
them through the alias table below, refuses anything it does not recognise
instead of guessing, and reports what it resolved.

MONEY
-----
UK amounts are **pounds**. They are converted to integer **pence**, never a
float. `pence` is the name used throughout this package. The one place that
name changes is `as_voucher`, and it says so there, loudly, because
`Voucher.amount_paise` is named for the Indian product and this data is not
Indian.

MALFORMED ROWS
--------------
Counted, named and reported. Never dropped. A loader that quietly discards the
rows it cannot read will report a clean load of a broken file, and every number
downstream inherits the lie.

NETWORK
-------
None. This module reads bytes. `accountant/ingest/fetch.py` is the only place
that could reach the network, it is called separately, and no test calls it
with a live opener.
"""

from __future__ import annotations

import csv
import datetime
import io
import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from accountant.extract.adapter import NOT_FOUND
from accountant.ingest.sources import Source
from accountant.schema import Voucher
from accountant.score.book import Book, GroundTruth

# The unit every amount in this package is carried in. Stated as a value so a
# test can assert it rather than trusting a comment.
MINOR_UNIT = "pence"
PENCE_PER_POUND = 100

# Tried in this order. utf-8-sig first because it also accepts plain UTF-8;
# cp1252 second because the pound sign in several published files is the single
# byte 0xA3, which is not valid UTF-8 at all.
ENCODINGS: tuple[str, ...] = ("utf-8-sig", "cp1252")

# Published headers, normalised, that supply each field this loader needs.
# Every entry was observed in a real file - none is speculative. An unlisted
# header is a refusal, not a guess.
NARRATION_HEADERS: tuple[str, ...] = (
    "narrative",  # MHCLG
    "description",  # DHSC, DBT
    "item text",  # DfT
    "publication description",  # HM Treasury
    "invoice cost centre description",  # DWP
    "po catergory description",  # Defra - the typo is in the published file
)
ACCOUNT_HEADERS: tuple[str, ...] = (
    "expense type",  # MHCLG, DfT, Defra, DBT, and "Expense type" at DHSC/HMT
    "invoice account description",  # DWP
)
PARTY_HEADERS: tuple[str, ...] = (
    "supplier",  # everyone except DWP; Defra publishes it as "Supplier "
    "supplier name",  # DWP
)
DATE_HEADERS: tuple[str, ...] = (
    "date of payment",  # MHCLG, DBT
    "invoice paid date",  # DWP
    "date",  # DHSC, DfT, Defra, HM Treasury
)
AMOUNT_HEADERS: tuple[str, ...] = (
    "amount in sterling",  # MHCLG
    "transparency invoice amount",  # DWP
    "amount",  # DHSC, Defra, DBT, HM Treasury
    "£",  # DfT publishes the column as " £ "
)

# Why a row was not loaded. One row can carry several.
EMPTY_NARRATION = "narration is empty"
EMPTY_ACCOUNT = "account is empty"
EMPTY_PARTY = "party is empty"
UNREADABLE_DATE = "date is unreadable"
UNREADABLE_AMOUNT = "amount is unreadable"
SHORT_ROW = "row has fewer columns than the header"

# The credit side is not published. UK spend files carry one side of the entry
# only, so this is recorded as an absence rather than invented as an account.
CREDIT_NOT_IN_SOURCE = "not in source"

# Real data is not generated, so there is no seed and no injected error rate.
# `accountant/score` requires both, so the absence is recorded as zero and the
# ingest report states the source URL and row count in their place.
NO_SEED = 0
NO_INJECTED_ERRORS = 0

# Locale-independent on purpose. `%b` follows the machine's locale, and a file
# that parses on one machine and not another is not a loader.
MONTH_ABBREVIATIONS: tuple[str, ...] = (
    "jan",
    "feb",
    "mar",
    "apr",
    "may",
    "jun",
    "jul",
    "aug",
    "sep",
    "oct",
    "nov",
    "dec",
)

# The published files write two-digit years. They are read as 20yy, which is
# the only reading that makes sense for a monthly return, and is stated rather
# than hidden.
CENTURY = 2000

_SLASH_DATE = re.compile(r"(\d{1,2})/(\d{1,2})/(\d{4})")
_ABBREVIATED_DATE = re.compile(r"(\d{1,2})-([A-Za-z]{3})-(\d{2})")
_MONEY = re.compile(r"([+-]?)(\d+)(?:\.(\d{1,2}))?")
_SPACE = re.compile(r"\s+")

# U+00A0. It appears inside published header names and inside field values, and
# it is not caught by str.strip() on every path, so it is replaced explicitly.
# Written as an escape because a literal one is invisible in a diff.
NBSP = "\u00a0"


def clean(text: str) -> str:
    """Collapse published whitespace, including the non-breaking kind."""
    return _SPACE.sub(" ", text.replace(NBSP, " ")).strip()


def normalise_header(text: str) -> str:
    """A published header reduced to the form the alias table is written in.

    Case and whitespace only. The pound sign is deliberately kept, because one
    department publishes its amount column as the single character " £ " and
    stripping punctuation would leave nothing to match on.
    """
    return clean(text).casefold()


def decode(raw: bytes, *, encodings: Sequence[str] = ENCODINGS) -> tuple[str, str]:
    """Text and the encoding that produced it, or a refusal naming what failed."""
    for encoding in encodings:
        try:
            return raw.decode(encoding), encoding
        except UnicodeDecodeError:
            continue
    raise ValueError(f"could not decode the file as any of: {', '.join(encodings)}")


def parse_date(text: str) -> datetime.date | None:
    """`03/11/2025` or `04-Nov-25`. Both are published; anything else is None."""
    value = clean(text)

    slashed = _SLASH_DATE.fullmatch(value)
    if slashed is not None:
        return _date_or_none(int(slashed[3]), int(slashed[2]), int(slashed[1]))

    abbreviated = _ABBREVIATED_DATE.fullmatch(value)
    if abbreviated is None:
        return None
    month_name = abbreviated[2].casefold()
    if month_name not in MONTH_ABBREVIATIONS:
        return None
    month = MONTH_ABBREVIATIONS.index(month_name) + 1
    return _date_or_none(CENTURY + int(abbreviated[3]), month, int(abbreviated[1]))


def _date_or_none(year: int, month: int, day: int) -> datetime.date | None:
    """A date that exists, or None. `31/02/2025` is a typo, not a date."""
    try:
        return datetime.date(year, month, day)
    except ValueError:
        return None


def parse_pence(text: str) -> int | None:
    """A published sterling amount as integer pence, or None if unreadable.

    Handles the shapes that actually appear: `£105,400.00`, `-£82,354.00`,
    ` £534,722,000.00 `, `37,109.87`. Never a float - the decimal digits are
    read as digits and added, so nothing rounds.

    Negative amounts are real. They are refunds and credit notes, and they are
    kept with their sign rather than dropped or made positive.
    """
    value = clean(text).replace(",", "").replace("£", "").replace(" ", "")
    matched = _MONEY.fullmatch(value)
    if matched is None:
        return None
    pence = int(matched[2]) * PENCE_PER_POUND + int((matched[3] or "0").ljust(2, "0"))
    return -pence if matched[1] == "-" else pence


@dataclass(frozen=True)
class Column:
    """One field this loader needs, and the published header that supplied it."""

    field: str
    header: str
    index: int


@dataclass(frozen=True)
class Columns:
    """The whole resolution, so a report can show it and a test can check it."""

    date: Column
    party: Column
    narration: Column
    account: Column
    amount: Column

    @property
    def resolved(self) -> tuple[Column, ...]:
        return (self.date, self.party, self.narration, self.account, self.amount)

    @property
    def last_index(self) -> int:
        return max(c.index for c in self.resolved)


def _resolve_one(
    positions: dict[str, tuple[str, int]], field: str, aliases: Sequence[str]
) -> Column:
    """The single published header that supplies `field`, or a refusal.

    Two accepted headers in one file is ambiguity, not a preference to be
    resolved silently - the file is telling us the alias table is wrong.
    """
    found = [(alias, positions[alias]) for alias in aliases if alias in positions]
    if not found:
        raise ValueError(
            f"no column supplies {field!r}; accepted headers are "
            f"{', '.join(repr(a) for a in aliases)}"
        )
    if len(found) > 1:
        raise ValueError(
            f"{len(found)} columns could supply {field!r}: "
            f"{', '.join(repr(positions[a][0]) for a, _ in found)}"
        )
    header, index = found[0][1]
    return Column(field=field, header=header, index=index)


def resolve_columns(header: Sequence[str]) -> Columns:
    """Map published headers onto the five fields a voucher needs."""
    positions: dict[str, list[tuple[str, int]]] = {}
    for index, name in enumerate(header):
        positions.setdefault(normalise_header(name), []).append((name, index))

    repeated = sorted(key for key, seen in positions.items() if len(seen) > 1)
    if repeated:
        raise ValueError(
            f"the header repeats a column name, so a row cannot be read "
            f"unambiguously: {', '.join(repr(r) for r in repeated)}"
        )

    first = {key: seen[0] for key, seen in positions.items()}
    return Columns(
        date=_resolve_one(first, "date", DATE_HEADERS),
        party=_resolve_one(first, "party", PARTY_HEADERS),
        narration=_resolve_one(first, "narration", NARRATION_HEADERS),
        account=_resolve_one(first, "account", ACCOUNT_HEADERS),
        amount=_resolve_one(first, "amount", AMOUNT_HEADERS),
    )


@dataclass(frozen=True)
class SpendRow:
    """One published payment, read. `amount_pence` is pence, and only pence."""

    row_number: int
    date: datetime.date
    party: str
    narration: str
    account: str
    amount_pence: int


@dataclass(frozen=True)
class RejectedRow:
    """One published row that could not be read, and every reason why."""

    row_number: int
    reasons: tuple[str, ...]

    def __post_init__(self) -> None:
        if not self.reasons:
            raise ValueError(f"row {self.row_number} was rejected without a reason")


@dataclass(frozen=True)
class LoadResult:
    """Everything one load produced, including what it could not read.

    `source_url` and `row_count` travel with the data rather than sitting in a
    log line, so no number derived from this can be quoted without them.
    """

    code: str
    department: str
    source_url: str
    encoding: str
    header: tuple[str, ...]
    columns: Columns
    rows: tuple[SpendRow, ...]
    rejected: tuple[RejectedRow, ...]

    @property
    def row_count(self) -> int:
        """Data rows found in the file. Loaded plus rejected, always."""
        return len(self.rows) + len(self.rejected)

    @property
    def loaded_count(self) -> int:
        return len(self.rows)

    @property
    def rejected_count(self) -> int:
        return len(self.rejected)

    @property
    def refund_count(self) -> int:
        """Rows carrying a negative amount. Real, kept, and stated separately."""
        return sum(1 for r in self.rows if r.amount_pence < 0)

    @property
    def statement(self) -> str:
        """Row count and source URL, in one line, always together."""
        return (
            f"{self.row_count} rows read from {self.source_url} "
            f"({self.loaded_count} loaded, {self.rejected_count} rejected)"
        )

    def rejected_by_reason(self) -> tuple[tuple[str, int], ...]:
        """How many rows carried each reason, ordered by reason."""
        counts: dict[str, int] = {}
        for row in self.rejected:
            for reason in row.reasons:
                counts[reason] = counts.get(reason, 0) + 1
        return tuple((reason, counts[reason]) for reason in sorted(counts))

    @property
    def accounts(self) -> tuple[str, ...]:
        """This organisation's chart, as published, plus the missing credit side.

        `CREDIT_NOT_IN_SOURCE` is in here because every loaded voucher uses it,
        and a chart that omits an account the book uses would make every entry
        fail `accounts_exist` for a reason that has nothing to do with the data.
        """
        return tuple(sorted({r.account for r in self.rows} | {CREDIT_NOT_IN_SOURCE}))


def why_rejected(
    narration: str,
    account: str,
    party: str,
    when: datetime.date | None,
    pence: int | None,
) -> tuple[str, ...]:
    """Every reason one row could not be read. All of them, not the first."""
    found: list[str] = []
    if not narration:
        found.append(EMPTY_NARRATION)
    if not account:
        found.append(EMPTY_ACCOUNT)
    if not party:
        found.append(EMPTY_PARTY)
    if when is None:
        found.append(UNREADABLE_DATE)
    if pence is None:
        found.append(UNREADABLE_AMOUNT)
    return tuple(found)


def read_row(
    number: int, cells: Sequence[str], columns: Columns
) -> SpendRow | RejectedRow:
    """One published row: read, or refused with every reason stated."""
    if len(cells) <= columns.last_index:
        return RejectedRow(row_number=number, reasons=(SHORT_ROW,))

    narration = clean(cells[columns.narration.index])
    account = clean(cells[columns.account.index])
    party = clean(cells[columns.party.index])
    when = parse_date(cells[columns.date.index])
    pence = parse_pence(cells[columns.amount.index])

    if when is not None and pence is not None and narration and account and party:
        return SpendRow(
            row_number=number,
            date=when,
            party=party,
            narration=narration,
            account=account,
            amount_pence=pence,
        )

    return RejectedRow(
        row_number=number,
        reasons=why_rejected(narration, account, party, when, pence),
    )


def load_bytes(
    raw: bytes, *, code: str, department: str, source_url: str
) -> LoadResult:
    """Read one published spend file. States what it read and what it could not."""
    text, encoding = decode(raw)
    reader = csv.reader(io.StringIO(text, newline=""))
    # An empty file has no header row at all, and that is a refusal from
    # resolve_columns rather than a silent load of nothing.
    no_header: list[str] = []
    header = tuple(next(reader, no_header))
    columns = resolve_columns(header)

    rows: list[SpendRow] = []
    rejected: list[RejectedRow] = []
    for number, cells in enumerate(reader, start=1):
        read = read_row(number, cells, columns)
        if isinstance(read, SpendRow):
            rows.append(read)
        else:
            rejected.append(read)

    return LoadResult(
        code=code,
        department=department,
        source_url=source_url,
        encoding=encoding,
        header=header,
        columns=columns,
        rows=tuple(rows),
        rejected=tuple(rejected),
    )


def load_source(source: Source) -> LoadResult:
    """Load the committed slice of one department's published file.

    Reads bytes from disk. Nothing here reaches the network - see `fetch.py`.
    """
    return load_bytes(
        source.fixture_path.read_bytes(),
        code=source.code,
        department=source.department,
        source_url=source.url,
    )


def as_voucher(row: SpendRow, *, code: str, source_url: str) -> Voucher:
    """One published payment as a `Voucher`.

    **The money field is named for a different currency.** `Voucher.amount_paise`
    is named for the Indian product. This row is UK sterling, so the integer put
    into that field is **pence**, not paise. It is not converted, not
    rate-adjusted and not pretended to be rupees; only the field it lands in is
    misnamed for this dataset, and this sentence is the only reason that is
    acceptable.

    `Narrative` becomes the narration. `Expense Type` becomes the debit account.
    The credit side and the VAT split are not published, so both are recorded as
    absent rather than guessed - a field with no source is a hallucination by
    definition.
    """
    return Voucher(
        id=f"{code}-{row.row_number:05d}",
        date=row.date,
        party=row.party,
        narration=row.narration,
        debit_account=row.account,
        credit_account=CREDIT_NOT_IN_SOURCE,
        amount_paise=row.amount_pence,
        gst_paise=None,
        provenance={
            "date": source_url,
            "party": source_url,
            "narration": source_url,
            "debit_account": source_url,
            "amount_paise": f"{source_url} ({MINOR_UNIT})",
            "credit_account": NOT_FOUND,
            "gst_paise": NOT_FOUND,
        },
    )


def vouchers(result: LoadResult) -> tuple[Voucher, ...]:
    """Every loaded row as a `Voucher`, in published order."""
    return tuple(
        as_voucher(row, code=result.code, source_url=result.source_url)
        for row in result.rows
    )


def split_point(count: int) -> int:
    """Where a department's own rows divide into history and entries to score.

    The earlier half is the history an index learns from; the later half is what
    gets predicted. Published order, no shuffling, so the split is the same on
    every machine and on every run.
    """
    return count // 2


def as_score_book(result: LoadResult) -> Book:
    """One department, in the shape `accountant/score` already consumes.

    `accountant/score` is not modified and not imported the other way round: it
    declares the narrow input contract in `score/book.py`, and this function
    fills it in. There is no answer key, because nobody injected errors into a
    real government ledger, so the ground truth is empty and the harness will
    correctly report N3 as FAIL on absent evidence rather than a vacuous pass.
    """
    stream = vouchers(result)
    cut = split_point(len(stream))
    return Book(
        company=result.department,
        accounts=result.accounts,
        history=stream[:cut],
        entries=stream[cut:],
        truth=GroundTruth(
            seed=NO_SEED, error_rate_per_10_000=NO_INJECTED_ERRORS, injected=()
        ),
    )


def load_all(sources: Iterable[Source]) -> tuple[LoadResult, ...]:
    """Load several departments, in the order given."""
    return tuple(load_source(s) for s in sources)
