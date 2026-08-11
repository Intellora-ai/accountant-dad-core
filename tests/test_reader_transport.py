"""`accountant/reader/` is a courier. It must not quietly become a reader.

WHY THIS FILE EXISTS
--------------------
`tests/test_no_reader.py` guards `accountant/extract/` against three things: a
third-party import, a word from the vocabulary of somebody hand-writing an OCR,
and anything that reaches another program or a socket.

The vendor selected on 2026-08-11 needed a socket, so the transport moved out of
that package.
Moving code out of a guarded directory is the oldest way to lose a guard, and
this file is the reason that did not happen here: the two rules that still apply
were carried across, and the count of guarded packages went from one to two.

The one rule that could NOT be carried is the socket ban — opening a socket is
the entire job of this package. So this file states precisely which module is
permitted to do it, and refuses a second one appearing without anybody noticing.
"""

from __future__ import annotations

import ast
import pathlib

REPO = pathlib.Path(__file__).resolve().parent.parent
PACKAGE = REPO / "accountant" / "reader"

#: The same list `tests/test_no_reader.py` uses, and for the same reason: these
#: are the words somebody writes when they start building a reader by hand, not
#: the product names of services that already exist.
READER_WORDS = (
    "ocr",
    "tesseract",
    "pixel",
    "grayscale",
    "greyscale",
    "binarize",
    "binarise",
    "deskew",
    "despeckle",
    "denoise",
    "dilate",
    "erode",
    "contour",
    "bbox",
    "bounding_box",
    "glyph",
    "segmentation",
    "layout_analysis",
    "word_boxes",
    "text_regions",
)

#: Reaching the network is this package's job. Reaching ANOTHER PROGRAM is not,
#: and `subprocess` is how the cheapest hand-rolled reader gets built —
#: shelling out to `tesseract` carries no third-party import and none of the
#: words above.
STILL_FORBIDDEN = (
    "subprocess",
    "ctypes",
    "multiprocessing",
    "importlib",
)

#: The only module permitted to open a socket, named so a second one cannot
#: arrive unremarked. Every other file here must stay pure.
MAY_REACH_THE_NETWORK = frozenset({"azure.py"})

NETWORKING = ("urllib", "socket", "http", "ftplib", "smtplib", "socketserver")


def modules() -> list[tuple[pathlib.Path, ast.Module]]:
    files = sorted(PACKAGE.rglob("*.py"))
    assert files, f"nothing to scan at {PACKAGE}; the guard would pass vacuously"
    return [(f, ast.parse(f.read_text(encoding="utf-8"))) for f in files]


def imported_roots(tree: ast.Module) -> set[str]:
    roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            roots.add(node.module.split(".")[0])
    return roots


def code_without_comments_or_docstrings(tree: ast.Module) -> str:
    """The source with prose removed, so the word ban reads CODE.

    This repository has twice had a check match a word inside its own
    explanatory comment and pass while measuring nothing. A docstring that
    explains why there is no OCR here must not be the thing that fails an OCR
    ban.
    """
    stripped = ast.parse(ast.unparse(tree))
    for node in ast.walk(stripped):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            node.value = ""
    return ast.unparse(stripped)


def test_the_courier_package_contains_no_reader() -> None:
    offenders = {
        path.name: sorted(
            word
            for word in READER_WORDS
            if word in code_without_comments_or_docstrings(tree).lower()
        )
        for path, tree in modules()
    }
    found = {name: words for name, words in offenders.items() if words}

    assert found == {}, (
        "accountant/reader/ carries a document to somebody else's service. It "
        f"does not read one, and these words say otherwise: {found}"
    )


def test_the_courier_package_starts_no_other_program() -> None:
    """The socket ban could not come along; this one could.

    Shelling out to `tesseract` is the cheapest reader anybody can build, and it
    would carry no third-party import and none of the words above.
    """
    offenders = {
        path.name: sorted(imported_roots(tree) & set(STILL_FORBIDDEN))
        for path, tree in modules()
        if imported_roots(tree) & set(STILL_FORBIDDEN)
    }

    assert offenders == {}, (
        "accountant/reader/ may open a socket, which is its job. Starting "
        f"another program is not. Found: {offenders}"
    )


def test_only_the_named_module_reaches_the_network() -> None:
    """One socket, in one file, named here.

    A second networking module appearing in this package is not necessarily
    wrong — it is a thing somebody should have to write down, the way
    `registry._READY` makes a new backend a name somebody typed.
    """
    reaching = {
        path.name for path, tree in modules() if imported_roots(tree) & set(NETWORKING)
    }

    assert reaching == MAY_REACH_THE_NETWORK, (
        "the set of modules here that reach the network changed. Expected "
        f"{sorted(MAY_REACH_THE_NETWORK)}, found {sorted(reaching)}"
    )


def test_the_courier_takes_nothing_from_the_extraction_package() -> None:
    """The boundary `tests/test_adapter_contract.py` measures, asserted from
    this side as well.

    Stated twice on purpose. The contract test scans every module in
    `accountant/` and would catch this too, but it reports the whole tree at
    once; a person reading THIS package should find the rule written where the
    temptation is, because the temptation here is specific and strong: the
    reason sentences a person reads live in `accountant/extract/service.py` and
    importing them would be one line.
    """
    offenders = {
        path.name: sorted(
            node.module
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom)
            and node.module
            and node.module.startswith("accountant.extract")
        )
        for path, tree in modules()
    }
    found = {name: mods for name, mods in offenders.items() if mods}

    assert found == {}, (
        "accountant/reader/ must not import extraction internals; it reports a "
        f"short `kind` and the other side turns it into words. Found: {found}"
    )
