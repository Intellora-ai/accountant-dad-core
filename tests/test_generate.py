"""Child 1 - the synthetic book generator and the error injector.

Every acceptance criterion from the frozen plan has a test here that fails if
the criterion breaks:

1. same seed, byte-identical output   test_the_same_seed_*
2. at least 12 simulated months       test_a_book_covers_*
3. every voucher balances             test_every_*_balances, test_balances_rejects_*
4. an exact error rate                test_five_percent_*, test_count_for_*
5. ground truth in a separate file    test_the_voucher_stream_*
6. one vendor, three or more spellings test_at_least_one_vendor_*

Two things are deliberately tested from the other side as well. `balances` is
checked against vouchers that do NOT balance, because a function that always
returns True would pass criterion 3 and mean nothing. A different seed is
checked to produce different bytes, because a generator that ignores its seed
would pass criterion 1 and mean nothing.
"""

from __future__ import annotations

import datetime
import hashlib
from collections import Counter, defaultdict
from collections.abc import Sequence
from fractions import Fraction
from pathlib import Path

import pytest

from accountant.detect import detectors
from accountant.generate import serialise
from accountant.generate.book import (
    CHART,
    IRREGULAR,
    MIN_MONTHS,
    POSTED_ACCOUNTS,
    SCHEDULED,
    UNPOSTED_ACCOUNTS,
    Book,
    Vendor,
    add_months,
    balances,
    contributions,
    generate_book,
    gst_of,
)
from accountant.generate.inject import (
    ERROR_TYPES,
    Corruption,
    InjectedBook,
    count_for,
    inject,
    percent,
)
from accountant.generate.serialise import (
    truth_bytes,
    voucher_bytes,
    voucher_record,
    write_book,
)
from accountant.memory.identity import legal_form
from accountant.memory.index import MemoryIndex, normalise_vendor
from accountant.schema import Voucher
from accountant.tallyio.client import operation_id_in
from accountant.tallyio.fake import FakeTally

SEED = 7

# A prefix whose length is a whole number of twentieths, so "exactly 5%" is a
# request the injector can honour rather than refuse.
ROUND_TO = 20

FIVE_PERCENT: Fraction = percent(5)


def book() -> Book:
    return generate_book(seed=SEED)


