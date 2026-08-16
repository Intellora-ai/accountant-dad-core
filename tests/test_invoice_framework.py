"""The guards: determinism, the batch, and the four doors that stay shut.

WHY THESE ARE STRUCTURAL AND NOT BEHAVIOURAL
----------------------------------------------
"This package never posts to Tally" cannot be proved by calling it and watching
nothing happen - that passes on the day somebody adds a write down a branch the
test does not take. So the assertions here read the IMPORT GRAPH and the SYNTAX
TREE, which is the same evidence `tests/test_no_reader.py`,
`tests/test_the_wall.py` and `tests/test_runtime_backend.py` use for the same
kind of claim.

EVERY SCANNER HAS A CONTROL. Defect J1 in this repository's own history: a scan
that matches nothing passes for ever and proves nothing. Each scanner below is
handed a planted example it must find.
"""

from __future__ import annotations

import ast
import pathlib
import tomllib

import pytest

from accountant.cage.state import State
from accountant.invoice import batch
from accountant.invoice.bridge import DEFAULT_THRESHOLDS, Thresholds, describe
from accountant.invoice.fields import Where
from accountant.invoice.parse import Reading, Word
from accountant.invoice.result import SCHEMA_VERSION, ExtractionResult
from accountant.invoice.status import (
    CAGE_STATE_OF,
    NOTHING_WAS_READ,
    REQUIRES_REVIEW,
    SAID,
    DocumentStatus,
)
from accountant.invoice.validate import MANDATORY
from accountant.labels import Printing
from tests.invoice_documents import (
    INTER_STATE,
    INTRA_STATE,
    MISSING_FIELDS,
    NOT_AN_INVOICE,
    Fixture,
    text_reading,
    word_reading,
)

REPO = pathlib.Path(__file__).resolve().parent.parent
PACKAGE = REPO / "accountant" / "invoice"
PYPROJECT = REPO / "pyproject.toml"
EXACT = Printing.EXACT_CHARACTERS

#: The exact set `docs/DECISIONS.md` D-30 cleared, matched as a SET and not as
#: a floor. Restated here rather than imported from `tests/test_no_reader.py`,
#: because a guard that reads its expectation from the thing it guards proves
#: nothing - and this one exists to catch a dependency THIS package added.
APPROVED_RUNTIME_DEPENDENCIES = {"pypdf", "pytesseract", "Pillow"}

#: Modules in `accountant/extract/` that do the READING. Nothing in this package
#: may import one: it is handed characters somebody else already read, and that
#: is the whole of the promise that wiring it in changes no reading behaviour.
THE_READERS = {
    "accountant.extract.freeocr",
    "accountant.extract.pagereader",
    "accountant.extract.textlayer",
    "accountant.extract.service",
    "accountant.extract.registry",
    "accountant.extract.placeholder",
    "accountant.extract.ladder",
}

#: Anything that can reach a customer's books, directly or by being the thing
#: that calls the thing that can.
THE_WRITE_PATH = {
    "accountant.tallyio",
    "accountant.pipeline",
    "accountant.web",
    "accountant.agent",
    "accountant.reversal",
}

#: Attribute and function names that read a clock or draw a random number. A
#: module naming any of them cannot promise that two runs agree.
NOT_DETERMINISTIC = {
    "now",
    "today",
    "utcnow",
    "monotonic",
    "perf_counter",
    "random",
    "randint",
    "choice",
    "shuffle",
    "uuid4",
    "uuid1",
    "getenv",
    "environ",
}


# ---------------------------------------------------------------------------
# the scanners, and their controls
# ---------------------------------------------------------------------------


def modules() -> list[pathlib.Path]:
    found = sorted(PACKAGE.rglob("*.py"))
    assert found, f"nothing to scan at {PACKAGE}; the guard would pass vacuously"
    return found


def imports_in(tree: ast.Module) -> set[str]:
    """Every module name an import mentions, however the import is written."""
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            names.add(node.module)
            names.update(f"{node.module}.{a.name}" for a in node.names)
    return names


def _accountant_module(name: str) -> pathlib.Path | None:
    direct = REPO / (name.replace(".", "/") + ".py")
    if direct.exists():
        return direct
    package = REPO / name.replace(".", "/") / "__init__.py"
    return package if package.exists() else None


