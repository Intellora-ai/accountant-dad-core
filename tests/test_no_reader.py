"""Child 15 criterion 6 — `accountant/extract/` contains no reader.

The frozen plan, verbatim:

    The rule that defines this child: we write an adapter, never a reader.
    Not one line of OCR, layout analysis or field-detection code.
    ...
    6. A test asserts this package contains no OCR, image-processing or
       layout-analysis code. The rule is enforced, not trusted.

Criterion 6 has been unenforced since the package was written. `docs/EPIC.md`
and the adapter's own docstring both state the rule; a docstring is a promise,
and this file is the mechanism. Until now the only thing stopping a reader
appearing in here was that nobody had tried.

WHY AN ALLOWLIST AND NOT A BLOCKLIST
------------------------------------
The obvious guard is a list of banned libraries — pytesseract, cv2, Pillow,
paddleocr. It is the wrong shape: the ban is defeated by the next library
nobody has heard of, and the list needs a maintainer forever.

The package's actual rule is stronger and needs no maintenance. `pyproject.toml`
declares `dependencies = []`. Reading a document — any way, with any library —
requires a dependency. So: **this package may import stdlib modules and
`accountant.*`, and nothing else.** Every reader is excluded by construction,
including ones that do not exist yet.

The second guard covers a reader written by hand with no import at all. It
scans IDENTIFIERS in the parsed tree — never comments, never docstrings, never
string literals — so this file's own prose, and the adapter's, cannot trip it.

WHAT THIS FILE DOES NOT PROVE
-----------------------------
That the third-party backend behind the adapter is any good, or that it exists.
No backend is wired up. This proves only that when one is, the reading happens
on its side of the boundary.
"""

from __future__ import annotations

import ast
import pathlib
import sys

import pytest

PACKAGE = pathlib.Path(__file__).resolve().parent.parent / "accountant" / "extract"

#: Words that name the work of reading a document rather than adapting one.
#: Matched against identifiers only. Deliberately includes the general
#: vocabulary (`pixel`, `deskew`, `bbox`) and not only product names, because a
#: hand-rolled reader would import nothing at all.
READER_WORDS = (
    "ocr",
    "tesseract",
    "pixel",
    "grayscale",
    "greyscale",
    "binarize",
    "binarise",
    "threshold",
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


def modules() -> list[tuple[pathlib.Path, ast.Module]]:
    files = sorted(PACKAGE.rglob("*.py"))
    assert files, f"nothing to scan at {PACKAGE}; the guard would pass vacuously"
    return [(f, ast.parse(f.read_text())) for f in files]


def imported_roots(tree: ast.Module) -> set[str]:
    """Top-level package name of every import, however it is written."""
    roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots.update(a.name.split(".")[0] for a in node.names)
        # `level > 0` is a relative import, which is inside this package.
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            roots.add(node.module.split(".")[0])
    return roots


def identifiers(tree: ast.Module) -> set[str]:
    """Every name the CODE uses. No comments, no docstrings, no literals."""
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            names.add(node.id)
        elif isinstance(node, ast.Attribute):
            names.add(node.attr)
        elif isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
            names.add(node.name)
            if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                names.update(a.arg for a in node.args.args)
                names.update(a.arg for a in node.args.kwonlyargs)
        elif isinstance(node, ast.alias):
            names.add((node.asname or node.name).split(".")[0])
    return names


def test_the_extraction_package_imports_nothing_a_reader_would_need():
    """stdlib and `accountant` only. Every reader needs more than that.

    `sys.stdlib_module_names` is Python's own answer to "is this stdlib",
    which is why this needs no hand-kept list of allowed modules either.
    """
    allowed = set(sys.stdlib_module_names) | {"accountant", "__future__"}
    offenders = {
        path.name: sorted(imported_roots(tree) - allowed)
        for path, tree in modules()
        if imported_roots(tree) - allowed
    }
    assert offenders == {}, (
        "accountant/extract/ is an adapter, never a reader, and reading a "
        f"document needs a dependency. Third-party imports found: {offenders}"
    )


def test_no_module_in_the_extraction_package_names_the_work_of_reading():
    """A reader written by hand imports nothing. This is the guard for that one."""
    offenders: dict[str, list[str]] = {}
    for path, tree in modules():
        hits = sorted(
            name
            for name in identifiers(tree)
            if any(word in name.lower() for word in READER_WORDS)
        )
        if hits:
            offenders[path.name] = hits
    assert offenders == {}, (
        "these identifiers name OCR, image-processing or layout-analysis work, "
        f"which belongs to the third-party backend and not to us: {offenders}"
    )


# ---- the guard has to be able to fail, or it proves nothing -----------------


@pytest.mark.parametrize(
    ("label", "source"),
    [
        ("an OCR library", "import pytesseract\n"),
        ("an imaging library", "from PIL import Image\n"),
        ("a PDF layout library", "import pdfplumber as p\n"),
        ("a numeric library, for pixels", "import numpy\n"),
    ],
)
def test_the_import_guard_actually_catches_a_reader(label: str, source: str):
    allowed = set(sys.stdlib_module_names) | {"accountant", "__future__"}
    assert imported_roots(ast.parse(source)) - allowed, (
        f"{label} slipped past the import guard, so the guard proves nothing"
    )


@pytest.mark.parametrize(
    ("label", "source"),
    [
        ("a function", "def deskew(page):\n    return page\n"),
        ("a class", "class GlyphSegmenter:\n    pass\n"),
        ("an argument", "def read(pixel_rows):\n    return pixel_rows\n"),
        ("a method call", "def f(x):\n    return x.binarize()\n"),
    ],
)
def test_the_identifier_guard_actually_catches_a_hand_rolled_reader(
    label: str, source: str
):
    hits = [
        n
        for n in identifiers(ast.parse(source))
        if any(w in n.lower() for w in READER_WORDS)
    ]
    assert hits, f"{label} slipped past the identifier guard"


def test_the_identifier_guard_does_not_fire_on_prose_about_readers():
    """The disconfirming case. This file and the adapter both discuss OCR.

    A guard that reads comments would flag the very docstring that states the
    rule, and the usual fix for that is to weaken the guard. Scanning the
    parsed tree makes the question not arise.
    """
    prose = '''
"""We never write OCR. No tesseract, no pixel work, no deskew, no layout."""
# also no bbox, no contour, no binarize
NOTE = "grayscale and threshold belong to the backend"
def extract(data, mime):
    return data
'''
    hits = [
        n
        for n in identifiers(ast.parse(prose))
        if any(w in n.lower() for w in READER_WORDS)
    ]
    assert hits == [], f"the guard read prose as code: {hits}"


def test_the_scan_covers_every_module_actually_in_the_package():
    """A guard that silently scans nothing passes for the wrong reason."""
    scanned = {path.name for path, _ in modules()}
    on_disk = {p.name for p in PACKAGE.rglob("*.py")}
    assert scanned == on_disk
    assert "adapter.py" in scanned
