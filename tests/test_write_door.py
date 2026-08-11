"""Every write to TallyPrime goes through one door, and the door is guarded.

THE MEASURED DEFECT, 2026-08-11
-------------------------------
`ci/educational_slice.py:233` constructed a `RealTally` and called
`client.write_voucher(...)` on it, and `:301` called
`client.reverse_by_operation_id(...)`. Both reached the connector directly, so
neither passed the Valid gate, the decision/operation-id binding, the
field-by-field read-back (C6) or the register check (G3) that `pipeline.post`
enforces, and the undo was a bare boolean rather than a trial-balance
comparison.

`tests/test_runtime_backend.py` exists precisely to keep the write door
singular. It could not see this one: it scans `accountant/` only, and this was
a second door in `ci/` — pointed, unlike anything in `accountant/`, at a real
TallyPrime running on somebody's machine.

So the scan is widened to every root that holds runnable code, and the
exceptions stop being implicit. `ALLOWED` below is the complete, reviewable
list of places that may call a write method directly, each with the reason it
is there and the exact number of calls it is permitted. An eighth call site, or
a third call inside a site permitted two, fails this file.

WHY AST AND NOT A SUBSTRING SCAN
---------------------------------
This repository has twice been burned by a substring scan matching the word it
was looking for inside its own explanatory comment. Every file in this
codebase describes what it does and why, so `"write_voucher" in source` is true
of a dozen modules that never call it — and a scan that cries wolf gets
loosened until it catches nothing. `test_a_substring_scan_would_fire_where_this
_one_correctly_stays_silent` measures that difference rather than asserting it.

WHAT THIS FILE DOES NOT PROVE
-----------------------------
That `pipeline.post` is correct — that is `tests/test_pipeline.py` and
`tests/test_adversarial_write_path.py`. That the connector is correct — that is
`tests/test_real_tally.py`. This file proves only that there is one door and
that the doors deliberately left beside it are named out loud.
"""

from __future__ import annotations

import ast
import pathlib
from collections import Counter
from collections.abc import Iterable, Sequence
from typing import NamedTuple

import pytest

REPO = pathlib.Path(__file__).resolve().parent.parent

#: The two connector methods that CHANGE somebody's statutory books. Reads are
#: not here on purpose: a read that goes wrong wastes a round trip, a write that
#: goes wrong is in a person's books until somebody notices.
WRITE_METHODS = ("write_voucher", "reverse_by_operation_id")

#: Every directory in this repository that holds runnable Python. `tests/` is
#: deliberately absent — a test IS allowed to drive a connector directly, that
#: is what a connector test is — and so is `.venv`, which is not ours.
#:
#: `scripts/` holds zero write calls today and is scanned anyway: it ships in
#: the repository and a write added there would reach Tally exactly as hard as
#: one added to `ci/`.
DEFAULT_ROOTS = (REPO / "accountant", REPO / "ci", REPO / "scripts")

CONNECTOR = REPO / "accountant" / "tallyio"

#: The needle for the other way to write: a hand-built Tally import envelope
#: posted at the gateway without going through the connector at all.
IMPORT_ENVELOPE = "TALLYREQUEST>Import"

#: Names that carry the ability to put bytes on the wire to Tally.
TRANSPORT_NAMES = ("Transport", "HttpTransport", "_post_bytes")


class Site(NamedTuple):
    """A place a write may be called from. Deliberately WITHOUT a line number.

    A line number in the key would mean every unrelated edit above a permitted
    call churned this allow-list, and a list that churns is a list nobody reads.
    The line still reaches the failure message, where it is useful.
    """

    path: str
    scope: str
    method: str


class Permit(NamedTuple):
    """Permission for a site, and the number of calls it covers.

    The count is what stops the allow-list becoming a blanket. Naming a
    function as permitted would otherwise permit every future call added inside
    it, which is how a reviewed exception turns into an unreviewed one.
    """

    calls: int
    why: str


class Call(NamedTuple):
    """One call to a write method, located well enough to go and fix it."""

    path: str
    line: int
    scope: str
    method: str
    receiver: str

    @property
    def site(self) -> Site:
        return Site(self.path, self.scope, self.method)