def everything_reachable() -> set[str]:
    """Every `accountant` module this package can reach, however many hops.

    TRANSITIVE, and that is the point. A direct-import check passes on the day
    somebody imports a helper that imports the write door. Following the graph
    is what makes "this package cannot reach Tally" a fact rather than a habit.
    """
    seen: set[str] = set()
    queue = [f"accountant.invoice.{path.stem}" for path in modules()]
    while queue:
        name = queue.pop()
        if name in seen:
            continue
        seen.add(name)
        path = _accountant_module(name)
        if path is None:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        queue.extend(
            found
            for found in imports_in(tree)
            if found.startswith("accountant") and found not in seen
        )
    return seen


def names_used(tree: ast.Module) -> set[str]:
    """Every name the CODE uses. No comments, no docstrings, no literals."""
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            names.add(node.id)
        elif isinstance(node, ast.Attribute):
            names.add(node.attr)
        elif isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            names.add(node.name)
    return names


def test_the_control_the_import_scanner_can_actually_see_an_import() -> None:
    """A scan that matches nothing passes for ever. This is what notices."""
    planted = ast.parse(
        "import accountant.tallyio.real\nfrom accountant.pipeline import post\n"
    )
    assert "accountant.tallyio.real" in imports_in(planted)
    assert "accountant.pipeline.post" in imports_in(planted)
    assert imports_in(ast.parse('"""prose about accountant.tallyio."""\n')) == set()


def test_the_control_the_name_scanner_can_actually_see_a_clock() -> None:
    assert "now" in names_used(ast.parse("import datetime\ndatetime.now()\n"))
    assert names_used(ast.parse('"""prose about now and random."""\n')) == set()


def test_the_control_the_reachability_walk_visited_a_real_graph() -> None:
    """A walk over an empty file list satisfies every assertion below it."""
    reachable = everything_reachable()
    assert len(reachable) > 15
    assert "accountant.cage.conservation" in reachable
    assert "accountant.labels" in reachable
    # The walk has to cross INTO `accountant/extract/` or the equality below is
    # satisfied by a walk that never got there. `labels.py` was this line until
    # 2026-08-17, when it moved out of the package to `accountant/labels.py`.
    assert "accountant.extract.adapter" in reachable


# ---------------------------------------------------------------------------
# 1. the write door is not reachable
# ---------------------------------------------------------------------------


def test_nothing_in_this_package_can_reach_tally_however_many_hops() -> None:
    """The strongest form of the promise available without running anything.

    Not "no module here imports tallyio" - that is true and weak. This says
    that following every import from every module in the package, through every
    module those reach, `accountant.tallyio` never appears. The write door and
    its approval are exactly where they were.
    """
    reachable = everything_reachable()
    offenders = sorted(
        found
        for found in reachable
        if any(found == door or found.startswith(f"{door}.") for door in THE_WRITE_PATH)
    )
    assert offenders == [], (
        "accountant/invoice/ reads documents and posts nothing. These reach the "
        f"write path: {offenders}"
    )


def test_no_module_here_names_a_write() -> None:
    """The second half, because an import graph cannot see a call made through
    an object somebody handed in. Nothing here is even NAMED like a write."""
    forbidden = {"post", "write_voucher", "create_voucher", "delete_voucher", "commit"}
    offenders = {
        path.name: sorted(names_used(ast.parse(path.read_text())) & forbidden)
        for path in modules()
        if names_used(ast.parse(path.read_text())) & forbidden
    }
    assert offenders == {}


# ---------------------------------------------------------------------------
# 2. no reading behaviour is changed
# ---------------------------------------------------------------------------


def test_this_package_imports_no_reader() -> None:
    """It is handed characters. It cannot open a file, start a program or call
    an engine, so it cannot change what any reader returns."""
    reachable = everything_reachable()
    offenders = sorted(reachable & THE_READERS)
    assert offenders == [], (
        "accountant/invoice/ is downstream of reading and must stay there. "
        f"These readers are reachable: {offenders}"
    )


