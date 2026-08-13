"""The contract pages in `docs/interfaces/` are checked against their modules.

WHY THIS FILE EXISTS
--------------------
`docs/interfaces/decision.md` said "Six hard rules" while
`accountant/cage/decision.py` said "EIGHT HARD RULES" and listed eight. Both
were prose, nothing compared them, and a contract page that miscounts the rules
is worse than no contract page because somebody will trust it.

Prose cannot be trusted to agree with prose. Every assertion here is a quantity
that must be equal - the same shape as `conservation.py`: the count in the
module, the count in the page's own heading, and the number of rows in the
page's own table are three statements about one fact, and a conservation law
needs no reviewer.
"""

from __future__ import annotations

import ast
import pathlib
import re

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
PAGES = ROOT / "docs" / "interfaces"
DECISION_PY = ROOT / "accountant" / "cage" / "decision.py"
DECISION_MD = PAGES / "decision.md"

#: The heading both the module and its page carry, so drift in either is caught.
_RULE_HEADING = re.compile(r"(\w+) hard rules, each of which always blocks", re.I)

_NUMBER_WORDS = {
    "three": 3, "four": 4, "five": 5, "six": 6,
    "seven": 7, "eight": 8, "nine": 9, "ten": 10,
}  # fmt: skip


def _rules_claimed(text: str) -> int:
    """The count written in words in the "N hard rules" heading."""
    found = _RULE_HEADING.search(text)
    assert found, "no 'N hard rules, each of which always blocks' heading found"
    word = found.group(1).lower()
    assert word in _NUMBER_WORDS, f"unhandled number word {word!r}"
    return _NUMBER_WORDS[word]


def _table_rows_after(text: str, heading: re.Pattern[str]) -> int:
    """Body rows of the first markdown table below a heading - no header, no rule."""
    found = heading.search(text)
    assert found
    rows = 0
    seen_table = False
    for line in text[found.end() :].splitlines():
        if line.startswith("|"):
            seen_table = True
            rows += 1
        elif seen_table and not line.strip():
            break
    return rows - 2  # the header row and the |---| rule


# ---- the mechanical count of what actually refuses a bill -------------------


def _reason_functions(tree: ast.Module) -> tuple[ast.FunctionDef, ...]:
    """`_blocking` and every helper it extends its reason list from.

    DERIVED from the source, never typed here: a helper added to `_blocking`
    is counted automatically rather than being exempt by omission - the same
    failure shape as the empty-set AST guard `wall.py` shipped once.
    """
    funcs = {n.name: n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)}
    names = ["_blocking"]
    for node in ast.walk(funcs["_blocking"]):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "extend"
            and node.args
            and isinstance(node.args[0], ast.Call)
            and isinstance(node.args[0].func, ast.Name)
        ):
            names.append(node.args[0].func.id)
    return tuple(funcs[n] for n in names)


def _arms(expr: ast.expr) -> int:
    """One branch, plus one more for each conditional arm inside it.

    The two arms of `_period_closed(seen) if period_open is False else
    _PERIOD_UNKNOWN` are two different facts - "the books are shut" and "nobody
    looked" - carrying two different sentences, so they are two branches.
    """
    return 1 + sum(1 for n in ast.walk(expr) if isinstance(n, ast.IfExp))


def _sites_in(fn: ast.FunctionDef) -> int:
    """Every site in one function that puts a refusal sentence into the list."""
    total = 0
    for node in ast.walk(fn):
        if isinstance(node, ast.Return) and isinstance(
            node.value, ast.List | ast.Tuple
        ):
            total += _arms(node.value) if node.value.elts else 0
        elif (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "append"
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "reasons"
        ):
            total += _arms(node.args[0])
    return total


def _decide_own_blocks(tree: ast.Module) -> int:
    """`return _spoken(Action.BLOCK, ...)` in `decide` that carries a NEW sentence.

    The first one re-emits what `_blocking` already produced, so counting it
    would count every one of those branches twice.
    """
    decide = next(
        n
        for n in ast.walk(tree)
        if isinstance(n, ast.FunctionDef) and n.name == "decide"
    )
    return sum(
        1
        for n in ast.walk(decide)
        if isinstance(n, ast.Return)
        and isinstance(n.value, ast.Call)
        and isinstance(n.value.func, ast.Name)
        and n.value.func.id == "_spoken"
        and ast.unparse(n.value.args[0]) == "Action.BLOCK"
        and "blocking" not in ast.unparse(n.value.args[1])
    )


def _block_branches() -> int:
    tree = ast.parse(DECISION_PY.read_text())
    return sum(_sites_in(fn) for fn in _reason_functions(tree)) + _decide_own_blocks(
        tree
    )


# ---- what a module actually depends on at run time --------------------------