# ---------------------------------------------------------------------------
# THE ALLOW-LIST. Every direct write in this repository, and why it is allowed.
#
# Adding an entry here is a decision somebody makes on purpose and a reviewer
# reads in the diff. That is the whole point of writing it down instead of
# excluding a directory.
# ---------------------------------------------------------------------------

ALLOWED: dict[Site, Permit] = {
    Site("accountant/pipeline.py", "post", "write_voucher"): Permit(
        1,
        "THE DOOR. Everything the write path is allowed to do lives behind it: "
        "the Valid-outcome gate, the decision/operation-id binding, the "
        "write-ahead audit row, the field-by-field read-back (C6) and the "
        "register check (G3). This is the entry the other six exist relative to.",
    ),
    Site(
        "accountant/pipeline.py", "reverse_operation", "reverse_by_operation_id"
    ): Permit(
        1,
        "THE DOOR for undoing. It reads the voucher, snapshots the trial "
        "balance either side and raises ReversalMismatch rather than reporting "
        "a success the books do not support. A reversal that returns True and "
        "leaves the books unchanged is the worst of the three outcomes because "
        "it is the one that gets believed.",
    ),
    Site("ci/acceptance.py", "run_acceptance", "write_voucher"): Permit(
        2,
        "The N = 10 acceptance harness, and the one place a direct write is "
        "the point. It measures the CONNECTOR: condition 5 is a per-voucher "
        "field-by-field read-back and condition 6 is the connector's own "
        "refusal of a repeated operation id. pipeline.post performs the "
        "read-back itself and RAISES, which would collapse fifteen reported "
        "conditions into one traceback — and this harness's contract is that a "
        "Tally-shaped failure is a result, not a crash. There is also nothing "
        "for the Valid gate to judge: no document, no extraction, no memory, "
        "ten fixtures from controlled_voucher(i). Its CLEANUP is not exempt — "
        "it goes through reversal.execute -> pipeline.reverse_operation, and "
        "test_the_acceptance_harness_never_undoes_anything_directly holds that.",
    ),
    Site("ci/readiness.py", "post_one", "write_voucher"): Permit(
        1,
        "Phase 5B fixture posting, against FakeTally and nothing else. The "
        "parameter is typed FakeTally as of 2026-08-11, so this call cannot "
        "reach a socket: FakeTally is in-process, keeps its vouchers in a list, "
        "and tests/test_runtime_backend.py proves the runtime cannot reach it.",
    ),
    Site("ci/readiness.py", "_retry_creates", "write_voucher"): Permit(
        1,
        "The duplicate-operation-id probe. It exists to count how many vouchers "
        "a refused retry actually created, because a connector that refuses "
        "loudly and writes anyway is the defect only a count can see. Same "
        "FakeTally-typed parameter, so it reaches no real Tally.",
    ),
    Site(
        "ci/readiness.py",
        "DelaysThenDrops.reverse_by_operation_id",
        "reverse_by_operation_id",
    ): Permit(
        2,
        "A fault-injection double: the delete lands and the answer is lost, "
        "which is the case that produces an unknown outcome for reconciliation "
        "to settle. Both calls are super() inside a FakeTally subclass, so they "
        "reach the fake's own implementation and never a connector. A failure "
        "is never manufactured in real statutory books.",
    ),
    Site(
        "ci/readiness.py",
        "RefusesOne.reverse_by_operation_id",
        "reverse_by_operation_id",
    ): Permit(
        1,
        "The other fault-injection double: Tally refuses exactly one reversal. "
        "super() inside a FakeTally subclass, same reasoning as above.",
    ),
}


# ---------------------------------------------------------------------------
# the finder
# ---------------------------------------------------------------------------


def _label(path: pathlib.Path) -> str:
    """Repo-relative where possible, so failures name something recognisable."""
    try:
        return path.relative_to(REPO).as_posix()
    except ValueError:
        return path.as_posix()


def _modules_under(roots: Sequence[pathlib.Path]) -> list[pathlib.Path]:
    return sorted(
        p for root in roots for p in root.rglob("*.py") if "__pycache__" not in p.parts
    )