def test_the_only_extract_modules_reachable_are_the_ones_that_read_nothing() -> None:
    """`adapter.py` is the record contract and `dates.py` parses a date out of a
    string. NEITHER OF THEM OPENS ANYTHING — `dates.py` imports `__future__`,
    `dataclasses`, `datetime`, `enum`, `re` and `typing` and nothing else — so
    the invariant this guards, that this package stays downstream of reading,
    holds for both names below.

    THE SET SHRANK ON 2026-08-17 AND THAT IS THE DIRECTION IT IS ALLOWED TO
    MOVE. It read `labels`, `invoicelike`, `adapter`, `dates`. `labels.py`
    matches labels in strings and `invoicelike.py` counts signals in strings;
    neither ever touched a reader, so neither belonged inside a package whose
    boundary exists to keep readers in. They are now `accountant/labels.py` and
    `accountant/invoicelike.py`, which this package imports directly, and what
    remains here is the extraction contract and the date parser it reaches.

    `dates.py` had joined the set the same day, through the function-local
    `from accountant.extract.dates import DateLocale, read_date` in
    `accountant/extract/adapter.py`, and the test name said "the two pure string
    ones" while the set listed three. A count in a test name is a number that
    goes stale; what is actually being claimed is that none of these reads, and
    `test_this_package_imports_no_reader` above is the other half of it.

    Stated as an EQUALITY so a new one appearing is a failure rather than a
    shrug — anything added here has to be justified the way `dates.py` was.
    """
    reachable = everything_reachable()
    from_extract = {
        name
        for name in reachable
        if name.startswith("accountant.extract.") and _accountant_module(name)
    }
    assert from_extract == {
        "accountant.extract.adapter",
        "accountant.extract.dates",
    }


def test_no_module_here_opens_a_file_starts_a_program_or_reaches_a_network() -> None:
    reaching = {
        "subprocess",
        "socket",
        "urllib",
        "http",
        "shutil",
        "tempfile",
        "importlib",
        "pathlib",
        "os",
    }
    offenders = {
        path.name: sorted(
            {found.split(".")[0] for found in imports_in(ast.parse(path.read_text()))}
            & reaching
        )
        for path in modules()
    }
    assert {name: found for name, found in offenders.items() if found} == {}


def test_no_module_here_calls_open_exec_or_eval() -> None:
    forbidden = {"open", "exec", "eval", "compile", "__import__"}
    called: set[str] = set()
    for path in modules():
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                called.add(node.func.id)
    assert called & forbidden == set()


# ---------------------------------------------------------------------------
# 3. no new dependency
# ---------------------------------------------------------------------------


def test_this_package_added_no_dependency() -> None:
    """`pypdf`, `pytesseract`, `Pillow`, and a fourth entry is a failure.

    Everything here is stdlib plus `accountant`. Deterministic parsing needs no
    library, and a library is where a non-deterministic answer would come from.
    """
    declared = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))
    names = {
        entry.split(">")[0].split("=")[0].split("[")[0].strip()
        for entry in declared["project"]["dependencies"]
    }
    assert names == APPROVED_RUNTIME_DEPENDENCIES


def test_no_module_here_imports_anything_but_stdlib_and_accountant() -> None:
    import sys

    offenders: dict[str, list[str]] = {}
    for path in modules():
        outside = sorted(
            {
                found.split(".")[0]
                for found in imports_in(ast.parse(path.read_text()))
                if found.split(".")[0] not in sys.stdlib_module_names
                and found.split(".")[0] != "accountant"
            }
        )
        if outside:
            offenders[path.name] = outside
    assert offenders == {}


# ---------------------------------------------------------------------------
# 4. determinism
# ---------------------------------------------------------------------------


def test_no_module_here_reads_a_clock_or_draws_a_random_number() -> None:
    """The structural half of determinism. The behavioural half is below, and
    neither covers the other's failure: a behavioural test only proves the two
    runs it took agreed, and a static one cannot prove the arithmetic is stable.
    """
    offenders = {
        path.name: sorted(names_used(ast.parse(path.read_text())) & NOT_DETERMINISTIC)
        for path in modules()
        if names_used(ast.parse(path.read_text())) & NOT_DETERMINISTIC
    }
    assert offenders == {}


