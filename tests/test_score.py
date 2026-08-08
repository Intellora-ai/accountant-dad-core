"""Child 4 — the scoring harness, against fixtures built here.

One section per acceptance criterion:

    1  a zero-error book reports catch rate n/a plus a false-alarm figure
    2  an injected-error book reports catch rate per error type
    3  N1, N2 and N3 each appear with an explicit PASS or FAIL, tested on
       both sides of every threshold
    4  same seed, identical numbers
    5  the report states the seed, the error rate, and the R and D values used

accountant/generate/ is a separate package and is deliberately not imported.
Every book here is constructed in this file.
"""

from __future__ import annotations

import ast
import datetime
import inspect
import re
from collections.abc import Sequence
from pathlib import Path

import pytest

import accountant.score as score_pkg
from accountant.detect import detectors
from accountant.schema import Outcome, Voucher
from accountant.score import harness
from accountant.score.book import Book, GroundTruth, InjectedError
from accountant.score.harness import ErrorTypeCatch, ScoreReport, Status, score
from accountant.score.report import render

COMPANY = "Score Co"
ACCOUNTS = ("Purchases", "Materials", "Utilities", "Repairs", "Bank", "Cash")
WHEN = datetime.date(2026, 1, 1)

# R and D used by most tests. They are arbitrary stand-ins for a stopwatch, and
# the harness refuses to supply any default of its own.
R = 30
D = 30


def _history(party: str, account: str, amount: int, n: int, tag: str) -> list[Voucher]:
    return [
        Voucher(
            id=f"hist-{tag}-{i}",
            date=WHEN,
            party=party,
            narration=f"{party} {account}",
            debit_account=account,
            credit_account="Cash",
            amount_paise=amount,
        )
        for i in range(n)
    ]


# Sharma Traders is a clean MATCH on Purchases, capped at 300000 paise.
# Kumar Cement is a clean MATCH on Materials.
# Verma Electric is CONFLICTED across Utilities and Repairs, capped at 120000.
HISTORY: tuple[Voucher, ...] = tuple(
    _history("Sharma Traders", "Purchases", 300_000, 12, "sharma")
    + _history("Kumar Cement", "Materials", 500_000, 8, "kumar")
    + _history("Verma Electric", "Utilities", 120_000, 5, "verma-u")
    + _history("Verma Electric", "Repairs", 120_000, 3, "verma-r")
)


def _entry(
    eid: str,
    party: str,
    account: str,
    amount: int = 100_000,
    gst: int | None = None,
) -> Voucher:
    return Voucher(
        id=eid,
        date=WHEN,
        party=party,
        narration=f"{party} {account}",
        debit_account=account,
        credit_account="Cash",
        amount_paise=amount,
        gst_paise=gst,
    )


def clean(i: int) -> Voucher:
    """Known vendor, usual account, unremarkable amount. No detector fires."""
    return _entry(f"clean-{i}", "Sharma Traders", "Purchases")


def noisy(i: int) -> Voucher:
    """Clean, but larger than this account has ever seen: magnitude fires.

    Nothing is wrong with it. That is exactly what a false alarm is.
    """
    return _entry(f"noisy-{i}", "Sharma Traders", "Purchases", amount=400_000)


def caught(i: int) -> Voucher:
    """Injected: a MATCHed vendor sent somewhere else. vendor_switch fires."""
    return _entry(f"caught-{i}", "Sharma Traders", "Materials")


def missed(i: int) -> Voucher:
    """Injected, but invisible: the vendor is CONFLICTED, the account is in

    use, and the amount is inside the account's own range. No detector fires.
    """
    return _entry(f"missed-{i}", "Verma Electric", "Repairs")


def gst_error(i: int) -> Voucher:
    """Injected: GST on an account that has never carried it."""
    return _entry(f"gst-{i}", "Sharma Traders", "Purchases", gst=5_000)


def new_account(i: int) -> Voucher:
    """Injected: an account the company has never posted to. first_use fires."""
    return _entry(f"new-{i}", "Verma Electric", "Bank")


def make_book(
    entries: Sequence[tuple[Voucher, str | None]],
    *,
    seed: int = 4242,
    error_rate_per_10_000: int = 0,
) -> Book:
    return Book(
        company=COMPANY,
        accounts=ACCOUNTS,
        history=HISTORY,
        entries=tuple(v for v, _ in entries),
        truth=GroundTruth(
            seed=seed,
            error_rate_per_10_000=error_rate_per_10_000,
            injected=tuple(
                InjectedError(voucher_id=v.id, error_type=t)
                for v, t in entries
                if t is not None
            ),
        ),
    )


