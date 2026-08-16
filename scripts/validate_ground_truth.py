#!/usr/bin/env python3
"""Phase 8 PR-1 — refuse a ground-truth corpus that has stopped being true.

WHY A SEPARATE SCRIPT AND NOT JUST A TEST
-----------------------------------------
`scripts/build_ground_truth.py` writes the corpus. Anything that only ever
checks the builder's own output in the same process proves that the builder
agrees with itself. This reads what is COMMITTED, from disk, and re-derives
every claim in it from the canonical truth beside it.

THE FAILURE THIS EXISTS FOR
---------------------------
Somebody runs a reader, sees it disagree with the corpus, and "fixes" the
corpus by pasting the reader's answer into `expected`. Every test still passes.
The benchmark now measures nothing at all, and it looks exactly like a
benchmark that works. There is no red anywhere.

It is made mechanically detectable by locking three things into a ring:

    raw_fields  --generation_hash-->  the stored hash
    raw_fields  --expected_from()-->  the stored expected
    raw_fields  --render()-------->   the stored document bytes (sha256)

`expected` is not a stored opinion; it is a PURE PROJECTION of `raw_fields`
computed by exactly one function, `build_ground_truth.expected_from`. Pasting a
reader's answer into it makes the recomputation differ, and `EXPECTED_NOT_FROM_
TRUTH` names the case and the field. Editing `raw_fields` to match the reader
instead breaks the other two links, and the stale hash and the stale document
are named separately. There is no edit to one file that leaves the ring intact.

THE CONSERVATION LAW
--------------------
`subtotal + tax_amount == total`, and where a case has line items,
`sum(line_items) == subtotal`. Two quantities that must be equal need no
expert, no label and no reader: they hold or they do not. A discount is carried
as a NEGATIVE line item precisely so the law keeps holding rather than
acquiring an exception.

WHAT THIS DOES NOT PROVE
------------------------
That the documents are readable by any real backend. It checks the corpus, not
a reader. Whether a PDF opens in a viewer is `NOT_MEASURED` — no library is
permitted to look.

That `rule_ids` are right. `artifacts/ground_truth/rules/` belongs to another
workstream. Until it exists, a non-empty `rule_ids` is a dangling reference and
is a failure; once it exists, every id must resolve inside it.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any, Final, TypeGuard

_REPO = Path(__file__).resolve().parent.parent
if str(_REPO) not in sys.path:
    # Run as `python scripts/validate_ground_truth.py`, the repo root is not on
    # the path and the sibling module cannot be imported. Under pytest it
    # already is, so this is a no-op there. Stated rather than left to luck.
    sys.path.insert(0, str(_REPO))

from scripts.build_ground_truth import (  # noqa: E402
    CASE_COUNT,
    CATEGORIES,
    CORPUS_ROOT,
    GENERATION_VERSION,
    INPUT_TYPES,
    Case,
    build_cases,
    case_record,
    expected_from,
    generation_hash,
    load_records,
    render,
    sha256_of,
)

#: Every key a case file must carry. Absent is a failure; empty is a failure
#: too, except for the two list fields that are legitimately empty today.
REQUIRED_KEYS: Final = (
    "case_id",
    "source_label",
    "corpus_label",
    "input_type",
    "category",
    "mime",
    "document",
    "raw_fields",
    "gst_context",
    "expected",
    "rule_ids",
    "source_urls",
    "generation_version",
    "created_at",
    "generation_hash",
    "sha256",
)

REQUIRED_EXPECTED_FIELDS: Final = (
    "date",
    "party",
    "total_amount",
    "tax_amount",
    "line_items",
)

REQUIRED_RAW_FIELDS: Final = (
    "date",
    "party",
    "subtotal",
    "tax_amount",
    "total",
    "line_items",
)

MAY_BE_EMPTY: Final = frozenset({"rule_ids", "source_urls"})


@dataclass(frozen=True)
class Finding:
    kind: str
    case_id: str
    detail: str

    def __str__(self) -> str:
        return f"{self.kind}  {self.case_id}  {self.detail}"


# A case file is decoded JSON, so every value in it is `object` until something
# checks. `isinstance(x, dict)` on its own narrows only as far as
# `dict[Unknown, Unknown]`, which makes every later `.get`, `.items()` and loop
# variable unknown in turn - that is where all 25 of this file's strict errors
# came from. These three are the same checks the code already made, stated once
# so the narrowed shape survives.
def _is_obj(value: object) -> TypeGuard[dict[str, object]]:
    """A decoded JSON object. Its keys are strings by construction."""
    return isinstance(value, dict)


def _is_seq(value: object) -> TypeGuard[list[object]]:
    """A decoded JSON array."""
    return isinstance(value, list)


def _obj(value: object) -> dict[str, object]:
    """The object, or an empty one. Reads a value that SHOULD be an object."""
    return value if _is_obj(value) else {}


def _decimal(value: object, where: str) -> Decimal:
    try:
        return Decimal(str(value))
    except (ArithmeticError, ValueError) as exc:
        raise ValueError(f"{where} is not a number: {value!r}") from exc


def check_shape(record: dict[str, Any]) -> list[Finding]:
    case_id = str(record.get("case_id", "<no case_id>"))
    out: list[Finding] = []
    for key in REQUIRED_KEYS:
        if key not in record:
            out.append(Finding("MISSING_KEY", case_id, f"no {key!r}"))
        elif key not in MAY_BE_EMPTY and record[key] in ("", None, {}):
            out.append(Finding("EMPTY_KEY", case_id, f"{key!r} is empty"))

    expected = record.get("expected")
    if isinstance(expected, dict):
        for name in REQUIRED_EXPECTED_FIELDS:
            if name not in expected:
                out.append(
                    Finding("MISSING_EXPECTED_FIELD", case_id, f"expected.{name}")
                )
    else:
        out.append(
            Finding("MISSING_EXPECTED_FIELD", case_id, "expected is not an object")
        )

    raw = record.get("raw_fields")
    if isinstance(raw, dict):
        for name in REQUIRED_RAW_FIELDS:
            if name not in raw:
                out.append(Finding("MISSING_CANONICAL_TRUTH", case_id, f"raw.{name}"))
    else:
        out.append(
            Finding("MISSING_CANONICAL_TRUTH", case_id, "raw_fields is not an object")
        )

    if not str(record.get("generation_hash", "")).strip():
        out.append(Finding("MISSING_HASH", case_id, "generation_hash"))
    if not str(record.get("sha256", "")).strip():
        out.append(Finding("MISSING_HASH", case_id, "sha256"))
    if str(record.get("generation_version", "")) != GENERATION_VERSION:
        out.append(
            Finding(
                "GENERATION_VERSION",
                case_id,
                f"{record.get('generation_version')!r} != {GENERATION_VERSION!r}",
            )
        )
    if not str(record.get("source_label", "")).strip():
        out.append(Finding("MISSING_SOURCE_LABEL", case_id, "source_label"))
    if not str(record.get("corpus_label", "")).strip():
        out.append(Finding("MISSING_SOURCE_LABEL", case_id, "corpus_label"))
    return out


def check_expected_is_derived(record: dict[str, Any]) -> list[Finding]:
    """The one that catches a reader's answer pasted into `expected`."""
    case_id = str(record.get("case_id", "<no case_id>"))
    raw = record.get("raw_fields")
    expected = record.get("expected")
    if not _is_obj(raw) or not _is_obj(expected):
        return []
    derived = expected_from({str(k): v for k, v in raw.items()})
    out: list[Finding] = []
    for name, wanted in derived.items():
        if expected.get(name) != wanted:
            out.append(
                Finding(
                    "EXPECTED_NOT_FROM_TRUTH",
                    case_id,
                    f"expected.{name} is {expected.get(name)!r}; the canonical "
                    f"truth says {wanted!r}. `expected` is a projection of "
                    f"`raw_fields`, never a reader's answer",
                )
            )
    return out