@pytest.mark.parametrize(
    "fixture", [INTRA_STATE, INTER_STATE, MISSING_FIELDS, NOT_AN_INVOICE]
)
def test_the_same_bytes_twice_produce_an_identical_record(fixture: Fixture) -> None:
    """The behavioural half. Compared as OBJECTS and again as text, because two
    records can be equal while their sentences differ in order - and the
    sentences are what a person reads."""
    first = describe(text_reading(fixture), printing=EXACT, file_hash=fixture.name)
    second = describe(text_reading(fixture), printing=EXACT, file_hash=fixture.name)
    assert first == second
    assert repr(first) == repr(second)
    assert first.review_reasons == second.review_reasons
    assert dict(first.field_confidence) == dict(second.field_confidence)


def test_a_second_process_would_see_the_same_answer_because_nothing_varies() -> None:
    """Ten runs, not two. A hash-ordering difference shows up occasionally
    rather than every time, so two runs is not the test somebody thinks it is."""
    answers = {
        repr(describe(text_reading(INTRA_STATE), printing=EXACT, file_hash="d"))
        for _ in range(10)
    }
    assert len(answers) == 1


def test_the_record_states_its_own_shape() -> None:
    """A result stored today and read back after this package changes shape has
    to say which shape it is, or the reader guesses."""
    result = describe(text_reading(INTRA_STATE), printing=EXACT, file_hash="v")
    assert result.document.schema_version == SCHEMA_VERSION
    assert SCHEMA_VERSION == "invoice-extraction-1"


# ---------------------------------------------------------------------------
# 5. the statuses, and how they map onto the machine that already exists
# ---------------------------------------------------------------------------


def test_ocr_failure_and_field_failure_are_different_statuses() -> None:
    """THE POINT OF THE WHOLE PACKAGE, asserted as a fact about the enum rather
    than only as a behaviour, so nobody can merge them by accident."""
    assert DocumentStatus.OCR_FAILED is not DocumentStatus.INVOICE_MISSING_FIELDS
    assert (
        SAID[DocumentStatus.OCR_FAILED] != SAID[DocumentStatus.INVOICE_MISSING_FIELDS]
    )


def test_every_status_has_a_sentence_a_person_can_read() -> None:
    """A status with no sentence is a blank on somebody's screen."""
    assert set(SAID) == set(DocumentStatus)
    assert all(SAID[status].strip() for status in DocumentStatus)


@pytest.mark.parametrize("status", list(DocumentStatus))
def test_no_status_sentence_uses_a_word_from_the_code(status: DocumentStatus) -> None:
    """The sentences go to a person, so none of them may name a module, a
    field, an enum value or a failure mode in the words the code uses."""
    jargon = (
        "ocr",
        "parse",
        "regex",
        "null",
        "exception",
        "extract",
        "schema",
        "confidence",
        "validation",
        "_",
    )
    said = SAID[status]
    assert [word for word in jargon if word in said.lower()] == []
    # `None` the code value, case-sensitively. The English word "None of the
    # things a bill prints" is on one of these sentences and is exactly right
    # there - a case-insensitive check flagged it, which is the guard being
    # blunt rather than the sentence being wrong.
    assert "None" not in said


def test_every_status_says_whether_a_proposal_exists_for_it() -> None:
    """`None` is the honest answer for most of them: no proposal was ever made,
    so there is no state in `cage/state.py` to be in."""
    assert set(CAGE_STATE_OF) == set(DocumentStatus)
    assert CAGE_STATE_OF[DocumentStatus.OCR_FAILED] is None
    assert CAGE_STATE_OF[DocumentStatus.INVOICE_MISSING_FIELDS] is None
    assert CAGE_STATE_OF[DocumentStatus.INVOICE_VALIDATION_FAILED] is State.BLOCKED
    assert CAGE_STATE_OF[DocumentStatus.READY_FOR_REVIEW] is State.OBSERVED
    assert CAGE_STATE_OF[DocumentStatus.POSTED] is State.POSTED


def test_ready_for_review_still_requires_review() -> None:
    """The name is why this has to be said out loud: 'ready for review' is the
    good outcome and it still means nobody has looked."""
    assert DocumentStatus.READY_FOR_REVIEW in REQUIRES_REVIEW
    assert DocumentStatus.APPROVED not in REQUIRES_REVIEW
    assert DocumentStatus.POSTED not in REQUIRES_REVIEW
    result = describe(text_reading(INTRA_STATE), printing=EXACT, file_hash="r")
    assert result.status is DocumentStatus.READY_FOR_REVIEW
    assert result.requires_review is True