def _receiver(func: ast.expr) -> str:
    """What the call was made ON — `client`, `super()`, or nothing at all.

    Kept because the reason two of the allow-listed sites are safe IS their
    receiver: a `super()` call inside a FakeTally subclass reaches the fake.
    """
    if isinstance(func, ast.Attribute):
        return ast.unparse(func.value)
    return "<bare name>"


def _calls_in(source: str, path: str) -> list[Call]:
    """Every call to a write method in one module, with its enclosing scope.

    AST, not text. A definition is not a call, a comment is not a call, and a
    docstring that names the method is not a call — this repository has been
    burned twice by a scan that could not tell those apart.
    """
    found: list[Call] = []

    def named(func: ast.expr) -> str | None:
        if isinstance(func, ast.Attribute) and func.attr in WRITE_METHODS:
            return func.attr
        if isinstance(func, ast.Name) and func.id in WRITE_METHODS:
            return func.id
        return None

    def walk(node: ast.AST, scope: tuple[str, ...]) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.Call):
                method = named(child.func)
                if method is not None:
                    found.append(
                        Call(
                            path=path,
                            line=child.lineno,
                            scope=".".join(scope) or "<module>",
                            method=method,
                            receiver=_receiver(child.func),
                        )
                    )
            inner = scope
            if isinstance(child, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
                inner = (*scope, child.name)
            walk(child, inner)

    walk(ast.parse(source), ())
    return found


def _calls_under(roots: Sequence[pathlib.Path] = DEFAULT_ROOTS) -> list[Call]:
    calls: list[Call] = []
    for path in _modules_under(roots):
        calls.extend(_calls_in(path.read_text(encoding="utf-8"), _label(path)))
    return calls


def _unsanctioned(
    calls: Iterable[Call], allowed: dict[Site, Permit] = ALLOWED
) -> list[Call]:
    """Every call the allow-list does not cover, and every call over its count.

    When a site overruns its permit, ALL of its calls are reported. Nothing here
    can tell which one is new, and a message naming an arbitrary one of them
    would send a reader to the wrong line.
    """
    seen = Counter(call.site for call in calls)
    return [
        call
        for call in calls
        if call.site not in allowed or seen[call.site] > allowed[call.site].calls
    ]


def _stale(calls: Iterable[Call], allowed: dict[Site, Permit] = ALLOWED) -> list[Site]:
    """Allow-list entries no call matches. A permit for a door that is not there.

    Not cosmetic. An entry left behind after its call moved is standing
    permission for the next person who happens to add a call in that function.
    """
    seen = Counter(call.site for call in calls)
    return [site for site in allowed if seen[site] == 0]


def _describe(calls: Sequence[Call]) -> str:
    """The part of a failure that says WHERE, not merely that."""
    return "; ".join(
        f"{c.path}:{c.line} in {c.scope} calls {c.receiver}.{c.method}" for c in calls
    )


def _string_constants(source: str) -> list[str]:
    return [
        node.value
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    ]


def _imported_names(source: str) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.ImportFrom):
            for alias in node.names:
                names.add(alias.name)
    return names


# ---------------------------------------------------------------------------
# the gate itself
# ---------------------------------------------------------------------------


def test_no_module_outside_the_allow_list_can_write_to_tally() -> None:
    """The whole claim, in one assertion, over every root that ships code."""
    offenders = _unsanctioned(_calls_under())

    assert offenders == [], (
        f"a write to TallyPrime is reachable outside the sanctioned set. Route "
        f"it through accountant/pipeline.py — post() for a write, "
        f"reverse_operation() for an undo — or add it to ALLOWED in "
        f"tests/test_write_door.py with the reason it must stay direct. "
        f"Found: {_describe(offenders)}"
    )


def test_the_only_door_for_a_write_is_pipeline_post() -> None:
    """Naming it, so the failure above is not the only place a reader learns it."""
    writes = [c for c in _calls_under() if c.method == "write_voucher"]
    in_the_package = [c for c in writes if c.path.startswith("accountant/")]

    assert [(c.path, c.scope) for c in in_the_package] == [
        ("accountant/pipeline.py", "post")
    ], f"a second write door inside the package: {_describe(in_the_package)}"