def check_hashes(record: dict[str, Any], root: Path) -> list[Finding]:
    case_id = str(record.get("case_id", "<no case_id>"))
    raw = record.get("raw_fields")
    gst = record.get("gst_context")
    out: list[Finding] = []
    if _is_obj(raw) and _is_obj(gst):
        recomputed = generation_hash(
            {str(k): v for k, v in raw.items()},
            {str(k): str(v) for k, v in gst.items()},
        )
        if recomputed != record.get("generation_hash"):
            out.append(
                Finding(
                    "STALE_GENERATION_HASH",
                    case_id,
                    "raw_fields or gst_context changed and the case was never "
                    "rebuilt; the stored hash is for different truth",
                )
            )

    document = root / "documents" / str(record.get("document", ""))
    if not document.exists():
        out.append(Finding("MISSING_DOCUMENT", case_id, str(document)))
        return out
    data = document.read_bytes()
    if sha256_of(data) != record.get("sha256"):
        out.append(
            Finding(
                "STALE_DOCUMENT_HASH",
                case_id,
                f"{document.name} on disk does not hash to the recorded sha256",
            )
        )
    return out


def check_rules(record: dict[str, Any], root: Path) -> list[Finding]:
    case_id = str(record.get("case_id", "<no case_id>"))
    ids = record.get("rule_ids")
    if not _is_seq(ids):
        return [Finding("MISSING_RULE_REFERENCE", case_id, "rule_ids is not a list")]
    if not ids:
        return []
    rules_dir = root / "rules"
    if not rules_dir.exists():
        return [
            Finding(
                "MISSING_RULE_REFERENCE",
                case_id,
                f"cites {ids} but {rules_dir} does not exist, so every one of "
                "them is a dangling reference",
            )
        ]
    known = {p.stem for p in rules_dir.glob("*.json")}
    return [
        Finding(
            "MISSING_RULE_REFERENCE", case_id, f"rule {rid!r} is not in {rules_dir}"
        )
        for rid in ids
        if str(rid) not in known
    ]