def test_nothing_in_this_package_ever_sets_approved_or_posted() -> None:
    """Those two are set by a person and by the write path, neither of which is
    here. Asserted by reading the source: no module names either member."""
    offenders = {
        path.name
        for path in modules()
        if path.name != "status.py"
        and {"APPROVED", "POSTED"} & names_used(ast.parse(path.read_text()))
    }
    assert offenders == set()


def test_the_statuses_where_nothing_was_read_produce_a_full_empty_record() -> None:
    """A caller branching on 'did we get a result at all' has two shapes of
    answer, and the second shape is the one nobody tests."""
    result = describe(
        Reading.from_text("   \n", source="a test", confidence=1.0),
        printing=EXACT,
        file_hash="blank",
    )
    assert result.status in NOTHING_WAS_READ
    assert isinstance(result, ExtractionResult)
    assert result.read_fields == ()
    assert result.average_confidence == 0.0
    assert result.lowest_confidence == 0.0
    assert len(result.field_confidence) > 20


# ---------------------------------------------------------------------------
# 6. the two lists of mandatory field names have to agree
# ---------------------------------------------------------------------------


def test_the_bridge_and_the_law_use_the_same_names_for_mandatory_fields() -> None:
    """A rename on one side would make a mandatory field permanently missing and
    every bill would fail for a reason nobody could find."""
    from accountant.invoice.bridge import mandatory_found

    result = describe(text_reading(INTRA_STATE), printing=EXACT, file_hash="m")
    reported = mandatory_found(
        supplier=result.supplier, invoice=result.invoice, totals=result.totals
    )
    assert set(reported) == {name for name, _words in MANDATORY}


# ---------------------------------------------------------------------------
# 7. the batch
# ---------------------------------------------------------------------------


def a_document(fixture: Fixture, *, name: str, file_hash: str) -> batch.Document:
    return batch.Document(
        name=name,
        file_hash=file_hash,
        reading=text_reading(fixture),
        engine="a test",
    )


def test_the_same_bytes_twice_produce_one_result_and_one_recorded_repeat() -> None:
    """IDEMPOTENT BY FILE HASH. Not two results, not two drafts."""
    report = batch.run(
        [
            a_document(INTRA_STATE, name="scan-01.pdf", file_hash="aaaa"),
            a_document(INTRA_STATE, name="scan-01-copy.pdf", file_hash="aaaa"),
        ],
        printing=EXACT,
    )
    assert len(report.read) == 1
    assert report.read[0].name == "scan-01.pdf"
    assert len(report.repeats) == 1
    assert report.repeats[0].name == "scan-01-copy.pdf"
    assert report.repeats[0].first_seen_as == "scan-01.pdf"


def test_the_same_bill_arriving_as_different_bytes_is_caught_by_its_number() -> None:
    """A bill photographed twice has two hashes and one number, so the hash
    check cannot see it and the repeat law can."""
    report = batch.run(
        [
            a_document(INTRA_STATE, name="photo-a.jpg", file_hash="aaaa"),
            a_document(INTRA_STATE, name="photo-b.jpg", file_hash="bbbb"),
        ],
        printing=EXACT,
    )
    assert len(report.read) == 2
    second = report.read[1].result
    assert second.status is DocumentStatus.INVOICE_VALIDATION_FAILED
    assert any(
        "has already been read in this run" in one.said for one in second.findings
    )


def test_one_document_that_raises_does_not_stop_the_rest() -> None:
    """A batch that stops at the first bad file is one somebody runs overnight
    and finds one-tenth finished, with no way to tell which tenth."""

    class Exploding(Reading):
        def words_under(self, where: Where) -> tuple[Word, ...]:
            del where
            raise RuntimeError("this document is not readable in any way")

    scored = word_reading(INTRA_STATE, confidence=90)
    exploding = Exploding(
        lines=scored.lines,
        words=scored.words,
        source=scored.source,
        stated_confidence=None,
        text=scored.text,
    )
    report = batch.run(
        [
            a_document(INTRA_STATE, name="good-1.pdf", file_hash="aaaa"),
            batch.Document(name="bad.pdf", file_hash="bbbb", reading=exploding),
            a_document(INTER_STATE, name="good-2.pdf", file_hash="cccc"),
        ],
        printing=EXACT,
    )
    assert [one.name for one in report.read] == ["good-1.pdf", "good-2.pdf"]
    assert len(report.broken) == 1
    assert report.broken[0].name == "bad.pdf"
    assert report.broken[0].failure == (
        "RuntimeError: this document is not readable in any way"
    )


