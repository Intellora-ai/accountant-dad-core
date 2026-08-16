"""Say which test failed, on the page a person actually looks at.

WHAT THIS IS FOR
----------------
Measured on run 31958210449, commit 2fe7081. GitHub captured everything and
surfaced none of it:

    check-run output_title   null
    check-run output_summary null
    annotation (pr-fast)     "Process completed with exit code 1."  .github:10537
    annotation (ci-gate)     "Process completed with exit code 1."  .github:32

The `.github` in those is a literal string, not a file, and 10537 is the log's
line COUNT, not a source line. So the pull-request page named no test, no file,
no assertion. Meanwhile the log held the whole answer - full tracebacks, the
`E   AssertionError` lines, the `file.py:NNN:` footers, all 75 of them - at line
868 of 11,388, with the failure LIST at 10,724 because a 5,261-line warnings
summary sits between the two. Nothing was truncated. It was 97% of the way down
a log nobody scrolls.

This reads the junit.xml that `changed-tests` has ALREADY written and restates
it as annotations and a job summary.

IT IS NOT A GATE AND CANNOT BECOME ONE. It declares nothing in ci/gates.toml, it
runs only under `if: failure()` - which means a gate has already failed - and it
re-runs no test. It can only restate a verdict that was reached without it. It
exits 0 on every path it controls, deliberately: a reporting step that fails
would replace the real failure with its own.

junit.xml AND NOT THE LOG, FOR A MEASURED REASON
------------------------------------------------
`FORCE_COLOR: "1"` in the workflow makes pytest emit ANSI escapes: 26,726 of
them over 11,129 lines. The escape lands BETWEEN `FAILED` and the space after
it, so `grep "FAILED "` over the raw log returns zero matches. Every
log-scraping version of this script is silently empty.

THE XML CARRIES THE COLOUR TOO, and that is not a reason to go back to the log -
it is one more reason not to. Measured against the real report from run
31958210449: the traceback footer arrives as
`&#x1B;[1mtests/test_x.py&#x1B;[0m:187:`, with the escape INSIDE the path, and a
location regex run over it matched 0 of 75 failures. The difference is that the
XML's structure survives colour - the test id and the message are attributes, not
text a colour code can split - so stripping the escapes is enough here and is
never enough on the log.

REGEX AND NOT AN XML PARSER, ALSO FOR A REASON
-----------------------------------------------
The same one `.github/workflows/pr-fast.yml` already gives where it reads the
test count: stdlib XML parsers accept external entities, and a diagnostic should
not widen the attack surface of the thing it reports on. `defusedxml` is not an
option either - `pyproject.toml` asserts an exact dependency set, and a fourth
entry fails `test_the_project_declares_exactly_the_dependencies_the_owner_approved`.

A LOCATION IS PRINTED ONLY WHEN IT IS TRUSTWORTHY. `file=` and `line=` are
emitted only when the path parsed out of the traceback EXISTS in this checkout
and the line number is a positive integer. An annotation pinned to a file that
is not there is worse than one with no location: it sends a reader somewhere
real-looking and wrong. Everything else falls back to a job-level annotation
that still names the test.

    python ci/explain_failure.py junit.xml
"""

from __future__ import annotations

import html
import os
import pathlib
import re
import sys
from typing import Final

#: One `<testcase ...>` element and whatever it contains, up to its close. Both
#: forms are matched: the self-closing `<testcase/>` of a test that passed, and
#: the open/close pair that a failure needs in order to carry its traceback.
_TESTCASE: Final = re.compile(
    r"<testcase\b(?P<attrs>[^>]*?)(?:/>|>(?P<body>.*?)</testcase>)",
    re.DOTALL,
)

#: `<failure message="...">text</failure>`, and `<error>` in the same shape. An
#: error is a test that never got to assert anything - a collection failure, an
#: exception in a fixture - and it is as much a failure as a false assertion.
_PROBLEM: Final = re.compile(
    r"<(?P<tag>failure|error)\b(?P<attrs>[^>]*?)(?:/>|>(?P<text>.*?)</(?P=tag)>)",
    re.DOTALL,
)

_ATTR: Final = re.compile(r'(?P<key>[a-zA-Z_:][\w:.-]*)\s*=\s*"(?P<value>[^"]*)"')

#: The `path/to/file.py:123:` footer pytest writes at the end of a traceback
#: frame. Anchored to a line start so it cannot match a path mentioned inside a
#: sentence, and the path is left unconstrained here because whether it is real
#: is decided by asking the filesystem, not by guessing at a prefix.
_LOCATION: Final = re.compile(r"^(?P<path>[\w./\\-]+\.py):(?P<line>\d+):", re.MULTILINE)

#: The assertion lines pytest indents with `E   `. These carry the message and,
#: when the assertion was a comparison, the expected and actual values.
_ASSERTION: Final = re.compile(r"^E\s+(?P<text>\S.*)$", re.MULTILINE)

_SUITE: Final = re.compile(r"<testsuite\b[^>]*>")

#: Every ANSI control sequence pytest writes under `FORCE_COLOR`, in BOTH forms.
#:
#: It must come off before anything else is matched: measured on the real report
#: from run 31958210449, the escape sits inside the traceback footer, so a
#: location regex run over the untreated text matched 0 of 75 failures.
#:
#: TWO FORMS, AND THE SECOND IS THE ONE THAT CATCHES PEOPLE OUT. ESC is 0x1B,
#: which is not a legal character in XML 1.0 at all, so pytest's junit writer
#: does not emit it and does not emit a character reference for it either - it
#: substitutes the LITERAL FOUR CHARACTERS `#x1B`. `html.unescape` therefore
#: never sees an entity to resolve, and a `\x1b`-only pattern removes nothing.
#: Verbatim from that report:
#:
#:     #x1B[1m#x1B[31mtests/test_company_identity.py#x1B[0m:187: AssertionError
_ANSI: Final = re.compile(r"(?:\x1b|#x1B)\[[0-9;]*[A-Za-z]")