def scored(
    entries: Sequence[tuple[Voucher, str | None]],
    *,
    read_seconds: int = R,
    dismiss_seconds: int = D,
    seed: int = 4242,
    error_rate_per_10_000: int = 0,
) -> ScoreReport:
    return score(
        make_book(entries, seed=seed, error_rate_per_10_000=error_rate_per_10_000),
        read_seconds=read_seconds,
        dismiss_seconds=dismiss_seconds,
    )


# ---- the fixtures do what the tests assume ---------------------------------


def test_a_clean_entry_raises_no_flag_and_would_post() -> None:
    r = scored([(clean(0), None)])
    assert r.entries[0].flags == ()
    assert r.entries[0].outcome is Outcome.VALID
    assert r.false_alarms == 0


def test_each_injected_shape_fires_the_detector_it_was_built_for() -> None:
    r = scored(
        [
            (caught(0), "wrong_account"),
            (gst_error(0), "phantom_gst"),
            (new_account(0), "unused_account"),
        ]
    )
    fired = {e.voucher_id: e.flags for e in r.entries}
    assert fired["caught-0"] == ("vendor_switch",)
    assert fired["gst-0"] == ("gst_anomaly",)
    assert fired["new-0"] == ("first_use",)


def test_a_caught_error_never_posts_by_itself() -> None:
    """A flag becomes a question, so the entry is not auto-posted."""
    r = scored([(caught(0), "wrong_account")])
    assert r.entries[0].outcome is not Outcome.VALID


def test_a_missed_error_fires_nothing() -> None:
    r = scored([(missed(0), "wrong_account")])
    assert r.entries[0].flags == ()
    assert r.caught == 0


# ---- criterion 1: a zero-error book ----------------------------------------


def test_a_zero_error_book_reports_catch_rate_not_available() -> None:
    r = scored([(clean(i), None) for i in range(10)])
    assert r.injected_entries == 0
    assert r.overall_catch_hundredths is None
    assert r.per_type == ()
    assert "n/a" in render(r)


def test_a_zero_error_book_still_reports_a_false_alarm_figure() -> None:
    entries: list[tuple[Voucher, str | None]] = [(clean(i), None) for i in range(9)]
    entries.append((noisy(0), None))
    r = scored(entries)
    assert r.clean_entries == 10
    assert r.false_alarms == 1
    assert r.n1.measured_hundredths == 1000  # 10.00 per 100 clean entries
    assert "false alarms (clean)" in render(r)


def test_a_zero_error_book_fails_n3_rather_than_passing_on_no_evidence() -> None:
    r = scored([(clean(i), None) for i in range(10)])
    assert r.n3.status is Status.MISSED
    assert "nothing was measured" in r.n3.detail


# ---- criterion 2: catch rate per error type --------------------------------


def test_an_injected_book_reports_catch_rate_per_error_type() -> None:
    r = scored(
        [
            (caught(0), "wrong_account"),
            (caught(1), "wrong_account"),
            (missed(0), "wrong_account"),
            (gst_error(0), "phantom_gst"),
            (new_account(0), "unused_account"),
        ]
    )
    rows = {t.error_type: (t.caught, t.injected) for t in r.per_type}
    assert rows == {
        "wrong_account": (2, 3),
        "phantom_gst": (1, 1),
        "unused_account": (1, 1),
    }


def test_per_type_rows_are_ordered_by_error_type_name() -> None:
    r = scored(
        [
            (new_account(0), "unused_account"),
            (gst_error(0), "phantom_gst"),
            (caught(0), "wrong_account"),
        ]
    )
    names = [t.error_type for t in r.per_type]
    assert names == sorted(names)


def test_per_type_rates_round_to_two_places_without_a_float() -> None:
    r = scored(
        [
            (caught(0), "wrong_account"),
            (missed(0), "wrong_account"),
            (missed(1), "wrong_account"),
        ]
    )
    (row,) = r.per_type
    assert row.rate_hundredths == 3333  # 1 of 3 = 33.33%
    assert isinstance(row.rate_hundredths, int)
    assert "33.33%" in render(r)


def _percent_tokens(text: str) -> list[str]:
    return re.findall(r"\d+\.\d\d%", text)