def _runtime_first_party(
    module: pathlib.Path,
) -> tuple[tuple[str, frozenset[str]], ...]:
    """Every `accountant.*` import that costs something when the module loads.

    Imports under `if TYPE_CHECKING:` are excluded, because they are not a
    run-time dependency and every page that has one says so.

    Each import is paired with the words a page could reasonably use to name it:
    the last component of the dotted path and the names imported from it. So
    `from accountant.cage import conservation` is named by "conservation", which
    is what a reader calls it, and not only by "cage".
    """
    tree = ast.parse(module.read_text())
    deferred = {
        n
        for branch in ast.walk(tree)
        if isinstance(branch, ast.If) and "TYPE_CHECKING" in ast.unparse(branch.test)
        for n in ast.walk(branch)
    }
    found: list[tuple[str, frozenset[str]]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.ImportFrom) or node in deferred:
            continue
        dotted = node.module or ""
        if not dotted.startswith("accountant"):
            continue
        names = {a.name for a in node.names} | {dotted.rsplit(".", 1)[-1]}
        found.append((dotted, frozenset(names)))
    return tuple(found)


# ---- the tests ---------------------------------------------------------------


def test_the_page_and_the_module_count_the_hard_rules_the_same() -> None:
    """Three statements about one fact, and they have to agree.

    This is the defect that started this file: the module heading said EIGHT and
    listed eight, the page heading said Six and its table had six rows. The page
    was internally consistent, which is exactly why nobody noticed.
    """
    in_code = _rules_claimed(DECISION_PY.read_text())
    page = DECISION_MD.read_text()
    assert _rules_claimed(page) == in_code
    assert _table_rows_after(page, _RULE_HEADING) == in_code


def test_the_page_reports_the_measured_count_of_everything_that_refuses() -> None:
    """The named hard rules are not the whole set, and the page has to say so.

    A reader who takes the hard-rule table for the complete list of refusals
    will be surprised by two thirds of them.
    """
    # Whitespace-tolerant: a contract test that fails when a paragraph is
    # re-wrapped teaches people to stop re-wrapping paragraphs.
    stated = re.search(
        r"(\d+)\s+distinct\s+block-producing\s+branches", DECISION_MD.read_text()
    )
    assert stated, "the page must state the measured branch count"
    assert int(stated.group(1)) == _block_branches()


def test_the_control_the_scanner_finds_the_helpers_and_not_an_empty_set() -> None:
    """The guard against the guard: a scan over nothing passes and proves nothing.

    This build already shipped one AST scan that asserted over an empty set
    (`wall.py`, defect J1's neighbour). A count of zero here would make the test
    above pass by measuring nothing at all.
    """
    tree = ast.parse(DECISION_PY.read_text())
    names = {fn.name for fn in _reason_functions(tree)}
    assert "_blocking" in names
    assert {"_world_blocks", "_budget_blocks", "_account_blocks"} <= names
    assert _decide_own_blocks(tree) > 0
    assert _block_branches() > len(names)


@pytest.mark.parametrize("page", sorted(p.name for p in PAGES.glob("*.md")))
def test_a_page_claiming_no_dependencies_has_a_module_with_none(page: str) -> None:
    """ "Stdlib only" is a claim about imports, so it is checked against imports.

    `wall.md` said "**None.** Stdlib only." while `wall.py` imports
    `accountant.money` - an exception `conservation.md` documents at length for
    the identical import and `wall.md` never mentioned.
    """
    text = (PAGES / page).read_text()
    if not re.search(r"^\*\*None\.\*\* Stdlib only\.", text, re.M):
        pytest.skip(f"{page} claims no such thing")
    module = ROOT / "accountant" / "cage" / f"{pathlib.Path(page).stem}.py"
    imports = _runtime_first_party(module)
    assert not imports, f"{page} claims stdlib only, {module.name} imports {imports}"


@pytest.mark.parametrize("page", sorted(p.name for p in PAGES.glob("*.md")))
def test_a_depends_on_section_names_every_run_time_import(page: str) -> None:
    """A dependency budget nobody counts is not a budget.

    `gate.md` claimed four dependencies "against a limit of five" and did not
    mention `accountant.extract.adapter`, which it imports at run time. Five
    against a limit of five is a different fact from four: it says the next
    import has to displace something.
    """
    text = (PAGES / page).read_text()
    # Both headings, because the pages use both and the one that was wrong used
    # the one a narrower pattern would have skipped.
    section = re.search(
        r"^## (?:Depends on|Dependencies)\n(.+?)(?=\n## |\Z)", text, re.M | re.S
    )
    if not section:
        pytest.skip(f"{page} has no dependency section")
    module = ROOT / "accountant" / "cage" / f"{pathlib.Path(page).stem}.py"
    for dotted, named in _runtime_first_party(module):
        assert any(n in section.group(1) for n in named), (
            f"{page} never names its run-time import of {dotted}"
        )
