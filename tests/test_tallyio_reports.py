"""The report filters, which had no test until four mutants survived.

WHY THIS FILE EXISTS
--------------------
`accountant/tallyio/reports.py` was rewritten on 2026-08-12 after the first
version returned empty results for a company that demonstrably had a voucher in
it. The rewrite worked against the live TallyPrime - and then four mutants were
run against it and ALL FOUR SURVIVED:

    the date filter accepting everything
    the date filter rejecting everything
    the ledger filter matching any ledger
    the count of unreadable vouchers reported as zero

Every one of those changes the numbers a person reads about their own books, and
nothing in the suite noticed. A module that works when you run it by hand and
has no test is a module that works today.

WHAT IS TESTED HERE, AND WHY IT IS THE PURE PART
-------------------------------------------------
`in_range` and `touches` are the whole of the filtering, and they are pure
functions over a `Voucher`. Testing them directly is not a shortcut around
testing `Reports` - it is testing the thing the mutants broke, with no transport
in the way.

`Reports` itself is exercised through an injected transport that returns
canned Tally XML. NO NETWORK. A test that reaches 127.0.0.1 fails on a laptop
with no VM running and passes for the wrong reason on one that has a different
company loaded.
"""

from __future__ import annotations

import datetime
from pathlib import Path

import pytest

from accountant.schema import Voucher
from accountant.tallyio import audit
from accountant.tallyio.reports import Reports, in_range, touches

TODAY = datetime.date(2026, 8, 12)
YESTERDAY = datetime.date(2026, 8, 11)
TOMORROW = datetime.date(2026, 8, 13)


def a_voucher(
    *,
    when: datetime.date | None = TODAY,
    party: str = "Test Supplier",
    debit: str = "Purchase",
    credit: str = "Test Supplier",
    paise: int = 100_000,
    ident: str = "v1",
) -> Voucher:
    return Voucher(
        id=ident,
        date=when or TODAY,
        party=party,
        narration="a bill",
        debit_account=debit,
        credit_account=credit,
        amount_paise=paise,
    )


# ---------------------------------------------------------------------------
# in_range - the mutants "accepts everything" and "rejects everything"
# ---------------------------------------------------------------------------


def test_a_voucher_inside_the_range_is_kept() -> None:
    assert in_range(TODAY, YESTERDAY, TOMORROW) is True


def test_both_ends_of_the_range_are_inclusive() -> None:
    """A one-day report asks from X to X. Exclusive ends would return nothing,
    which is the shape of the bug this module was rewritten to fix."""
    assert in_range(TODAY, TODAY, TODAY) is True


def test_a_voucher_before_the_range_is_dropped() -> None:
    """THE CONTROL on the tests above. Without it, a filter that accepts
    everything passes all of them - which is a mutant that survived on
    2026-08-12."""
    assert in_range(YESTERDAY, TODAY, TOMORROW) is False


def test_a_voucher_after_the_range_is_dropped() -> None:
    assert in_range(TOMORROW, YESTERDAY, TODAY) is False


def test_a_voucher_with_no_date_is_in_no_range() -> None:
    """Excluded, not included. An entry whose date could not be read is not
    evidence that something happened on the day being asked about, and a caller
    counting it would be counting a guess as a fact."""
    assert in_range(None, YESTERDAY, TOMORROW) is False


# ---------------------------------------------------------------------------
# touches - the mutant "matches any ledger"
# ---------------------------------------------------------------------------


def test_the_party_ledger_is_matched() -> None:
    assert touches(a_voucher(), "Test Supplier") is True


def test_either_side_of_the_entry_is_matched() -> None:
    """A Purchase names the expense ledger on the debit side, and asking for
    'Purchase' must find it even though it is not the party."""
    assert touches(a_voucher(), "Purchase") is True


def test_a_ledger_the_voucher_never_names_is_not_matched() -> None:
    """THE CONTROL. A filter returning True unconditionally passes both tests
    above, and reports one ledger's entries under another's name."""
    assert touches(a_voucher(), "Bank Of Test") is False


def test_the_match_is_exact_and_not_case_folded() -> None:
    """TallyPrime reports the spelling a master was created with. Matching
    loosely would show 'test supplier' the entries belonging to 'Test
    Supplier', and in a company with both, silently merge two parties."""
    assert touches(a_voucher(), "test supplier") is False


def test_a_partial_name_is_not_a_match() -> None:
    assert touches(a_voucher(), "Test") is False


# ---------------------------------------------------------------------------
# Reports, over a transport that returns canned XML. No network.
# ---------------------------------------------------------------------------

ONE_VOUCHER = """<ENVELOPE><BODY><DATA><COLLECTION>
<VOUCHER>
 <DATE>20260812</DATE>
 <VOUCHERNUMBER>1</VOUCHERNUMBER>
 <VOUCHERTYPENAME>Purchase</VOUCHERTYPENAME>
 <PARTYLEDGERNAME>Test Supplier</PARTYLEDGERNAME>
 <NARRATION>MVP end-to-end run</NARRATION>
 <ALLLEDGERENTRIES.LIST>
  <LEDGERNAME>Purchase</LEDGERNAME>
  <ISDEEMEDPOSITIVE>Yes</ISDEEMEDPOSITIVE>
  <AMOUNT>-1000.00</AMOUNT>
 </ALLLEDGERENTRIES.LIST>
 <ALLLEDGERENTRIES.LIST>
  <LEDGERNAME>Test Supplier</LEDGERNAME>
  <ISDEEMEDPOSITIVE>No</ISDEEMEDPOSITIVE>
  <AMOUNT>1000.00</AMOUNT>
 </ALLLEDGERENTRIES.LIST>
</VOUCHER>
</COLLECTION></DATA></BODY></ENVELOPE>"""