def check_arithmetic(record: dict[str, Any]) -> list[Finding]:
    """The conservation law. It holds or it does not; no expert required."""
    case_id = str(record.get("case_id", "<no case_id>"))
    raw = record.get("raw_fields")
    if not _is_obj(raw):
        return []
    try:
        subtotal = _decimal(raw.get("subtotal"), "subtotal")
        tax = _decimal(raw.get("tax_amount"), "tax_amount")
        total = _decimal(raw.get("total"), "total")
    except ValueError as exc:
        return [Finding("UNBALANCED", case_id, str(exc))]

    out: list[Finding] = []
    if subtotal + tax != total:
        out.append(
            Finding(
                "UNBALANCED",
                case_id,
                f"subtotal {subtotal} + tax {tax} = {subtotal + tax}, "
                f"but total says {total}",
            )
        )
    items = raw.get("line_items")
    if _is_seq(items) and items:
        summed = sum(
            (_decimal(_obj(i).get("amount"), "line amount") for i in items), Decimal(0)
        )
        if summed != subtotal:
            out.append(
                Finding(
                    "UNBALANCED",
                    case_id,
                    f"line items sum to {summed}, subtotal says {subtotal}",
                )
            )
    return out


def check_documents_are_a_rebuild(root: Path) -> list[Finding]:
    """Third link in the ring: the bytes are what this truth renders to.

    Both directions are needed, and missing the second was a real hole found
    while proving the first. `DOCUMENT_NOT_A_REBUILD` catches an edited
    document. It does NOT catch an edited case file, because the document on
    disk still matches what the BUILDER renders — so somebody who changes
    `raw_fields`, recomputes `generation_hash` and re-derives `expected` would
    leave a self-consistent case file describing a document that says something
    else. `CASE_NOT_A_REBUILD` closes that: this corpus is generated, so every
    case file must equal what the generator produces for it.
    """
    by_id: dict[str, Case] = {case.case_id: case for case in build_cases()}
    out: list[Finding] = []
    for record in load_records(root):
        case_id = str(record["case_id"])
        case = by_id.get(case_id)
        if case is None:
            out.append(Finding("UNKNOWN_CASE", case_id, "no such case in the builder"))
            continue
        path = root / "documents" / str(record["document"])
        rendered = render(case)
        if path.exists() and path.read_bytes() != rendered:
            out.append(
                Finding(
                    "DOCUMENT_NOT_A_REBUILD",
                    case_id,
                    f"{path.name} is not what the canonical truth renders to",
                )
            )
        canonical = case_record(case, rendered)
        differing = sorted(
            key
            for key in set(canonical) | set(record)
            if record.get(key) != canonical.get(key)
        )
        if differing:
            out.append(
                Finding(
                    "CASE_NOT_A_REBUILD",
                    case_id,
                    f"these keys are not what the generator produces: {differing}",
                )
            )
    return out


