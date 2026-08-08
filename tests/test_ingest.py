"""Child 5 - real UK central-government spend, loaded and measured.

One section per frozen acceptance criterion:

1. a month of UK central-government spend loads into the `Voucher` schema,
   `Narrative` -> narration, `Expense Type` -> account
       test_a_published_month_*, test_the_narrative_*, test_the_expense_type_*
2. the loader states row count and source URL
       test_the_loader_states_*, test_the_load_report_*
3. `accountant/score/` runs unmodified against it
       test_the_score_harness_*, test_accountant_score_*
4. child 8 can run against it: index on A, test on B, within, cross, and the
   gap as a single number per pair, for at least three pairs
       test_at_least_three_*, test_every_pair_*, test_the_gap_*

Plus the hard constraints, each with its own test rather than a promise:

    money is integer pence, and the seam that puts pence into a field named
    paise says so                    test_money_is_*, test_the_voucher_seam_*
    malformed rows are counted, never dropped   test_*_rejected_*, test_dbt_*
    nothing in the load path can reach the network  test_*_network*, test_only_*

Fixtures are slices of the real published files, byte for byte, including the
published typo `PO Catergory Description ` and the single-byte cp1252 pound
sign. Row counts are recorded in `accountant/ingest/sources.py` and checked
here against the committed bytes.
"""

from __future__ import annotations

import ast
import datetime
from pathlib import Path

import pytest

from accountant import score
from accountant.ingest import crossorg, fetch, report, sources, spend
from accountant.ingest.crossorg import Accuracy, CrossOrgReport, PairResult
from accountant.ingest.spend import LoadResult, RejectedRow, SpendRow
from accountant.memory.index import MemoryIndex
from accountant.schema import Voucher

INGEST_DIR = Path(spend.__file__).parent
SCORE_DIR = Path(score.__file__).parent
THIS_FILE = Path(__file__)

# Anything that could open a connection. The load path must import none of it.
NETWORK_MODULES = frozenset(
    {"urllib", "socket", "http", "ssl", "ftplib", "requests", "httpx", "asyncio"}
)

# R and D for the score run. Self-timed inputs; the harness prints them and
# refuses to invent them, so a test has to supply them too.
READ_SECONDS = 8
DISMISS_SECONDS = 20

# The real MHCLG header, verbatim, used to build deliberately broken rows.
BROKEN_HEADER = (
    "Reference No,Entity,Date of Payment,Expense Type,Expense Area,Supplier,"
    "Transaction number,Narrative,Amount in Sterling"
)
BROKEN_ROWS = (
    # good
    "2,MHCLG,03/11/2025,IT - Service Contracts,Digital,ACME LTD,2100009658,"
    'IT Services,"£1,000.00"',
    # narration empty - exactly what DBT publishes for every row
    "3,MHCLG,03/11/2025,IT - Service Contracts,Digital,ACME LTD,2100009659,,"
    '"£1,000.00"',
    # amount unreadable
    "4,MHCLG,03/11/2025,IT - Service Contracts,Digital,ACME LTD,2100009660,"
    "IT Services,not a number",
    # date that is not a date
    "5,MHCLG,31/02/2025,IT - Service Contracts,Digital,ACME LTD,2100009661,"
    'IT Services,"£1,000.00"',
    # account, party and narration all empty
    '6,MHCLG,03/11/2025,,Digital,,2100009662,,"£1,000.00"',
    # fewer columns than the header
    "7,MHCLG",
)


def broken_file() -> bytes:
    """A real header with rows broken on purpose, in the published encoding."""
    return ("\r\n".join((BROKEN_HEADER, *BROKEN_ROWS)) + "\r\n").encode("cp1252")


def broken_result() -> LoadResult:
    return spend.load_bytes(
        broken_file(),
        code="TEST",
        department="A department that published a broken file",
        source_url="https://assets.publishing.service.gov.uk/media/test/broken.csv",
    )


def header_for(**overrides: str) -> list[str]:
    """A complete published header, with named columns replaced or removed."""
    base = {
        "date": "Date of Payment",
        "party": "Supplier",
        "narration": "Narrative",
        "account": "Expense Type",
        "amount": "Amount in Sterling",
    }
    base.update(overrides)
    return [value for value in base.values() if value]