def test_every_percentage_renders_to_exactly_two_decimal_places() -> None:
    """Pins the printed digits, not just the integer behind them."""
    entries: list[tuple[Voucher, str | None]] = [
        (caught(i), "wrong_account") for i in range(9)
    ]
    entries.extend([(missed(0), "wrong_account"), (gst_error(0), "phantom_gst")])
    tokens = _percent_tokens(render(scored(entries)))
    assert "90.00%" in tokens  # wrong_account, 9 of 10
    assert "100.00%" in tokens  # phantom_gst, 1 of 1
    assert "90.91%" in tokens  # overall, 10 of 11, rounded half up


def test_the_measured_value_is_printed_with_its_unit() -> None:
    entries: list[tuple[Voucher, str | None]] = [(clean(i), None) for i in range(9)]
    entries.extend([(noisy(0), None), (caught(0), "wrong_account")])
    text = render(scored(entries, read_seconds=30, dismiss_seconds=30))
    assert "measured 10.00 per 100 clean entries" in text
    assert "measured 18.18 percent of read-everything time" in text
    assert "measured 100.00 percent, worst error type" in text


def test_a_metric_with_nothing_measured_prints_n_a_and_fails() -> None:
    text = render(scored([(clean(0), None)]))
    assert "measured n/a" in text
    assert "FAIL" in text


def test_every_error_type_row_appears_in_the_rendered_report() -> None:
    r = scored(
        [(caught(0), "wrong_account"), (gst_error(0), "phantom_gst")],
    )
    text = render(r)
    assert "wrong_account" in text
    assert "phantom_gst" in text


# ---- criterion 3: N1, N2, N3 each PASS or FAIL, both sides of each ---------


def test_all_three_targets_carry_an_explicit_verdict() -> None:
    r = scored([(caught(0), "wrong_account"), (clean(0), None)])
    assert [m.name for m in r.metrics] == ["N1", "N2", "N3"]
    for m in r.metrics:
        assert m.status in (Status.MET, Status.MISSED)
    text = render(r)
    for name in ("N1", "N2", "N3"):
        assert name in text
    assert "PASS" in text or "FAIL" in text


def test_n1_passes_at_exactly_ten_false_alarms_per_hundred() -> None:
    entries: list[tuple[Voucher, str | None]] = [(clean(i), None) for i in range(9)]
    entries.append((noisy(0), None))
    r = scored(entries)
    assert r.false_alarms == 1
    assert r.clean_entries == 10
    assert r.n1.measured_hundredths == 1000
    assert r.n1.status is Status.MET


def test_n1_fails_one_false_alarm_above_the_target() -> None:
    entries: list[tuple[Voucher, str | None]] = [(clean(i), None) for i in range(8)]
    entries.extend([(noisy(0), None), (noisy(1), None)])
    r = scored(entries)
    assert r.false_alarms == 2
    assert r.clean_entries == 10
    assert r.n1.measured_hundredths == 2000
    assert r.n1.status is Status.MISSED


def test_n2_passes_at_exactly_ten_percent() -> None:
    entries: list[tuple[Voucher, str | None]] = [(clean(i), None) for i in range(9)]
    entries.append((noisy(0), None))
    r = scored(entries, read_seconds=30, dismiss_seconds=30)
    assert r.flagged_entries == 1
    assert r.total_entries == 10
    assert r.n2.measured_hundredths == 1000
    assert r.n2.status is Status.MET


def test_n2_fails_one_second_of_d_above_the_target() -> None:
    entries: list[tuple[Voucher, str | None]] = [(clean(i), None) for i in range(9)]
    entries.append((noisy(0), None))
    r = scored(entries, read_seconds=30, dismiss_seconds=31)
    assert r.n2.measured_hundredths == 1033
    assert r.n2.status is Status.MISSED


def test_n2_counts_every_flagged_entry_not_only_the_false_alarms() -> None:
    entries: list[tuple[Voucher, str | None]] = [(clean(i), None) for i in range(8)]
    entries.extend([(noisy(0), None), (caught(0), "wrong_account")])
    r = scored(entries, read_seconds=30, dismiss_seconds=30)
    assert r.flagged_entries == 2
    assert "60s dismissing 2 flagged entries against 300s reading all 10" in r.n2.detail


def test_n3_passes_at_exactly_ninety_percent() -> None:
    entries: list[tuple[Voucher, str | None]] = [
        (caught(i), "wrong_account") for i in range(9)
    ]
    entries.append((missed(0), "wrong_account"))
    r = scored(entries)
    assert r.per_type == (ErrorTypeCatch("wrong_account", injected=10, caught=9),)
    assert r.n3.measured_hundredths == 9000
    assert r.n3.status is Status.MET


