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

THE THREE GUARDS ADDED FOR PHASE 7 EXIT 7.3, AND THE HOLE THEY CLOSE
---------------------------------------------------------------------
The two guards above were written against the case where somebody imports a
reader library or writes one out longhand. Both left the same hole open, and it
is the cheapest way to build a reader that exists:

    import subprocess
    subprocess.run(["tesseract", path, "out"])

Stdlib only. No banned import. No `pixel`, no `deskew`, no `bbox`. It would
have passed. So the third guard says what this package may TOUCH, not only what
it may name: no other program, no socket, no file. An adapter needs none of the
three — its transport is injected, which is the whole reason it is an adapter —
and a reader needs at least one of them, because the model or the service has
to live somewhere.

The fourth reads `pyproject.toml`. `dependencies = []` is the load-bearing fact
the import guard rests on, and until now nothing checked it: adding one line
there would have widened the allowlist without touching this file.

The fifth looks at what the package SHIPS. A trained model is not Python and
would not be parsed by any scan above. `accountant/extract/` is source and
nothing else.

WHAT THIS FILE DOES NOT PROVE
-----------------------------
That the third-party backend behind the adapter is any good, or that it exists.
No backend is wired up. This proves only that when one is, the reading happens
on its side of the boundary.

That a reader cannot be reached through the INJECTED transport. It can, and
that is the design: `accountant/extract/service.py` takes a `ServiceCall` and a
deployment supplies one that talks to whoever it pays. The rule is that the
reading is not OURS, not that reading never happens.
"""

from __future__ import annotations

import ast
import pathlib
import sys
import tomllib
from typing import cast

import pytest

REPO = pathlib.Path(__file__).resolve().parent.parent
PACKAGE = REPO / "accountant" / "extract"
PYPROJECT = REPO / "pyproject.toml"

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
    # Added 2026-08-10, same rationale as the list above: the general
    # vocabulary of a reader somebody writes by hand, not product names.
    "dpi",
    "crop",
    "histogram",
    "morpholog",
    "template_match",
    "connected_components",
    "field_detect",
    "line_detection",
    "table_detection",
)

#: Stdlib modules that let this package reach something outside itself.
#:
#: `sys.stdlib_module_names` says `subprocess` is stdlib, so the import guard
#: above waves it through — and `subprocess.run(["tesseract", ...])` is a
#: reader with no third-party import and none of the words above in it. An
#: ADAPTER needs none of these: its transport is injected. A READER needs at
#: least one, because the model or the service has to be somewhere.
REACHES_OUTSIDE = (
    "subprocess",
    "socket",
    "socketserver",
    "ctypes",
    "urllib",
    "http",
    "ftplib",
    "smtplib",
    "multiprocessing",
    "shutil",
    "tempfile",
    "importlib",
)

#: Builtins that open a file or run code that was not reviewed.
FORBIDDEN_CALLS = ("open", "exec", "eval", "compile", "__import__")

#: Anything in the package that is not Python source. A trained model is not
#: parsed by any scan in this file, so it has to be caught by its presence.
SOURCE_SUFFIX = ".py"


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


# =============================================================================
# WHAT THE PACKAGE MAY TOUCH — the hole `subprocess` left open
# =============================================================================


def calls_by_name(tree: ast.Module) -> set[str]:
    """Bare-name calls: `open(...)`, `exec(...)`. Not `self.open(...)`."""
    return {
        node.func.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }


def test_the_extraction_package_starts_no_other_program_and_opens_no_socket():
    """`import subprocess` is stdlib, so the import guard allows it. Shelling
    out to `tesseract` is the cheapest reader anybody can build, and it would
    carry no third-party import and none of the words in READER_WORDS."""
    offenders = {
        path.name: sorted(imported_roots(tree) & set(REACHES_OUTSIDE))
        for path, tree in modules()
        if imported_roots(tree) & set(REACHES_OUTSIDE)
    }

    assert offenders == {}, (
        "accountant/extract/ is an adapter: its transport is INJECTED, so it "
        "needs nothing that reaches another program, a socket or the network. "
        f"Found: {offenders}"
    )


def test_the_extraction_package_opens_no_file_and_runs_no_unreviewed_code():
    """A reader needs its model on disk. An adapter needs no disk at all."""
    offenders = {
        path.name: sorted(calls_by_name(tree) & set(FORBIDDEN_CALLS))
        for path, tree in modules()
        if calls_by_name(tree) & set(FORBIDDEN_CALLS)
    }

    assert offenders == {}, (
        "accountant/extract/ reads no file and compiles no code. A model or a "
        f"trained weight has to live somewhere, and it is not here: {offenders}"
    )


@pytest.mark.parametrize(
    ("label", "source"),
    [
        ("shelling out", "import subprocess\n"),
        ("a raw socket", "import socket\n"),
        ("an HTTP call of its own", "from urllib.request import urlopen\n"),
        ("a native library", "import ctypes\n"),
        ("a scratch directory for page images", "import tempfile\n"),
    ],
)
def test_the_reach_guard_actually_catches_a_reader_that_imports_only_stdlib(
    label: str, source: str
):
    assert imported_roots(ast.parse(source)) & set(REACHES_OUTSIDE), (
        f"{label} slipped past the reach guard, so the guard proves nothing"
    )


@pytest.mark.parametrize(
    ("label", "source"),
    [
        (
            "reading a model off disk",
            "def load():\n    return open('model.bin', 'rb')\n",
        ),
        ("compiling something", "def go(src):\n    return exec(src)\n"),
        ("importing by string", "def go(n):\n    return __import__(n)\n"),
    ],
)
def test_the_file_and_code_guard_actually_catches_what_it_is_aimed_at(
    label: str, source: str
):
    assert calls_by_name(ast.parse(source)) & set(FORBIDDEN_CALLS), (
        f"{label} slipped past the call guard, so the guard proves nothing"
    )


def test_the_reach_guard_leaves_the_stdlib_the_adapter_actually_uses_alone():
    """The disconfirming case. A guard that forbids `hashlib` and `datetime`
    would be quietly deleted the first time somebody needed one."""
    everyday = ast.parse(
        "import datetime\nimport hashlib\nimport re\nfrom decimal import Decimal\n"
    )

    assert imported_roots(everyday) & set(REACHES_OUTSIDE) == set()


# =============================================================================
# WHAT THE PROJECT DECLARES — the fact the import guard rests on
# =============================================================================


def declared() -> dict[str, object]:
    return tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))


def _table(value: object, what: str) -> dict[str, object]:
    assert isinstance(value, dict), f"{what} is not a table in pyproject.toml"
    return cast("dict[str, object]", value)


def _strings(value: object, what: str) -> list[str]:
    assert isinstance(value, list), f"{what} is not a list in pyproject.toml"
    return [str(item) for item in cast("list[object]", value)]


def runtime_dependencies(table: dict[str, object]) -> list[str]:
    project = _table(table.get("project"), "[project]")
    return _strings(project.get("dependencies"), "project.dependencies")


def dependency_names(table: dict[str, object]) -> list[str]:
    """Every dependency string the project declares, in every group."""
    project = _table(table.get("project"), "[project]")
    out = runtime_dependencies(table)
    optional = _table(
        project.get("optional-dependencies", {}), "[project.optional-dependencies]"
    )
    for name, group in optional.items():
        out.extend(_strings(group, f"optional-dependencies.{name}"))
    return out


def test_the_project_declares_no_runtime_dependency_at_all():
    """`dependencies = []` is the whole reason the import allowlist works.

    Reading a document — any way, with any library — needs a dependency. The
    import guard says "stdlib and accountant only" and derives its authority
    from this line. Nothing checked the line itself until now, so a reader
    could have arrived by widening the project rather than by widening the
    guard.
    """
    runtime = runtime_dependencies(declared())

    assert runtime == [], (
        "accountant-dad declares no runtime dependency. A new one is either a "
        "reader or the door a reader walks through; either way it is an owner "
        f"decision and not a test change. Found: {runtime!r}"
    )


def test_no_dependency_in_any_group_names_a_document_reader():
    """Second net, over the dev tools too. The first net is the line above."""
    offenders = [
        name
        for name in dependency_names(declared())
        if any(word in name.lower() for word in READER_WORDS)
    ]

    assert offenders == [], f"a declared dependency names reader work: {offenders}"


def test_the_dependency_guard_actually_catches_a_reader_being_declared():
    planted = tomllib.loads(
        '[project]\ndependencies = ["pytesseract>=0.3"]\n'
        '[project.optional-dependencies]\ndev = ["opencv-python"]\n'
    )
    names = dependency_names(planted)

    assert names == ["pytesseract>=0.3", "opencv-python"]
    assert [n for n in names if any(w in n.lower() for w in READER_WORDS)] == [
        "pytesseract>=0.3"
    ]
    assert runtime_dependencies(planted) != []


# =============================================================================
# WHAT THE PACKAGE SHIPS — a model is not Python and no scan above would see it
# =============================================================================


def shipped() -> list[pathlib.Path]:
    return [
        p
        for p in sorted(PACKAGE.rglob("*"))
        if p.is_file() and "__pycache__" not in p.parts
    ]


def test_the_extraction_package_ships_source_and_nothing_else():
    """A trained model, a font, a page image or a `.traineddata` file is not
    parsed by anything above, so its mere presence has to be the failure."""
    strangers = [
        p.relative_to(PACKAGE).as_posix()
        for p in shipped()
        if p.suffix != SOURCE_SUFFIX
    ]

    assert strangers == [], (
        "accountant/extract/ holds only Python source. A model, a weight file "
        f"or a sample page belongs to the third-party backend: {strangers}"
    )


def test_the_shipped_file_scan_is_looking_at_a_package_that_has_files_in_it():
    """A scan over nothing passes the assertion above for the wrong reason."""
    names = {p.name for p in shipped()}

    assert {"adapter.py", "service.py", "registry.py"} <= names
    assert len(names) >= 4