def test_the_report_counts_every_status_including_the_ones_at_zero() -> None:
    """A map with the zeroes left out reads as though those statuses cannot
    happen, when what it means is that they did not happen this time."""
    report = batch.run(
        [
            a_document(INTRA_STATE, name="a", file_hash="1"),
            a_document(MISSING_FIELDS, name="b", file_hash="2"),
            a_document(NOT_AN_INVOICE, name="c", file_hash="3"),
        ],
        printing=EXACT,
    )
    counts = report.counts
    assert set(counts) == set(DocumentStatus)
    assert counts[DocumentStatus.READY_FOR_REVIEW] == 1
    assert counts[DocumentStatus.INVOICE_MISSING_FIELDS] == 1
    assert counts[DocumentStatus.UNKNOWN_DOCUMENT] == 1
    assert counts[DocumentStatus.OCR_FAILED] == 0
    assert counts[DocumentStatus.POSTED] == 0


def test_the_report_keeps_what_every_reader_actually_returned() -> None:
    report = batch.run(
        [a_document(INTRA_STATE, name="a.pdf", file_hash="1")], printing=EXACT
    )
    assert report.raw_text["a.pdf"] == INTRA_STATE.text


def test_running_the_same_batch_twice_produces_the_same_report() -> None:
    documents = [
        a_document(INTRA_STATE, name="a", file_hash="1"),
        a_document(MISSING_FIELDS, name="b", file_hash="2"),
    ]
    assert repr(batch.run(documents, printing=EXACT)) == repr(
        batch.run(documents, printing=EXACT)
    )


def test_a_document_in_a_batch_must_have_a_name() -> None:
    with pytest.raises(ValueError, match="must have a name"):
        batch.Document(name="  ", file_hash="1", reading=text_reading(INTRA_STATE))


def test_every_document_needing_review_is_listed_as_such() -> None:
    report = batch.run(
        [
            a_document(INTRA_STATE, name="a", file_hash="1"),
            a_document(MISSING_FIELDS, name="b", file_hash="2"),
        ],
        printing=EXACT,
    )
    assert len(report.needing_review) == 2


# ---------------------------------------------------------------------------
# 8. the thresholds, and what they are not
# ---------------------------------------------------------------------------


def test_the_unmeasured_thresholds_are_in_one_place_and_replaceable() -> None:
    """Two of the three are shape arguments and nothing has measured them, so
    they sit on a type a caller can replace rather than in an `if`."""
    assert DEFAULT_THRESHOLDS.enough_characters == 200
    assert DEFAULT_THRESHOLDS.legible_share == 0.5
    thin = describe(
        text_reading(INTRA_STATE),
        printing=EXACT,
        file_hash="t",
        thresholds=Thresholds(enough_characters=10_000),
    )
    assert thin.status is DocumentStatus.INVOICE_LOW_TEXT


def test_the_low_confidence_floor_is_the_owners_number_and_not_a_second_copy() -> None:
    from accountant.cage.decision import ASK_FLOOR

    assert DEFAULT_THRESHOLDS.low_confidence_below == ASK_FLOOR
    assert ASK_FLOOR == 0.70


# ---------------------------------------------------------------------------
# 9. the record is inert
# ---------------------------------------------------------------------------


def test_the_result_has_no_method_that_writes_saves_or_posts() -> None:
    """The same guard `cage/wall.py::Observation` carries: a record that can
    turn itself into something postable is a wall with a door in it."""
    forbidden = ("post", "save", "write", "send", "commit", "to_voucher", "as_entry")
    found = [
        name
        for name in dir(ExtractionResult)
        if any(word in name.lower() for word in forbidden)
    ]
    assert found == []


def test_every_field_on_the_record_is_frozen() -> None:
    result = describe(text_reading(INTRA_STATE), printing=EXACT, file_hash="f")
    with pytest.raises((AttributeError, TypeError)):
        result.raw_text = "edited"  # pyright: ignore[reportAttributeAccessIssue]
    with pytest.raises((AttributeError, TypeError)):
        result.totals.grand_total.field.value = 1  # pyright: ignore[reportAttributeAccessIssue]
