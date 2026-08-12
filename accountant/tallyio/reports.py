"""Reading the books back out of TallyPrime.

WHY A REPORT IS PART OF THE WRITE PATH, NOT A SEPARATE FEATURE
---------------------------------------------------------------
`vouchers.create_purchase_voucher` calls `ledger_vouchers` to confirm the entry
it just posted actually exists. That is why this module is in the MVP rather
than after it: without a read-back, "success" means "TallyPrime did not
complain", and this repository has already measured a write the gateway
accepted and did not keep.

So the report is not reporting. It is the evidence.

THE FIRST VERSION OF THIS FILE WAS WRONG, AND HOW IT WAS WRONG MATTERS
-----------------------------------------------------------------------
Measured 2026-08-12 against the live TallyPrime. It asked for reports the
obvious way:

    <TALLYREQUEST>Export</TALLYREQUEST><TYPE>Data</TYPE><ID>Day Book</ID>

TallyPrime answered `STATUS 1` and `<DATA>  </DATA>`. Empty. Not an error, not
a refusal - a successful response containing nothing. Meanwhile a Purchase
voucher for Rs 1,000 was sitting in the company, and `real.read_vouchers` could
see it perfectly well.

Two consequences, and the second is the serious one:

    1. every report came back empty
    2. `create_purchase_voucher` uses this to confirm its OWN write, so it
       reported `confirmed=False` for a voucher that had posted correctly

A false negative on a write is worse than an empty read. It tells a caller to
retry something that already happened, and in bookkeeping a retry is a
duplicated entry in a customer's books.

So this file no longer builds its own request. It asks
`real.build_voucher_list_request` - an Export COLLECTION, which is the shape
TallyPrime actually answers - and parses with `real.parse_vouchers`, the parser
the existing suite already holds down. Date and ledger filtering happens here,
on the parsed result.

WHY THE FILTERING IS IN PYTHON, WHICH IS A REAL LIMITATION
------------------------------------------------------------
This fetches the company's vouchers and filters them here. For a company with a
hundred thousand entries that is the wrong shape, and the fix is pushing
`SVFROMDATE`/`SVTODATE` into the request once that is measured against a real
book of that size. It is right for today because it reuses a parser that is
known to work against this gateway, and a correct slow answer beats a fast
empty one.

`skipped` IS REPORTED, NEVER DROPPED
-------------------------------------
`parse_vouchers` counts vouchers it cannot represent - anything that is not
exactly one debit and one credit. Those come back as `skipped` and are surfaced
on every result here, because "this ledger has three entries" and "this ledger
has three entries we can read" are different sentences, and only one of them is
safe to show a person looking at their own books.
"""

from __future__ import annotations

import datetime
from dataclasses import dataclass, field
from typing import Final

from accountant.schema import Voucher
from accountant.tallyio import audit, errors
from accountant.tallyio.real import (
    DEFAULT_MAX_RESPONSE_BYTES,
    HttpTransport,
    TallyConfig,
    Transport,
    VoucherPage,
    build_voucher_list_request,
    parse_vouchers,
)
from accountant.tallyio.real import (
    TallyError as ConnectorError,
)

#: Taken from the connector rather than chosen here, so one number governs how
#: large a response may be before it is refused instead of parsed.
MAX_RESPONSE_BYTES: Final = DEFAULT_MAX_RESPONSE_BYTES


@dataclass(frozen=True)
class Entry:
    """One line of a report, in this system's units.

    Money is integer paise. The conversion happens inside
    `real.parse_vouchers`, which uses `Decimal` and refuses a fractional paise
    rather than rounding it - so no float ever exists on this path.
    """

    date: datetime.date | None
    voucher_type: str
    voucher_number: str
    party: str
    amount_paise: int
    narration: str = ""
    debit_account: str = ""
    credit_account: str = ""

    @classmethod
    def of(cls, voucher: Voucher) -> Entry:
        return cls(
            date=voucher.date,
            voucher_type=getattr(voucher, "voucher_type", "") or "",
            voucher_number=voucher.id,
            party=voucher.party,
            amount_paise=voucher.amount_paise,
            narration=voucher.narration,
            debit_account=voucher.debit_account,
            credit_account=voucher.credit_account,
        )


@dataclass(frozen=True)
class LedgerVouchersResult:
    """Every voucher touching one ledger in a date range."""

    ledger: str
    from_date: datetime.date
    to_date: datetime.date
    entries: tuple[Entry, ...] = ()
    skipped: int = 0
    raw_xml: str = ""

    @property
    def total_paise(self) -> int:
        return sum(e.amount_paise for e in self.entries)


