"""Child 15 criterion 6 — only the readers `D-30` approved may live here.

WHAT THIS FILE USED TO ASSERT
-----------------------------
Until 2026-08-13 this file asserted, repo-wide, that **no document reader
existed**: nothing in `accountant/extract/` could import a reader library, no
module in there could so much as name the work of reading, and `pyproject.toml`
had to declare `dependencies = []`. That was the true and correct answer when
the file was written in Phase 7. It is no longer the answer.

WHAT IT ASSERTS NOW, AND WHY IT CHANGED
---------------------------------------
It asserts an **allow-list**. Two reader modules are approved by name, three
runtime dependencies are approved by name, and **everything outside those two
lists is exactly as forbidden as it was before**.

The authority is `D-30` in [`docs/DECISIONS.md`](../docs/DECISIONS.md), owner
decision dated **2026-08-13**, which cleared `pypdf` plus the free OCR stack as
runtime dependencies and closes the eight-item register `D-04` requires.

Not one assertion was deleted to get here. Each was rewritten to carry an
exception that it names, because a ban that has to be deleted the first time
reality changes protects nothing afterwards. In a world where readers exist,
"these two files, these three packages, and nothing else" is the stronger guard
— it keeps working, and it keeps working against readers nobody has thought of.

**Widening either list is an owner decision and still not a test change.**

THE FIVE GUARDS, AND WHAT EACH ONE'S EXCEPTION COSTS
-----------------------------------------------------
1. **Imports.** stdlib and `accountant.*` everywhere, plus — for an approved
   reader only — the libraries listed for *that module*. `freeocr.py` importing
   `pypdf`, or `textlayer.py` importing `pytesseract`, fails. One shared pool
   would let either module quietly become the other.

2. **Identifiers.** No module may name the work of reading — `deskew`, `bbox`,
   `tesseract`, `binarize` — except the approved two. Scanned over the parsed
   tree, never comments, never docstrings, never string literals, so this file's
   own prose cannot trip it.

3. **Dependencies.** `project.dependencies` must equal
   `APPROVED_RUNTIME_DEPENDENCIES` **as a set**. Not "contains". Not "is a
   subset". A fourth package appearing is a failure, and so is one of the three
   going missing.

4. **Reach.** No other program, no socket, no network, no file open, no `eval`
   and no `exec` — with exactly **one** exception: `subprocess`, in
   `freeocr.py`, and nowhere else. Tesseract is a binary; the module that drives
   it has to bound the wait and catch `subprocess.TimeoutExpired` by name.
   `socket`, `eval`, `exec`, `compile`, `__import__` and arbitrary `open()`
   stay forbidden **in the readers too** — `textlayer.py` is handed bytes and
   parses them in memory, so no file-read exception was needed and none was
   granted.

5. **What ships.** Source and nothing else. A trained model is not Python and
   would be parsed by none of the scans above, so its presence is the failure.

WHAT THIS FILE DOES NOT PROVE
-----------------------------
That the approved readers are any good, or that they exist. `freeocr.py` was
not on disk when this was written. This proves only which doors are open.

That a reader cannot be reached through the INJECTED transport. It can, and
that is the design: `accountant/extract/service.py` takes a `ServiceCall` and a
deployment supplies one. The rule was never that reading never happens.

That the approved libraries are safe. `pypdf` parses untrusted PDFs in-process;
that risk is named and mitigated in `D-30`, not here.
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

#: The modules in `accountant/extract/` that are allowed to be readers.
#: Authority: `D-30` in docs/DECISIONS.md, owner decision dated 2026-08-13.
#: A module not named here is under the original blanket ban, unchanged.
APPROVED_READER_MODULES = ("textlayer.py", "freeocr.py")

#: The runtime dependencies this project is allowed to declare — the exact set,
#: matched as a set and not as a floor.
#: Authority: `D-30` in docs/DECISIONS.md, owner decision dated 2026-08-13.
APPROVED_RUNTIME_DEPENDENCIES = ("pypdf", "pytesseract", "Pillow")

#: Which approved reader may import which approved library, by IMPORT name.
#: Each reader gets only what it needs. A single shared pool would mean nothing
#: noticed if the PDF reader started doing OCR or the OCR reader started
#: parsing PDFs, which is the whole failure this split exists to catch.
READER_LIBRARIES: dict[str, frozenset[str]] = {
    "textlayer.py": frozenset({"pypdf"}),
    "freeocr.py": frozenset({"pytesseract", "PIL"}),
}

#: Import name -> the name it is installed under, where the two differ. Pillow
#: is declared as `Pillow` and imported as `PIL`; nothing else here differs.
DISTRIBUTION_OF = {"PIL": "Pillow"}

#: The one reach exception `D-30` grants, and the whole of it. Tesseract is a
#: binary, so the module that drives it may name `subprocess` — to bound the
#: wait and to catch `subprocess.TimeoutExpired`. Socket, `eval`, `exec` and
#: arbitrary file opens stay forbidden everywhere, readers included.
#:
#: MEASURED 2026-08-13: `freeocr.py` as landed imports
#: `PIL, accountant, collections, dataclasses, datetime, io, pytesseract,
#: typing` — and NOT `subprocess`. `pytesseract` owns the process launch, so
#: this exception is currently granted and unused. **If it is still unused when
#: `freeocr.py` next changes, delete it**; an exception nothing exercises is a
#: hole that costs nothing to keep and everything to forget about.
SUBPROCESS_ALLOWED_IN = "freeocr.py"

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
#: waves it through — and `subprocess.run(["tesseract", ...])` is a reader with
#: no third-party import at all. An ADAPTER needs none of these: its transport
#: is injected. A READER needs at least one, because the model or the service
#: has to be somewhere. `SUBPROCESS_ALLOWED_IN` is the single, named exception.
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

#: Builtins that open a file or run code that was not reviewed. No module has
#: an exception to this list, and that includes the two approved readers.
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


def calls_by_name(tree: ast.Module) -> set[str]:
    """Bare-name calls: `open(...)`, `exec(...)`. Not `self.open(...)`."""
    return {
        node.func.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }


# ---- the allow-list, applied ------------------------------------------------
#
# Every guard below and every control below goes through these four functions,
# so a control that passes is evidence about the guard and not about a second
# copy of the rule that happens to agree with it today.


def allowed_imports_for(module: str) -> set[str]:
    """Every top-level package `module` may import, by name."""
    allowed = set(sys.stdlib_module_names) | {"accountant", "__future__"}
    return allowed | set(READER_LIBRARIES.get(module, frozenset()))


def import_offenders(module: str, tree: ast.Module) -> list[str]:
    return sorted(imported_roots(tree) - allowed_imports_for(module))


def reach_offenders(module: str, tree: ast.Module) -> list[str]:
    forbidden = set(REACHES_OUTSIDE)
    if module == SUBPROCESS_ALLOWED_IN:
        forbidden -= {"subprocess"}
    return sorted(imported_roots(tree) & forbidden)


def reader_words_in(tree: ast.Module) -> list[str]:
    return sorted(
        name
        for name in identifiers(tree)
        if any(word in name.lower() for word in READER_WORDS)
    )


def test_no_module_outside_the_allow_list_imports_a_reader_library():
    """stdlib and `accountant` everywhere; a reader library only where D-30
    named it, and only the one that module needs.

    `sys.stdlib_module_names` is Python's own answer to "is this stdlib", which
    is why the stdlib half still needs no hand-kept list.
    """
    offenders = {
        path.name: import_offenders(path.name, tree)
        for path, tree in modules()
        if import_offenders(path.name, tree)
    }
    assert offenders == {}, (
        "accountant/extract/ is an adapter except in the modules D-30 approved, "
        f"and each of those may import only its own library. Found: {offenders}"
    )


def test_no_module_outside_the_allow_list_names_the_work_of_reading():
    """A reader written by hand imports nothing. This is the guard for that one.

    The approved two are exempt because naming the work IS their job; every
    other module in the package is under the original ban.
    """
    offenders = {
        path.name: reader_words_in(tree)
        for path, tree in modules()
        if path.name not in APPROVED_READER_MODULES and reader_words_in(tree)
    }
    assert offenders == {}, (
        "these identifiers name OCR, image-processing or layout-analysis work "
        "in a module D-30 did not approve as a reader, so it belongs behind "
        f"the boundary and not here: {offenders}"
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
    assert import_offenders("adapter.py", ast.parse(source)), (
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
    assert reader_words_in(ast.parse(source)), (
        f"{label} slipped past the identifier guard"
    )


def test_an_unapproved_module_is_still_caught_importing_and_naming_a_reader():
    """The hole an allow-list opens if nobody checks it: a new file with a
    plausible name doing exactly what the approved two are allowed to do.

    `sneaky_ocr.py` is not in `APPROVED_READER_MODULES`, so both guards must
    treat it the way they treated everything before 2026-08-13.
    """
    planted = ast.parse(
        "import pytesseract\n"
        "class TesseractReader:\n"
        "    def deskew(self, page):\n"
        "        return page\n"
    )

    assert import_offenders("sneaky_ocr.py", planted) == ["pytesseract"]
    assert "sneaky_ocr.py" not in APPROVED_READER_MODULES
    assert reader_words_in(planted) == ["TesseractReader", "deskew", "pytesseract"]


@pytest.mark.parametrize(
    ("module", "source", "library"),
    [
        ("freeocr.py", "import pypdf\n", "pypdf"),
        ("textlayer.py", "import pytesseract\n", "pytesseract"),
        ("textlayer.py", "from PIL import Image\n", "PIL"),
        ("freeocr.py", "import pdfplumber\n", "pdfplumber"),
    ],
)
def test_an_approved_reader_importing_a_library_that_is_not_its_own_is_caught(
    module: str, source: str, library: str
):
    """Being on the allow-list buys one library, not the whole approved set."""
    assert module in APPROVED_READER_MODULES
    assert library in import_offenders(module, ast.parse(source)), (
        f"{module} was allowed to import {library}, which is not its own"
    )


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
    assert reader_words_in(ast.parse(prose)) == [], "the guard read prose as code"


def test_the_scan_covers_every_module_actually_in_the_package():
    """A guard that silently scans nothing passes for the wrong reason."""
    scanned = {path.name for path, _ in modules()}
    on_disk = {p.name for p in PACKAGE.rglob("*.py")}
    assert scanned == on_disk
    assert "adapter.py" in scanned


# =============================================================================
# WHAT THE PACKAGE MAY TOUCH — the hole `subprocess` left open
# =============================================================================


def test_no_module_starts_another_program_or_opens_a_socket_unless_d30_said_so():
    """`import subprocess` is stdlib, so the import guard allows it. Shelling
    out to `tesseract` is the cheapest reader anybody can build, and it would
    carry no third-party import and none of the words in READER_WORDS.

    One module has an exception, and it is named. Every other module in the
    package — approved reader or not — is under the original ban.
    """
    offenders = {
        path.name: reach_offenders(path.name, tree)
        for path, tree in modules()
        if reach_offenders(path.name, tree)
    }

    assert offenders == {}, (
        "accountant/extract/ has an INJECTED transport, so nothing here needs "
        f"another program, a socket or the network. Found: {offenders}. The "
        f"one exception D-30 grants is subprocess in {SUBPROCESS_ALLOWED_IN}."
    )


def test_no_module_opens_a_file_or_runs_unreviewed_code_including_the_readers():
    """A reader needs its model on disk. Neither approved reader does: one is
    handed PDF bytes and parses them in memory, the other hands an image to a
    binary. So no file-read exception was needed and none was granted."""
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
    assert reach_offenders("adapter.py", ast.parse(source)), (
        f"{label} slipped past the reach guard, so the guard proves nothing"
    )


@pytest.mark.parametrize(
    ("label", "source"),
    [
        ("a raw socket", "import socket\n"),
        ("an HTTP call of its own", "from urllib.request import urlopen\n"),
        ("a scratch directory for page images", "import tempfile\n"),
    ],
)
def test_the_reach_exception_for_freeocr_is_subprocess_and_nothing_else(
    label: str, source: str
):
    """Being allowed to start Tesseract does not buy a network or a disk."""
    assert reach_offenders(SUBPROCESS_ALLOWED_IN, ast.parse(source)), (
        f"{label} slipped past the reach guard inside {SUBPROCESS_ALLOWED_IN}"
    )


def test_subprocess_is_allowed_in_exactly_one_module_and_no_other():
    """The exception is a single file name, and this is the pair that says so.

    `textlayer.py` is on the reader allow-list and still may not shell out;
    being approved as a reader and being approved to start a program are two
    different grants, and only one module has the second one.
    """
    shelling_out = ast.parse("import subprocess\n")

    assert reach_offenders(SUBPROCESS_ALLOWED_IN, shelling_out) == []
    assert reach_offenders("textlayer.py", shelling_out) == ["subprocess"]
    assert reach_offenders("adapter.py", shelling_out) == ["subprocess"]
    assert SUBPROCESS_ALLOWED_IN in APPROVED_READER_MODULES


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


@pytest.mark.parametrize(
    ("label", "source"),
    [
        ("a reader opening its own page file", "def f():\n    return open('p.png')\n"),
        ("a reader evaluating something", "def f(s):\n    return eval(s)\n"),
        ("a reader compiling something", "def f(s):\n    return compile(s, '', 'x')\n"),
    ],
)
def test_an_approved_reader_may_not_open_a_file_or_run_unreviewed_code(
    label: str, source: str
):
    """The call guard has no module dimension on purpose: D-30 granted no
    exception to it, so the approved two are covered by the same assertion as
    everything else. This is the control that proves it."""
    assert calls_by_name(ast.parse(source)) & set(FORBIDDEN_CALLS), (
        f"{label} slipped past the call guard, which the readers do not escape"
    )


def test_the_reach_guard_leaves_the_stdlib_the_adapter_actually_uses_alone():
    """The disconfirming case. A guard that forbids `hashlib` and `datetime`
    would be quietly deleted the first time somebody needed one."""
    everyday = ast.parse(
        "import datetime\nimport hashlib\nimport re\nfrom decimal import Decimal\n"
    )

    assert reach_offenders("adapter.py", everyday) == []


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


def package_name(requirement: str) -> str:
    """`pypdf>=5.0` -> `pypdf`. The name, with the version specifier removed."""
    for separator in ("[", "<", ">", "=", "!", "~", ";", " "):
        requirement = requirement.split(separator)[0]
    return requirement.strip()


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


def test_the_project_declares_exactly_the_dependencies_the_owner_approved():
    """The set, not a floor. `dependencies = []` was the whole reason the old
    import allowlist worked; `dependencies == {pypdf, pytesseract, Pillow}` is
    the whole reason this one does.

    Compared as a SET so that a fourth package is a failure and a missing third
    is a failure. "Contains the approved three" would let a reader arrive by
    widening the project rather than by widening this file, which is the exact
    route the old version of this test existed to close.
    """
    runtime = {package_name(item) for item in runtime_dependencies(declared())}

    assert runtime == set(APPROVED_RUNTIME_DEPENDENCIES), (
        "accountant-dad declares exactly the three runtime dependencies D-30 "
        "approved on 2026-08-13. Another one is either a reader or the door a "
        "reader walks through; either way it is an owner decision and not a "
        f"test change. Found: {sorted(runtime)!r}"
    )


def test_no_unapproved_dependency_in_any_group_names_a_document_reader():
    """Second net, over the dev tools too. The first net is the line above.

    The three approved runtime packages are exempt by name — `pytesseract`
    contains a reader word, which is the point of it. Nothing else is.
    """
    offenders = [
        name
        for name in dependency_names(declared())
        if package_name(name) not in APPROVED_RUNTIME_DEPENDENCIES
        and any(word in name.lower() for word in READER_WORDS)
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
    # An approved NAME in an unapproved SET is still a failure: this declares
    # one of the three and omits the other two.
    runtime = {package_name(item) for item in runtime_dependencies(planted)}
    assert runtime != set(APPROVED_RUNTIME_DEPENDENCIES)


@pytest.mark.parametrize(
    ("label", "declared_runtime"),
    [
        ("a fourth package", ["pypdf", "pytesseract", "Pillow", "easyocr"]),
        ("one of the three swapped out", ["pypdf", "pytesseract", "paddleocr"]),
        ("one of the three dropped", ["pypdf>=5.0", "pytesseract>=0.3"]),
        ("none at all, the old posture", []),
    ],
)
def test_the_dependency_guard_catches_any_set_that_is_not_exactly_the_approved_one(
    label: str, declared_runtime: list[str]
):
    """A fourth package is the way a reader arrives without touching this file."""
    runtime = {package_name(item) for item in declared_runtime}

    assert runtime != set(APPROVED_RUNTIME_DEPENDENCIES), (
        f"{label} was accepted as the approved set, so the guard proves nothing"
    )


# =============================================================================
# THE ALLOW-LIST ITSELF — it is only a guard while it stays this short
# =============================================================================


def test_the_allow_lists_are_exactly_the_size_the_owner_approved():
    """TWO modules and THREE packages since 2026-08-13. The counts are asserted
    so the lists cannot be quietly widened to make a future failure disappear -
    widening them is allowed, but only together with these numbers and a new
    owner decision in docs/DECISIONS.md, which is exactly the procedure D-30
    itself followed."""
    assert len(APPROVED_READER_MODULES) == 2
    assert APPROVED_READER_MODULES == ("textlayer.py", "freeocr.py")

    assert len(APPROVED_RUNTIME_DEPENDENCIES) == 3
    assert APPROVED_RUNTIME_DEPENDENCIES == ("pypdf", "pytesseract", "Pillow")

    # And the per-module split covers the approved modules and only those, so a
    # third reader cannot arrive by appearing in one list and not the other.
    assert set(READER_LIBRARIES) == set(APPROVED_READER_MODULES)


def test_every_library_an_approved_reader_may_import_is_an_approved_dependency():
    """The two lists have to describe the same three packages. Otherwise
    `READER_LIBRARIES` gains `cv2` on its own and the import guard waves it
    through while `pyproject.toml` never mentions it."""
    granted = {lib for libs in READER_LIBRARIES.values() for lib in libs}
    installed = {DISTRIBUTION_OF.get(lib, lib) for lib in granted}

    assert installed == set(APPROVED_RUNTIME_DEPENDENCIES), (
        "a reader may import a library the project does not declare, or "
        f"declares one no reader may import: granted {sorted(installed)}"
    )
    # No two readers share a library; that is what makes the split a boundary.
    assert sum(len(libs) for libs in READER_LIBRARIES.values()) == len(granted)


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
    parsed by anything above, so its mere presence has to be the failure.

    No exception here either: an approved reader is approved to CALL a model,
    never to carry one. Tesseract's language data belongs to the host.
    """
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