def _plain(value: str) -> str:
    """The text with its colour removed, entities already resolved."""
    return _ANSI.sub("", value)


def _attrs(raw: str) -> dict[str, str]:
    """The attributes of one element, entities resolved and colour stripped."""
    return {m["key"]: _plain(html.unescape(m["value"])) for m in _ATTR.finditer(raw)}


def _one_line(value: str) -> str:
    """Collapse to a single line.

    A workflow command ends at the first newline, so an annotation message
    carrying one would be cut off mid-sentence and the remainder would be
    printed as stray log text.
    """
    return " ".join(value.split())


def _located(text: str, root: pathlib.Path) -> tuple[str, int] | None:
    """The last traceback location that names a file really in this checkout.

    LAST, not first. The final frame is the one that actually raised; the
    earlier ones are the callers that led there.
    """
    for match in reversed(list(_LOCATION.finditer(text))):
        path, line = match["path"], int(match["line"])
        if line > 0 and (root / path).is_file():
            return path, line
    return None


class Failure:
    """One failing test, and everything a person needs to act on it."""

    def __init__(self, case: dict[str, str], problem: dict[str, str], text: str):
        self.classname = case.get("classname", "")
        self.name = case.get("name", "unknown test")
        self.message = _one_line(problem.get("message", ""))
        self.text = text
        #: Kept apart from `message` because pytest puts the bare assertion in
        #: the attribute and the expected-versus-actual expansion in the body.
        self.assertions = [_one_line(m["text"]) for m in _ASSERTION.finditer(text)]

    @property
    def test_id(self) -> str:
        """`path/to/test.py::test_name`, the string you can paste into pytest."""
        module = self.classname.replace(".", "/")
        return f"{module}.py::{self.name}" if module else self.name


def failures(report: str) -> list[Failure]:
    """Every failing or erroring test in the report, in file order."""
    out: list[Failure] = []
    for case in _TESTCASE.finditer(report):
        body = case["body"]
        if not body:
            continue
        problem = _PROBLEM.search(body)
        if problem is None:
            continue
        out.append(
            Failure(
                _attrs(case["attrs"]),
                _attrs(problem["attrs"]),
                _plain(html.unescape(problem["text"] or "")),
            )
        )
    return out


def totals(report: str) -> dict[str, str]:
    """The counts off the `<testsuite>` element, or empty if it is not there."""
    suite = _SUITE.search(report)
    return _attrs(suite.group()) if suite else {}


def annotate(one: Failure, root: pathlib.Path) -> str:
    """One `::error` workflow command for one failing test."""
    detail = one.message or (one.assertions[0] if one.assertions else "test failed")
    title = _one_line(one.test_id)
    where = _located(one.text, root)
    if where is None:
        return f"::error title={title}::{detail}"
    path, line = where
    return f"::error file={path},line={line},title={title}::{detail}"


def summary(found: list[Failure], counts: dict[str, str], root: pathlib.Path) -> str:
    """The Markdown written to the job summary page."""
    lines = ["## Test failures", ""]
    if counts:
        lines += [
            "| tests | failures | errors | skipped | time |",
            "|---|---|---|---|---|",
            "| {tests} | {failures} | {errors} | {skipped} | {time} |".format(
                tests=counts.get("tests", "?"),
                failures=counts.get("failures", "?"),
                errors=counts.get("errors", "?"),
                skipped=counts.get("skipped", "?"),
                time=counts.get("time", "?"),
            ),
            "",
        ]
    if not found:
        lines.append("No failing test was recorded in the report.")
        return "\n".join(lines) + "\n"

    first = found[0]
    where = _located(first.text, root)
    lines += [
        "### First failure",
        "",
        "| field | value |",
        "|---|---|",
        f"| test | `{first.test_id}` |",
        f"| where | `{where[0]}:{where[1]}` |" if where else "| where | not located |",
        f"| message | {first.message or '(none)'} |",
        "",
    ]
    if first.assertions:
        lines += ["Assertion, as pytest reported it:", "", "```text"]
        lines += first.assertions
        lines += ["```", ""]
    if len(found) > 1:
        lines += [f"### All {len(found)} failing tests", "", "```text"]
        lines += [one.test_id for one in found]
        lines += ["```", ""]
    lines.append(
        "Complete tracebacks and the machine-readable report are in the "
        "`pr-fast-evidence` artifact on this run."
    )
    return "\n".join(lines) + "\n"


def main(argv: list[str]) -> int:
    root = pathlib.Path.cwd()
    report = pathlib.Path(argv[1] if len(argv) > 1 else "junit.xml")
    if not report.is_file():
        # Not an error. The step runs after ANY failed gate, and a lint or type
        # failure stops the run long before pytest writes a report. Saying so is
        # honest; exiting non-zero here would bury the failure that really
        # happened under a failure to describe it.
        print(f"::notice::no test report at {report}, so there is nothing to explain")
        return 0

    text = report.read_text(encoding="utf-8", errors="replace")
    found = failures(text)
    counts = totals(text)

    for one in found:
        print(annotate(one, root))

    written = os.environ.get("GITHUB_STEP_SUMMARY")
    if written:
        with pathlib.Path(written).open("a", encoding="utf-8") as handle:
            handle.write(summary(found, counts, root))
    else:
        print(summary(found, counts, root))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
