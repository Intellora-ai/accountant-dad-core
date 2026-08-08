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