EMPTY = "<ENVELOPE><BODY><DATA><COLLECTION></COLLECTION></DATA></BODY></ENVELOPE>"


class Wire:
    """A transport that answers with canned XML and counts what it was asked."""

    def __init__(self, answer: str) -> None:
        self.answer = answer
        self.sent: list[str] = []
        self.retries: list[bool] = []

    def send(self, payload: str, *, retry: bool = False) -> str:
        # `retry` is RECORDED, not ignored. A read that does not retry turns one
        # dropped packet into a report that says the books are empty, and ruff
        # flagging the unused argument is what surfaced that nothing checked it.
        self.retries.append(retry)
        self.sent.append(payload)
        return self.answer


@pytest.fixture
def logger(tmp_path: Path) -> audit.JsonLineAuditLogger:
    return audit.JsonLineAuditLogger(tmp_path / "logs", tmp_path / "logs" / "xml")


def test_a_voucher_on_the_day_asked_about_is_reported(
    logger: audit.JsonLineAuditLogger,
) -> None:
    wire = Wire(ONE_VOUCHER)
    found = Reports("Co", transport=wire, log=logger).ledger_vouchers(
        "Test Supplier", TODAY, TODAY
    )

    assert len(found.entries) == 1
    assert found.entries[0].amount_paise == 100_000
    assert found.total_paise == 100_000


def test_the_same_voucher_is_absent_from_a_day_it_was_not_posted_on(
    logger: audit.JsonLineAuditLogger,
) -> None:
    """THE CONTROL on the test above: the same response, a different day. A
    filter that accepts everything passes the first and fails this."""
    wire = Wire(ONE_VOUCHER)
    found = Reports("Co", transport=wire, log=logger).ledger_vouchers(
        "Test Supplier", YESTERDAY, YESTERDAY
    )

    assert found.entries == ()


def test_a_ledger_that_owns_none_of_it_gets_nothing(
    logger: audit.JsonLineAuditLogger,
) -> None:
    wire = Wire(ONE_VOUCHER)
    found = Reports("Co", transport=wire, log=logger).ledger_vouchers(
        "Bank Of Test", TODAY, TODAY
    )

    assert found.entries == ()


def test_the_day_book_reports_the_same_entry_without_a_ledger_filter(
    logger: audit.JsonLineAuditLogger,
) -> None:
    wire = Wire(ONE_VOUCHER)
    book = Reports("Co", transport=wire, log=logger).day_book(TODAY, TODAY)

    assert len(book.entries) == 1
    assert book.total_paise == 100_000


def test_an_empty_company_reports_nothing_rather_than_failing(
    logger: audit.JsonLineAuditLogger,
) -> None:
    """A company with no entries is an ordinary state, not an error."""
    wire = Wire(EMPTY)
    book = Reports("Co", transport=wire, log=logger).day_book(TODAY, TODAY)

    assert book.entries == ()
    assert book.total_paise == 0


def test_every_report_writes_one_audit_line(
    logger: audit.JsonLineAuditLogger,
) -> None:
    """A read is an operation too. A log that only records writes cannot answer
    'what did we ask Tally' when a number looks wrong."""
    wire = Wire(ONE_VOUCHER)
    reports = Reports("Co", transport=wire, log=logger)

    reports.day_book(TODAY, TODAY)
    reports.ledger_vouchers("Test Supplier", TODAY, TODAY)

    lines = audit.recent(directory=logger.path.parent)
    assert len(lines) == 2
    assert {line["operation"] for line in lines} == {
        "get_day_book",
        "get_ledger_vouchers",
    }
    assert all(line["status"] == "success" for line in lines)


def test_the_request_is_a_collection_and_not_a_day_book_report(
    logger: audit.JsonLineAuditLogger,
) -> None:
    """THE REGRESSION TEST for 2026-08-12.

    The first version asked `<TYPE>Data</TYPE><ID>Day Book</ID>` and TallyPrime
    answered `STATUS 1` with `<DATA>  </DATA>` - a successful response holding
    nothing - while a real voucher sat in the company. Worse, the write path
    used that same query to confirm its own writes, so it reported
    `confirmed=False` for vouchers that had posted.

    Read off the request bytes, because that is the thing that was wrong.
    """
    wire = Wire(ONE_VOUCHER)
    Reports("Co", transport=wire, log=logger).day_book(TODAY, TODAY)

    request = wire.sent[0]
    assert "<ID>Day Book</ID>" not in request
    assert "COLLECTION" in request.upper()


def test_a_read_is_retried_because_a_dropped_packet_is_not_an_empty_company(
    logger: audit.JsonLineAuditLogger,
) -> None:
    """One lost response must not read as "this ledger has no entries".

    A write must NOT retry blindly - that is how a voucher is posted twice, and
    `RealTally` guards it with an operation id. A read has no such hazard and
    every reason to try again.
    """
    wire = Wire(ONE_VOUCHER)
    Reports("Co", transport=wire, log=logger).day_book(TODAY, TODAY)

    assert wire.retries == [True]