def test_the_only_door_for_an_undo_is_pipeline_reverse_operation() -> None:
    """A delete is the more destructive of the two and had no gate at all
    until §39.1. It has one door for the same reason a write does."""
    undos = [c for c in _calls_under() if c.method == "reverse_by_operation_id"]
    in_the_package = [c for c in undos if c.path.startswith("accountant/")]

    assert [(c.path, c.scope) for c in in_the_package] == [
        ("accountant/pipeline.py", "reverse_operation")
    ], f"a second undo door inside the package: {_describe(in_the_package)}"


def test_every_allow_list_entry_still_matches_a_call_that_exists() -> None:
    """A permit for a call that is gone is standing permission for the next one."""
    stale = _stale(_calls_under())

    assert stale == [], (
        "ALLOWED in tests/test_write_door.py permits call sites that no longer "
        f"exist, so it now permits calls nobody has reviewed: {stale}"
    )


def test_every_allow_list_entry_carries_a_reason_somebody_wrote() -> None:
    """An exception with no argument for itself is an exclusion in disguise."""
    thin = [site for site, permit in ALLOWED.items() if len(permit.why.split()) < 15]

    assert thin == [], (
        f"these allow-list entries state no real reason: {thin}. The list is "
        "only useful if a reviewer can judge each entry from the entry itself"
    )


def test_every_allow_list_entry_permits_at_least_one_call() -> None:
    """`Permit(0, ...)` would be an entry that forbids and reads as permission."""
    empty = [site for site, permit in ALLOWED.items() if permit.calls < 1]

    assert empty == [], f"allow-list entries permitting no calls: {empty}"


def test_the_line_reported_for_every_call_site_really_holds_the_call() -> None:
    """A line number nobody can trust makes the failure message useless."""
    wrong: list[str] = []
    for call in _calls_under():
        line = (
            (REPO / call.path).read_text(encoding="utf-8").splitlines()[call.line - 1]
        )
        if call.method not in line:
            wrong.append(f"{call.path}:{call.line} reads {line.strip()!r}")

    assert wrong == [], f"call sites reported at the wrong line: {wrong}"


# ---------------------------------------------------------------------------
# the other way to write: a hand-built import envelope at the gateway
# ---------------------------------------------------------------------------


def test_nothing_outside_the_connector_builds_a_tally_import_envelope() -> None:
    """A raw `<TALLYREQUEST>Import</TALLYREQUEST>` bypasses the methods entirely.

    Scanning for `write_voucher` alone would be a guard on the doorway with the
    wall left open: the connector's write is only XML on a socket, and anybody
    able to build that XML can post it without naming either method.
    """
    offenders = [
        f"{_label(path)} builds a Tally import envelope"
        for path in _modules_under(DEFAULT_ROOTS)
        if CONNECTOR not in path.parents
        and any(
            IMPORT_ENVELOPE in text
            for text in _string_constants(path.read_text(encoding="utf-8"))
        )
    ]

    assert offenders == [], (
        "an import envelope is built outside accountant/tallyio/, so a write "
        f"could reach Tally without calling either method: {offenders}"
    )


def test_the_import_envelope_needle_is_one_the_connector_actually_uses() -> None:
    """A needle that matches nothing anywhere excludes nothing and proves nothing.

    Same idiom as `test_the_tally_connector_is_excluded_and_is_a_real_place_to
    _exclude` in tests/test_runtime_backend.py.
    """
    builders = [
        _label(path)
        for path in sorted(CONNECTOR.rglob("*.py"))
        if any(
            IMPORT_ENVELOPE in text
            for text in _string_constants(path.read_text(encoding="utf-8"))
        )
    ]

    assert builders, (
        f"no module under accountant/tallyio/ contains {IMPORT_ENVELOPE!r}, so "
        "the scan above is looking for a string this codebase never writes"
    )


def test_nothing_outside_the_connector_imports_the_tally_transport() -> None:
    """The transport is the thing that opens the socket. One importer, by name."""
    offenders = [
        f"{_label(path)} imports {sorted(names & set(TRANSPORT_NAMES))}"
        for path in _modules_under(DEFAULT_ROOTS)
        if CONNECTOR not in path.parents
        and (names := _imported_names(path.read_text(encoding="utf-8")))
        & set(TRANSPORT_NAMES)
    ]

    assert offenders == [], (
        "the Tally transport is reachable outside accountant/tallyio/, so bytes "
        f"could be put on the wire without the connector: {offenders}"
    )


