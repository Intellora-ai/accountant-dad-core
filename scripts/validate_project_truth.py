#!/usr/bin/env python3
"""One source of truth for project status, and the thing that keeps it true.

`docs/CONTROL_PLANE.yaml` is canonical. Every other document is a rendering of
it. This script fails, loudly and with a file:line, whenever:

  * the control plane contradicts itself (a phase declared twice, a phase with
    two statuses, a metric with two current values, a PASSED phase with no
    evidence, a blocker with no kind, a dangling blocker reference)
  * a secondary document contradicts the control plane (asserts a different
    status for a phase, or a different number for a metric the control plane
    defines)
  * a document uses a status word that is not in `allowed_statuses`
  * an owner decision exists in `docs/DECISIONS.md` or `docs/OWNER_ACTIONS.md`
    but not in the control plane

Exit 0 only when every check passes. `--json` prints the same result machine
readably. `--root` points the whole run at a different tree, which is how the
tests drive it against small purpose-built fixtures.

WHY THERE IS A YAML PARSER IN HERE
----------------------------------
`dependencies = []` in `pyproject.toml` and it stays that way. PyYAML is not a
declared dependency - it arrives only as a transitive dependency of `bandit`,
which is a dev tool and may not be installed wherever this script runs.

There is a second, larger reason. PyYAML's loader **silently keeps the last of
a set of duplicate keys**. A phase with two `status:` lines would parse clean
and report one status. Two of the failures this script must catch - "a phase
with two statuses" and "a metric with two current values" - are duplicate keys,
so a parser that discards duplicates cannot see them. `_parse_yaml` below keeps
every key/value pair in source order, with its line number.

The parser covers the subset the schema uses and REFUSES anything else, rather
than guessing. A refusal is a non-zero exit naming the line.

THE ALLOW-LIST
--------------
The docs deliberately preserve old, wrong claims inside dated audit sections.
Those lines are quotes, not assertions, and the contradiction scan cannot tell
the difference. `docs/TRUTH_ALLOWLIST.json` (or `--allow-list PATH`) suppresses
individual findings. Its schema:

    [
      {
        "document": "docs/PROJECT_STATE.md",
        "line": 1483,
        "fragment": "Phase 2 status:         ENVIRONMENT-LIMITED",
        "reason": "Dated 2026-08-08 audit section; kept verbatim as the
                   record of what was believed then, not a current claim."
      }
    ]

Every field is required. `document` must be an exact tracked path with no glob
characters, `line` must be a real line number, `fragment` must be at least
8 characters and must still be present on that line, and `reason` must be at
least 20 characters. There is no way to name a whole file: an entry pins one
line and the text on it. An entry that no longer matches, or that no longer
suppresses anything, is itself a failure - so the list cannot silently rot.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, cast

DEFAULT_ROOT = Path(__file__).resolve().parent.parent

CONTROL_PLANE = "docs/CONTROL_PLANE.yaml"
ALLOW_LIST = "docs/TRUTH_ALLOWLIST.json"

# Every document that is a rendering of the control plane and must therefore
# agree with it. A document that does not exist is not a failure - it is simply
# not scanned, and the report says so.
TRACKED_DOCUMENTS: tuple[str, ...] = (
    "docs/PROJECT_STATE.md",
    "docs/ARCHITECTURE.md",
    "docs/OWNER_ACTIONS.md",
    "docs/BLOCKERS.md",
    "docs/CLAUDE_CONTEXT.md",
    "docs/LAUNCH_GATES.md",
    "README.md",
)

# Where owner decisions are written up in prose. Every `D-nn` found here must
# also exist in the control plane.
DECISION_DOCUMENTS: tuple[str, ...] = (
    "docs/DECISIONS.md",
    "docs/OWNER_ACTIONS.md",
)

ALLOW_LIST_DOCUMENTS = frozenset(TRACKED_DOCUMENTS) | frozenset(DECISION_DOCUMENTS)

DECISION_STATUSES = frozenset(
    {
        "OPEN",
        "ANSWERED",
        "WAITING_FOR_EVIDENCE",
        "BLOCKED_ENVIRONMENT",
        "IMPLEMENTED_AFTER_OWNER_DECISION",
        "SUPERSEDED",
    }
)

# A decision claiming one of these has been settled by the owner, so it must
# carry the owner's words.
DECISION_STATUSES_NEEDING_AN_ANSWER = frozenset(
    {"ANSWERED", "IMPLEMENTED_AFTER_OWNER_DECISION"}
)

BLOCKER_KINDS = frozenset({"ENVIRONMENT", "OWNER"})

# Uppercase words that are unmistakably a status claim even when they are not
# in `allowed_statuses`. Listing them by hand means the scan reports them as a
# vocabulary failure rather than shrugging.
KNOWN_STATUS_WORDS = frozenset(
    {
        "COMPLETE",
        "COMPLETED",
        "DONE",
        "FINISHED",
        "INCOMPLETE",
        "UNFINISHED",
        "IN_PROGRESS",
        "WIP",
        "TODO",
        "PENDING",
        "PARTIAL",
        "PARTIALLY_COMPLETE",
        "PARTIALLY_DONE",
        "BLOCKED",
        "OWNER_BLOCKED",
        "ENVIRONMENT_LIMITED",
        "ENVIRONMENT-LIMITED",
        "NOT_RUN",
        "NOT-RUN",
        "NOT_VERIFIED",
        "UNVERIFIED",
        "VERIFIED",
        "FAILED",
        "FAILING",
        "PASSING",
        "SHIPPED",
        "READY",
        "SETTLED",
        "OPEN",
        "CLOSED",
        "SKIPPED",
        "DEFERRED",
        "ABANDONED",
        "SUPERSEDED",
    }
)

# Uppercase words that turn up beside a phase number and are emphatically not a
# status. Without this the scan reports the word TALLY as a phase status.
NON_STATUS_WORDS = frozenset(
    {
        "TALLY",
        "TALLYPRIME",
        "FAKETALLY",
        "REALTALLY",
        "OWNER",
        "PHASE",
        "PHASES",
        "XML",
        "JSON",
        "YAML",
        "TOML",
        "HTTP",
        "HTTPS",
        "CI",
        "CLI",
        "API",
        "URL",
        "UUID",
        "SHA",
        "GST",
        "TDS",
        "INR",
        "AND",
        "THE",
        "NOT",
        "ALL",
        "ANY",
        "NONE",
        "ONE",
        "TWO",
        "SIX",
        "TEN",
        "NOTE",
        "WHAT",
        "THIS",
        "FILE",
        "DOES",
        "PROVE",
        "NEVER",
        "ALWAYS",
        "MUST",
        "ONLY",
        "GATE",
        "GATES",
        "ARCHITECTURE",
        "DECISION",
        "DECISIONS",
        "RELEASE",
        "FEATURE",
        "CAPABILITY",
        "RUNBOOK",
        "TAXONOMY",
        "BOTTLENECKS",
        "EPIC",
        "BRANCH",
        "COMMIT",
        "TEST",
        "TESTS",
        "CODE",
        "DOCS",
        "README",
        "MAIN",
        "ROOT",
        "TRUE",
        "FALSE",
        "NULL",
    }
)

_ANY_STATUS_BUT_PASSED = frozenset(
    {
        "NOT_PASSED",
        "PARTIALLY_VERIFIED",
        "BLOCKED_ENVIRONMENT",
        "OWNER_DECISION_REQUIRED",
        "NOT_STARTED",
    }
)

# Prose claims about a phase, and the canonical statuses each one is compatible
# with. English cannot be parsed exactly; the brief is to over-report rather
# than miss, and the allow-list is the escape hatch. Order matters: the first
# pattern's matches are removed from the line before the next is tried, so
# "not yet complete" never also reads as "complete".
PROSE_CLAIMS: tuple[tuple[str, frozenset[str], str], ...] = (
    (
        # `\w+\s+` cannot cross a full stop, so the reach stays inside one
        # sentence: "does not make Phase 6 complete" is one negated claim.
        r"\bnot\s+(?:\w+\s+){0,4}?(?:complete|completed|done|finished|passing"
        r"|passed)\b",
        _ANY_STATUS_BUT_PASSED,
        "not complete",
    ),
    (
        r"\bnot\s+(?:yet\s+|fully\s+|entirely\s+|quite\s+)*started\b",
        frozenset({"NOT_STARTED"}),
        "not started",
    ),
    (
        r"\bpartially\s+verified\b",
        frozenset({"PARTIALLY_VERIFIED"}),
        "partially verified",
    ),
    (
        r"\b(?:complete|completed|done|finished)\b",
        frozenset({"PASSED"}),
        "complete",
    ),
    (
        r"\b(?:in\s+progress|incomplete|unfinished)\b",
        _ANY_STATUS_BUT_PASSED,
        "in progress",
    ),
    (
        r"\bblocked\b",
        frozenset({"BLOCKED_ENVIRONMENT", "OWNER_DECISION_REQUIRED"}),
        "blocked",
    ),
)

# "Phases 0 and 1" names two. "Phase 5B, 30 of 30 lifecycles" names one - so a
# chained list is only read after the PLURAL word.
PHASE_MENTION = re.compile(
    r"\bPhase(?P<plural>s)?\b[\s*`_]*(?P<first>[0-9]+[A-Za-z]?)"
    r"(?P<rest>(?:\s*(?:,|and|&|/|\+)\s*[0-9]+[A-Za-z]?)*)",
    re.IGNORECASE,
)
# ARCHITECTURE in "ARCHITECTURE.md" is a filename, not a status.
FILE_EXTENSION = re.compile(r"\.[a-z]{1,5}\b")
PHASE_TAIL_ID = re.compile(r"[0-9]+[A-Za-z]?")
UPPER_TOKEN = re.compile(r"\b[A-Z][A-Z0-9]*(?:[_-][A-Z0-9]+)*\b")
DECISION_ID = re.compile(r"\bD-[0-9]{1,3}\b")
NUMBER = re.compile(r"-?[0-9]+(?:\.[0-9]+)?")


# ---------------------------------------------------------------------------
# The YAML subset parser. Keeps duplicate keys. Refuses what it cannot parse.
# ---------------------------------------------------------------------------


class YamlError(Exception):
    """The control plane is not in the subset this script can read."""

    def __init__(self, line: int, message: str) -> None:
        super().__init__(f"line {line}: {message}")
        self.line = line
        self.message = message


@dataclass(frozen=True)
class Node:
    """Any parsed node, carrying the 1-based source line it started on."""

    line: int


@dataclass(frozen=True)
class ScalarNode(Node):
    value: str | int | float | bool | None


@dataclass(frozen=True)
class SequenceNode(Node):
    items: tuple[Node, ...]


@dataclass(frozen=True)
class MappingNode(Node):
    """Key, key line, value - for every pair, duplicates included."""

    pairs: tuple[tuple[str, int, Node], ...]

    def all_of(self, key: str) -> list[tuple[int, Node]]:
        return [(ln, node) for k, ln, node in self.pairs if k == key]

    def first(self, key: str) -> Node | None:
        for k, _, node in self.pairs:
            if k == key:
                return node
        return None

    def line_of(self, key: str) -> int:
        for k, ln, _ in self.pairs:
            if k == key:
                return ln
        return self.line


@dataclass(frozen=True)
class SourceLine:
    number: int
    indent: int
    text: str
    block_value: str | None = None


_BLOCK_SCALAR = re.compile(r"^(?P<head>.*?):[ \t]*(?P<style>[|>])(?P<chomp>[+-]?)\s*$")


def _logical_lines(text: str) -> list[SourceLine]:
    raw = text.splitlines()
    out: list[SourceLine] = []
    i = 0
    while i < len(raw):
        line = raw[i].rstrip()
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or stripped in {"---", "..."}:
            i += 1
            continue
        indent = len(line) - len(line.lstrip(" "))
        if "\t" in line[:indent]:
            raise YamlError(i + 1, "tab in indentation")
        content = line[indent:]

        block = _BLOCK_SCALAR.match(content)
        if block is not None:
            body: list[str] = []
            j = i + 1
            while j < len(raw):
                candidate = raw[j].rstrip()
                if not candidate.strip():
                    body.append("")
                    j += 1
                    continue
                candidate_indent = len(candidate) - len(candidate.lstrip(" "))
                if candidate_indent <= indent:
                    break
                body.append(candidate)
                j += 1
            while body and not body[-1]:
                body.pop()
            folded = _fold_block(body, block.group("style"), block.group("chomp"))
            out.append(
                SourceLine(i + 1, indent, block.group("head") + ":", block_value=folded)
            )
            i = j
            continue

        if content in {"|", ">", "|-", ">-", "|+", ">+"}:
            raise YamlError(i + 1, "block scalars are only supported as mapping values")

        out.append(SourceLine(i + 1, indent, content))
        i += 1
    return out


def _fold_block(body: list[str], style: str, chomp: str) -> str:
    if not body:
        return ""
    indents = [len(b) - len(b.lstrip(" ")) for b in body if b.strip()]
    strip = min(indents) if indents else 0
    lines = [b[strip:] if b.strip() else "" for b in body]
    joined = "\n".join(lines) if style == "|" else " ".join(x for x in lines if x)
    if chomp == "-":
        return joined.rstrip("\n")
    return joined


def _split_key(text: str, line: int) -> tuple[str, str]:
    if text[0] in "\"'":
        quote = text[0]
        end = text.find(quote, 1)
        if end < 0:
            raise YamlError(line, "unterminated quoted key")
        key = text[1:end]
        rest = text[end + 1 :].lstrip()
        if not rest.startswith(":"):
            raise YamlError(line, "expected ':' after quoted key")
        return key, rest[1:].strip()
    for index, char in enumerate(text):
        if char != ":":
            continue
        if index + 1 == len(text) or text[index + 1] in " \t":
            key = text[:index].strip()
            if not key:
                raise YamlError(line, "empty key")
            return key, text[index + 1 :].strip()
    raise YamlError(line, f"expected 'key: value', found {text!r}")


def _strip_comment(text: str) -> str:
    quote: str | None = None
    depth = 0
    for index, char in enumerate(text):
        if quote is not None:
            if char == quote:
                quote = None
            continue
        if char in "\"'":
            quote = char
        elif char in "[{":
            depth += 1
        elif char in "]}":
            depth -= 1
        elif char == "#" and depth == 0 and (index == 0 or text[index - 1] in " \t"):
            return text[:index].rstrip()
    return text.rstrip()


def _scalar(text: str, line: int) -> str | int | float | bool | None:
    if not text or text in {"null", "Null", "NULL", "~"}:
        return None
    if text in {"true", "True", "TRUE"}:
        return True
    if text in {"false", "False", "FALSE"}:
        return False
    if text[0] in "\"'":
        quote = text[0]
        if len(text) < 2 or text[-1] != quote:
            raise YamlError(line, "unterminated quoted string")
        inner = text[1:-1]
        if quote == '"':
            return inner.replace('\\"', '"').replace("\\n", "\n").replace("\\\\", "\\")
        return inner.replace("''", "'")
    if re.fullmatch(r"-?[0-9]+", text):
        return int(text)
    if re.fullmatch(r"-?[0-9]*\.[0-9]+(?:[eE][-+]?[0-9]+)?", text):
        return float(text)
    return text


def _split_flow(text: str, line: int) -> list[str]:
    items: list[str] = []
    current = ""
    quote: str | None = None
    for char in text:
        if quote is not None:
            current += char
            if char == quote:
                quote = None
            continue
        if char in "\"'":
            quote = char
            current += char
        elif char == ",":
            items.append(current.strip())
            current = ""
        elif char in "[]{}":
            raise YamlError(line, "nested flow collections are not supported")
        else:
            current += char
    if quote is not None:
        raise YamlError(line, "unterminated quoted string in flow sequence")
    if current.strip():
        items.append(current.strip())
    return items


def _parse_inline(text: str, line: int) -> Node:
    text = _strip_comment(text).strip()
    if text.startswith("{"):
        raise YamlError(line, "flow mappings are not supported")
    if text.startswith("["):
        if not text.endswith("]"):
            raise YamlError(line, "unterminated flow sequence")
        inner = text[1:-1].strip()
        items = [] if not inner else _split_flow(inner, line)
        return SequenceNode(
            line, tuple(ScalarNode(line, _scalar(i, line)) for i in items)
        )
    return ScalarNode(line, _scalar(text, line))


def _looks_like_a_mapping_entry(text: str) -> bool:
    try:
        _split_key(text, 0)
    except YamlError:
        return False
    return True


def _parse_block(block: list[SourceLine]) -> Node:
    first = block[0]
    if first.text == "-" or first.text.startswith("- "):
        return _parse_sequence(block)
    return _parse_mapping(block)


def _parse_sequence(block: list[SourceLine]) -> Node:
    indent = block[0].indent
    items: list[Node] = []
    i = 0
    while i < len(block):
        line = block[i]
        if line.indent != indent:
            raise YamlError(line.number, "inconsistent indentation inside a sequence")
        if line.text != "-" and not line.text.startswith("- "):
            raise YamlError(line.number, "expected a '-' sequence item")
        content = line.text[1:].lstrip(" ")
        content_column = indent + len(line.text) - len(content)
        j = i + 1
        while j < len(block) and block[j].indent > indent:
            j += 1
        # `- PASSED` is a scalar item, not a one-key mapping. Without this the
        # whole block-sequence form of allowed_statuses is unreadable.
        if (
            content
            and j == i + 1
            and line.block_value is None
            and not _looks_like_a_mapping_entry(content)
        ):
            items.append(_parse_inline(content, line.number))
            i = j
            continue
        body: list[SourceLine] = []
        if content:
            body.append(
                SourceLine(line.number, content_column, content, line.block_value)
            )
        for follower in block[i + 1 : j]:
            if content and follower.indent < content_column:
                raise YamlError(
                    follower.number, "under-indented continuation of a sequence item"
                )
            body.append(follower)
        items.append(_parse_block(body) if body else ScalarNode(line.number, None))
        i = j
    return SequenceNode(block[0].number, tuple(items))


def _parse_mapping(block: list[SourceLine]) -> Node:
    indent = block[0].indent
    pairs: list[tuple[str, int, Node]] = []
    i = 0
    while i < len(block):
        line = block[i]
        if line.indent != indent:
            raise YamlError(line.number, "inconsistent indentation inside a mapping")
        key, rest = _split_key(line.text, line.number)
        j = i + 1
        while j < len(block) and block[j].indent > indent:
            j += 1
        child = block[i + 1 : j]
        value: Node
        if line.block_value is not None:
            if child:
                raise YamlError(line.number, "block scalar followed by a nested block")
            value = ScalarNode(line.number, line.block_value)
        elif _strip_comment(rest).strip():
            if child:
                raise YamlError(line.number, "value given inline and as a block")
            value = _parse_inline(rest, line.number)
        elif child:
            value = _parse_block(child)
        else:
            value = ScalarNode(line.number, None)
        pairs.append((key, line.number, value))
        i = j
    return MappingNode(block[0].number, tuple(pairs))


def _parse_yaml(text: str) -> Node:
    lines = _logical_lines(text)
    if not lines:
        raise YamlError(1, "the file is empty")
    if lines[0].indent != 0:
        raise YamlError(lines[0].number, "the document must start at column 0")
    return _parse_block(lines)


# ---------------------------------------------------------------------------
# Results
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Failure:
    document: str
    line: int
    found: str
    expected: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "file": self.document,
            "line": self.line,
            "found": self.found,
            "expected": self.expected,
        }


@dataclass
class Check:
    name: str
    what_it_proves: str
    failures: list[Failure] = field(default_factory=list[Failure])
    suppressed: int = 0
    skipped: str | None = None

    @property
    def ok(self) -> bool:
        return self.skipped is None and not self.failures

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "proves": self.what_it_proves,
            "status": "SKIPPED"
            if self.skipped is not None
            else ("PASS" if self.ok else "FAIL"),
            "skipped_because": self.skipped,
            "suppressed_by_allow_list": self.suppressed,
            "failures": [f.as_dict() for f in self.failures],
        }


@dataclass(frozen=True)
class AllowEntry:
    document: str
    line: int
    fragment: str
    reason: str
    index: int


class Report:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.checks: list[Check] = []
        self._used: set[int] = set()
        self.allow: list[AllowEntry] = []

    def check(self, name: str, proves: str) -> Check:
        found = Check(name, proves)
        self.checks.append(found)
        return found

    def suppresses(self, check: Check, failure: Failure) -> bool:
        """True when the allow-list pins exactly this line, and says why."""
        for entry in self.allow:
            if entry.document != failure.document or entry.line != failure.line:
                continue
            line_text = _read_line(self.root / entry.document, entry.line)
            if line_text is None or entry.fragment not in line_text:
                continue
            self._used.add(entry.index)
            check.suppressed += 1
            return True
        return False

    def add(self, check: Check, failure: Failure) -> None:
        if not self.suppresses(check, failure):
            check.failures.append(failure)

    @property
    def used_allow_entries(self) -> set[int]:
        return self._used

    @property
    def ok(self) -> bool:
        return all(c.ok for c in self.checks)


_LINE_CACHE: dict[Path, list[str]] = {}


def _lines_of(path: Path) -> list[str]:
    cached = _LINE_CACHE.get(path)
    if cached is None:
        cached = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
        _LINE_CACHE[path] = cached
    return cached


def _read_line(path: Path, number: int) -> str | None:
    lines = _lines_of(path)
    if 1 <= number <= len(lines):
        return lines[number - 1]
    return None


# ---------------------------------------------------------------------------
# Reading the control plane into plain shapes the checks can use
# ---------------------------------------------------------------------------


def _text_of(node: Node | None) -> str | None:
    if isinstance(node, ScalarNode) and node.value is not None:
        return str(node.value).strip()
    return None


def _mappings_under(root: MappingNode, key: str) -> list[MappingNode]:
    node = root.first(key)
    if not isinstance(node, SequenceNode):
        return []
    return [item for item in node.items if isinstance(item, MappingNode)]


def _allowed_statuses(root: MappingNode) -> list[str]:
    node = root.first("allowed_statuses")
    if not isinstance(node, SequenceNode):
        return []
    out: list[str] = []
    for item in node.items:
        text = _text_of(item)
        if text:
            out.append(text)
    return out


# ---------------------------------------------------------------------------
# Checks against the control plane itself
# ---------------------------------------------------------------------------


def _check_shape(report: Report, root: MappingNode) -> list[str]:
    check = report.check(
        "the_control_plane_declares_a_version_and_a_status_vocabulary",
        "version, generated_from_commit and allowed_statuses are all present",
    )
    for key in ("version", "generated_from_commit", "allowed_statuses"):
        if root.first(key) is None or _text_of(root.first(key)) in (None, ""):
            if key == "allowed_statuses" and isinstance(
                root.first("allowed_statuses"), SequenceNode
            ):
                continue
            report.add(
                check,
                Failure(
                    CONTROL_PLANE,
                    root.line_of(key),
                    f"{key} is missing or empty",
                    f"a non-empty {key}",
                ),
            )
    statuses = _allowed_statuses(root)
    if not statuses:
        report.add(
            check,
            Failure(
                CONTROL_PLANE,
                root.line_of("allowed_statuses"),
                "allowed_statuses is empty or is not a list",
                "a non-empty list of status names",
            ),
        )
    return statuses


def _check_unique_ids(
    report: Report,
    items: list[MappingNode],
    kind: str,
    check_name: str,
) -> None:
    check = report.check(check_name, f"every {kind} id is declared exactly once")
    seen: dict[str, int] = {}
    for item in items:
        ident = _text_of(item.first("id"))
        if ident is None:
            report.add(
                check,
                Failure(
                    CONTROL_PLANE,
                    item.line,
                    f"a {kind} with no id",
                    f"every {kind} carries an id",
                ),
            )
            continue
        if ident in seen:
            report.add(
                check,
                Failure(
                    CONTROL_PLANE,
                    item.line_of("id"),
                    f'{kind} id "{ident}" declared again',
                    f"declared once (first at line {seen[ident]})",
                ),
            )
            continue
        seen[ident] = item.line_of("id")


def _check_single_valued(
    report: Report,
    items: list[MappingNode],
    kind: str,
    keys: tuple[str, ...],
    check_name: str,
) -> None:
    check = report.check(
        check_name,
        f"no {kind} carries two values for {', '.join(keys)}",
    )
    for item in items:
        ident = _text_of(item.first("id")) or "<no id>"
        for key in keys:
            occurrences = item.all_of(key)
            if len(occurrences) > 1:
                report.add(
                    check,
                    Failure(
                        CONTROL_PLANE,
                        occurrences[1][0],
                        f'{kind} "{ident}" has {len(occurrences)} {key} values '
                        f"({', '.join(str(_text_of(n)) for _, n in occurrences)})",
                        f"exactly one {key} (first at line {occurrences[0][0]})",
                    ),
                )


def _check_phase_statuses(
    report: Report, phases: list[MappingNode], statuses: list[str]
) -> None:
    check = report.check(
        "every_phase_status_is_in_the_allowed_vocabulary",
        "no phase carries a status outside allowed_statuses",
    )
    for phase in phases:
        ident = _text_of(phase.first("id")) or "<no id>"
        status = _text_of(phase.first("status"))
        if status is None:
            report.add(
                check,
                Failure(
                    CONTROL_PLANE,
                    phase.line,
                    f'phase "{ident}" has no status',
                    "a status from allowed_statuses",
                ),
            )
        elif status not in statuses:
            report.add(
                check,
                Failure(
                    CONTROL_PLANE,
                    phase.line_of("status"),
                    f'phase "{ident}" status {status}',
                    f"one of {', '.join(statuses)}",
                ),
            )


def _check_phase_evidence(report: Report, phases: list[MappingNode]) -> None:
    check = report.check(
        "every_phase_that_is_not_not_started_carries_evidence",
        "a PASSED phase with no evidence is a claim, not a fact",
    )
    for phase in phases:
        ident = _text_of(phase.first("id")) or "<no id>"
        status = _text_of(phase.first("status"))
        if status == "NOT_STARTED":
            continue
        evidence = _text_of(phase.first("evidence"))
        if not evidence:
            report.add(
                check,
                Failure(
                    CONTROL_PLANE,
                    phase.line,
                    f'phase "{ident}" is {status} with empty or missing evidence',
                    "a non-empty evidence string",
                ),
            )


def _check_exit_criteria(report: Report, phases: list[MappingNode]) -> None:
    check = report.check(
        "every_exit_criterion_marked_met_carries_evidence",
        "met: true with no evidence is an unbacked claim",
    )
    for phase in phases:
        ident = _text_of(phase.first("id")) or "<no id>"
        node = phase.first("exit_criteria")
        if not isinstance(node, SequenceNode):
            continue
        for item in node.items:
            if not isinstance(item, MappingNode):
                continue
            met = item.first("met")
            is_met = isinstance(met, ScalarNode) and met.value is True
            if not is_met:
                continue
            if not _text_of(item.first("evidence")):
                text = _text_of(item.first("text")) or "<no text>"
                report.add(
                    check,
                    Failure(
                        CONTROL_PLANE,
                        item.line,
                        f'phase "{ident}" exit criterion "{text}" is met with '
                        f"no evidence",
                        "a non-empty evidence string",
                    ),
                )


def _check_blockers(report: Report, blockers: list[MappingNode]) -> None:
    check = report.check(
        "every_blocker_declares_a_kind_of_environment_or_owner",
        "a blocker with no kind cannot be routed to anyone",
    )
    for blocker in blockers:
        ident = _text_of(blocker.first("id")) or "<no id>"
        kind = _text_of(blocker.first("kind"))
        if kind is None:
            report.add(
                check,
                Failure(
                    CONTROL_PLANE,
                    blocker.line,
                    f'blocker "{ident}" has no kind',
                    "kind: ENVIRONMENT or kind: OWNER",
                ),
            )
        elif kind not in BLOCKER_KINDS:
            report.add(
                check,
                Failure(
                    CONTROL_PLANE,
                    blocker.line_of("kind"),
                    f'blocker "{ident}" kind {kind}',
                    "one of ENVIRONMENT, OWNER",
                ),
            )


def _check_blocker_references(
    report: Report, phases: list[MappingNode], blockers: list[MappingNode]
) -> None:
    check = report.check(
        "every_blocker_a_phase_names_actually_exists",
        "a dangling blocker id hides the reason a phase is stuck",
    )
    known = {_text_of(b.first("id")) for b in blockers}
    for phase in phases:
        ident = _text_of(phase.first("id")) or "<no id>"
        blocker = _text_of(phase.first("blocker"))
        if blocker is None:
            continue
        if blocker not in known:
            report.add(
                check,
                Failure(
                    CONTROL_PLANE,
                    phase.line_of("blocker"),
                    f'phase "{ident}" names blocker "{blocker}", which is not declared',
                    "a blocker id present in the blockers list",
                ),
            )


def _check_launch_gates(report: Report, gates: list[MappingNode]) -> None:
    check = report.check(
        "every_launch_gate_names_a_test_and_evidence",
        "a launch gate with no test is a sentence, not a gate",
    )
    for gate in gates:
        ident = _text_of(gate.first("id")) or "<no id>"
        for key in ("test", "evidence"):
            if not _text_of(gate.first(key)):
                report.add(
                    check,
                    Failure(
                        CONTROL_PLANE,
                        gate.line,
                        f'launch gate "{ident}" has no {key} reference',
                        f"a non-empty {key}",
                    ),
                )


def _check_decisions(report: Report, decisions: list[MappingNode]) -> None:
    vocabulary = report.check(
        "every_decision_status_is_a_known_decision_status",
        "decisions have their own vocabulary and it is closed",
    )
    answered = report.check(
        "every_answered_decision_records_the_owner_answer",
        "status ANSWERED with owner_answer null is a fabricated decision",
    )
    for decision in decisions:
        ident = _text_of(decision.first("id")) or "<no id>"
        status = _text_of(decision.first("status"))
        if status is None:
            report.add(
                vocabulary,
                Failure(
                    CONTROL_PLANE,
                    decision.line,
                    f'decision "{ident}" has no status',
                    f"one of {', '.join(sorted(DECISION_STATUSES))}",
                ),
            )
            continue
        if status not in DECISION_STATUSES:
            report.add(
                vocabulary,
                Failure(
                    CONTROL_PLANE,
                    decision.line_of("status"),
                    f'decision "{ident}" status {status}',
                    f"one of {', '.join(sorted(DECISION_STATUSES))}",
                ),
            )
        if status in DECISION_STATUSES_NEEDING_AN_ANSWER and not _text_of(
            decision.first("owner_answer")
        ):
            report.add(
                answered,
                Failure(
                    CONTROL_PLANE,
                    decision.line_of("status"),
                    f'decision "{ident}" is {status} with owner_answer null',
                    "the owner's answer, recorded verbatim",
                ),
            )


def _check_metric_dependencies(
    report: Report,
    metrics: list[MappingNode],
    blockers: list[MappingNode],
    decisions: list[MappingNode],
) -> None:
    check = report.check(
        "every_metric_dependency_names_something_the_control_plane_declares",
        "depends_on pointing at nothing hides a broken measurement chain",
    )
    # `depends_on` is used three ways: another metric, a specific blocker or
    # decision, and the bare kind of thing that is in the way. All three must
    # resolve to something declared, so a typo is still caught.
    known = {_text_of(m.first("id")) for m in metrics}
    known |= {_text_of(b.first("id")) for b in blockers}
    known |= {_text_of(d.first("id")) for d in decisions}
    known |= BLOCKER_KINDS
    for metric in metrics:
        ident = _text_of(metric.first("id")) or "<no id>"
        depends = _text_of(metric.first("depends_on"))
        if depends is None:
            continue
        if depends not in known:
            report.add(
                check,
                Failure(
                    CONTROL_PLANE,
                    metric.line_of("depends_on"),
                    f'metric "{ident}" depends on "{depends}", which is not declared',
                    "a metric, blocker or decision id, or ENVIRONMENT or OWNER",
                ),
            )


def _check_metric_statuses(
    report: Report, metrics: list[MappingNode], statuses: list[str]
) -> None:
    check = report.check(
        "every_metric_status_is_in_the_allowed_vocabulary",
        "metrics report progress with the same words as phases",
    )
    for metric in metrics:
        ident = _text_of(metric.first("id")) or "<no id>"
        status = _text_of(metric.first("status"))
        if status is not None and status not in statuses:
            report.add(
                check,
                Failure(
                    CONTROL_PLANE,
                    metric.line_of("status"),
                    f'metric "{ident}" status {status}',
                    f"one of {', '.join(statuses)}",
                ),
            )


# ---------------------------------------------------------------------------
# Checks across the secondary documents
# ---------------------------------------------------------------------------


def _phase_mentions(line: str) -> list[tuple[str, int]]:
    """Every phase named on the line, with the column its mention ends at."""
    found: list[tuple[str, int]] = []
    for match in PHASE_MENTION.finditer(line):
        found.append((match.group("first").upper(), match.end("first")))
        if match.group("plural"):
            for tail in PHASE_TAIL_ID.finditer(match.group("rest")):
                found.append((tail.group(0).upper(), match.start("rest") + tail.end()))
    return found


def _phase_ids_on(line: str) -> list[str]:
    return [ident for ident, _ in _phase_mentions(line)]


def _status_positions(line: str) -> list[tuple[int, int]]:
    """Spans of the line where a word is being used as a status, not prose."""
    spans: list[tuple[int, int]] = []
    for pattern in (r"`([^`]*)`", r"\*\*([^*]*)\*\*", r"[:=]\s*([^|`*]+)"):
        spans.extend(m.span(1) for m in re.finditer(pattern, line))
    for match in re.finditer(r"(?i)\bstatus\b(.{0,40})", line):
        spans.append(match.span(1))
    offset = 0
    for cell in line.split("|"):
        cleaned = cell.strip().strip("*").strip("`").strip()
        if cleaned and " " not in cleaned:
            start = offset + cell.index(cleaned) if cleaned in cell else offset
            spans.append((start, start + len(cleaned)))
        offset += len(cell) + 1
    return spans


def _status_tokens(line: str, allowed: frozenset[str]) -> list[str]:
    spans = _status_positions(line)
    out: list[str] = []
    for match in UPPER_TOKEN.finditer(line):
        token = match.group(0)
        if len(token) < 4 or token in NON_STATUS_WORDS:
            continue
        if FILE_EXTENSION.match(line, match.end()):
            continue  # ARCHITECTURE.md is a filename, not a status
        if token in allowed or token in KNOWN_STATUS_WORDS:
            out.append(token)
            continue
        start, end = match.span()
        if any(s <= start and end <= e for s, e in spans):
            out.append(token)
    return out


def _documents(root: Path, names: tuple[str, ...]) -> list[tuple[str, list[str]]]:
    out: list[tuple[str, list[str]]] = []
    for name in names:
        path = root / name
        if path.exists():
            out.append((name, _lines_of(path)))
    return out


def _check_document_phase_statuses(
    report: Report,
    root: Path,
    canonical: dict[str, set[str]],
    described: dict[str, str],
    statuses: list[str],
) -> None:
    allowed = frozenset(statuses)
    contradiction = report.check(
        "no_document_contradicts_the_control_plane_on_a_phase_status",
        "a second document asserting a different status for a phase",
    )
    vocabulary = report.check(
        "no_document_uses_a_phase_status_outside_the_allowed_vocabulary",
        "COMPLETE, DONE and friends are not statuses this project has",
    )
    unknown = report.check(
        "no_document_asserts_a_status_for_a_phase_the_control_plane_omits",
        "a phase nobody tracks is a phase nobody can be held to",
    )
    for name, lines in _documents(root, TRACKED_DOCUMENTS):
        for number, line in enumerate(lines, start=1):
            phase_ids = _phase_ids_on(line)
            if not phase_ids:
                continue
            for token in _status_tokens(line, allowed):
                for phase_id in phase_ids:
                    if token not in allowed:
                        report.add(
                            vocabulary,
                            Failure(
                                name,
                                number,
                                f"Phase {phase_id} asserted as {token}",
                                f"one of {', '.join(statuses)}",
                            ),
                        )
                    elif phase_id not in canonical:
                        report.add(
                            unknown,
                            Failure(
                                name,
                                number,
                                f"Phase {phase_id} asserted as {token}",
                                "a phase the control plane declares",
                            ),
                        )
                    elif token not in canonical[phase_id]:
                        report.add(
                            contradiction,
                            Failure(
                                name,
                                number,
                                f"Phase {phase_id} asserted as {token}",
                                f"{described[phase_id]}, per {CONTROL_PLANE}",
                            ),
                        )


# How far before a prose claim a phase mention may sit and still be what the
# claim is about. Beyond this, or across a sentence or table-cell boundary, the
# two are unrelated - "Phases 3-8 have not started. Phase 9 has been built" must
# not read as a claim about Phase 9.
PROSE_REACH = 80
SENTENCE_BREAK = re.compile(r"[.;|]")


def _check_document_prose(
    report: Report,
    root: Path,
    canonical: dict[str, set[str]],
    described: dict[str, str],
) -> None:
    check = report.check(
        "no_document_says_in_prose_what_the_control_plane_denies",
        "'Phase 3 is complete' in English is still a status claim",
    )
    compiled = [
        (re.compile(p, re.IGNORECASE), ok, label) for p, ok, label in PROSE_CLAIMS
    ]
    for name, lines in _documents(root, TRACKED_DOCUMENTS):
        for number, line in enumerate(lines, start=1):
            mentions = [(p, e) for p, e in _phase_mentions(line) if p in canonical]
            if not mentions:
                continue
            masked = line
            for pattern, acceptable, label in compiled:
                for match in list(pattern.finditer(masked)):
                    start, end = match.span()
                    masked = masked[:start] + " " * (end - start) + masked[end:]
                    for phase_id, mention_end in mentions:
                        # The claim must come AFTER the mention it describes.
                        if start < mention_end:
                            continue
                        gap = line[mention_end:start]
                        if len(gap) > PROSE_REACH:
                            continue
                        if SENTENCE_BREAK.search(gap):
                            continue
                        if canonical[phase_id] & acceptable:
                            continue
                        report.add(
                            check,
                            Failure(
                                name,
                                number,
                                f'Phase {phase_id} described as "{label}"',
                                f"{described[phase_id]}, per {CONTROL_PLANE}",
                            ),
                        )


def _numbers_match(text: str, wanted: set[str]) -> bool:
    found = {_normalise_number(m.group(0)) for m in NUMBER.finditer(text)}
    return bool(found & wanted)


def _normalise_number(text: str) -> str:
    try:
        value = float(text)
    except ValueError:
        return text
    if value == int(value):
        return str(int(value))
    return repr(round(value, 6))


def _check_document_metrics(
    report: Report, root: Path, metrics: list[MappingNode]
) -> None:
    check = report.check(
        "no_document_contradicts_the_control_plane_on_a_metric_value",
        "the same metric id carrying a different number in two places",
    )
    wanted: dict[str, set[str]] = {}
    for metric in metrics:
        ident = _text_of(metric.first("id"))
        if ident is None:
            continue
        values: set[str] = set()
        for key in ("current", "target"):
            text = _text_of(metric.first(key))
            if text is not None:
                values.add(_normalise_number(text))
        if values:
            wanted[ident] = values
    if not wanted:
        return
    for name, lines in _documents(root, TRACKED_DOCUMENTS):
        for number, line in enumerate(lines, start=1):
            for ident, values in wanted.items():
                if not re.search(rf"\b{re.escape(ident)}\b", line):
                    continue
                if not NUMBER.search(line.replace(ident, " ")):
                    continue
                if _numbers_match(line.replace(ident, " "), values):
                    continue
                report.add(
                    check,
                    Failure(
                        name,
                        number,
                        f"{ident} quoted with none of {', '.join(sorted(values))}",
                        f"one of {', '.join(sorted(values))}, per {CONTROL_PLANE}",
                    ),
                )


def _check_documented_decisions(
    report: Report, root: Path, decisions: list[MappingNode]
) -> None:
    check = report.check(
        "every_owner_decision_written_up_in_the_docs_is_in_the_control_plane",
        "a decision that exists only in prose is a decision nothing tracks",
    )
    known = {_text_of(d.first("id")) for d in decisions}
    for name, lines in _documents(root, DECISION_DOCUMENTS):
        seen: set[str] = set()
        for number, line in enumerate(lines, start=1):
            for match in DECISION_ID.finditer(line):
                ident = match.group(0)
                if ident in known or ident in seen:
                    continue
                seen.add(ident)
                report.add(
                    check,
                    Failure(
                        name,
                        number,
                        f"decision {ident} is written up here",
                        f"{ident} declared in {CONTROL_PLANE}",
                    ),
                )


# ---------------------------------------------------------------------------
# The allow-list, and the checks that keep it narrow
# ---------------------------------------------------------------------------


def _load_allow_list(path: Path) -> tuple[list[AllowEntry], str | None]:
    if not path.exists():
        return [], None
    try:
        raw: object = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        return [], f"{path.name} is not valid JSON: {error}"
    if not isinstance(raw, list):
        return [], f"{path.name} must contain a JSON list of entries"
    entries: list[AllowEntry] = []
    items: list[object] = list(raw)
    for index, item in enumerate(items):
        if not isinstance(item, dict):
            return [], f"{path.name} entry {index} is not an object"
        mapping = cast("dict[str, object]", item)
        document = mapping.get("document")
        line = mapping.get("line")
        fragment = mapping.get("fragment")
        reason = mapping.get("reason")
        entries.append(
            AllowEntry(
                document=document if isinstance(document, str) else "",
                line=line
                if isinstance(line, int) and not isinstance(line, bool)
                else 0,
                fragment=fragment if isinstance(fragment, str) else "",
                reason=reason if isinstance(reason, str) else "",
                index=index,
            )
        )
    return entries, None


def _check_allow_list(report: Report, root: Path, source: str) -> None:
    narrow = report.check(
        "every_allow_list_entry_pins_one_line_and_says_why",
        "no entry may name a whole file, and none may omit its reason",
    )
    for entry in report.allow:
        where = f"{source} entry {entry.index}"
        if not entry.document or entry.document not in ALLOW_LIST_DOCUMENTS:
            report.add(
                narrow,
                Failure(
                    source,
                    0,
                    f"{where} names document {entry.document!r}",
                    f"one of {', '.join(sorted(ALLOW_LIST_DOCUMENTS))}",
                ),
            )
        if any(ch in entry.document for ch in "*?[]"):
            report.add(
                narrow,
                Failure(
                    source,
                    0,
                    f"{where} uses a glob in the document name",
                    "an exact path - a whole file can never be suppressed",
                ),
            )
        if entry.line < 1:
            report.add(
                narrow,
                Failure(
                    source,
                    0,
                    f"{where} has line {entry.line}",
                    "a 1-based line number - an entry pins one line, not a file",
                ),
            )
        if len(entry.fragment) < 8:
            report.add(
                narrow,
                Failure(
                    source,
                    0,
                    f"{where} fragment is {len(entry.fragment)} characters",
                    "at least 8 characters, so the entry pins the actual text",
                ),
            )
        if len(entry.reason) < 20:
            report.add(
                narrow,
                Failure(
                    source,
                    0,
                    f"{where} reason is {len(entry.reason)} characters",
                    "at least 20 characters of written reason",
                ),
            )

    matching = report.check(
        "every_allow_list_entry_still_matches_the_line_it_pins",
        "an entry whose text has moved is silently suppressing something else",
    )
    for entry in report.allow:
        if entry.line < 1 or not entry.fragment:
            continue
        text = _read_line(root / entry.document, entry.line)
        if text is None:
            report.add(
                matching,
                Failure(
                    source,
                    0,
                    f"{entry.document}:{entry.line} does not exist",
                    "a line that is really there",
                ),
            )
        elif entry.fragment not in text:
            report.add(
                matching,
                Failure(
                    source,
                    0,
                    f"{entry.document}:{entry.line} no longer contains the fragment",
                    f"a line containing {entry.fragment!r}",
                ),
            )


def _check_allow_list_is_alive(report: Report, source: str) -> None:
    check = report.check(
        "no_allow_list_entry_is_suppressing_nothing",
        "a dead entry is an exemption nobody is reviewing",
    )
    for entry in report.allow:
        if entry.index not in report.used_allow_entries:
            report.add(
                check,
                Failure(
                    source,
                    0,
                    f"entry {entry.index} for {entry.document}:{entry.line} "
                    f"suppresses nothing",
                    "delete it - the contradiction it covered is gone",
                ),
            )


# ---------------------------------------------------------------------------
# Driver
# ---------------------------------------------------------------------------


def validate(root: Path, allow_list_path: Path | None) -> Report:
    report = Report(root)

    source = ALLOW_LIST if allow_list_path is None else str(allow_list_path)
    resolved = root / ALLOW_LIST if allow_list_path is None else allow_list_path
    entries, allow_error = _load_allow_list(resolved)
    report.allow = entries

    readable = report.check(
        "the_allow_list_is_readable",
        "an unreadable allow-list must not be treated as an empty one",
    )
    if allow_error is not None:
        report.add(readable, Failure(source, 1, allow_error, "a JSON list of entries"))

    exists = report.check(
        "the_control_plane_exists",
        f"{CONTROL_PLANE} is the one source of truth and must be there",
    )
    path = root / CONTROL_PLANE
    if not path.exists():
        report.add(
            exists,
            Failure(
                CONTROL_PLANE,
                0,
                f"{path} does not exist",
                f"a control plane at {CONTROL_PLANE}",
            ),
        )
        return report

    parses = report.check(
        "the_control_plane_parses",
        "the file is in the YAML subset this validator reads, exactly",
    )
    try:
        parsed = _parse_yaml(path.read_text(encoding="utf-8"))
    except YamlError as error:
        report.add(
            parses,
            Failure(CONTROL_PLANE, error.line, error.message, "parseable YAML"),
        )
        return report
    if not isinstance(parsed, MappingNode):
        report.add(
            parses,
            Failure(CONTROL_PLANE, 1, "the document is not a mapping", "a mapping"),
        )
        return report

    statuses = _check_shape(report, parsed)
    phases = _mappings_under(parsed, "phases")
    metrics = _mappings_under(parsed, "metrics")
    decisions = _mappings_under(parsed, "decisions")
    blockers = _mappings_under(parsed, "blockers")
    gates = _mappings_under(parsed, "launch_gates")

    _check_unique_ids(report, phases, "phase", "no_phase_id_is_declared_twice")
    _check_unique_ids(report, metrics, "metric", "no_metric_id_is_declared_twice")
    _check_unique_ids(report, blockers, "blocker", "no_blocker_id_is_declared_twice")
    _check_unique_ids(report, decisions, "decision", "no_decision_id_is_declared_twice")
    _check_unique_ids(
        report, gates, "launch gate", "no_launch_gate_id_is_declared_twice"
    )

    _check_single_valued(
        report, phases, "phase", ("status", "evidence"), "no_phase_has_two_statuses"
    )
    _check_single_valued(
        report,
        metrics,
        "metric",
        ("current", "target"),
        "no_metric_has_two_currents_or_two_targets",
    )

    _check_phase_statuses(report, phases, statuses)
    _check_phase_evidence(report, phases)
    _check_exit_criteria(report, phases)
    _check_blockers(report, blockers)
    _check_blocker_references(report, phases, blockers)
    _check_launch_gates(report, gates)
    _check_decisions(report, decisions)
    _check_metric_dependencies(report, metrics, blockers, decisions)
    _check_metric_statuses(report, metrics, statuses)

    # A phase the control plane splits - "5-implementation" and "5-live" - is
    # still called "Phase 5" in prose, and prose naming it is not wrong. So the
    # family key "5" accepts either of its members' statuses.
    canonical: dict[str, set[str]] = {}
    described: dict[str, str] = {}
    for phase in phases:
        ident = _text_of(phase.first("id"))
        status = _text_of(phase.first("status"))
        if ident is None or status is None:
            continue
        for key in {ident.upper(), ident.upper().split("-")[0]}:
            canonical.setdefault(key, set()).add(status)
    for key, values in canonical.items():
        described[key] = (
            next(iter(values))
            if len(values) == 1
            else "one of " + ", ".join(sorted(values))
        )

    _check_document_phase_statuses(report, root, canonical, described, statuses)
    _check_document_prose(report, root, canonical, described)
    _check_document_metrics(report, root, metrics)
    _check_documented_decisions(report, root, decisions)

    _check_allow_list(report, root, source)
    _check_allow_list_is_alive(report, source)
    return report


def render(report: Report, root: Path) -> str:
    out: list[str] = []
    out.append("project truth validation")
    out.append(f"  root:          {root}")
    out.append(f"  control plane: {CONTROL_PLANE}")
    out.append("")
    for check in report.checks:
        if check.skipped is not None:
            out.append(f"SKIP  {check.name}")
            out.append(f"        {check.skipped}")
            continue
        if check.ok:
            suffix = ""
            if check.suppressed:
                suffix = f"  ({check.suppressed} suppressed by the allow-list)"
            out.append(f"PASS  {check.name}{suffix}")
            continue
        out.append(f"FAIL  {check.name}")
        out.append(f"        {check.what_it_proves}")
        for failure in check.failures:
            where = failure.document
            if failure.line:
                where = f"{failure.document}:{failure.line}"
            out.append(f"        {where}")
            out.append(f"          found:    {failure.found}")
            out.append(f"          expected: {failure.expected}")
    passed = sum(1 for c in report.checks if c.ok)
    failed = sum(1 for c in report.checks if not c.ok and c.skipped is None)
    out.append("")
    out.append(f"{len(report.checks)} checks, {passed} passed, {failed} failed")
    return "\n".join(out)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Fail when project truth contradicts itself.",
    )
    parser.add_argument(
        "--root",
        type=Path,
        default=DEFAULT_ROOT,
        help="tree to validate (default: the repository root)",
    )
    parser.add_argument(
        "--allow-list",
        type=Path,
        default=None,
        help=f"allow-list JSON (default: <root>/{ALLOW_LIST} when present)",
    )
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    args = parser.parse_args(argv)

    root: Path = args.root.resolve()
    allow_list: Path | None = args.allow_list
    _LINE_CACHE.clear()
    report = validate(root, allow_list)

    if args.json:
        payload = {
            "ok": report.ok,
            "root": str(root),
            "control_plane": CONTROL_PLANE,
            "checks": [c.as_dict() for c in report.checks],
        }
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        print(render(report, root))
    return 0 if report.ok else 1


if __name__ == "__main__":
    sys.exit(main())