def test_n3_fails_one_missed_error_below_the_target() -> None:
    entries: list[tuple[Voucher, str | None]] = [
        (caught(i), "wrong_account") for i in range(8)
    ]
    entries.extend([(missed(0), "wrong_account"), (missed(1), "wrong_account")])
    r = scored(entries)
    assert r.n3.measured_hundredths == 8000
    assert r.n3.status is Status.MISSED


def test_n3_fails_when_any_single_error_type_is_below_the_target() -> None:
    """One perfect type must not carry a bad one. N3 is per type."""
    entries: list[tuple[Voucher, str | None]] = [
        (gst_error(i), "phantom_gst") for i in range(10)
    ]
    entries.extend(
        [(caught(0), "wrong_account"), (missed(0), "wrong_account")],
    )
    r = scored(entries)
    assert {t.error_type: t.passes for t in r.per_type} == {
        "phantom_gst": True,
        "wrong_account": False,
    }
    assert r.n3.status is Status.MISSED
    assert "wrong_account" in r.n3.detail


def test_n3_reports_the_worst_error_type_as_its_measurement() -> None:
    entries: list[tuple[Voucher, str | None]] = [
        (gst_error(i), "phantom_gst") for i in range(4)
    ]
    entries.extend(
        [
            (caught(0), "wrong_account"),
            (missed(0), "wrong_account"),
            (missed(1), "wrong_account"),
            (missed(2), "wrong_account"),
        ]
    )
    r = scored(entries)
    assert r.n3.measured_hundredths == 2500  # 1 of 4, the worse of the two
    assert r.n3.status is Status.MISSED


def test_a_book_with_no_clean_entries_fails_n1_rather_than_passing() -> None:
    r = scored([(caught(0), "wrong_account")])
    assert r.clean_entries == 0
    assert r.n1.measured_hundredths is None
    assert r.n1.status is Status.MISSED
    assert "nothing was measured" in r.n1.detail


def test_an_empty_book_fails_all_three() -> None:
    r = scored([])
    assert [m.status for m in r.metrics] == [Status.MISSED] * 3
    assert r.passed is False
    assert "n/a" in render(r)


def test_all_three_passing_reports_a_pass_overall() -> None:
    entries: list[tuple[Voucher, str | None]] = [(clean(i), None) for i in range(20)]
    entries.append((caught(0), "wrong_account"))
    r = scored(entries, read_seconds=60, dismiss_seconds=30)
    assert [m.status for m in r.metrics] == [Status.MET] * 3
    assert r.passed is True
    assert "All three targets: PASS" in render(r)


def test_a_clarifying_question_is_not_a_false_alarm() -> None:
    """An unseen vendor makes the system ask, not claim something is wrong.

    Questions and false alarms are counted separately in the frozen
    definitions, and N1 counts flags only.
    """
    unseen = _entry("clean-unseen", "Gupta Hardware", "Purchases")
    r = scored([(unseen, None)])
    assert r.entries[0].outcome is Outcome.UNCLEAR
    assert r.entries[0].flags == ()
    assert r.false_alarms == 0
    assert r.n1.status is Status.MET


# ---- criterion 4: same seed, identical numbers ------------------------------


def test_scoring_the_same_book_twice_gives_identical_numbers() -> None:
    entries: list[tuple[Voucher, str | None]] = [
        (clean(0), None),
        (noisy(0), None),
        (caught(0), "wrong_account"),
        (missed(0), "wrong_account"),
        (gst_error(0), "phantom_gst"),
    ]
    book = make_book(entries, seed=7, error_rate_per_10_000=0)
    first = score(book, read_seconds=R, dismiss_seconds=D)
    second = score(book, read_seconds=R, dismiss_seconds=D)
    assert first == second
    assert render(first) == render(second)


def test_two_identically_built_books_score_identically() -> None:
    def build() -> Book:
        return make_book(
            [
                (clean(0), None),
                (noisy(0), None),
                (caught(0), "wrong_account"),
                (missed(0), "wrong_account"),
            ],
            seed=99,
            error_rate_per_10_000=250,
        )

    a = score(build(), read_seconds=R, dismiss_seconds=D)
    b = score(build(), read_seconds=R, dismiss_seconds=D)
    assert a == b
    assert render(a) == render(b)