def test_the_transport_names_are_defined_where_this_test_says_they_are() -> None:
    """The same emptiness trap as the envelope needle, for the import check."""
    source = (CONNECTOR / "real.py").read_text(encoding="utf-8")
    defined = {
        node.name
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.ClassDef | ast.FunctionDef)
    }

    assert set(TRANSPORT_NAMES) <= defined, (
        f"accountant/tallyio/real.py defines {sorted(set(TRANSPORT_NAMES) - defined)} "
        "nowhere, so the import scan above forbids names that do not exist"
    )


# ---------------------------------------------------------------------------
# the fixed defect, held fixed
# ---------------------------------------------------------------------------


def test_the_educational_slice_writes_only_through_the_door() -> None:
    """The measured defect of 2026-08-11, asserted rather than remembered.

    A behaviour test cannot hold this: the slice needs a live TallyPrime to run
    at all, so nothing in this suite ever executes that line. Only the call
    graph can say which function it calls.
    """
    slice_path = REPO / "ci" / "educational_slice.py"
    source = slice_path.read_text(encoding="utf-8")
    direct = _calls_in(source, "ci/educational_slice.py")
    tree = ast.parse(source)
    through = {
        ast.unparse(node.func)
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
    }

    assert direct == [], (
        f"ci/educational_slice.py drives the connector directly again: "
        f"{_describe(direct)}. It points at a real TallyPrime, which makes it "
        "the worst place in the repository for a second write door"
    )
    assert "pipeline.post" in through, (
        "ci/educational_slice.py no longer calls pipeline.post, so its write "
        "passes no gate at all"
    )
    assert "pipeline.reverse_operation" in through, (
        "ci/educational_slice.py no longer calls pipeline.reverse_operation, so "
        "its undo is back to trusting a boolean"
    )


def test_the_educational_slice_still_posts_on_a_date_educational_mode_permits() -> None:
    """Routing through the door must not have moved the date.

    `tests/test_evidence_classes.py` owns this rule; it is restated here because
    the change that introduced the door is the change most likely to have
    disturbed the fixture, and a guard that only checks its own subject is how
    a fix breaks something one file over.
    """
    from ci import educational_slice

    assert educational_slice.EDUCATIONAL_DATE.day in (1, 2, 31)
    assert educational_slice.UNTOUCHED_FIXTURE_DATE.isoformat() == "2026-08-07"
    assert educational_slice.EVIDENCE_CLASS == "EDUCATIONAL_MODE_COMPATIBILITY"


def test_the_acceptance_harness_never_undoes_anything_directly() -> None:
    """Its two direct WRITES are allow-listed. Its cleanup is not, and must not be.

    A batch that called the connector's reverse directly would be fast, would
    look right, and would reintroduce the defect §39.1 closed — "reversed"
    meaning "a boolean said so".
    """
    source = (REPO / "ci" / "acceptance.py").read_text(encoding="utf-8")
    undos = [
        c for c in _calls_in(source, "ci/acceptance.py") if c.method != "write_voucher"
    ]

    assert undos == [], (
        f"ci/acceptance.py reverses directly at {_describe(undos)}; every undo "
        "goes through reversal.execute -> pipeline.reverse_operation"
    )
    assert "reversal.execute" in source