def loaded() -> tuple[LoadResult, ...]:
    return spend.load_all(sources.COMPARABLE_SOURCES)


def comparison() -> CrossOrgReport:
    return crossorg.compare(loaded())


def imported_roots(path: Path) -> set[str]:
    """The top-level module every import in one file reaches for."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            roots.add(node.module.split(".")[0])
    return roots


class FakeResponse:
    """Bytes the test already holds, shaped like an HTTP response."""

    def __init__(self, payload: bytes) -> None:
        self.payload = payload
        self.asked_for: int | None = None

    def read(self, amount: int, /) -> bytes:
        self.asked_for = amount
        return self.payload[:amount]

    def __enter__(self) -> FakeResponse:
        return self

    def __exit__(self, *details: object) -> None:
        return None


class FakeOpener:
    """Stands in for urlopen. Opens nothing and records what it was asked."""

    def __init__(self, payload: bytes) -> None:
        self.payload = payload
        self.url: str | None = None
        self.timeout: int | None = None

    def __call__(self, url: str, /, *, timeout: int) -> FakeResponse:
        self.url = url
        self.timeout = timeout
        return FakeResponse(self.payload)


# --------------------------------------------------------------------------
# 1. a published month loads into the Voucher schema
# --------------------------------------------------------------------------


def test_a_published_month_loads_into_the_voucher_schema() -> None:
    result = spend.load_source(sources.MHCLG)
    stream = spend.vouchers(result)

    assert len(stream) == sources.MHCLG.fixture_rows
    assert all(isinstance(v, Voucher) for v in stream)
    assert all(v.id.startswith("MHCLG-") for v in stream)
    assert len({v.id for v in stream}) == len(stream)


def test_the_narrative_column_becomes_the_narration() -> None:
    result = spend.load_source(sources.MHCLG)

    assert result.columns.narration.header == "Narrative"
    published = [row.narration for row in result.rows]
    carried = [v.narration for v in spend.vouchers(result)]
    assert carried == published
    assert all(text for text in carried)


def test_the_expense_type_column_becomes_the_debit_account() -> None:
    result = spend.load_source(sources.MHCLG)

    assert result.columns.account.header == "Expense Type"
    published = [row.account for row in result.rows]
    carried = [v.debit_account for v in spend.vouchers(result)]
    assert carried == published
    assert "Current (non AEF) Grants to Local Authorities" in set(carried)


def test_every_department_resolves_its_own_column_names() -> None:
    """Seven departments, seven header shapes, one month. This is the evidence."""
    narration_headers = {
        r.code: r.columns.narration.header for r in spend.load_all(sources.ALL_SOURCES)
    }
    assert narration_headers == {
        "MHCLG": "Narrative",
        "DHSC": "Description",
        "DFT": "Item Text",
        "DWP": "Invoice Cost Centre Description",
        "DEFRA": "PO Catergory Description ",
        "HMT": "Publication Description",
        "DBT": "Description",
    }


def test_both_published_text_encodings_are_read() -> None:
    encodings = {r.code: r.encoding for r in spend.load_all(sources.ALL_SOURCES)}
    assert encodings["MHCLG"] == "cp1252"
    assert encodings["DHSC"] == "utf-8-sig"


def test_both_published_date_formats_are_read() -> None:
    slashed = spend.load_source(sources.MHCLG)
    abbreviated = spend.load_source(sources.HMT)

    assert slashed.columns.date.header == "Date of Payment"
    assert all(row.date.year == 2025 and row.date.month == 11 for row in slashed.rows)
    assert all(
        row.date.year == 2025 and row.date.month == 11 for row in abbreviated.rows
    )


def test_the_same_bytes_produce_the_same_vouchers() -> None:
    once = spend.vouchers(spend.load_source(sources.DFT))
    twice = spend.vouchers(spend.load_source(sources.DFT))
    assert once == twice


# --------------------------------------------------------------------------
# money: integer pence, and the one place the field name lies
# --------------------------------------------------------------------------


def test_the_minor_unit_this_package_carries_is_named() -> None:
    assert spend.MINOR_UNIT == "pence"
    assert spend.PENCE_PER_POUND == 100


def test_money_is_integer_pence_never_a_float() -> None:
    for result in spend.load_all(sources.COMPARABLE_SOURCES):
        for row in result.rows:
            assert type(row.amount_pence) is int


def test_pounds_become_integer_pence() -> None:
    assert spend.parse_pence("£105,400.00") == 10_540_000
    assert spend.parse_pence(" £534,722,000.00 ") == 53_472_200_000
    assert spend.parse_pence("37,109.87") == 3_710_987


def test_an_amount_with_no_decimals_or_one_decimal_is_read() -> None:
    assert spend.parse_pence("£1,234") == 123_400
    assert spend.parse_pence("12.5") == 1250


def test_a_negative_amount_keeps_its_sign() -> None:
    assert spend.parse_pence("-£82,354.00") == -8_235_400
    assert spend.parse_pence("+12.50") == 1250


def test_a_refund_is_kept_and_counted_rather_than_dropped() -> None:
    result = spend.load_source(sources.MHCLG)
    assert result.refund_count == 1
    assert any(v.amount_paise < 0 for v in spend.vouchers(result))


def test_an_unreadable_amount_is_refused_rather_than_guessed() -> None:
    assert spend.parse_pence("not a number") is None
    assert spend.parse_pence("") is None
    assert spend.parse_pence("12.345") is None


def test_the_voucher_seam_states_that_the_paise_field_holds_pence() -> None:
    """The single point where a pence figure lands in a field named for paise.

    If this sentence ever goes missing, the codebase is silently claiming that
    pounds are rupees.
    """
    text = spend.as_voucher.__doc__ or ""
    assert "amount_paise" in text
    assert "pence" in text
    assert "not paise" in text


def test_the_voucher_carries_pence_in_the_paise_field_with_its_unit_stated() -> None:
    result = spend.load_source(sources.MHCLG)
    row = result.rows[0]
    voucher = spend.as_voucher(row, code=result.code, source_url=result.source_url)

    assert voucher.amount_paise == row.amount_pence
    provenance = voucher.provenance or {}
    assert spend.MINOR_UNIT in provenance["amount_paise"]


def test_the_credit_side_and_the_vat_split_are_recorded_as_absent() -> None:
    """Not published, so not invented. A field with no source is a
    hallucination by definition."""
    result = spend.load_source(sources.DHSC)
    voucher = spend.vouchers(result)[0]
    provenance = voucher.provenance or {}

    assert voucher.credit_account == spend.CREDIT_NOT_IN_SOURCE
    assert voucher.gst_paise is None
    assert provenance["credit_account"] == "not_found"
    assert provenance["gst_paise"] == "not_found"
    assert provenance["narration"] == sources.DHSC.url


# --------------------------------------------------------------------------
# 2. the loader states row count and source URL
# --------------------------------------------------------------------------


def test_the_loader_states_the_row_count_and_the_source_url() -> None:
    result = spend.load_source(sources.MHCLG)

    assert str(result.row_count) in result.statement
    assert result.source_url in result.statement
    assert result.source_url.startswith("https://")


def test_the_row_count_is_loaded_plus_rejected() -> None:
    result = broken_result()
    assert result.row_count == result.loaded_count + result.rejected_count
    assert result.row_count == len(BROKEN_ROWS)


def test_the_load_report_names_the_source_url_and_the_row_count() -> None:
    text = report.render_load(spend.load_source(sources.MHCLG))

    assert sources.MHCLG.url in text
    assert sources.LICENCE in text
    assert sources.RETRIEVED.isoformat() in text
    assert f"read from the file        {sources.MHCLG.fixture_rows}" in text
    assert text.endswith("\n")


def test_the_load_report_names_every_column_it_resolved() -> None:
    text = report.render_load(spend.load_source(sources.DEFRA))
    assert "'PO Catergory Description '" in text
    assert "'Supplier '" in text


def test_the_load_report_says_so_when_nothing_was_rejected() -> None:
    text = report.render_load(spend.load_source(sources.MHCLG))
    assert "every row in the file was read" in text


def test_the_load_report_names_every_reason_a_row_was_not_loaded() -> None:
    """A file that half-loaded must never read like a file that loaded."""
    text = report.render_load(broken_result())

    assert "every row in the file was read" not in text
    for reason in (
        spend.EMPTY_NARRATION,
        spend.EMPTY_ACCOUNT,
        spend.EMPTY_PARTY,
        spend.UNREADABLE_DATE,
        spend.UNREADABLE_AMOUNT,
        spend.SHORT_ROW,
    ):
        assert reason in text


def test_every_recorded_source_carries_a_url_a_licence_and_a_row_count() -> None:
    assert sources.LICENCE_URL.startswith("https://")
    assert sources.MONTH == "2025-11"
    for source in sources.ALL_SOURCES:
        assert source.url.startswith("https://")
        assert source.published_rows > 0
        assert source.fixture_path.exists()


def test_every_recorded_fixture_row_count_matches_the_committed_bytes() -> None:
    """The recorded numbers cannot drift away from the files they describe."""
    for source in sources.ALL_SOURCES:
        result = spend.load_source(source)
        assert result.row_count == source.fixture_rows, source.code


# --------------------------------------------------------------------------
# malformed rows: counted and named, never dropped
# --------------------------------------------------------------------------


def test_dbt_publishes_an_empty_narration_column_and_every_row_is_counted() -> None:
    """Real, not contrived: DBT publishes the column and leaves it empty."""
    result = spend.load_source(sources.DBT)

    assert result.loaded_count == 0
    assert result.rejected_count == sources.DBT.fixture_rows
    assert result.rejected_by_reason() == ((spend.EMPTY_NARRATION, result.row_count),)


def test_a_malformed_row_is_reported_as_a_count_never_dropped() -> None:
    result = broken_result()

    assert result.loaded_count == 1
    assert result.rejected_count == 5
    assert dict(result.rejected_by_reason()) == {
        spend.EMPTY_NARRATION: 2,
        spend.EMPTY_ACCOUNT: 1,
        spend.EMPTY_PARTY: 1,
        spend.UNREADABLE_DATE: 1,
        spend.UNREADABLE_AMOUNT: 1,
        spend.SHORT_ROW: 1,
    }


def test_a_rejected_row_names_its_published_row_number() -> None:
    numbers = [r.row_number for r in broken_result().rejected]
    assert numbers == [2, 3, 4, 5, 6]


def test_one_row_can_be_rejected_for_several_reasons_at_once() -> None:
    worst = [r for r in broken_result().rejected if r.row_number == 5]
    assert worst[0].reasons == (
        spend.EMPTY_NARRATION,
        spend.EMPTY_ACCOUNT,
        spend.EMPTY_PARTY,
    )


def test_every_reason_a_row_can_be_refused_is_reported_together() -> None:
    assert spend.why_rejected("", "", "", None, None) == (
        spend.EMPTY_NARRATION,
        spend.EMPTY_ACCOUNT,
        spend.EMPTY_PARTY,
        spend.UNREADABLE_DATE,
        spend.UNREADABLE_AMOUNT,
    )
    assert spend.why_rejected("n", "a", "p", datetime.date(2025, 11, 3), 1) == ()


def test_a_rejected_row_without_a_reason_is_refused() -> None:
    with pytest.raises(ValueError, match="without a reason"):
        RejectedRow(row_number=1, reasons=())


def test_a_short_row_is_rejected_by_name() -> None:
    columns = spend.resolve_columns(header_for())
    read = spend.read_row(1, ["only-one-cell"], columns)
    assert isinstance(read, RejectedRow)
    assert read.reasons == (spend.SHORT_ROW,)


def test_a_complete_row_is_read() -> None:
    columns = spend.resolve_columns(header_for())
    read = spend.read_row(
        1,
        ["03/11/2025", "ACME LTD", "IT Services", "IT - Service Contracts", "10.00"],
        columns,
    )
    assert isinstance(read, SpendRow)
    assert read.amount_pence == 1000


# --------------------------------------------------------------------------
# header resolution: refuse, never guess
# --------------------------------------------------------------------------


def test_a_header_is_matched_on_case_and_whitespace_only() -> None:
    assert spend.normalise_header("  Expense\u00a0Type ") == "expense type"
    assert spend.normalise_header(" £ ") == "£"


def test_a_missing_column_is_refused_by_name() -> None:
    with pytest.raises(ValueError, match="no column supplies 'narration'"):
        spend.resolve_columns(header_for(narration=""))


def test_two_columns_that_could_supply_one_field_is_refused() -> None:
    with pytest.raises(ValueError, match="could supply 'narration'"):
        spend.resolve_columns([*header_for(), "Description"])


def test_a_header_that_repeats_a_column_name_is_refused() -> None:
    with pytest.raises(ValueError, match="repeats a column name"):
        spend.resolve_columns([*header_for(), "amount in sterling"])


def test_a_file_with_no_header_at_all_is_refused() -> None:
    with pytest.raises(ValueError, match="no column supplies"):
        spend.load_bytes(b"", code="X", department="X", source_url="https://x.gov.uk/x")


def test_a_file_that_is_not_text_in_any_published_encoding_is_refused() -> None:
    with pytest.raises(ValueError, match="could not decode"):
        spend.decode(b"\x81")


def test_the_published_encodings_are_tried_in_order() -> None:
    assert spend.decode(b"a,b\r\n") == ("a,b\r\n", "utf-8-sig")
    assert spend.decode(b"\xa3") == ("£", "cp1252")


def test_an_impossible_date_is_refused_rather_than_shifted() -> None:
    assert spend.parse_date("31/02/2025") is None
    assert spend.parse_date("30-Feb-25") is None


def test_a_month_name_that_is_not_a_month_is_refused() -> None:
    assert spend.parse_date("04-Zzz-25") is None
    assert spend.parse_date("hello") is None


def test_the_two_published_date_shapes_parse_to_the_same_type() -> None:
    assert spend.parse_date("03/11/2025") == datetime.date(2025, 11, 3)
    assert spend.parse_date("04-Nov-25") == datetime.date(2025, 11, 4)


# --------------------------------------------------------------------------
# 3. accountant/score runs unmodified against it
# --------------------------------------------------------------------------


def test_the_score_harness_runs_against_real_data_unmodified() -> None:
    book = spend.as_score_book(spend.load_source(sources.MHCLG))
    result = score.score(
        book, read_seconds=READ_SECONDS, dismiss_seconds=DISMISS_SECONDS
    )

    assert book.company == sources.MHCLG.department
    assert len(book.history) + len(book.entries) == sources.MHCLG.fixture_rows
    assert result.total_entries == len(book.entries)
    assert result.total_entries > 0


def test_the_score_harness_fails_n3_on_real_data_because_there_is_no_answer_key() -> (
    None
):
    """Nobody injected errors into a real government ledger, so there is
    nothing to catch. The harness must say so rather than pass on no evidence."""
    book = spend.as_score_book(spend.load_source(sources.DHSC))
    result = score.score(
        book, read_seconds=READ_SECONDS, dismiss_seconds=DISMISS_SECONDS
    )

    assert result.injected_entries == 0
    assert result.n3.status is score.Status.MISSED
    assert result.n3.measured_hundredths is None
    assert "no injected errors" in result.n3.detail


def test_the_score_book_uses_the_published_chart_plus_the_absent_credit_side() -> None:
    result = spend.load_source(sources.HMT)
    book = spend.as_score_book(result)

    assert spend.CREDIT_NOT_IN_SOURCE in book.accounts
    assert set(book.accounts) - {spend.CREDIT_NOT_IN_SOURCE} == {
        row.account for row in result.rows
    }


def test_accountant_score_does_not_know_this_package_exists() -> None:
    """ "Runs unmodified" means the dependency points one way only."""
    for path in sorted(SCORE_DIR.glob("*.py")):
        assert "ingest" not in path.read_text(encoding="utf-8"), path


def test_the_history_and_the_entries_do_not_overlap() -> None:
    book = spend.as_score_book(spend.load_source(sources.DFT))
    assert not {v.id for v in book.history} & {v.id for v in book.entries}
    assert spend.split_point(len(book.history) + len(book.entries)) == len(book.history)


# --------------------------------------------------------------------------
# 4. cross-organisation generalisation - child 8 runs against this
# --------------------------------------------------------------------------


def test_at_least_three_department_pairs_are_measured() -> None:
    result = comparison()
    assert len(result.pairs) >= crossorg.MIN_PAIRS
    assert len(result.departments) == len(sources.COMPARABLE_SOURCES)


def test_every_pair_reports_within_cross_and_one_gap_number() -> None:
    for pair in comparison().pairs:
        assert pair.index_code != pair.test_code
        assert pair.within.tested > 0
        assert pair.cross.tested > 0
        assert isinstance(pair.gap_hundredths, int)


def test_the_gap_is_within_minus_cross() -> None:
    for pair in comparison().pairs:
        assert pair.gap_hundredths == (
            pair.within.percent_hundredths - pair.cross.percent_hundredths
        )


def test_an_account_mapping_does_not_transfer_between_organisations() -> None:
    """The measured answer to the design-validity question.

    Every ordered pair of the six comparable departments: an index built on one
    department's own history gets zero of the other department's entries right.
    """
    result = comparison()
    assert result.best_cross_hundredths == 0
    assert all(pair.cross.correct == 0 for pair in result.pairs)


def test_within_department_accuracy_is_far_above_cross_department() -> None:
    result = comparison()
    worst_within = min(p.within.percent_hundredths for p in result.pairs)
    best_cross = max(p.cross.percent_hundredths for p in result.pairs)
    assert worst_within > best_cross
    assert result.worst_gap_hundredths > 0


def test_a_supplier_the_index_never_saw_is_a_no_match_not_a_wrong_answer() -> None:
    """A non-answer and a wrong answer are different failures and are counted
    separately, because averaging them hides which one happened."""
    pairs = {(p.index_code, p.test_code): p for p in comparison().pairs}
    dwp_to_defra = pairs[("DWP", "DEFRA")]
    assert dwp_to_defra.cross.matched == 3
    assert dwp_to_defra.cross.correct == 0
    assert dwp_to_defra.cross.no_match == 16


def test_the_comparison_is_the_same_on_every_run() -> None:
    once = comparison()
    twice = comparison()
    assert once == twice


def test_the_cross_report_shows_within_cross_and_gap_for_every_pair() -> None:
    result = comparison()
    text = report.render_cross(result)

    assert f"pairs measured            {len(result.pairs)}" in text
    for pair in result.pairs:
        assert f"{pair.index_code:<7}-> {pair.test_code:<7}" in text
    measured = [line for line in text.splitlines() if "within " in line and "%" in line]
    assert len(measured) == len(result.pairs)
    assert all("gap" in line for line in measured)
    assert text.endswith("\n")


def test_a_negative_gap_is_reported_with_its_sign() -> None:
    """A cross-department index that beat the within-department one would be a
    real result, so the report must be able to print it."""
    better_elsewhere = Accuracy(
        tested=10, matched=10, correct=1, conflicted=0, no_match=0
    )
    at_home = Accuracy(tested=10, matched=10, correct=5, conflicted=0, no_match=0)
    pairs = tuple(
        PairResult(
            index_code="A", test_code=f"B{n}", within=better_elsewhere, cross=at_home
        )
        for n in range(crossorg.MIN_PAIRS)
    )
    text = report.render_cross(CrossOrgReport(departments=("A",), pairs=pairs))

    assert "-40.00%" in text
    assert pairs[0].gap_hundredths == -4000


def test_a_comparison_with_fewer_than_three_pairs_is_refused() -> None:
    with pytest.raises(ValueError, match=f"at least {crossorg.MIN_PAIRS} department"):
        crossorg.compare(spend.load_all((sources.MHCLG, sources.DHSC)))


def test_the_same_department_supplied_twice_is_refused() -> None:
    with pytest.raises(ValueError, match="supplied twice"):
        crossorg.compare(spend.load_all((sources.MHCLG, sources.DHSC, sources.MHCLG)))


def test_a_department_with_no_usable_rows_cannot_take_part() -> None:
    with pytest.raises(ValueError, match="DBT has 0 history"):
        crossorg.split(spend.load_source(sources.DBT))


def test_an_accuracy_over_nothing_is_refused() -> None:
    with pytest.raises(ValueError, match="nothing was tested"):
        Accuracy(tested=0, matched=0, correct=0, conflicted=0, no_match=0)


def test_a_conflicted_lookup_is_counted_as_a_non_answer() -> None:
    index = MemoryIndex()
    index.record("ACME LTD", "Rent")
    index.record("ACME LTD", "Legal Fees")
    entry = Voucher(
        id="X-1",
        date=datetime.date(2025, 11, 3),
        party="ACME LTD",
        narration="anything",
        debit_account="Rent",
        credit_account=spend.CREDIT_NOT_IN_SOURCE,
        amount_paise=1000,
    )
    measured = crossorg.measure(index, (entry,))

    assert measured.conflicted == 1
    assert measured.correct == 0
    assert measured.matched == 0


def test_a_correct_match_is_counted_as_correct() -> None:
    index = MemoryIndex()
    index.record("ACME LTD", "Rent")
    entry = Voucher(
        id="X-1",
        date=datetime.date(2025, 11, 3),
        party="ACME LTD",
        narration="anything",
        debit_account="Rent",
        credit_account=spend.CREDIT_NOT_IN_SOURCE,
        amount_paise=1000,
    )
    measured = crossorg.measure(index, (entry,))

    assert measured.matched == 1
    assert measured.correct == 1
    assert measured.percent_hundredths == 10_000


# --------------------------------------------------------------------------
# the network: none of it, anywhere near the load path
# --------------------------------------------------------------------------


def test_nothing_in_the_load_path_imports_a_networking_module() -> None:
    """fetch.py is the only module allowed to know the network exists."""
    for path in sorted(INGEST_DIR.glob("*.py")):
        if path.name == "fetch.py":
            continue
        assert not imported_roots(path) & NETWORK_MODULES, path


def test_only_fetch_can_reach_the_network() -> None:
    assert imported_roots(INGEST_DIR / "fetch.py") & NETWORK_MODULES == {"urllib"}


def test_no_test_in_this_file_uses_the_live_opener() -> None:
    """Every call into fetch here passes its own opener, so no socket opens.

    This is checked by reading this file rather than by reading the tests, so a
    future test cannot quietly start fetching from gov.uk.
    """
    tree = ast.parse(THIS_FILE.read_text(encoding="utf-8"))
    reached = ("read_url", "fetch_source")
    calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr in reached
    ]
    assert calls
    for call in calls:
        assert any(keyword.arg == "opener" for keyword in call.keywords)


def test_a_recorded_source_is_fetched_from_the_url_it_records() -> None:
    opener = FakeOpener(b"Reference No\r\n")
    data = fetch.fetch_source(sources.MHCLG, opener=opener)

    assert data == b"Reference No\r\n"
    assert opener.url == sources.MHCLG.url
    assert opener.timeout == fetch.TIMEOUT_SECONDS


def test_a_non_https_url_is_refused() -> None:
    with pytest.raises(ValueError, match="refusing http URL"):
        fetch.check_url("http://assets.publishing.service.gov.uk/media/x/y.csv")
    with pytest.raises(ValueError, match="refusing a schemeless URL"):
        fetch.check_url("assets.publishing.service.gov.uk/media/x/y.csv")


def test_a_host_outside_gov_uk_is_refused() -> None:
    with pytest.raises(ValueError, match="refusing host"):
        fetch.check_url("https://example.com/spend.csv")


def test_an_oversized_body_is_refused_without_being_fully_buffered() -> None:
    opener = FakeOpener(b"x" * 10)
    with pytest.raises(ValueError, match="larger than the 4 byte cap"):
        fetch.read_url("https://x.gov.uk/spend.csv", opener=opener, max_bytes=4)


def test_a_body_inside_the_cap_is_returned() -> None:
    opener = FakeOpener(b"abcd")
    assert (
        fetch.read_url("https://x.gov.uk/a.csv", opener=opener, max_bytes=4) == b"abcd"
    )


def test_the_default_opener_is_the_standard_library() -> None:
    """Named once, so the live path is visible rather than buried."""
    assert fetch.URLLIB_OPENER is not None
    assert fetch.MAX_BYTES == 32 * 1024 * 1024