def test_entry_results_keep_the_order_of_the_book() -> None:
    entries: list[tuple[Voucher, str | None]] = [
        (caught(0), "wrong_account"),
        (clean(0), None),
        (missed(0), "wrong_account"),
    ]
    r = scored(entries)
    assert [e.voucher_id for e in r.entries] == [v.id for v, _ in entries]


# ---- criterion 5: the report states seed, error rate, R and D ---------------


def test_the_report_states_the_seed_the_error_rate_and_r_and_d() -> None:
    r = scored(
        [(clean(0), None), (caught(0), "wrong_account")],
        read_seconds=45,
        dismiss_seconds=20,
        seed=31337,
        error_rate_per_10_000=250,
    )
    text = render(r)
    assert "seed" in text
    assert "31337" in text
    assert "250 per 10,000 entries" in text
    assert "R, read one entry" in text
    assert "45 s" in text
    assert "D, dismiss one flagged" in text
    assert "20 s" in text


def test_the_report_says_r_and_d_are_self_timed_not_a_measurement() -> None:
    r = scored([(clean(0), None)], read_seconds=5, dismiss_seconds=5)
    text = render(r)
    assert "self-timed" in text
    assert "not a professional measurement" in text


def test_the_report_names_every_detector_that_ran() -> None:
    r = scored([(clean(0), None)])
    assert set(r.detectors) == {
        "vendor_switch",
        "first_use",
        "magnitude",
        "gst_anomaly",
    }
    for name in r.detectors:
        assert name in render(r)


# ---- R and D are parameters, never constants -------------------------------


def test_r_and_d_have_no_default_value() -> None:
    """The owner has not supplied these numbers, so the code must not either."""
    params = inspect.signature(score).parameters
    assert params["read_seconds"].default is inspect.Parameter.empty
    assert params["dismiss_seconds"].default is inspect.Parameter.empty


@pytest.mark.parametrize(("read_seconds", "dismiss_seconds"), [(0, 30), (30, 0)])
def test_r_and_d_must_be_at_least_one_second(
    read_seconds: int, dismiss_seconds: int
) -> None:
    with pytest.raises(ValueError, match="self-timed"):
        scored(
            [(clean(0), None)],
            read_seconds=read_seconds,
            dismiss_seconds=dismiss_seconds,
        )


def test_changing_d_changes_n2_and_nothing_else() -> None:
    entries: list[tuple[Voucher, str | None]] = [(clean(i), None) for i in range(9)]
    entries.append((noisy(0), None))
    cheap = scored(entries, read_seconds=30, dismiss_seconds=1)
    dear = scored(entries, read_seconds=30, dismiss_seconds=300)
    assert cheap.n1 == dear.n1
    assert cheap.n2.status is Status.MET
    assert dear.n2.status is Status.MISSED


def test_scoring_with_no_detectors_is_refused() -> None:
    with pytest.raises(ValueError, match="no detectors"):
        score(
            make_book([(clean(0), None)]),
            read_seconds=R,
            dismiss_seconds=D,
            detector_set=(),
        )


def test_a_narrower_detector_set_is_reported_as_such() -> None:
    r = score(
        make_book([(gst_error(0), "phantom_gst")]),
        read_seconds=R,
        dismiss_seconds=D,
        detector_set=detectors.SLICE_4_DETECTORS,
    )
    assert r.detectors == ("vendor_switch",)
    assert r.caught == 0  # gst_anomaly did not run, so nothing caught it


# ---- the input contract validates itself -----------------------------------


def test_ground_truth_naming_an_entry_the_book_lacks_is_refused() -> None:
    with pytest.raises(ValueError, match="does not contain"):
        Book(
            company=COMPANY,
            accounts=ACCOUNTS,
            history=HISTORY,
            entries=(clean(0),),
            truth=GroundTruth(
                seed=1,
                error_rate_per_10_000=0,
                injected=(InjectedError("ghost-1", "wrong_account"),),
            ),
        )


def test_duplicate_entry_ids_are_refused() -> None:
    with pytest.raises(ValueError, match="duplicate entry ids"):
        Book(
            company=COMPANY,
            accounts=ACCOUNTS,
            history=HISTORY,
            entries=(clean(0), clean(0)),
            truth=GroundTruth(seed=1, error_rate_per_10_000=0),
        )