def test_the_readiness_gate_can_only_ever_drive_the_fake() -> None:
    """What makes its four allow-list entries safe, checked instead of promised.

    The entries say "it can only reach FakeTally". Until 2026-08-11 the
    annotations said `TallyClient`, so `run_a(client=RealTally(...))`
    type-checked and thirty injected failures could have landed in real books.
    The reason is now a type, and this is what holds it there.
    """
    source = (REPO / "ci" / "readiness.py").read_text(encoding="utf-8")
    tree = ast.parse(source)

    functions = {
        node.name: node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef)
    }
    for name in ("post_one", "_retry_creates", "run_a"):
        client = functions[name].args.args[0]
        assert client.arg == "client", f"{name}'s first argument is not the client"
        assert client.annotation is not None, f"{name}'s client is unannotated"
        assert "FakeTally" in ast.unparse(client.annotation), (
            f"ci/readiness.py::{name} accepts "
            f"{ast.unparse(client.annotation)}, which lets a REAL Tally be "
            "handed to a gate whose whole method is injecting failures"
        )

    doubles = {
        node.name: [ast.unparse(base) for base in node.bases]
        for node in ast.walk(tree)
        if isinstance(node, ast.ClassDef)
    }
    for name in ("DelaysThenDrops", "RefusesOne"):
        assert doubles[name] == ["FakeTally"], (
            f"ci/readiness.py::{name} no longer derives from FakeTally, so its "
            f"super() call reaches {doubles[name]} instead of the fake"
        )

    undos = [
        c for c in _calls_in(source, "ci/readiness.py") if c.method != "write_voucher"
    ]
    assert [c.receiver for c in undos] == ["super()"] * len(undos), (
        f"a reverse in ci/readiness.py is no longer a super() call into the "
        f"fake: {_describe(undos)}"
    )


# ---------------------------------------------------------------------------
# the scan is looking at something, and is aimed at real method names
# ---------------------------------------------------------------------------


def test_the_scan_reaches_every_root_and_a_meaningful_number_of_modules() -> None:
    """A scan over an empty file list passes every count above at zero."""
    scanned = [_label(p) for p in _modules_under(DEFAULT_ROOTS)]

    for root in DEFAULT_ROOTS:
        assert root.is_dir(), f"{_label(root)} is not a directory"
        assert any(p.startswith(f"{root.name}/") for p in scanned), (
            f"{root.name}/ contributed no modules to the scan"
        )
    assert "accountant/pipeline.py" in scanned
    assert "accountant/memory/store.py" in scanned, "rglob missed a subpackage"
    assert "accountant/web/app.py" in scanned
    assert "ci/educational_slice.py" in scanned
    assert len(scanned) >= 45, f"measured 55 at HEAD 9a28d22, saw {len(scanned)}"


def test_the_connector_defines_both_methods_the_scan_looks_for() -> None:
    """A scan for a misspelled method finds nothing and reports a clean repo."""
    defined: set[str] = set()
    for path in sorted(CONNECTOR.rglob("*.py")):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.FunctionDef) and node.name in WRITE_METHODS:
                defined.add(node.name)

    assert defined == set(WRITE_METHODS), (
        f"accountant/tallyio/ defines {sorted(defined)}; the scan is looking "
        f"for {sorted(WRITE_METHODS)}, so {sorted(set(WRITE_METHODS) - defined)} "
        "is a name nothing in this codebase has"
    )


def test_the_real_scan_finds_the_call_sites_that_are_known_to_be_there() -> None:
    """The roots are wired to the finder, measured against the actual repository.

    Without this, `DEFAULT_ROOTS` could be pointed at anything — a renamed
    directory, a typo — and every assertion above would pass on nothing.
    """
    sites = Counter(call.site for call in _calls_under())

    assert sites == Counter({site: permit.calls for site, permit in ALLOWED.items()}), (
        "the call sites in the repository and the allow-list have drifted "
        f"apart. Found: {dict(sites)}"
    )
    assert sum(sites.values()) == 9, (
        f"9 direct calls at HEAD, found {sum(sites.values())}"
    )


# ---------------------------------------------------------------------------
# CONTROL. The guard is load-bearing, proved by planting violations.
#
# Planted into a tree under `tmp_path` rather than into `accountant/` itself,
# because CI runs this suite under `pytest -n auto`: a file living for
# milliseconds inside the real package would be seen by whatever unrelated test
# another worker happened to be running at that instant. The finder, the
# allow-list and `_unsanctioned` are the same objects the real scan uses;
# `test_the_real_scan_finds_the_call_sites_that_are_known_to_be_there` is what
# ties them to the real directories.
# ---------------------------------------------------------------------------

