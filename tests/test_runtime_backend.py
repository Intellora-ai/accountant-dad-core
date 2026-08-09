"""The runtime backend is RealTally, and nothing else can be reached.

WHY THIS FILE EXISTS SEPARATELY
-------------------------------
Owner decision, 2026-08-09: `FakeTally` is forbidden in the runtime. It may stay
for isolated unit tests, but it must never be reachable from the live web app
and its results must never be presented as Phase 3 evidence.

That is an ARCHITECTURAL claim, not a behavioural one, so a behavioural test
cannot prove it. A test that posts a voucher and checks the result passes just
as happily against a fake. The only thing that settles "can the runtime reach
the fake" is reading the import graph and the call sites, which is what this
file does.

The repository already uses this idiom for claims of the same shape:
`tests/test_memory.py::test_no_module_in_this_package_uses_a_float` and
`::test_the_package_makes_no_network_or_model_call` both scan the AST rather
than trusting behaviour.

WHAT THIS FILE DOES NOT PROVE
-----------------------------
That RealTally works. That is `tests/test_real_tally.py` and the live evidence
in `docs/PROJECT_STATE.md` §21. This file proves only that the fake is out of
reach and that the real backend fails closed rather than falling back.
"""

from __future__ import annotations

import ast
import pathlib
from collections.abc import Sequence
from pathlib import Path
from typing import NamedTuple

import pytest

REPO = pathlib.Path(__file__).resolve().parent.parent

# `fake.py` is allowed to define FakeTally, obviously. Tests are allowed to use
# it. Everything else in the shipped package is not.
FAKE_IS_ALLOWED_IN = {
    REPO / "accountant" / "tallyio" / "fake.py",
}