def test_one_entry_cannot_carry_two_injected_errors() -> None:
    with pytest.raises(ValueError, match="two injected errors"):
        GroundTruth(
            seed=1,
            error_rate_per_10_000=0,
            injected=(
                InjectedError("caught-0", "wrong_account"),
                InjectedError("caught-0", "phantom_gst"),
            ),
        )


@pytest.mark.parametrize("rate", [-1, 10_001])
def test_an_impossible_error_rate_is_refused(rate: int) -> None:
    with pytest.raises(ValueError, match="outside"):
        GroundTruth(seed=1, error_rate_per_10_000=rate)


@pytest.mark.parametrize(
    ("voucher_id", "error_type", "message"),
    [("", "wrong_account", "names no voucher"), ("e-1", "  ", "names no type")],
)
def test_an_unnamed_injected_error_is_refused(
    voucher_id: str, error_type: str, message: str
) -> None:
    with pytest.raises(ValueError, match=message):
        InjectedError(voucher_id=voucher_id, error_type=error_type)


def test_a_book_counts_its_own_clean_and_injected_entries() -> None:
    book = make_book([(clean(0), None), (caught(0), "wrong_account")])
    assert book.injected_count == 1
    assert book.clean_count == 1


@pytest.mark.parametrize(("injected", "caught"), [(0, 0), (2, 3), (2, -1)])
def test_an_impossible_error_type_row_is_refused(injected: int, caught: int) -> None:
    with pytest.raises(ValueError):
        ErrorTypeCatch(error_type="wrong_account", injected=injected, caught=caught)


# ---- the N3 caveat travels with the number ---------------------------------


CAVEAT_WORDS = ("build-correctness check", "not evidence of product value")


def test_the_n3_caveat_is_in_the_package_docstring() -> None:
    doc = score_pkg.__doc__ or ""
    for phrase in CAVEAT_WORDS:
        assert phrase in doc


def test_the_n3_caveat_is_in_the_harness_module_docstring() -> None:
    doc = harness.__doc__ or ""
    for phrase in CAVEAT_WORDS:
        assert phrase in doc


def test_the_rendered_report_carries_the_n3_caveat() -> None:
    # Normalised, because the caveat is wrapped to the report width.
    text = " ".join(render(scored([(caught(0), "wrong_account")])).split())
    for phrase in CAVEAT_WORDS:
        assert phrase in text
    assert "not a claim about a real book" in text


# ---- what this package must not do -----------------------------------------


def _sources() -> list[tuple[str, str]]:
    directory = Path(str(score_pkg.__file__)).parent
    return [
        (p.name, p.read_text(encoding="utf-8")) for p in sorted(directory.glob("*.py"))
    ]


def test_the_package_has_source_to_inspect() -> None:
    assert {name for name, _ in _sources()} == {
        "__init__.py",
        "book.py",
        "harness.py",
        "report.py",
    }


@pytest.mark.parametrize(
    "forbidden", ["write_voucher", "TallyClient", "reverse_by_operation_id", "tallyio"]
)
def test_the_harness_never_touches_the_tally_connector(forbidden: str) -> None:
    for name, text in _sources():
        assert forbidden not in text, name


def test_the_harness_does_not_import_the_generator() -> None:
    for name, text in _sources():
        assert "accountant.generate" not in text, name


def test_the_package_does_arithmetic_in_integers_only() -> None:
    """No float literal, no float(), no true division anywhere in the package.

    Percentages are carried as whole hundredths, so a reported number can never
    drift with binary floating point.
    """
    for name, text in _sources():
        for node in ast.walk(ast.parse(text)):
            if isinstance(node, ast.Constant):
                assert not isinstance(node.value, float), name
            if isinstance(node, ast.Name):
                assert node.id != "float", name
            assert not isinstance(node, ast.Div), name


def test_every_reported_number_is_an_integer() -> None:
    r = scored([(clean(0), None), (caught(0), "wrong_account")])
    numbers: list[int] = [
        r.seed,
        r.error_rate_per_10_000,
        r.read_seconds,
        r.dismiss_seconds,
        r.total_entries,
        r.clean_entries,
        r.injected_entries,
        r.flagged_entries,
        r.false_alarms,
        r.caught,
    ]
    for value in numbers:
        assert isinstance(value, int)
    for m in r.metrics:
        assert m.measured_hundredths is None or isinstance(m.measured_hundredths, int)


def test_the_report_ends_with_a_newline() -> None:
    assert render(scored([(clean(0), None)])).endswith("\n")