CONTROL_WRITE = "def post(client, v, op):\n    client.write_voucher('Co', v, op)\n"
CONTROL_UNDO = "def undo(client, op):\n    client.reverse_by_operation_id('Co', op)\n"


def _plant(root: pathlib.Path, package: str, name: str, source: str) -> pathlib.Path:
    directory = root / package
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / name
    path.write_text(source, encoding="utf-8")
    return path


def test_the_guard_catches_a_write_planted_in_a_module_it_scans(
    tmp_path: pathlib.Path,
) -> None:
    """The mutation. Without this, a guard over an empty list would look green."""
    planted = _plant(tmp_path, "accountant", "_smuggler.py", CONTROL_WRITE)

    offenders = _unsanctioned(_calls_under([tmp_path / "accountant"]))

    assert [(c.line, c.scope, c.method) for c in offenders] == [
        (2, "post", "write_voucher")
    ], f"the guard did not catch {planted}"


def test_the_guard_catches_an_undo_planted_in_a_module_it_scans(
    tmp_path: pathlib.Path,
) -> None:
    """The same mutation for the more destructive of the two methods."""
    _plant(tmp_path, "ci", "_smuggler.py", CONTROL_UNDO)

    offenders = _unsanctioned(_calls_under([tmp_path / "ci"]))

    assert [(c.line, c.scope, c.method) for c in offenders] == [
        (2, "undo", "reverse_by_operation_id")
    ]


def test_the_guard_catches_a_write_planted_in_a_deep_subpackage(
    tmp_path: pathlib.Path,
) -> None:
    """rglob, not listdir. A door one directory further down is still a door."""
    _plant(tmp_path, "accountant/web/handlers", "_smuggler.py", CONTROL_WRITE)

    offenders = _unsanctioned(_calls_under([tmp_path / "accountant"]))

    assert len(offenders) == 1, "the scan does not descend into subpackages"


def test_one_call_more_than_a_permit_allows_is_caught() -> None:
    """The count is what stops an entry becoming a blanket over a function."""
    site = Site("ci/acceptance.py", "run_acceptance", "write_voucher")
    permits = {site: Permit(2, "two, and only two")}
    two = [Call(site.path, n, site.scope, site.method, "client") for n in (1, 2)]

    assert _unsanctioned(two, permits) == []
    assert (
        len(
            _unsanctioned(
                [*two, Call(site.path, 3, site.scope, site.method, "c")], permits
            )
        )
        == 3
    )


def test_a_call_in_an_unlisted_function_of_a_listed_file_is_caught() -> None:
    """The key is the SCOPE, not the file. A permitted file is not a permitted file."""
    listed = Site("ci/acceptance.py", "run_acceptance", "write_voucher")
    permits = {listed: Permit(1, "the acceptance run")}
    elsewhere = Call("ci/acceptance.py", 900, "a_new_helper", "write_voucher", "client")

    assert _unsanctioned([elsewhere], permits) == [elsewhere]


def test_a_permit_for_one_method_does_not_cover_the_other() -> None:
    """A write permit is not an undo permit. They fail differently and undo is worse."""
    permits = {
        Site("ci/acceptance.py", "run_acceptance", "write_voucher"): Permit(1, "write")
    }
    undo = Call(
        "ci/acceptance.py", 300, "run_acceptance", "reverse_by_operation_id", "client"
    )

    assert _unsanctioned([undo], permits) == [undo]


def test_a_stale_allow_list_entry_is_reported() -> None:
    """The mutation for the staleness check, which is otherwise unfalsifiable.

    A permit that matches nothing is the quiet failure: it costs nothing, so
    nobody removes it, and it sits there as standing permission for whatever
    call is added to that function next.
    """
    here = Site("ci/acceptance.py", "run_acceptance", "write_voucher")
    gone = Site("ci/removed.py", "vanished", "write_voucher")
    permits = {here: Permit(1, "still here"), gone: Permit(1, "not any more")}

    assert _stale([Call(here.path, 1, here.scope, here.method, "client")], permits) == [
        gone
    ]