@dataclass(frozen=True)
class DayBookResult:
    """Everything posted in a date range, whatever ledger it touched."""

    from_date: datetime.date
    to_date: datetime.date
    entries: tuple[Entry, ...] = ()
    skipped: int = 0
    raw_xml: str = ""

    @property
    def total_paise(self) -> int:
        return sum(e.amount_paise for e in self.entries)


def in_range(
    when: datetime.date | None, from_: datetime.date, to: datetime.date
) -> bool:
    """Inclusive at both ends. A voucher with NO date is in no range.

    Excluded rather than included: an entry whose date could not be read is not
    evidence that something happened on the day being asked about, and a caller
    counting it would be counting a guess as a fact.
    """
    return when is not None and from_ <= when <= to


def touches(voucher: Voucher, ledger: str) -> bool:
    """Does this voucher name the ledger on either side, or as the party?

    Exact match, no case folding. TallyPrime reports the spelling a master was
    created with, and a loose match here would show one ledger's entries under
    another's name.
    """
    return ledger in (voucher.party, voucher.debit_account, voucher.credit_account)


@dataclass
class Reports:
    """Read one company's books. No writes, so no write door is involved."""

    company: str
    transport: Transport = field(default_factory=lambda: HttpTransport(TallyConfig()))
    log: audit.JsonLineAuditLogger = field(default_factory=audit.JsonLineAuditLogger)

    def day_book(self, from_: datetime.date, to: datetime.date) -> DayBookResult:
        """Everything posted in the range, whatever ledger it touched."""
        page, raw = self._collection(
            "get_day_book", from_date=str(from_), to_date=str(to)
        )
        return DayBookResult(
            from_date=from_,
            to_date=to,
            entries=tuple(
                Entry.of(v) for v in page.vouchers if in_range(v.date, from_, to)
            ),
            skipped=page.skipped,
            raw_xml=raw,
        )

    def ledger_vouchers(
        self, ledger: str, from_: datetime.date, to: datetime.date
    ) -> LedgerVouchersResult:
        """Every voucher touching one ledger. The read-back the write path uses."""
        page, raw = self._collection(
            "get_ledger_vouchers",
            ledger=ledger,
            from_date=str(from_),
            to_date=str(to),
        )
        return LedgerVouchersResult(
            ledger=ledger,
            from_date=from_,
            to_date=to,
            entries=tuple(
                Entry.of(v)
                for v in page.vouchers
                if touches(v, ledger) and in_range(v.date, from_, to)
            ),
            skipped=page.skipped,
            raw_xml=raw,
        )

    def _collection(self, operation: str, **inputs: object) -> tuple[VoucherPage, str]:
        """One collection read, logged, parsed by the connector's own parser."""
        request = build_voucher_list_request(self.company)
        with self.log.record(operation, company=self.company, **inputs) as entry:
            entry.request_xml = request
            # The connector has its OWN exception tree (`real.TallyError`), and
            # it is not this module's. A gateway that is down raises
            # `real.TallyUnreachable`, which `except errors.TallyError` does not
            # catch - so `mvp_real_tally.py`, which promises a code and a
            # sentence for every failure this system understands, printed a raw
            # traceback instead. Translated here, at the boundary, so every
            # report gets it rather than only the write path's duplicate probe.
            try:
                raw = self.transport.send(request, retry=True)
            except ConnectorError as unreachable:
                entry.error_summary = str(unreachable)[:300]
                raise errors.for_unreachable(unreachable) from unreachable
            except OSError as unreachable:
                # A transport that is not `HttpTransport` need not wrap its own
                # socket errors, and an unwrapped one would walk straight past
                # the handler above.
                entry.error_summary = str(unreachable)[:300]
                raise errors.for_unreachable(unreachable) from unreachable
            entry.response_xml = raw
            if not raw.strip():
                raise errors.for_unusable_body(raw)
            if failure := errors.classify(raw):
                entry.error_summary = f"{failure.code}: {failure.message}"[:300]
                raise failure
            try:
                page = parse_vouchers(raw, MAX_RESPONSE_BYTES)
            except ConnectorError as unusable:
                entry.error_summary = str(unusable)[:300]
                raise errors.for_unusable_body(raw) from unusable
            entry.status = "success"
            entry.extra["vouchers_read"] = len(page.vouchers)
            entry.extra["skipped"] = page.skipped
            return page, raw