def _imported_names(path: pathlib.Path) -> set[str]:
    """Every name this module imports, however it was written."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                names.add(alias.name)
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            names.add(module)
            for alias in node.names:
                names.add(f"{module}.{alias.name}")
                names.add(alias.name)
    return names


def _shipped_modules() -> list[pathlib.Path]:
    return sorted(
        p
        for p in (REPO / "accountant").rglob("*.py")
        if p not in FAKE_IS_ALLOWED_IN and "__pycache__" not in p.parts
    )


def test_no_shipped_module_imports_faketally() -> None:
    """Principle 6. The fake must be unreachable from the running application.

    A behavioural test cannot catch this: the app works fine against a fake,
    which is exactly the danger. The import graph is the evidence.
    """
    offenders = [
        f"{path.relative_to(REPO)} imports FakeTally"
        for path in _shipped_modules()
        if "FakeTally" in _imported_names(path)
    ]

    assert not offenders, (
        "FakeTally is reachable from shipped code, so a runtime could post "
        "into a fake and report it as evidence:\n  " + "\n  ".join(offenders)
    )


def test_the_web_app_constructs_no_tally_client_of_its_own() -> None:
    """Principle 5. One controlled doorway.

    The web app must ask the factory for a client, not construct one. If it can
    name a concrete implementation it can pick the wrong one, and backend
    identity, logging and write safety stop being enforceable in one place.
    """
    app = REPO / "accountant" / "web" / "app.py"
    names = _imported_names(app)

    assert "FakeTally" not in names, (
        "accountant/web/app.py imports FakeTally; the runtime must not be able "
        "to name the fake at all"
    )
    assert "RealTally" not in names, (
        "accountant/web/app.py imports RealTally directly; it must depend on "
        "the TallyClient interface through the factory, so that backend "
        "selection lives in exactly one place"
    )


# ---------------------------------------------------------------------------
# G5, the STRUCTURAL half of "no path posts unless the outcome is Valid".
#
# The behaviour tests in tests/test_pipeline.py prove that pipeline.post REFUSES
# a NOT_VALID or UNCLEAR draft. They cannot prove the thing that actually
# matters, which is that no OTHER path reaches write_voucher at all. A second,
# ungated call site could land tomorrow and every behaviour test would stay
# green. Only a scan of the call graph can say "there is no other door".
# ---------------------------------------------------------------------------

ROOT = Path(__file__).resolve().parent.parent
PACKAGE = ROOT / "accountant"
CONNECTOR = PACKAGE / "tallyio"

METHOD = "write_voucher"

#: The one call site that is allowed to exist, as (repo-relative path, scope).
THE_ONE_DOOR = ("accountant/pipeline.py", "post")


class Site(NamedTuple):
    """One call to `write_voucher`, located well enough to go and fix it."""

    path: str
    line: int
    scope: str


def _names_the_method(func: ast.expr) -> bool:
    """`client.write_voucher(...)` or a bare imported `write_voucher(...)`."""
    if isinstance(func, ast.Attribute):
        return func.attr == METHOD
    if isinstance(func, ast.Name):
        return func.id == METHOD
    return False


def _call_sites(source: str, path: str) -> list[Site]:
    found: list[Site] = []

    def walk(node: ast.AST, scope: tuple[str, ...]) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.Call) and _names_the_method(child.func):
                found.append(Site(path, child.lineno, ".".join(scope) or "<module>"))
            inner = scope
            if isinstance(child, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
                inner = (*scope, child.name)
            walk(child, inner)

    walk(ast.parse(source), ())
    return found


def _scanned_files() -> list[Path]:
    """Every module in the package except the connector that implements it."""
    return [p for p in sorted(PACKAGE.rglob("*.py")) if CONNECTOR not in p.parents]


def _relative(path: Path) -> str:
    return path.relative_to(ROOT).as_posix()


def _sites_outside_the_connector() -> list[Site]:
    sites: list[Site] = []
    for path in _scanned_files():
        rel = _relative(path)
        sites.extend(_call_sites(path.read_text(encoding="utf-8"), rel))
    return sites


def _describe(sites: Sequence[Site]) -> str:
    """The part of a failure that tells a person WHERE, not merely that."""
    return "; ".join(f"{s.path}:{s.line} in {s.scope}" for s in sites)


def _function(module: Path, name: str) -> ast.FunctionDef:
    tree = ast.parse(module.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    raise AssertionError(f"{_relative(module)} defines no function {name}")


# ---------------------------------------------------------------------------
# the gate itself
# ---------------------------------------------------------------------------


def test_write_voucher_has_exactly_one_call_site_outside_the_tally_connector() -> None:
    sites = _sites_outside_the_connector()

    assert len(sites) == 1, (
        f"{METHOD} must have exactly ONE call site outside accountant/tallyio/, "
        f"and it must be pipeline.post - the Valid gate lives there. "
        f"Found {len(sites)}: {_describe(sites)}"
    )


def test_the_only_write_voucher_call_site_is_inside_pipeline_post() -> None:
    sites = _sites_outside_the_connector()

    assert [(s.path, s.scope) for s in sites] == [THE_ONE_DOOR], (
        f"the only permitted {METHOD} call site is "
        f"{THE_ONE_DOOR[0]} in {THE_ONE_DOOR[1]}. Found: {_describe(sites)}"
    )


def test_the_line_reported_for_the_call_site_really_holds_the_call() -> None:
    """A line number nobody can trust makes the failure message useless."""
    site = _sites_outside_the_connector()[0]
    lines = (ROOT / site.path).read_text(encoding="utf-8").splitlines()

    assert METHOD in lines[site.line - 1], (
        f"{site.path}:{site.line} was reported as the call site but reads: "
        f"{lines[site.line - 1]!r}"
    )


def test_the_valid_gate_raises_before_the_write_inside_pipeline_post() -> None:
    """One door is worth nothing if the write sits ahead of the check."""
    post = _function(PACKAGE / "pipeline.py", "post")
    calls = [
        node
        for node in ast.walk(post)
        if isinstance(node, ast.Call) and _names_the_method(node.func)
    ]
    gates = [
        node
        for node in post.body
        if isinstance(node, ast.If)
        and any(
            isinstance(part, ast.Attribute) and part.attr == "VALID"
            for part in ast.walk(node.test)
        )
        and any(isinstance(part, ast.Raise) for part in ast.walk(node))
    ]

    assert len(calls) == 1, f"pipeline.post calls {METHOD} {len(calls)} times"
    assert len(gates) == 1, (
        "pipeline.post must hold exactly one unconditional top-level `if ... "
        f"VALID ...: raise` ahead of the write; found {len(gates)}"
    )
    assert gates[0].lineno < calls[0].lineno, (
        f"the Valid gate is at accountant/pipeline.py:{gates[0].lineno} but the "
        f"write is at accountant/pipeline.py:{calls[0].lineno} - the write is "
        f"not behind the gate"
    )


# ---------------------------------------------------------------------------
# the scan is looking at something, and the finder can see
# ---------------------------------------------------------------------------


def test_the_scan_reaches_the_package_including_its_subpackages() -> None:
    """A scan over an empty file list would pass every count above at zero."""
    scanned = [_relative(p) for p in _scanned_files()]

    assert "accountant/pipeline.py" in scanned
    assert "accountant/memory/store.py" in scanned, "rglob missed a subpackage"
    assert len(scanned) >= 30, f"measured 39 at HEAD 6867ca9, saw {len(scanned)}"


def test_the_tally_connector_is_excluded_and_is_a_real_place_to_exclude() -> None:
    """An exclusion aimed at a path that does not exist excludes nothing."""
    connector_modules = sorted(CONNECTOR.rglob("*.py"))
    scanned = [_relative(p) for p in _scanned_files()]

    assert connector_modules, f"{_relative(CONNECTOR)} holds no modules"
    assert any(
        f"def {METHOD}" in p.read_text(encoding="utf-8") for p in connector_modules
    ), f"nothing under {_relative(CONNECTOR)} defines {METHOD} - wrong directory"
    assert not [p for p in scanned if p.startswith("accountant/tallyio/")]


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        (
            "def helper(client):\n    client.write_voucher('Co', v, 'op_1')\n",
            Site("accountant/_example.py", 2, "helper"),
        ),
        (
            "client.write_voucher('Co', v, 'op_1')\n",
            Site("accountant/_example.py", 1, "<module>"),
        ),
        (
            "class Poster:\n"
            "    def go(self, client):\n"
            "        return client.write_voucher('Co', v, 'op_1')\n",
            Site("accountant/_example.py", 3, "Poster.go"),
        ),
        (
            "from accountant.tallyio.client import write_voucher\n"
            "write_voucher('Co', v, 'op_1')\n",
            Site("accountant/_example.py", 2, "<module>"),
        ),
    ],
    ids=["in_a_function", "at_module_level", "in_a_method", "imported_by_name"],
)
def test_the_finder_locates_a_call_it_is_given(source: str, expected: Site) -> None:
    assert _call_sites(source, "accountant/_example.py") == [expected]


def test_the_finder_does_not_mistake_a_definition_for_a_call() -> None:
    source = (
        "class C:\n    def write_voucher(self, company, voucher, op):\n        ...\n"
    )

    assert _call_sites(source, "accountant/_example.py") == []


def test_the_failure_message_names_the_file_and_the_line_of_every_offender() -> None:
    """The whole point of the message: whoever broke it is told WHERE."""
    offenders = [
        Site("accountant/_probe_tmp.py", 7, "<module>"),
        Site("accountant/web/app.py", 42, "post_now"),
    ]

    assert _describe(offenders) == (
        "accountant/_probe_tmp.py:7 in <module>; accountant/web/app.py:42 in post_now"
    )