# ---------------------------------------------------------------------------
# the finder can see, and cannot be fooled by prose
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        (
            "def helper(client):\n    client.write_voucher('Co', v, 'op_1')\n",
            Call("m.py", 2, "helper", "write_voucher", "client"),
        ),
        (
            "client.reverse_by_operation_id('Co', 'op_1')\n",
            Call("m.py", 1, "<module>", "reverse_by_operation_id", "client"),
        ),
        (
            "class Poster:\n"
            "    def go(self, client):\n"
            "        return client.write_voucher('Co', v, 'op_1')\n",
            Call("m.py", 3, "Poster.go", "write_voucher", "client"),
        ),
        (
            "from accountant.tallyio.client import write_voucher\n"
            "write_voucher('Co', v, 'op_1')\n",
            Call("m.py", 2, "<module>", "write_voucher", "<bare name>"),
        ),
        (
            "class Double(FakeTally):\n"
            "    def reverse_by_operation_id(self, company, op):\n"
            "        return super().reverse_by_operation_id(company, op)\n",
            Call(
                "m.py",
                3,
                "Double.reverse_by_operation_id",
                "reverse_by_operation_id",
                "super()",
            ),
        ),
        (
            "async def go(client):\n    await client.write_voucher('Co', v, 'op')\n",
            Call("m.py", 2, "go", "write_voucher", "client"),
        ),
        (
            "def go(runtime):\n    runtime.client.write_voucher('Co', v, 'op')\n",
            Call("m.py", 2, "go", "write_voucher", "runtime.client"),
        ),
    ],
    ids=[
        "in_a_function",
        "at_module_level",
        "in_a_method",
        "imported_by_name",
        "through_super",
        "awaited",
        "through_a_chain",
    ],
)
def test_the_finder_locates_a_call_it_is_given(source: str, expected: Call) -> None:
    assert _calls_in(source, "m.py") == [expected]


def test_the_finder_does_not_mistake_a_definition_for_a_call() -> None:
    source = (
        "class C:\n"
        "    def write_voucher(self, company, voucher, op):\n"
        "        ...\n"
        "    def reverse_by_operation_id(self, company, op):\n"
        "        ...\n"
    )

    assert _calls_in(source, "m.py") == []


def test_the_finder_does_not_mistake_prose_for_a_call() -> None:
    """The exact failure this repository has already had twice.

    Every module here explains itself, so the word appears in comments,
    docstrings and error messages all over the codebase. A scan that counts
    those cries wolf until somebody loosens it, and a loosened guard catches
    nothing.
    """
    source = (
        '"""This module never calls write_voucher.\n\n'
        "    reverse_by_operation_id is the connector's, not ours.\n"
        '"""\n'
        "# do not call client.write_voucher here\n"
        "MESSAGE = 'refusing: use write_voucher through pipeline.post'\n"
        "ATTRIBUTE = client.write_voucher\n"
    )

    assert _calls_in(source, "m.py") == []


def test_a_substring_scan_would_fire_where_this_one_correctly_stays_silent() -> None:
    """The difference between the two, measured rather than asserted."""
    source = (
        '"""Explains write_voucher and reverse_by_operation_id at length."""\n'
        "# client.write_voucher(...) is what pipeline.post calls\n"
    )

    assert any(method in source for method in WRITE_METHODS), "the trap is not set"
    assert _calls_in(source, "m.py") == []


def test_the_finder_reads_a_module_that_only_names_the_methods_in_strings() -> None:
    """Sanity on the constant scan used by the envelope check, same idea."""
    source = "PAYLOAD = '<TALLYREQUEST>Import</TALLYREQUEST>'\n"

    assert _string_constants(source) == ["<TALLYREQUEST>Import</TALLYREQUEST>"]
    assert _calls_in(source, "m.py") == []


def test_the_failure_message_names_the_file_the_line_the_scope_and_the_receiver() -> (
    None
):
    """Whoever broke it is told WHERE, not merely that something is wrong."""
    offenders = [
        Call("accountant/_probe.py", 7, "<module>", "write_voucher", "client"),
        Call("ci/report.py", 42, "sweep", "reverse_by_operation_id", "super()"),
    ]

    assert _describe(offenders) == (
        "accountant/_probe.py:7 in <module> calls client.write_voucher; "
        "ci/report.py:42 in sweep calls super().reverse_by_operation_id"
    )
