"""Phase 9, child 8 - does an account mapping transfer between organisations?

This file IS the experiment. It holds the measurement functions that produce
`artifacts/phase9_cross_organisation_report.md`, and the tests that pin them so
the numbers in that report can be re-derived from the repository alone.

    the question    an index built on department A's own posted history is
                    asked to name the account for department B's entries
    the answer      one gap number per ordered pair: within minus cross
    the corpus      six UK central-government departments, one month, the
                    committed slices in `accountant/ingest/fixtures/`

EVIDENCE CLASS: PUBLIC_DATA_EVIDENCE
------------------------------------
Every number here comes from spend files published by UK central government
under the Open Government Licence. Where a number cannot be produced it is
labelled NOT_MEASURABLE with the reason, and never replaced with a guess.

WHAT THIS FILE DOES NOT PROVE
-----------------------------
It does not prove anything about Indian customer books. UK central-government
data tests the MECHANISM - a supplier-to-account memory index - and it tests
the TRANSFER question. It does not prove performance on Indian customer books,
and no number in the report it generates may be quoted as if it did.

It does not prove anything about TallyPrime. Nothing here opens a socket, and
no Tally ledger name is involved.

It does not prove anything about the full published files. The corpus is the
committed slice of each department's November 2025 return - 283 rows of the
16,011 those seven files contain. A slice is a real sample of a real file; it
is not the file.

It does not prove that the six departments measured are representative of UK
central government, still less of the private sector. Six is six.

WHY THE TESTS DO NOT DEMAND A PARTICULAR ANSWER
-----------------------------------------------
An experiment whose test insists on a happy result is not an experiment. So the
tests here pin three things and no more:

    the shape       every pair reports one gap, and the gap is within minus
                    cross, whatever those turn out to be
    the refusals    a department with no usable rows cannot be either side of
                    a pair, and says so
    the agreement   the published report and manifest carry the hash of the
                    text a fresh run produces

If the corpus changes, or the index changes, the hash tests fail and the report
must be regenerated. That is the intended behaviour: it makes a stale number
loud instead of silent. Two tests do record an observed outcome
(`test_no_department_shares...`, `test_the_only_two_suppliers...`) because those
two observations are the whole mechanism behind the headline, and a silent
change in either would invalidate the report while every other test still
passed.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from accountant.ingest import crossorg, report, sources, spend
from accountant.ingest.crossorg import Accuracy, CrossOrgReport, PairResult
from accountant.ingest.spend import LoadResult
from accountant.memory.index import normalise_vendor
from accountant.schema import Outcome
from accountant.score.book import Book, GroundTruth
from accountant.score.harness import PERCENT_SCALE, ScoreReport, scaled_rate, score

REPO = Path(__file__).resolve().parent.parent
REPORT_PATH = REPO / "artifacts" / "phase9_cross_organisation_report.md"
MANIFEST_PATH = REPO / "artifacts" / "phase9_reproducibility_manifest.json"

# The departments that publish a usable narration. DBT is not one of them and
# is not quietly dropped: `excluded_table` states why, with its row counts.
EXCLUDED_SOURCES: tuple[sources.Source, ...] = tuple(
    s for s in sources.ALL_SOURCES if s not in sources.COMPARABLE_SOURCES
)

# R (seconds to read one entry) and D (seconds to dismiss one flag) are
# self-timed inputs and nobody has supplied them. The harness refuses to
# default them, so 1 and 1 are passed to satisfy its floor and N2 is reported
# NOT_MEASURABLE everywhere rather than quoted from an invented number. N1 does
# not use either value, so N1 is unaffected by this choice.
UNSET_SECONDS = 1

# The hash seeds the manifest claims a run at. `random` is the string CPython
# accepts to mean "choose a fresh seed", and it is the one that would actually
# catch set-iteration order leaking into the output.
HASH_SEEDS: tuple[str, ...] = ("0", "1", "12345", "99991", "random")

NOT_MEASURABLE = "NOT_MEASURABLE"


# ---------------------------------------------------------------------------
# the measurement
# ---------------------------------------------------------------------------


def loaded_departments() -> tuple[LoadResult, ...]:
    """The six comparable departments, loaded from committed bytes."""
    return spend.load_all(sources.COMPARABLE_SOURCES)


def comparison() -> CrossOrgReport:
    """Every ordered pair, measured by `accountant/ingest/crossorg.py`."""
    return crossorg.compare(loaded_departments())


def relative_gap_hundredths(pair: PairResult) -> int | None:
    """The gap as a fraction of the within-department result it came from.

    None when the within-department result is zero: dividing by it would be a
    number with no meaning, and a number with no meaning is worse than a stated
    absence.
    """
    within = pair.within.percent_hundredths
    if within == 0:
        return None
    return scaled_rate(pair.gap_hundredths, within, PERCENT_SCALE)


def _percent(hundredths: int) -> str:
    return f"{hundredths // 100}.{hundredths % 100:02d}%"


def _signed(hundredths: int) -> str:
    sign = "-" if hundredths < 0 else "+"
    return f"{sign}{_percent(abs(hundredths))}"


def _optional_percent(hundredths: int | None) -> str:
    return NOT_MEASURABLE if hundredths is None else _signed(hundredths)


@dataclass(frozen=True)
class PairRow:
    """One ordered pair, with every column the frozen plan asks for."""

    index_code: str
    test_code: str
    training_rows: int
    test_rows: int
    within_hundredths: int
    cross_hundredths: int
    absolute_gap_hundredths: int
    relative_gap_hundredths: int | None
    confidently_wrong: int
    supplier_seen: int
    cross_n1_hundredths: int | None


def _histories() -> dict[str, crossorg.Split]:
    return {r.code: crossorg.split(r) for r in loaded_departments()}


def _cross_book(a: LoadResult, b: LoadResult) -> Book:
    """Department B's entries read with department A's chart and A's history.

    This is what a pooled model would actually be doing: bringing one
    organisation's vocabulary and one organisation's memory to another
    organisation's ledger. `accountant/score/harness.py` scores it unmodified.
    """
    splits = _histories()
    return Book(
        company=b.department,
        accounts=a.accounts,
        history=splits[a.code].history,
        entries=splits[b.code].entries,
        truth=GroundTruth(seed=0, error_rate_per_10_000=0, injected=()),
    )


def _scored(book: Book) -> ScoreReport:
    return score(book, read_seconds=UNSET_SECONDS, dismiss_seconds=UNSET_SECONDS)


def pair_rows() -> tuple[PairRow, ...]:
    """Every ordered pair, in a fixed order, with the counts behind each gap."""
    loaded = {r.code: r for r in loaded_departments()}
    splits = _histories()
    rows: list[PairRow] = []
    for pair in comparison().pairs:
        cross = pair.cross
        scored = _scored(_cross_book(loaded[pair.index_code], loaded[pair.test_code]))
        rows.append(
            PairRow(
                index_code=pair.index_code,
                test_code=pair.test_code,
                training_rows=len(splits[pair.index_code].history),
                test_rows=cross.tested,
                within_hundredths=pair.within.percent_hundredths,
                cross_hundredths=cross.percent_hundredths,
                absolute_gap_hundredths=pair.gap_hundredths,
                relative_gap_hundredths=relative_gap_hundredths(pair),
                confidently_wrong=cross.matched - cross.correct,
                supplier_seen=cross.matched + cross.conflicted,
                cross_n1_hundredths=scored.n1.measured_hundredths,
            )
        )
    return tuple(rows)


def pair_table() -> str:
    """The per-pair table. One gap number per pair, never pooled."""
    header = (
        "| A (index) | B (tested) | train rows | test rows | within | cross "
        "| absolute gap | relative gap | confidently wrong | supplier seen "
        "| cross N1 |"
    )
    lines = [header, "|" + "---|" * 11]
    for row in pair_rows():
        n1 = (
            NOT_MEASURABLE
            if row.cross_n1_hundredths is None
            else _percent(row.cross_n1_hundredths)
        )
        lines.append(
            f"| {row.index_code} | {row.test_code} | {row.training_rows} "
            f"| {row.test_rows} | {_percent(row.within_hundredths)} "
            f"| {_percent(row.cross_hundredths)} "
            f"| {_signed(row.absolute_gap_hundredths)} "
            f"| {_optional_percent(row.relative_gap_hundredths)} "
            f"| {row.confidently_wrong} | {row.supplier_seen}/{row.test_rows} "
            f"| {n1} |"
        )
    return "\n".join(lines) + "\n"


def shared_account_labels() -> tuple[tuple[str, str, int], ...]:
    """How many account labels each ordered pair of charts has in common.

    Casefolded, so a difference of capitalisation is not counted as a different
    account. The comparison is between published charts, not between rows.
    """
    charts = {
        r.code: {a.casefold() for a in r.accounts if a != spend.CREDIT_NOT_IN_SOURCE}
        for r in loaded_departments()
    }
    codes = [r.code for r in loaded_departments()]
    return tuple(
        (a, b, len(charts[a] & charts[b])) for a in codes for b in codes if a != b
    )


def shared_suppliers() -> tuple[tuple[str, str, str, str, str], ...]:
    """Every supplier in A's history that also appears in B's entries.

    Returns (A, B, vendor key, the account A used, the accounts B used). This
    is the mechanism behind the headline: it says whether the cross-department
    result is zero because the suppliers never overlap, or because they overlap
    and disagree.
    """
    splits = _histories()
    codes = [r.code for r in loaded_departments()]
    found: list[tuple[str, str, str, str, str]] = []
    for a in codes:
        history: dict[str, set[str]] = {}
        for v in splits[a].history:
            history.setdefault(normalise_vendor(v.party), set()).add(v.debit_account)
        for b in codes:
            if a == b:
                continue
            entries: dict[str, set[str]] = {}
            for v in splits[b].entries:
                entries.setdefault(normalise_vendor(v.party), set()).add(
                    v.debit_account
                )
            for key in sorted(set(history) & set(entries)):
                found.append(
                    (
                        a,
                        b,
                        key,
                        " / ".join(sorted(history[key])),
                        " / ".join(sorted(entries[key])),
                    )
                )
    return tuple(found)


def vocabulary_table() -> str:
    """Do the two organisations even use the same words for the same thing?"""
    lines = ["Account labels shared between two charts, casefolded:"]
    overlaps = shared_account_labels()
    for a, b, count in overlaps:
        if count:
            lines.append(f"  {a} and {b} share {count} account label(s)")
    if all(count == 0 for _, _, count in overlaps):
        lines.append("  none - no account label appears in two departments")
    lines.append("")
    lines.append(
        "Suppliers appearing in one department's history and another's entries:"
    )
    shared = shared_suppliers()
    if not shared:
        lines.append("  none")
    for a, b, key, a_account, b_accounts in shared:
        lines.append(f"  {key}")
        lines.append(f"    {a} posted it to   {a_account}")
        lines.append(f"    {b} posted it to   {b_accounts}")
    return "\n".join(lines) + "\n"


def within_scores() -> tuple[tuple[str, ScoreReport], ...]:
    """Each department scored on its own chart and its own history."""
    return tuple(
        (r.code, _scored(spend.as_score_book(r))) for r in loaded_departments()
    )


def outcome_mix(scored: ScoreReport) -> Counter[str]:
    return Counter(entry.outcome.value for entry in scored.entries)


def harness_table() -> str:
    """What `accountant/score/harness.py` says, within and across.

    N2 is absent on purpose. It needs R and D, which are self-timed inputs
    nobody has supplied, so it is NOT_MEASURABLE and is not printed as a
    number. N3 is FAIL everywhere: nobody injected errors into a real
    government ledger, so there is no catch rate to measure.
    """
    lines = ["Within department - own chart, own history:"]
    within_questions = 0
    within_entries = 0
    for code, scored in within_scores():
        mix = outcome_mix(scored)
        unclear = mix.get(Outcome.UNCLEAR.value, 0)
        within_questions += unclear
        within_entries += scored.total_entries
        n1 = scored.n1.measured_hundredths
        lines.append(
            f"  {code:<6} entries {scored.total_entries:>3}"
            f"   false alarms {scored.false_alarms:>2}"
            f"   N1 {NOT_MEASURABLE if n1 is None else _percent(n1):>9}"
            f"   {scored.n1.status.value}"
            f"   questions {unclear:>3}/{scored.total_entries}"
        )
    lines.append(
        f"  all    questions {within_questions}/{within_entries} within department"
    )
    lines.append("")
    lines.append("Across departments - A's chart and A's history, B's entries:")

    loaded = {r.code: r for r in loaded_departments()}
    cross_questions = 0
    cross_entries = 0
    cross_false_alarms = 0
    for pair in comparison().pairs:
        scored = _scored(_cross_book(loaded[pair.index_code], loaded[pair.test_code]))
        mix = outcome_mix(scored)
        cross_questions += mix.get(Outcome.UNCLEAR.value, 0)
        cross_entries += scored.total_entries
        cross_false_alarms += scored.false_alarms
    lines.append(f"  entries scored           {cross_entries}")
    lines.append(f"  false alarms             {cross_false_alarms}")
    lines.append(f"  questions to the human   {cross_questions}/{cross_entries}")
    lines.append("  N2                       " + NOT_MEASURABLE + " (R and D unset)")
    lines.append(
        "  N3                       " + NOT_MEASURABLE + " (no injected errors)"
    )
    return "\n".join(lines) + "\n"


def excluded_table() -> str:
    """The departments that could not take part, named, with the reason."""
    lines = ["Departments excluded from every pair:"]
    if not EXCLUDED_SOURCES:
        lines.append("  none")
    for source in EXCLUDED_SOURCES:
        loaded = spend.load_source(source)
        reasons = ", ".join(
            f"{reason} x{count}" for reason, count in loaded.rejected_by_reason()
        )
        lines.append(
            f"  {loaded.code}: {loaded.loaded_count} of {loaded.row_count} rows "
            f"loaded ({reasons})"
        )
    return "\n".join(lines) + "\n"


def experiment_text() -> str:
    """Everything this experiment produces, as one deterministic block.

    This is the text whose sha256 the manifest records. It is assembled from
    committed modules and committed bytes only, so anybody with the repository
    can reproduce the hash without a scratch script.
    """
    return "\n".join(
        (
            report.render_cross(comparison()),
            pair_table(),
            vocabulary_table(),
            harness_table(),
            excluded_table(),
        )
    )


def experiment_sha256() -> str:
    return hashlib.sha256(experiment_text().encode("utf-8")).hexdigest()


def _manifest() -> dict[str, Any]:
    loaded: dict[str, Any] = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    return loaded


def _runs() -> list[dict[str, Any]]:
    runs: list[dict[str, Any]] = _manifest()["runs"]
    return runs


# ---------------------------------------------------------------------------
# the shape of the experiment
# ---------------------------------------------------------------------------


def test_the_experiment_measures_at_least_three_department_pairs() -> None:
    rows = pair_rows()
    assert len(rows) >= crossorg.MIN_PAIRS
    assert len({(r.index_code, r.test_code) for r in rows}) == len(rows)
    assert all(r.index_code != r.test_code for r in rows)


def test_every_pair_reports_one_gap_number_and_the_counts_behind_it() -> None:
    """Per pair, not pooled. Pooling is the failure mode this file exists to
    prevent: an aggregate can hide a transfer gap inside an average."""
    for row in pair_rows():
        assert row.training_rows > 0
        assert row.test_rows > 0
        assert row.absolute_gap_hundredths == (
            row.within_hundredths - row.cross_hundredths
        )
        assert 0 <= row.supplier_seen <= row.test_rows
        assert row.confidently_wrong >= 0


def test_the_relative_gap_is_the_absolute_gap_over_the_within_result() -> None:
    for pair in comparison().pairs:
        relative = relative_gap_hundredths(pair)
        if pair.within.percent_hundredths == 0:
            assert relative is None
            continue
        assert relative == scaled_rate(
            pair.gap_hundredths, pair.within.percent_hundredths, PERCENT_SCALE
        )


def test_a_relative_gap_with_nothing_to_divide_by_is_not_measurable() -> None:
    """An index that got nothing right at home has no denominator, so the
    relative gap is stated as absent rather than invented as zero."""
    nothing_right = Accuracy(tested=4, matched=0, correct=0, conflicted=0, no_match=4)
    pair = PairResult(
        index_code="A", test_code="B", within=nothing_right, cross=nothing_right
    )
    assert relative_gap_hundredths(pair) is None
    assert _optional_percent(relative_gap_hundredths(pair)) == NOT_MEASURABLE


def test_a_negative_gap_would_be_reported_with_its_sign() -> None:
    """Transfer beating home ground is a possible result, so the table has to
    be able to print it. If it could not, the experiment would be rigged."""
    at_home = Accuracy(tested=10, matched=10, correct=2, conflicted=0, no_match=0)
    elsewhere = Accuracy(tested=10, matched=10, correct=6, conflicted=0, no_match=0)
    pair = PairResult(index_code="A", test_code="B", within=at_home, cross=elsewhere)
    assert pair.gap_hundredths == -4000
    assert _signed(pair.gap_hundredths) == "-40.00%"
    assert _optional_percent(relative_gap_hundredths(pair)) == "-200.00%"


# ---------------------------------------------------------------------------
# the refusals
# ---------------------------------------------------------------------------


def test_a_department_with_no_usable_row_cannot_be_either_side_of_a_pair() -> None:
    """DBT is excluded by measurement, not by preference, and it is named."""
    dbt = spend.load_source(sources.DBT)
    assert dbt.loaded_count == 0
    assert dbt.rejected_count == dbt.row_count
    assert dict(dbt.rejected_by_reason()) == {spend.EMPTY_NARRATION: dbt.row_count}
    with pytest.raises(ValueError, match="DBT has 0 history"):
        crossorg.split(dbt)


def test_the_excluded_department_appears_in_the_experiment_with_its_reason() -> None:
    text = excluded_table()
    for source in EXCLUDED_SOURCES:
        assert source.code in text
    assert spend.EMPTY_NARRATION in text
    assert "DBT" in experiment_text()


def test_no_pair_is_built_from_a_department_that_was_excluded() -> None:
    excluded = {s.code for s in EXCLUDED_SOURCES}
    taking_part = {r.index_code for r in pair_rows()} | {
        r.test_code for r in pair_rows()
    }
    assert not taking_part & excluded


# ---------------------------------------------------------------------------
# the mechanism behind the headline
# ---------------------------------------------------------------------------


def test_no_department_shares_an_account_label_with_another_department() -> None:
    """Recorded observation, pinned because the report rests on it.

    If any two of these charts ever do share a label, this fails, and the
    report has to be regenerated rather than quietly becoming wrong.
    """
    assert all(count == 0 for _, _, count in shared_account_labels())


def test_the_only_two_suppliers_seen_twice_were_posted_to_different_accounts() -> None:
    """Recorded observation, pinned for the same reason.

    Both overlaps are IT suppliers, and each receiving department filed them
    under its own name. This is the whole cross-department result in two rows.
    """
    shared = shared_suppliers()
    assert {(a, b, key) for a, b, key, _, _ in shared} == {
        ("DWP", "DEFRA", "accenture_uk_ltd"),
        ("DWP", "HMT", "softcat_plc"),
    }
    assert all(a_account != b_accounts for _, _, _, a_account, b_accounts in shared)


def test_a_cross_department_run_that_flags_nothing_has_not_therefore_passed() -> None:
    """N1 can look perfect while the system knows nothing.

    A detector that never fires produces no false alarms. That is the same
    number a detector that works produces, so N1 alone cannot tell a working
    transfer from a silent one, and the report must never quote it alone.
    """
    loaded = {r.code: r for r in loaded_departments()}
    for pair in comparison().pairs:
        scored = _scored(_cross_book(loaded[pair.index_code], loaded[pair.test_code]))
        if scored.false_alarms == 0 and pair.cross.correct == 0:
            assert scored.n1.status.value == "PASS"
            assert outcome_mix(scored).get(Outcome.VALID.value, 0) == 0
            return
    pytest.skip("no pair produced a silent zero, so there is nothing to warn about")


def test_the_harness_refuses_to_report_a_catch_rate_on_a_book_with_no_errors() -> None:
    """N3 is FAIL on absent evidence, never a vacuous PASS. Nobody injected
    errors into a real government ledger, so there is nothing to catch."""
    for _, scored in within_scores():
        assert scored.n3.measured_hundredths is None
        assert scored.n3.status.value == "FAIL"
        assert "no injected errors" in scored.n3.detail


# ---------------------------------------------------------------------------
# reproducibility - a gate, not a nicety
# ---------------------------------------------------------------------------


def test_the_experiment_text_is_byte_identical_when_run_twice() -> None:
    assert experiment_text() == experiment_text()


def test_the_experiment_text_survives_a_fresh_interpreter_and_a_new_hash_seed() -> None:
    """Two runs in one process share a hash seed, so they cannot catch
    set-iteration order leaking into the output. A subprocess with a different
    PYTHONHASHSEED can."""
    expected = experiment_sha256()
    for seed in ("0", "random"):
        environment = dict(os.environ, PYTHONHASHSEED=seed)
        finished = subprocess.run(
            [
                sys.executable,
                "-c",
                "from tests.test_cross_organisation import experiment_sha256;"
                "print(experiment_sha256())",
            ],
            capture_output=True,
            check=True,
            cwd=REPO,
            env=environment,
            text=True,
        )
        assert finished.stdout.strip() == expected, f"PYTHONHASHSEED={seed} differed"


def test_the_manifest_records_the_hash_this_experiment_produces_now() -> None:
    manifest = _manifest()
    assert manifest["output_sha256"] == experiment_sha256()
    assert manifest["evidence_class"] == "PUBLIC_DATA_EVIDENCE"


def test_the_manifest_records_a_run_at_every_hash_seed_it_claims() -> None:
    runs = _runs()
    assert {r["python_hash_seed"] for r in runs} == set(HASH_SEEDS)
    assert len(runs) >= 2 * len(HASH_SEEDS), "every report is run at least twice"
    assert {r["output_sha256"] for r in runs} == {experiment_sha256()}


def test_the_manifest_names_the_environment_a_number_was_produced_in() -> None:
    """A hash with no environment beside it cannot be checked by anybody."""
    manifest = _manifest()
    environment: dict[str, Any] = manifest["environment"]
    for field in ("git_commit", "python_version", "os", "tool_versions"):
        assert environment.get(field), f"manifest environment is missing {field}"
    for field in ("corpus_sha256", "taxonomy_sha256", "configuration_sha256"):
        assert manifest.get(field), f"manifest is missing {field}"


def test_the_manifest_corpus_hash_matches_the_committed_fixture_bytes() -> None:
    digest = hashlib.sha256()
    for source in sources.ALL_SOURCES:
        digest.update(source.fixture_name.encode("utf-8"))
        digest.update(source.fixture_path.read_bytes())
    assert _manifest()["corpus_sha256"] == digest.hexdigest()


def test_every_run_in_the_manifest_states_the_command_that_produced_it() -> None:
    for run in _runs():
        command = run["command"]
        assert isinstance(command, str)
        assert "experiment" in command
        assert command.strip() == command


# ---------------------------------------------------------------------------
# the report is not allowed to overclaim
# ---------------------------------------------------------------------------


def test_the_report_quotes_the_hash_the_manifest_records() -> None:
    text = REPORT_PATH.read_text(encoding="utf-8")
    assert experiment_sha256() in text


def test_the_report_labels_uk_data_as_a_test_of_the_mechanism_not_of_india() -> None:
    """The one sentence that stops a UK number being read as an Indian result."""
    text = REPORT_PATH.read_text(encoding="utf-8").casefold()
    assert "does not prove performance on indian customer books" in text
    assert "mechanism" in text
    assert "indian evidence" not in text


def test_the_report_carries_its_evidence_class_and_its_unmeasurables() -> None:
    text = REPORT_PATH.read_text(encoding="utf-8")
    assert "PUBLIC_DATA_EVIDENCE" in text
    assert NOT_MEASURABLE in text


def test_every_source_url_in_the_report_is_a_committed_central_government_one() -> None:
    """Councils are out of scope: about 2,600 publishers, no schema stability,
    and a live `EFEFCTIVE` header typo in a real Rochdale file. A council URL
    appearing here would mean the corpus had been widened without saying so.
    """
    permitted = {s.url for s in sources.ALL_SOURCES} | {sources.LICENCE_URL}
    text = REPORT_PATH.read_text(encoding="utf-8")
    cited = {
        word.strip("`<>()[],.")
        for word in text.split()
        if word.strip("`<>()[],.").startswith("http")
    }
    assert cited
    assert cited <= permitted, f"uncommitted sources cited: {sorted(cited - permitted)}"
