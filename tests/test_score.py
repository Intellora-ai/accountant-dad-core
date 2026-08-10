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
from accountant.detect.detectors import name_of
from accountant.schema import Outcome, Voucher
from accountant.score import calibration as cal
from accountant.score import harness
from accountant.score.book import Book, GroundTruth, InjectedError
from accountant.score.harness import ErrorTypeCatch, ScoreReport, Status, score
from accountant.score.report import render, render_calibration

COMPANY = "Score Co"
ACCOUNTS = ("Purchases", "Materials", "Utilities", "Repairs", "Bank", "Cash")
WHEN = datetime.date(2026, 1, 1)

# History happens BEFORE the entries it is history for. Dated explicitly since
# 2026-08-10, because `magnitude` stopped counting rows that are not prior to
# the entry into that entry's ceiling, and a fixture that dated both the same
# day was quietly asserting that a payment can be its own precedent. Nothing
# about what these tests assert changed - only the fixture stopped saying
# something it never meant.
BEFORE = WHEN - datetime.timedelta(days=1)

# R and D used by most tests. They are arbitrary stand-ins for a stopwatch, and
# the harness refuses to supply any default of its own.
R = 30
D = 30


def _history(party: str, account: str, amount: int, n: int, tag: str) -> list[Voucher]:
    return [
        Voucher(
            id=f"hist-{tag}-{i}",
            date=BEFORE,
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
    """Clean, but far larger than this account has ever seen: magnitude fires.

    Nothing is wrong with it. That is exactly what a false alarm is. The amount
    clears the calibrated margin, which is what "far larger" now means.
    """
    over = 300_000 * detectors.MAGNITUDE_OVER_PERCENT // detectors.PERCENT
    return _entry(f"noisy-{i}", "Sharma Traders", "Purchases", amount=over + 1)


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
    """Judged against every detector that exists, not only the active set.

    `first_use` is withdrawn by default, so it is asked for explicitly here:
    the fixture is checking that each shape still fires its own detector, not
    which detectors run by default.
    """
    r = score(
        make_book(
            [
                (caught(0), "wrong_account"),
                (gst_error(0), "phantom_gst"),
                (new_account(0), "unused_account"),
            ]
        ),
        read_seconds=R,
        dismiss_seconds=D,
        detector_set=detectors.ALL_DETECTORS,
    )
    fired = {e.voucher_id: e.fired for e in r.entries}
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
    r = score(
        make_book(
            [
                (caught(0), "wrong_account"),
                (caught(1), "wrong_account"),
                (missed(0), "wrong_account"),
                (gst_error(0), "phantom_gst"),
                (new_account(0), "unused_account"),
            ]
        ),
        read_seconds=R,
        dismiss_seconds=D,
        detector_set=detectors.ALL_DETECTORS,
    )
    rows = {t.error_type: (t.caught, t.injected) for t in r.per_type}
    assert rows == {
        "wrong_account": (2, 3),
        "phantom_gst": (1, 1),
        "unused_account": (1, 1),
    }


def test_withdrawing_a_detector_costs_catch_rate_and_the_report_says_so() -> None:
    """The price of the N1 work, stated rather than glossed over.

    `unused_account` was caught only by `first_use`. With `first_use`
    withdrawn it is missed, N3 fails for that type, and the report names the
    withdrawn detector and why.
    """
    entries: list[tuple[Voucher, str | None]] = [
        (caught(0), "wrong_account"),
        (new_account(0), "unused_account"),
    ]
    r = scored(entries)
    rows = {t.error_type: (t.caught, t.injected) for t in r.per_type}
    assert rows == {"wrong_account": (1, 1), "unused_account": (0, 1)}
    assert r.n3.status is Status.MISSED

    text = render(r)
    assert "Detectors that did NOT run" in text
    assert "first_use" in text


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
    assert set(r.detectors) == {name_of(d) for d in detectors.ACTIVE_DETECTORS}
    for name in r.detectors:
        assert name in render(r)


def test_the_report_names_every_detector_that_did_not_run_and_why() -> None:
    """A narrower detector set is a fact about the run, never a silence."""
    r = scored([(clean(0), None)])
    ran = set(r.detectors)
    absent = {name_of(d) for d in detectors.ALL_DETECTORS} - ran
    assert absent, "this test is meaningless if every detector runs"
    assert {w.detector for w in r.withdrawn} == absent
    text = render(r)
    for w in r.withdrawn:
        assert w.detector in text
        assert w.because.split(".")[0][:30] in text


def test_a_detector_left_out_by_the_caller_is_reported_as_left_out() -> None:
    r = score(
        make_book([(clean(0), None)]),
        read_seconds=R,
        dismiss_seconds=D,
        detector_set=detectors.SLICE_4_DETECTORS,
    )
    withdrawn = {w.detector: w.because for w in r.withdrawn}
    assert set(withdrawn) == {"first_use", "magnitude", "gst_anomaly"}
    assert "detector set this run was given" in withdrawn["magnitude"]


def test_running_every_detector_reports_nothing_withdrawn() -> None:
    r = score(
        make_book([(clean(0), None)]),
        read_seconds=R,
        dismiss_seconds=D,
        detector_set=detectors.ALL_DETECTORS,
    )
    assert r.withdrawn == ()
    assert "Every detector that exists ran" in render(r)


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
        "calibration.py",
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


# ---- calibration: choose on one clean set, measure on another ---------------


def a_book(name: str, entries: Sequence[tuple[Voucher, str | None]]) -> Book:
    """One named book, so a split has something to sort by."""
    return Book(
        company=name,
        accounts=ACCOUNTS,
        history=HISTORY,
        entries=tuple(v for v, _ in entries),
        truth=GroundTruth(
            seed=1,
            error_rate_per_10_000=0,
            injected=tuple(
                InjectedError(voucher_id=v.id, error_type=t)
                for v, t in entries
                if t is not None
            ),
        ),
    )


def test_a_measurement_counts_one_entry_once_however_many_detectors_fire() -> None:
    book = a_book("A", [(clean(0), None), (noisy(0), None)])
    m = cal.measure([book], detectors.ACTIVE_DETECTORS)
    assert (m.flagged, m.clean) == (1, 2)
    assert m.measured is True
    assert m.per_100_hundredths == 5000  # 50.00 per 100
    assert m.within(50) is True
    assert m.within(49) is False


def test_a_measurement_skips_the_injected_entries() -> None:
    """N1 is about clean entries. An injected one is not a false alarm."""
    book = a_book("A", [(caught(0), "wrong_account"), (clean(0), None)])
    m = cal.measure([book], detectors.ACTIVE_DETECTORS)
    assert (m.flagged, m.clean) == (0, 1)


def test_a_measurement_over_nothing_is_not_measured_and_is_not_a_pass() -> None:
    m = cal.measure([], detectors.ACTIVE_DETECTORS)
    assert m.measured is False
    assert m.per_100_hundredths is None
    assert m.within(harness.N1_MAX_FALSE_ALARMS_PER_100) is False


def test_running_no_detectors_finds_nothing_and_says_so_truthfully() -> None:
    book = a_book("A", [(noisy(0), None)])
    assert cal.measure([book], []) == cal.CleanMeasurement(flagged=0, clean=1)


@pytest.mark.parametrize(
    ("flagged", "clean", "message"),
    [(0, -1, "is not a count"), (2, 1, "flagged of"), (-1, 3, "flagged of")],
)
def test_an_impossible_measurement_is_refused(
    flagged: int, clean: int, message: str
) -> None:
    with pytest.raises(ValueError, match=message):
        cal.CleanMeasurement(flagged=flagged, clean=clean)


def test_a_grid_with_no_settings_is_refused() -> None:
    with pytest.raises(ValueError, match="offered no settings"):
        cal.Grid(detector="vendor_switch", settings=())


def test_a_detector_cannot_be_kept_at_no_setting() -> None:
    nothing = cal.CleanMeasurement(flagged=0, clean=0)
    with pytest.raises(ValueError, match="kept at no setting"):
        cal.DetectorChoice(
            detector="vendor_switch",
            setting=None,
            kept=True,
            calibration=nothing,
            held_out=nothing,
            detail="d",
        )


def test_the_split_sorts_by_company_and_alternates() -> None:
    given = [a_book(name, []) for name in ("D", "B", "A", "C")]
    first, second = cal.split(given)
    assert [b.company for b in first] == ["A", "C"]
    assert [b.company for b in second] == ["B", "D"]


def _calibrated(
    for_calibration: Sequence[Book], held_out: Sequence[Book], target: int
) -> cal.Calibration:
    return cal.calibrate(for_calibration, held_out, target_per_100=target)


def deafening(i: int) -> Voucher:
    """Past every point on the magnitude grid, so no setting can fit it."""
    return _entry(f"deafening-{i}", "Sharma Traders", "Purchases", amount=300_000 * 100)


def test_calibration_keeps_the_most_sensitive_setting_that_fits() -> None:
    """A mildly noisy book forces the threshold up rather than off.

    Two entries just over three times the account's own maximum, in a book of
    ten. Every grid point up to and including 300 percent flags both, which is
    20 per 100 and outside the target; 500 percent flags neither. The search
    stops at the first point that fits, so 500 is what is kept.
    """
    book = a_book(
        "A", [(noisy(i), None) for i in range(2)] + [(clean(i), None) for i in range(8)]
    )
    result = _calibrated([book], [book], target=10)

    chosen = {c.detector: c.setting for c in result.choices if c.kept}
    assert chosen["magnitude"] == "min_observations=2,over_percent=500"
    assert "magnitude" in result.kept


def test_calibration_withdraws_a_detector_no_setting_can_quieten() -> None:
    loud = a_book("A", [(deafening(i), None) for i in range(4)] + [(clean(0), None)])
    result = _calibrated([loud], [loud], target=10)

    chosen = {c.detector: c.setting for c in result.choices}
    assert chosen["magnitude"] is None  # nothing on its grid could fit here
    assert "magnitude" in result.withdrawn
    assert "vendor_switch" in result.kept


def test_calibration_reports_both_numbers_for_a_withdrawn_detector() -> None:
    loud = a_book("A", [(deafening(i), None) for i in range(4)] + [(clean(0), None)])
    quiet_book = a_book("B", [(clean(i), None) for i in range(5)])
    result = _calibrated([loud], [quiet_book], target=10)

    (magnitude,) = [c for c in result.choices if c.detector == "magnitude"]
    assert magnitude.kept is False
    assert magnitude.calibration.per_100_hundredths == 8000  # 4 of 5
    assert magnitude.held_out.per_100_hundredths == 0
    assert "no setting on its grid" in magnitude.detail


def test_a_detector_that_never_fired_is_not_reported_as_quiet() -> None:
    """Nought is what these books triggered, not evidence of anything else."""
    quiet = a_book("A", [(clean(i), None) for i in range(5)])
    result = _calibrated([quiet], [quiet], target=10)
    for choice in result.choices:
        assert choice.kept is True
        assert "not evidence that it is quiet" in choice.detail


def test_a_detector_that_did_fire_reports_the_plain_detail() -> None:
    mixed = a_book("A", [(noisy(0), None)] + [(clean(i), None) for i in range(19)])
    result = _calibrated([mixed], [mixed], target=10)
    (magnitude,) = [c for c in result.choices if c.detector == "magnitude"]
    assert magnitude.kept is True
    assert magnitude.detail.startswith("kept at min_observations=")
    assert "not evidence that it is quiet" not in magnitude.detail


def test_a_calibration_records_which_books_were_which() -> None:
    first = a_book("A", [(clean(0), None)])
    second = a_book("B", [(clean(1), None)])
    result = _calibrated([first], [second], target=10)
    assert result.calibration_books == ("A",)
    assert result.held_out_books == ("B",)
    assert result.target_per_100 == 10
    assert result.held_out_within_target is True


def test_a_held_out_set_above_the_target_reports_fail() -> None:
    """A truthful FAIL, printed as one. Nothing here is allowed to round it."""
    quiet = a_book("A", [(clean(i), None) for i in range(20)])
    loud = a_book("B", [(deafening(i), None) for i in range(4)] + [(clean(0), None)])
    result = _calibrated([quiet], [loud], target=10)

    assert result.held_out.per_100_hundredths == 8000  # 80.00 per 100
    assert result.held_out_within_target is False
    assert "Held-out verdict: FAIL" in render_calibration(result)


def test_the_calibration_report_prints_every_row_and_ends_with_a_newline() -> None:
    quiet = a_book("A", [(clean(0), None)])
    empty = a_book("B", [])
    text = render_calibration(_calibrated([quiet], [empty], target=10))
    for name in ("vendor_switch", "first_use", "magnitude", "gst_anomaly"):
        assert name in text
    assert "KEPT" in text
    assert "not measured - no clean entries" in text
    assert text.endswith("\n")


def test_a_calibration_over_nothing_keeps_nothing_rather_than_passing() -> None:
    """Fails closed. Nothing measured is not evidence that a detector is fine."""
    result = _calibrated([], [], target=10)
    assert result.calibration.measured is False
    assert result.held_out.measured is False
    assert result.held_out_within_target is False
    assert result.kept == ()
    assert set(result.withdrawn) == {name_of(d) for d in detectors.ALL_DETECTORS}


# ---- per-detector false alarms, duplicates, and who is to blame -------------


def test_the_report_charges_each_detector_for_its_own_false_alarms() -> None:
    entries: list[tuple[Voucher, str | None]] = [(clean(i), None) for i in range(9)]
    entries.append((noisy(0), None))
    r = scored(entries)

    rows = {d.detector: d for d in r.per_detector}
    assert set(rows) == set(r.detectors)
    assert rows["magnitude"].false_alarms == 1
    assert rows["magnitude"].clean_entries == 10
    assert rows["magnitude"].false_alarms_per_100_hundredths == 1000
    assert rows["magnitude"].within_target is True
    assert rows["vendor_switch"].false_alarms == 0


def test_a_detector_that_never_fires_is_listed_rather_than_left_out() -> None:
    r = scored([(clean(0), None)])
    assert {d.detector for d in r.per_detector} == set(r.detectors)
    for d in r.per_detector:
        assert d.false_alarms == 0
    assert r.worst_detector is None
    assert "no detector fired on a clean entry" in r.n1.detail


def test_n1_names_the_detector_responsible_for_most_of_it() -> None:
    entries: list[tuple[Voucher, str | None]] = [(clean(i), None) for i in range(8)]
    entries.extend([(noisy(0), None), (noisy(1), None)])
    r = scored(entries)
    worst = r.worst_detector
    assert worst is not None
    assert worst.detector == "magnitude"
    assert "most of them from magnitude" in r.n1.detail
    assert "magnitude" in render(r)


def test_a_detector_row_with_no_clean_entries_is_not_measured() -> None:
    r = scored([(caught(0), "wrong_account")])
    rows = {d.detector: d for d in r.per_detector}
    assert rows["vendor_switch"].measured is False
    assert rows["vendor_switch"].false_alarms_per_100_hundredths is None
    assert rows["vendor_switch"].within_target is False
    assert rows["vendor_switch"].caught == 1
    assert "not measured - this book has no clean entry" in render(r)


def test_duplicate_alerts_are_counted_apart_from_distinct_problems() -> None:
    """One entry, two detectors, one underlying problem, one alert."""
    both = _entry("both-0", "Sharma Traders", "Bank")
    r = score(
        make_book([(both, "wrong_account")]),
        read_seconds=R,
        dismiss_seconds=D,
        detector_set=detectors.ALL_DETECTORS,
    )
    (entry,) = r.entries
    assert entry.fired == ("first_use", "vendor_switch")
    assert entry.flags == ("vendor_switch",)
    assert entry.duplicate_flags == 1
    assert entry.distinct_problems == 1
    assert r.duplicate_flags == 1
    assert r.distinct_problems == 1

    text = render(r)
    assert "distinct problems" in text
    assert "duplicate flags folded in" in text


def test_a_folded_duplicate_still_charges_the_detector_that_raised_it() -> None:
    """Suppression removes an alert. It must not remove a detector's bill."""
    both = _entry("both-0", "Sharma Traders", "Bank")
    r = score(
        make_book([(both, None)]),
        read_seconds=R,
        dismiss_seconds=D,
        detector_set=detectors.ALL_DETECTORS,
    )
    rows = {d.detector: d.false_alarms for d in r.per_detector}
    assert rows["vendor_switch"] == 1
    assert rows["first_use"] == 1  # folded into the alert above, still charged
    assert r.false_alarms == 1  # but only ONE false alarm, because one entry