def round_stream(b: Book) -> tuple[Voucher, ...]:
    return b.vouchers[: len(b.vouchers) // ROUND_TO * ROUND_TO]


def injected(rate: Fraction = FIVE_PERCENT, seed: int = SEED) -> InjectedBook:
    return inject(round_stream(book()), rate=rate, seed=seed)


def by_id(vouchers: Sequence[Voucher]) -> dict[str, bytes]:
    """One serialised line per voucher, keyed by id."""
    return {v.id: voucher_bytes([v]) for v in vouchers}


def vendors() -> tuple[Vendor, ...]:
    return tuple(s.vendor for s in SCHEDULED) + tuple(i.vendor for i in IRREGULAR)


# ---- criterion 1: same seed, byte-identical output --------------------------


def test_the_same_seed_produces_byte_identical_vouchers() -> None:
    first = inject(round_stream(generate_book(seed=SEED)), rate=percent(5), seed=SEED)
    second = inject(round_stream(generate_book(seed=SEED)), rate=percent(5), seed=SEED)
    assert voucher_bytes(first.vouchers) == voucher_bytes(second.vouchers)


def test_the_same_seed_produces_a_byte_identical_answer_key() -> None:
    first = injected()
    second = injected()
    assert truth_bytes(first.truth) == truth_bytes(second.truth)


def test_the_same_seed_produces_byte_identical_files_on_disk(tmp_path: Path) -> None:
    """Criterion 1, all the way to the filesystem."""
    one = write_book(tmp_path / "one", injected())
    two = write_book(tmp_path / "two", injected())
    assert one.vouchers.read_bytes() == two.vouchers.read_bytes()
    assert one.ground_truth.read_bytes() == two.ground_truth.read_bytes()


def test_a_different_seed_produces_different_bytes() -> None:
    """Without this, criterion 1 would also be satisfied by ignoring the seed."""
    mine = voucher_bytes(generate_book(seed=SEED).vouchers)
    other = voucher_bytes(generate_book(seed=SEED + 1).vouchers)
    assert mine != other


def test_a_different_injection_seed_picks_different_vouchers() -> None:
    mine = {c.voucher_id for c in injected(seed=SEED).truth}
    other = {c.voucher_id for c in injected(seed=SEED + 1).truth}
    assert mine != other


def test_the_bytes_are_locked_to_a_known_digest() -> None:
    """Byte-for-byte regression lock across processes, interpreters and runs.

    Two runs inside one process share a hash seed; this digest does not. Any
    change to a constant, an order or an arithmetic step moves it, which is
    exactly what "byte-identical" is supposed to mean.
    """
    result = injected()
    assert hashlib.sha256(voucher_bytes(result.vouchers)).hexdigest() == (
        "9bdabb0804695e31d74ad3c76938f9598f97a7ed19b26a4f7cc65d471ec42347"
    )
    assert hashlib.sha256(truth_bytes(result.truth)).hexdigest() == (
        "516e0efc6101542988fbd47bd89492ad3c79f92a64ef49fcd95448234ecbc89c"
    )


# ---- criterion 2: at least twelve simulated months --------------------------


def test_a_book_covers_at_least_twelve_months() -> None:
    months = {(v.date.year, v.date.month) for v in book().vouchers}
    assert len(months) >= MIN_MONTHS


def test_a_book_covers_exactly_the_months_it_was_asked_for() -> None:
    for asked in (12, 18, 24):
        months = {
            (v.date.year, v.date.month)
            for v in generate_book(seed=SEED, months=asked).vouchers
        }
        assert len(months) == asked


def test_no_month_in_the_span_is_empty() -> None:
    """A twelve-month book with three empty months is not a twelve-month book."""
    b = generate_book(seed=SEED, months=12)
    counted = Counter((v.date.year, v.date.month) for v in b.vouchers)
    assert min(counted.values()) > 0
    assert len(counted) == 12


def test_a_book_shorter_than_twelve_months_is_refused() -> None:
    with pytest.raises(ValueError, match="at least 12 months"):
        generate_book(seed=SEED, months=MIN_MONTHS - 1)


def test_the_book_runs_forward_in_time_without_gaps_in_the_id_sequence() -> None:
    b = book()
    assert [v.date for v in b.vouchers] == sorted(v.date for v in b.vouchers)
    assert [v.id for v in b.vouchers] == sorted(v.id for v in b.vouchers)
    assert len({v.id for v in b.vouchers}) == len(b.vouchers)


def test_add_months_rolls_the_year_over() -> None:
    start = datetime.date(2024, 4, 1)
    assert add_months(start, 0) == (2024, 4)
    assert add_months(start, 9) == (2025, 1)
    assert add_months(start, 12) == (2025, 4)


def test_the_book_shows_a_season() -> None:
    """Monsoon months trade less than the post-monsoon peak. A flat book would
    make seasonality an untested word in the spec."""
    b = generate_book(seed=SEED, months=24)
    per_month: Counter[int] = Counter(v.date.month for v in b.vouchers)
    assert per_month[7] < per_month[11]


def test_the_book_carries_quarterly_and_annual_items_as_well_as_monthly() -> None:
    counted = Counter(
        v.debit_account for v in generate_book(seed=SEED, months=24).vouchers
    )
    assert counted["Rent"] == 24  # monthly
    assert counted["Professional Fees"] == 8  # quarterly
    assert counted["Insurance"] == 2  # annual


# ---- criterion 3: every voucher balances ------------------------------------


def test_every_generated_voucher_balances() -> None:
    assert all(balances(v) for v in book().vouchers)


def test_every_corrupted_voucher_still_balances() -> None:
    """Every injected error is a CLASSIFICATION error. A corrupted voucher that
    failed arithmetic would be findable by subtraction, which would make the
    whole exercise meaningless."""
    assert all(balances(v) for v in injected().vouchers)


def test_the_whole_book_nets_to_zero_paise() -> None:
    totals: dict[str, int] = defaultdict(int)
    for v in book().vouchers:
        for account, paise in contributions(v):
            totals[account] += paise
    assert sum(totals.values()) == 0
    assert totals


UNBALANCED: tuple[tuple[str, Voucher], ...] = (
    (
        "no amount",
        Voucher(
            id="x",
            date=datetime.date(2024, 4, 1),
            party="Sharma Traders",
            narration="n",
            debit_account="Purchases",
            credit_account="Cash",
            amount_paise=0,
        ),
    ),
    (
        "both sides the same account",
        Voucher(
            id="x",
            date=datetime.date(2024, 4, 1),
            party="Sharma Traders",
            narration="n",
            debit_account="Cash",
            credit_account="Cash",
            amount_paise=1000,
        ),
    ),
    (
        "gst larger than the total it sits inside",
        Voucher(
            id="x",
            date=datetime.date(2024, 4, 1),
            party="Sharma Traders",
            narration="n",
            debit_account="Purchases",
            credit_account="Cash",
            amount_paise=1000,
            gst_paise=1000,
        ),
    ),
    (
        "negative gst",
        Voucher(
            id="x",
            date=datetime.date(2024, 4, 1),
            party="Sharma Traders",
            narration="n",
            debit_account="Purchases",
            credit_account="Cash",
            amount_paise=1000,
            gst_paise=-1,
        ),
    ),
)


@pytest.mark.parametrize("name,voucher", UNBALANCED)
def test_balances_rejects_a_voucher_that_does_not(name: str, voucher: Voucher) -> None:
    """Otherwise `balances` could `return True` and criterion 3 would be free."""
    assert balances(voucher) is False, name


def test_money_is_integer_paise_and_never_a_float() -> None:
    for v in injected().vouchers:
        assert type(v.amount_paise) is int
        assert v.gst_paise is None or type(v.gst_paise) is int


def test_gst_is_extracted_from_a_tax_inclusive_total() -> None:
    assert gst_of(236_000, 1800) == 36_000
    assert gst_of(236_000, 0) is None


# ---- criterion 4: an exact error rate ---------------------------------------


def test_five_percent_of_the_book_is_exactly_five_percent() -> None:
    """Exact, not approximate. 5% of 260 vouchers is 13 - not 12, not 14."""
    stream = round_stream(book())
    assert len(stream) % ROUND_TO == 0
    result = inject(stream, rate=percent(5), seed=SEED)
    assert len(result.truth) == len(stream) // ROUND_TO


RATES: tuple[tuple[int, Fraction, int], ...] = (
    (100, percent(5), 5),
    (20, percent(5), 1),
    (400, percent(5), 20),
    (1000, percent(1), 10),
    (260, percent(50), 130),
    (17, percent(0), 0),
    (0, percent(5), 0),
)


@pytest.mark.parametrize("n,rate,expected", RATES)
def test_count_for_is_exact(n: int, rate: Fraction, expected: int) -> None:
    assert count_for(n, rate) == expected


def test_a_rate_that_is_not_a_whole_number_of_vouchers_is_refused() -> None:
    """Silently delivering 4.8% and calling it 5% is the failure this prevents."""
    with pytest.raises(ValueError, match="not a whole number"):
        count_for(333, percent(5))


def test_a_rate_outside_zero_to_one_is_refused() -> None:
    with pytest.raises(ValueError, match="between 0 and 1"):
        count_for(100, Fraction(3, 2))
    with pytest.raises(ValueError, match="between 0 and 1"):
        count_for(100, Fraction(-1, 100))


def test_a_negative_stream_length_is_refused() -> None:
    with pytest.raises(ValueError, match="cannot have"):
        count_for(-1, percent(5))


def test_zero_percent_corrupts_nothing() -> None:
    result = injected(rate=percent(0))
    assert result.truth == ()
    assert result.vouchers == round_stream(book())


def test_every_error_type_is_used_and_none_is_invented() -> None:
    used = Counter(c.error_type for c in injected().truth)
    assert set(used) == set(ERROR_TYPES)
    assert max(used.values()) - min(used.values()) <= 1


def test_no_voucher_carries_two_errors() -> None:
    truth = injected().truth
    assert len({c.voucher_id for c in truth}) == len(truth)


def test_a_rate_with_nowhere_left_to_put_an_error_is_refused_not_faked() -> None:
    """Not every voucher can carry every error type. When a quota cannot be met
    the injector says so, rather than quietly delivering fewer errors than the
    rate it reported."""
    with pytest.raises(ValueError, match="eligible vouchers"):
        injected(rate=Fraction(1))


def test_the_injected_rate_is_carried_on_the_result() -> None:
    result = injected(rate=percent(5))
    assert result.rate == Fraction(1, 20)
    assert result.seed == SEED


# ---- criterion 5: ground truth is a separate file ---------------------------


def test_the_two_streams_are_written_to_two_separate_files(tmp_path: Path) -> None:
    files = write_book(tmp_path / "book", injected())
    assert files.vouchers != files.ground_truth
    assert files.vouchers.name == serialise.VOUCHERS_FILENAME
    assert files.ground_truth.name == serialise.GROUND_TRUTH_FILENAME
    assert files.vouchers.read_bytes()
    assert files.ground_truth.read_bytes()


def test_every_voucher_record_carries_the_same_keys() -> None:
    """A key that appeared only on corrupted entries would be the marker."""
    result = injected()
    corrupt = {c.voucher_id for c in result.truth}
    keys = {frozenset(voucher_record(v)) for v in result.vouchers}
    assert len(keys) == 1
    assert corrupt  # the comparison above is only meaningful if some are corrupt


ANSWER_KEY_WORDS: tuple[str, ...] = (
    *ERROR_TYPES,
    "corrupt",
    "inject",
    "ground_truth",
    "error_type",
    "answer",
)


def test_the_voucher_stream_contains_no_word_from_the_answer_key() -> None:
    result = injected()
    stream = voucher_bytes(result.vouchers).lower()
    key = truth_bytes(result.truth).lower()
    for word in ANSWER_KEY_WORDS:
        assert word.encode() not in stream, word
    for word in ERROR_TYPES:
        assert word.encode() in key, word


def test_the_voucher_stream_differs_from_the_clean_book_only_where_truth_says() -> None:
    """The decisive one for criterion 5.

    Serialise the clean book and the corrupted book. The set of records that
    changed must equal the set of ids in the answer key - no more, so nothing
    was marked; no fewer, so the answer key is complete.
    """
    clean = round_stream(book())
    result = inject(clean, rate=percent(5), seed=SEED)
    before, after = by_id(clean), by_id(result.vouchers)
    assert set(before) == set(after)
    differing = {vid for vid in before if before[vid] != after[vid]}
    assert differing == {c.voucher_id for c in result.truth}


def test_injection_changes_no_voucher_id() -> None:
    clean = round_stream(book())
    result = inject(clean, rate=percent(5), seed=SEED)
    assert [v.id for v in result.vouchers] == [v.id for v in clean]


def test_corrupt_entries_are_not_clustered_where_position_would_reveal_them() -> None:
    result = injected()
    positions = [
        i
        for i, v in enumerate(result.vouchers)
        if v.id in {c.voucher_id for c in result.truth}
    ]
    months = {
        v.date.month
        for v in result.vouchers
        if v.id in {c.voucher_id for c in result.truth}
    }
    assert len(months) >= 3
    assert positions != list(range(positions[0], positions[0] + len(positions)))


def test_no_generated_narration_carries_our_own_marker() -> None:
    """A generated book is the company's own history. If a narration looked like
    something we wrote, `MemoryIndex.from_vouchers` would skip it."""
    assert all(operation_id_in(v.narration) is None for v in injected().vouchers)


def test_nothing_generated_pretends_to_have_come_from_us() -> None:
    for v in injected().vouchers:
        assert v.tally_id is None
        assert v.provenance is None


# ---- criterion 6: one vendor under three or more spellings ------------------


def test_at_least_one_vendor_appears_under_three_or_more_spellings() -> None:
    seen: dict[str, set[str]] = defaultdict(set)
    for v in book().vouchers:
        seen[normalise_vendor(v.party)].add(v.party)
    assert max(len(s) for s in seen.values()) >= 3


def test_every_spelling_of_a_vendor_collapses_to_one_key() -> None:
    """Name noise that did NOT collapse would silently split one vendor into
    two, and `vendor_switch` could never fire on either half.

    REWRITTEN 2026-08-10, owner ruling D-05. This used to require EVERY
    generated spelling of a vendor to reach one key, including spellings that
    differ by a legal form - the fixture pairs a bare "Maharashtra State
    Electricity Distribution" with a "... Ltd". The owner ruled that a legal
    form is identity-bearing and not name noise, so those two are no longer one
    key and must not be.

    The property that mattered is kept and narrowed: spellings that differ only
    in NOISE must still collapse. A spelling that adds or changes a legal form
    is excluded, because splitting it is now the correct answer.
    """
    for vendor in vendors():
        by_form: dict[str, set[str]] = defaultdict(set)
        for spelling in vendor.spellings:
            by_form[legal_form(spelling)].add(normalise_vendor(spelling))
        for form, keys in by_form.items():
            assert len(keys) == 1, (vendor.spellings, form)


def test_no_two_vendors_collapse_to_the_same_key() -> None:
    keys = [normalise_vendor(v.spellings[0]) for v in vendors()]
    assert len(set(keys)) == len(keys)


def test_the_memory_index_sees_exactly_one_account_per_vendor() -> None:
    index = MemoryIndex.from_vouchers(book().vouchers)
    for vendor in index.vendors():
        assert index.lookup(vendor).status.value == "match", vendor


# ---- the four error types are the detectors' four, not four we invented -----


def test_the_error_types_are_exactly_the_detectors_we_have() -> None:
    assert set(ERROR_TYPES) == {d.__name__ for d in detectors.ALL_DETECTORS}
    assert len(ERROR_TYPES) == len(detectors.ALL_DETECTORS)


def test_every_injected_error_is_caught_by_its_own_detector() -> None:
    """The injector's notion of each error must be the detector's notion.

    Judged against the clean history the entry would really have been compared
    with: every other voucher in the book.
    """
    clean = round_stream(book())
    result = inject(clean, rate=percent(5), seed=SEED)
    corrupted = {v.id: v for v in result.vouchers}
    by_name = {d.__name__: d for d in detectors.ALL_DETECTORS}

    missed: list[Corruption] = []
    for c in result.truth:
        history = tuple(v for v in clean if v.id != c.voucher_id)
        index = MemoryIndex.from_vouchers(history)
        if not by_name[c.error_type](corrupted[c.voucher_id], history, index):
            missed.append(c)
    assert missed == []


def test_a_first_use_account_has_no_history_in_the_clean_book() -> None:
    used = {v.debit_account for v in book().vouchers} | {
        v.credit_account for v in book().vouchers
    }
    assert not used & set(UNPOSTED_ACCOUNTS)
    assert set(POSTED_ACCOUNTS) <= used
    assert set(UNPOSTED_ACCOUNTS) <= set(CHART)


# ---- a voucher an error type cannot be applied to is not chosen for it ------


def hand(
    vid: str,
    party: str,
    debit: str,
    amount: int = 100_000,
    gst: int | None = None,
    credit: str = "Cash",
) -> Voucher:
    return Voucher(
        id=vid,
        date=datetime.date(2024, 4, 1),
        party=party,
        narration="n",
        debit_account=debit,
        credit_account=credit,
        amount_paise=amount,
        gst_paise=gst,
    )


def test_a_vendor_already_posted_to_two_accounts_cannot_be_switched() -> None:
    """`vendor_switch` means "consistently posted to X, this one goes to Y". A
    vendor with no consistent X has no switch to inject."""
    stream = (
        hand("a", "Sharma Traders", "Purchases"),
        hand("b", "Sharma Traders", "Rent"),
        hand("c", "Kohli Estates", "Rent"),
    )
    result = inject(stream, rate=Fraction(1, 3), seed=SEED)
    assert [c.error_type for c in result.truth] == ["vendor_switch"]
    assert result.truth[0].voucher_id == "c"


def test_a_voucher_with_only_one_place_left_to_go_cannot_be_switched() -> None:
    """The credit side is never a switch target, so a one-account book offers
    nowhere to move to."""
    stream = (hand("a", "Kohli Estates", "Rent"), hand("b", "Kohli Estates", "Rent"))
    with pytest.raises(ValueError, match="vendor_switch needs 1 eligible"):
        inject(stream, rate=percent(50), seed=SEED)


def test_a_voucher_already_on_an_unposted_account_is_not_a_first_use() -> None:
    stream = (
        hand("a", "Kohli Estates", UNPOSTED_ACCOUNTS[0]),
        hand("b", "Verma Cement Depot", UNPOSTED_ACCOUNTS[1]),
        hand("c", "Gupta Hardware Mart", UNPOSTED_ACCOUNTS[0]),
        hand("d", "Balaji Timber Works", UNPOSTED_ACCOUNTS[1]),
    )
    with pytest.raises(ValueError, match="first_use needs 1 eligible"):
        inject(stream, rate=percent(50), seed=SEED)


GST_EVERYWHERE: tuple[tuple[str, str], ...] = (
    ("Kohli Estates", "Rent"),
    ("Sharma Traders", "Purchases"),
    ("Verma Cement Depot", "Purchases"),
    ("Gupta Hardware Mart", "Purchases"),
    ("Balaji Timber Works", "Purchases"),
    ("Ravi Electricals", "Repairs & Maintenance"),
    ("Nagpur Ad Media", "Advertising"),
    ("Staff Payroll", "Salaries"),
)


def test_an_account_that_already_carries_gst_is_no_gst_anomaly() -> None:
    stream = tuple(
        hand(f"v{i}", party, account, gst=1000)
        for i, (party, account) in enumerate(GST_EVERYWHERE)
    )
    with pytest.raises(ValueError, match="gst_anomaly needs 1 eligible"):
        inject(stream, rate=percent(50), seed=SEED)


def test_an_empty_stream_is_injected_with_nothing(tmp_path: Path) -> None:
    result = inject((), rate=percent(5), seed=SEED)
    assert result.vouchers == ()
    assert result.truth == ()
    files = write_book(tmp_path / "empty", result)
    assert files.vouchers.read_bytes() == b""
    assert files.ground_truth.read_bytes() == b""


# ---- the output feeds the shapes downstream --------------------------------


def test_the_book_loads_into_the_tally_connector_and_balances_there() -> None:
    b = book()
    tally = FakeTally()
    tally.add_company(b.company, accounts=b.chart, vouchers=b.vouchers)
    assert len(tally.read_vouchers(b.company)) == len(b.vouchers)
    assert sum(tally.trial_balance(b.company).values()) == 0
    assert tally.list_our_vouchers(b.company) == ()