def check_corpus(root: Path = CORPUS_ROOT) -> list[Finding]:
    records = load_records(root)
    out: list[Finding] = []

    if len(records) != CASE_COUNT:
        out.append(
            Finding(
                "CASE_COUNT", "<corpus>", f"{len(records)} cases, want {CASE_COUNT}"
            )
        )

    seen: dict[str, int] = {}
    for record in records:
        case_id = str(record.get("case_id", "<no case_id>"))
        seen[case_id] = seen.get(case_id, 0) + 1
    out.extend(
        Finding("DUPLICATE_CASE_ID", case_id, f"appears {n} times")
        for case_id, n in sorted(seen.items())
        if n > 1
    )

    for record in records:
        out.extend(check_shape(record))
        out.extend(check_expected_is_derived(record))
        out.extend(check_hashes(record, root))
        out.extend(check_rules(record, root))
        out.extend(check_arithmetic(record))
    out.extend(check_documents_are_a_rebuild(root))

    by_type = dict.fromkeys(INPUT_TYPES, 0)
    by_category = dict.fromkeys(CATEGORIES, 0)
    for record in records:
        kind = str(record.get("input_type", ""))
        category = str(record.get("category", ""))
        if kind in by_type:
            by_type[kind] += 1
        else:
            out.append(Finding("UNKNOWN_INPUT_TYPE", str(record["case_id"]), kind))
        if category in by_category:
            by_category[category] += 1
        else:
            out.append(Finding("UNKNOWN_CATEGORY", str(record["case_id"]), category))
    want = CASE_COUNT // len(INPUT_TYPES)
    out.extend(
        Finding("TYPE_COUNT", "<corpus>", f"{kind}: {n} cases, want {want}")
        for kind, n in by_type.items()
        if n != want
    )
    out.extend(
        Finding("CATEGORY_COUNT", "<corpus>", f"{name}: {n} cases, want {want}")
        for name, n in by_category.items()
        if n != want
    )
    return out


def validate(root: Path = CORPUS_ROOT) -> tuple[bool, list[str]]:
    """The contract `scripts/run_ground_truth.py` documents, implemented here.

    That runner's `pack_validator()` docstring asks for

        validate(root: pathlib.Path) -> object

    and accepts a `(ok, failures)` pair as one of three shapes. This file only
    ever exposed `check_corpus(root) -> list[Finding]` and `main(argv)`, so the
    runner bound to `main`, handed it a `Path`, and argparse died on
    `list(PosixPath)`. The whole pack then reported INVALIDATED and measured
    nothing. Two halves, two contracts, and the name matched while the signature
    did not.

    Returning the documented pair is the fix on this side. The other half of it
    is that the runner no longer accepts `main` as an implementation of
    anything: every script has a `main`, and its signature is never the contract.
    """
    findings = check_corpus(root)
    return not findings, [f"{f.kind} {f.case_id}: {f.detail}" for f in findings]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate the ground-truth corpus.")
    parser.add_argument("--root", type=Path, default=CORPUS_ROOT)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    findings = check_corpus(Path(args.root))
    if args.json:
        print(
            json.dumps(
                {
                    "ok": not findings,
                    "findings": [
                        {"kind": f.kind, "case_id": f.case_id, "detail": f.detail}
                        for f in findings
                    ],
                },
                indent=2,
            )
        )
    else:
        for finding in findings:
            print(finding)
        print(f"findings: {len(findings)}")
    return 1 if findings else 0


if __name__ == "__main__":
    sys.exit(main())
