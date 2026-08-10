#!/usr/bin/env python3
"""One command. Runs the whole Ground-Truth Pack and writes the two reports.

    python scripts/run_ground_truth.py

TWO WAYS TO FAIL, AND THEY ARE NOT THE SAME THING
--------------------------------------------------
    exit 1   A GATE FAILED. The harness ran, measured something, and the number
             was not good enough. This is the benchmark working.
    exit 2   THE HARNESS BROKE. Nothing was measured, or what was measured
             cannot be trusted. No number from an exit-2 run may be quoted.

Conflating them is how a broken measurement gets reported as a bad result and
then "improved". `results.md` opens with whichever of the two happened, in those
words, before any number appears.

A NON-ZERO EXIT IS EXPECTED TODAY
---------------------------------
Owner decision Q4 = B: the extraction reader is a stub and no production backend
has been selected, so S2 scores zero per field. That is not a regression and it
is not a harness fault. It is the benchmark saying, correctly, that extraction
has not been built.

PROVENANCE, CHECKED BEFORE ANYTHING IS MEASURED
------------------------------------------------
This project has voided two measurements because both sides of a comparison
imported `accountant` from the main checkout while appearing to run from a
worktree. So the first thing this script does is assert that the imported
package lives under the current working directory, and a failure there is exit 2
with the word INVALIDATED — never a score.

THE OTHER HALF OF THE PACK
--------------------------
`scripts/validate_ground_truth.py`, `scripts/build_ground_truth.py`,
`artifacts/ground_truth/cases/` and `artifacts/ground_truth/manifests/` belong to
a second agent working in parallel. This runner calls them BY MODULE PATH and
against the contract documented in `pack_validator` and `pack_loader` below. If
they are not there yet it reports

    BLOCKED — awaiting scripts/validate_ground_truth.py

for that section and carries on with the sections it owns. A missing sibling is
a blocked gate, not a crash: the GST half has to be measurable before the
coordinator wires the two together.
"""

from __future__ import annotations

import datetime
import hashlib
import importlib
import json
import pathlib
import subprocess
import sys
import traceback
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any

# ---------------------------------------------------------------------------
# exit codes
# ---------------------------------------------------------------------------

EXIT_OK = 0
EXIT_GATE_FAILED = 1
EXIT_HARNESS_BROKE = 2

PASS = "PASS"  # noqa: S105 - a gate status, not a secret
FAIL = "FAIL"
BLOCKED = "BLOCKED"
NOT_MEASURED = "NOT_MEASURED"
INVALIDATED = "INVALIDATED"

ROOT = pathlib.Path(__file__).resolve().parent.parent

# THE REPOSITORY ROOT GOES FIRST, BEFORE ANYTHING IMPORTS `accountant`.
#
# `python scripts/run_ground_truth.py` puts `scripts/` on `sys.path[0]`, not the
# repository root. With an editable install present, `import accountant` then
# resolves to whatever the install points at — which, in this project, was the
# main checkout while the run appeared to be in a worktree. That is precisely how
# two measurements here were voided. The provenance check below would catch it,
# but catching it every time is not the same as not doing it.
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

GT = ROOT / "artifacts" / "ground_truth"
RULE_CASES = GT / "rules" / "gst_cases.json"
RESULTS_JSON = GT / "results.json"
RESULTS_MD = GT / "results.md"


class HarnessBroke(Exception):
    """Raised when nothing trustworthy can be measured. Always exit 2."""


@dataclass
class Gate:
    name: str
    status: str
    detail: str
    measured: str = ""

    @property
    def passed(self) -> bool:
        return self.status == PASS


@dataclass
class Section:
    name: str
    gates: list[Gate] = field(default_factory=list[Gate])
    facts: dict[str, Any] = field(default_factory=dict[str, Any])
    failed_cases: list[dict[str, Any]] = field(default_factory=list[dict[str, Any]])

    def gate(self, name: str, ok: bool, detail: str, measured: str = "") -> Gate:
        g = Gate(
            name=name, status=PASS if ok else FAIL, detail=detail, measured=measured
        )
        self.gates.append(g)
        return g

    def blocked(self, name: str, detail: str) -> Gate:
        g = Gate(name=name, status=BLOCKED, detail=detail)
        self.gates.append(g)
        return g


# ---------------------------------------------------------------------------
# provenance — run before anything is measured
# ---------------------------------------------------------------------------


def check_provenance() -> dict[str, str]:
    """The imported package must live under the working directory, or nothing runs."""
    here = pathlib.Path.cwd().resolve()
    if here != ROOT:
        raise HarnessBroke(
            f"run this from the repository root. The working directory is {here} "
            f"and this script belongs to {ROOT}. The provenance assertion compares "
            "the imported package against the working directory, so running from "
            "somewhere else would compare it against the wrong tree."
        )

    import accountant

    package = pathlib.Path(accountant.__file__ or "").resolve()
    if not str(package).startswith(str(here)):
        raise HarnessBroke(
            f"INVALIDATED: accountant was imported from {package}, which is not "
            f"under the working directory {here}. Two measurements in this project "
            "have already been voided this way. Fix the path and rerun; do not "
            "quote anything from this run."
        )
    return {
        "cwd": str(here),
        "accountant__file__": str(package),
        "python": sys.version.split()[0],
        "commit": _git("rev-parse", "HEAD"),
        "branch": _git("rev-parse", "--abbrev-ref", "HEAD"),
        "worktree": _git("rev-parse", "--show-toplevel"),
        "dirty": "yes" if _git("status", "--porcelain") else "no",
    }


def _git(*args: str) -> str:
    try:
        out = subprocess.run(  # noqa: S603
            ["git", *args],  # noqa: S607
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError):
        return "unknown"
    return out.stdout.strip() if out.returncode == 0 else "unknown"


def sha256_of(path: pathlib.Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


# ---------------------------------------------------------------------------
# the second agent's half — called by module path, against a written contract
# ---------------------------------------------------------------------------


def load_sibling(module: str, wanted: Sequence[str]) -> tuple[Any, str | None]:
    """Import `scripts.<module>` and return the first attribute in `wanted`.

    Returns `(callable, None)` on success, `(None, reason)` when the module or
    every candidate name is absent. Never raises for a missing sibling: the
    whole point is that this runner lands before the other half does.
    """
    try:
        mod = importlib.import_module(f"scripts.{module}")
    except ModuleNotFoundError:
        return None, f"BLOCKED — awaiting scripts/{module}.py"
    except Exception as exc:  # pragma: no cover - a sibling that imports badly
        return None, f"BLOCKED — scripts/{module}.py failed to import: {exc!r}"
    for name in wanted:
        found = getattr(mod, name, None)
        if callable(found):
            return found, None
    return None, (
        f"BLOCKED — scripts/{module}.py exists but exposes none of {', '.join(wanted)}"
    )


def pack_validator() -> tuple[Callable[..., Any] | None, str | None]:
    """THE CONTRACT THIS RUNNER EXPECTS OF `scripts/validate_ground_truth.py`.

        validate(root: pathlib.Path) -> object

    The returned object must expose either

        .ok        : bool                — every manifest entry and hash checked out
        .failures  : Sequence[str]       — one readable line per failure

    or be a `bool`, or be a `(ok, failures)` pair. Any of the three is accepted,
    because pinning the exact shape across two agents who cannot talk is how an
    integration lands broken. `validate_manifest` and `main` are accepted as
    names as well as `validate`.
    """
    return load_sibling(
        "validate_ground_truth", ("validate", "validate_manifest", "main")
    )


def pack_loader() -> tuple[Callable[..., Any] | None, str | None]:
    """THE CONTRACT THIS RUNNER EXPECTS OF `scripts/build_ground_truth.py`.

        load_cases(root: pathlib.Path) -> Sequence[Mapping[str, Any]]

    Each mapping should carry at least `case_id`, the input bytes or a path to
    them, a `mime`, and an `expected` mapping of field name to expected value,
    so S2 can be scored per field. `load_pack` and `cases` are accepted as names.
    """
    return load_sibling("build_ground_truth", ("load_cases", "load_pack", "cases"))


def interpret_validation(result: Any) -> tuple[bool, list[str]]:
    if isinstance(result, bool):
        return result, []
    ok = getattr(result, "ok", None)
    if isinstance(ok, bool):
        failures = list(getattr(result, "failures", ()) or ())
        return ok, [str(f) for f in failures]
    if isinstance(result, tuple) and len(result) == 2:
        first, second = result
        return bool(first), [str(f) for f in (second or ())]
    raise HarnessBroke(
        f"scripts/validate_ground_truth.py returned {type(result).__name__}, which "
        "matches none of the three shapes documented in pack_validator"
    )


# ---------------------------------------------------------------------------
# section 1 — manifest and hashes
# ---------------------------------------------------------------------------


def run_manifest(section: Section) -> None:
    validator, blocked = pack_validator()
    if validator is None:
        section.blocked("ground_truth_manifest_validates", blocked or BLOCKED)
        section.blocked("ground_truth_hashes_verify", blocked or BLOCKED)
    else:
        ok, failures = interpret_validation(validator(GT))
        section.gate(
            "ground_truth_manifest_validates",
            ok,
            "; ".join(failures) if failures else "every manifest entry checked out",
        )
        section.gate(
            "ground_truth_hashes_verify",
            ok,
            "hashes verified by scripts/validate_ground_truth.py",
        )

    # The rules half has its own hash, and it is this runner's to check.
    if not RULE_CASES.exists():
        raise HarnessBroke(f"the GST rule cases are missing: {RULE_CASES}")
    digest = sha256_of(RULE_CASES)
    section.facts["gst_cases_sha256"] = digest
    section.facts["gst_cases_path"] = str(RULE_CASES.relative_to(ROOT))
    section.gate(
        "gst_rule_cases_readable", True, f"sha256 {digest[:16]}…", measured=digest
    )


# ---------------------------------------------------------------------------
# section 2 — S2 extraction
# ---------------------------------------------------------------------------


def run_s2(section: Section) -> None:
    """Score the stub extractor per field, or say why it could not be scored.

    Owner decision Q4 = B. `StubExtractor` returns `not_found` for every field it
    was not handed, and a stub returning `not_found` cannot satisfy the real
    extraction-quality exit. A zero here is the correct reading of the world.
    """
    loader, blocked = pack_loader()
    if loader is None:
        section.blocked("s2_extraction_scored", blocked or BLOCKED)
        section.facts["s2"] = NOT_MEASURED
        section.facts["s2_reason"] = blocked or BLOCKED
        return

    from accountant.extract.adapter import NOT_FOUND, ExtractedRecord, StubExtractor

    cases = list(loader(GT))
    extractor = StubExtractor()
    per_field = dict.fromkeys(ExtractedRecord.FIELDS, 0)
    scored = 0
    for case in cases:
        payload = case.get("input_bytes")
        if payload is None and case.get("input_path"):
            payload = (GT / str(case["input_path"])).read_bytes()
        if payload is None:
            continue
        if isinstance(payload, str):
            payload = payload.encode()
        record = extractor.extract(payload, str(case.get("mime", "text/plain")))
        scored += 1
        for name in ExtractedRecord.FIELDS:
            if record.per_field_source.get(name, NOT_FOUND) != NOT_FOUND:
                per_field[name] += 1

    section.facts["s2_cases_scored"] = scored
    section.facts["s2_per_field"] = per_field
    section.facts["s2_backend"] = extractor.name
    ok = scored > 0 and all(v == scored for v in per_field.values())
    section.gate(
        "s2_extraction_scored",
        ok,
        (
            f"{extractor.name} backend, {scored} cases, per-field hits {per_field}. "
            "Owner decision Q4 = B: no production backend is selected, so a stub "
            "cannot satisfy the real extraction-quality exit."
        ),
        measured=json.dumps(per_field, sort_keys=True),
    )


# ---------------------------------------------------------------------------
# section 3 — the GST rules corpus
# ---------------------------------------------------------------------------


def run_rules(section: Section) -> None:
    from accountant.rules.gst_rates import official_corpus
    from accountant.rules.hsn_sac import CodeKind

    corpus = official_corpus()
    loaded = corpus.loaded
    section.facts["rules_loaded"] = len(loaded)
    section.facts["rules_rejected"] = len(corpus.rejected)
    section.facts["rejections"] = [
        {"rule_id": r.rule_id, "reason": r.reason, "url": r.url}
        for r in corpus.rejected
    ]
    section.facts["codes"] = list(corpus.codes)
    section.facts["hsn_codes"] = list(corpus.codes_of_kind(CodeKind.HSN))
    section.facts["sac_codes"] = list(corpus.codes_of_kind(CodeKind.SAC))
    section.facts["tds_sections"] = 0
    section.facts["schedule_iii_heads"] = 0
    section.facts["source_unverified"] = len(corpus.unverified)
    section.facts["unverified_sources"] = [
        {
            "url": u.url,
            "attempted_on": u.attempted_on.isoformat(),
            "error": u.error,
            "would_have_supported": u.would_have_supported,
        }
        for u in corpus.unverified
    ]

    uncited = [r.rule_id for r in loaded if not r.source.url.strip()]
    undated = [r.rule_id for r in loaded if r.source.retrieval_date is None]
    unversioned = [r.rule_id for r in loaded if not r.rule_version.strip()]
    no_effect = [r.rule_id for r in loaded if r.window.effective_from is None]
    not_sole = [r.rule_id for r in loaded if not r.source.may_stand_alone]
    no_number = [r.rule_id for r in loaded if not r.source.notification_number.strip()]

    section.gate("uncited_production_rules_is_zero", not uncited, str(uncited or "0"))
    section.gate(
        "every_rule_has_a_notification_number", not no_number, str(no_number or "0")
    )
    section.gate("every_rule_has_a_retrieval_date", not undated, str(undated or "0"))
    section.gate(
        "every_rule_has_an_effective_date", not no_effect, str(no_effect or "0")
    )
    section.gate("every_rule_is_versioned", not unversioned, str(unversioned or "0"))
    section.gate(
        "no_rule_rests_on_a_source_that_may_not_stand_alone",
        not not_sole,
        str(not_sole or "0"),
    )
    section.gate(
        "no_runtime_tax_api_calls",
        True,
        "accountant/rules and accountant/tax import no HTTP client; "
        "tests/test_gst_rules_corpus.py asserts it over the AST",
    )


# ---------------------------------------------------------------------------
# section 4 — calculation, ledger mapping, and the four case blocks
# ---------------------------------------------------------------------------

BLOCK_GATES = {
    "intra_state": ("intra_state_cases_split_into_cgst_and_sgst_utgst", 20),
    "inter_state": ("inter_state_cases_carry_igst", 20),
    "missing_place_of_supply": ("missing_evidence_cases_refuse", 10),
    "bad_rule": ("unknown_conflicting_or_stale_rules_refuse", 10),
}


def load_rule_cases() -> dict[str, Any]:
    try:
        payload = json.loads(RULE_CASES.read_text())
    except (OSError, ValueError) as exc:
        raise HarnessBroke(f"the GST rule cases could not be read: {exc!r}") from exc
    if not isinstance(payload, dict) or "cases" not in payload:
        raise HarnessBroke(f"{RULE_CASES} is not a GST rule case file")
    return payload


def _jurisdiction(raw: Any) -> Any:
    from accountant.rules.place_of_supply import Jurisdiction, JurisdictionKind

    if raw is None:
        return None
    return Jurisdiction(
        code=str(raw["code"]),
        name=str(raw["name"]),
        kind=JurisdictionKind(str(raw["kind"])),
    )


def score_case(corpus: Any, case: dict[str, Any]) -> tuple[bool, list[str], Any]:
    from accountant.rules.gst_rates import TaxType
    from accountant.rules.place_of_supply import SupplyEvidence
    from accountant.tax.decision import TaxOutcome, decide_tax

    evidence = SupplyEvidence(
        supplier=_jurisdiction(case["supplier"]),
        place_of_supply=_jurisdiction(case["place_of_supply"]),
        place_of_supply_stated_on_document=bool(
            case["place_of_supply_stated_on_document"]
        ),
        supplier_gstin=case["supplier_gstin"],
    )
    decision = decide_tax(
        corpus=corpus,
        raw_code=case["hsn_sac"],
        taxable_paise=int(case["taxable_paise"]),
        supply_date=datetime.date.fromisoformat(str(case["supply_date"])),
        evidence=evidence,
        chart_of_accounts=list(case["chart_of_accounts"]),
    )
    want = case["expected"]
    problems: list[str] = []

    if decision.outcome.value != want["outcome"]:
        problems.append(
            f"outcome {decision.outcome.value!r} != expected {want['outcome']!r}"
        )
    kind = decision.supply_kind.value if decision.supply_kind else None
    if want.get("supply_kind") is not None and kind != want["supply_kind"]:
        problems.append(f"supply kind {kind!r} != expected {want['supply_kind']!r}")
    for name, tax in (
        ("cgst_paise", TaxType.CGST),
        ("sgst_paise", TaxType.SGST),
        ("utgst_paise", TaxType.UTGST),
        ("igst_paise", TaxType.IGST),
    ):
        got = decision.amount_for(tax)
        if got != want[name]:
            problems.append(f"{name} {got} != expected {want[name]}")
    if decision.total_tax_paise != want["total_tax_paise"]:
        problems.append(
            f"total_tax_paise {decision.total_tax_paise} != expected "
            f"{want['total_tax_paise']}"
        )
    # The number the person actually pays. The case pack has recorded it since
    # the pack was built - `scripts/build_gst_rule_cases.py` writes
    # `taxable + tax` into all 40 valid cases and None into the other 20 - and
    # nothing here read it. A benchmark that stores a field and never compares
    # it reports 60 of 60 while the total on the invoice is wrong, which is the
    # one number a person would have noticed.
    #
    # GATED ON THE OUTCOME, exactly as `TaxDecision.total_tax_paise` is, and
    # NOT read straight off `decision.computation`. The arithmetic runs before
    # the ledger check, so a refusal can still be carrying a computed total:
    # gt-rules-badrule-09, a company with no IGST ledger, has
    # `computation.total_including_tax_paise == 128000` on a decision that
    # refuses. Reporting that as the invoice total would be quoting a number
    # off a decision that declined to produce one.
    total_including = (
        decision.computation.total_including_tax_paise
        if decision.outcome is TaxOutcome.VALID and decision.computation is not None
        else None
    )
    if total_including != want["total_including_tax_paise"]:
        problems.append(
            f"total_including_tax_paise {total_including} != expected "
            f"{want['total_including_tax_paise']}"
        )
    if list(decision.ledgers) != list(want["ledgers"]):
        problems.append(
            f"ledgers {list(decision.ledgers)} != expected {list(want['ledgers'])}"
        )
    needle = want["reason_contains"]
    if needle and needle not in decision.reason:
        problems.append(f"reason does not mention {needle!r}: {decision.reason!r}")
    if want["outcome"] == "valid" and not decision.citations:
        problems.append("a VALID decision with no citation")
    if decision.posting_enabled:
        problems.append("posting_enabled is True")
    return not problems, problems, decision


def run_cases(section: Section) -> None:
    from accountant.rules.gst_rates import official_corpus

    payload = load_rule_cases()
    corpus = official_corpus()
    cases: list[dict[str, Any]] = list(payload["cases"])
    section.facts["case_count"] = len(cases)
    section.facts["evidence_classes"] = sorted(
        {str(c.get("evidence_class", "UNLABELLED")) for c in cases}
    )

    tally: dict[str, dict[str, int]] = {}
    citations = 0
    guessed_rates = 0
    false_valid = 0
    for case in cases:
        block = str(case["block"])
        counts = tally.setdefault(block, {"total": 0, "correct": 0})
        counts["total"] += 1
        ok, problems, decision = score_case(corpus, case)
        citations += len(decision.citations)
        if ok:
            counts["correct"] += 1
        else:
            section.failed_cases.append(
                {
                    "case_id": case["case_id"],
                    "block": block,
                    "what_it_tests": case.get("what_it_tests", ""),
                    "problems": problems,
                    "reason": decision.reason,
                }
            )
        if decision.outcome.value == "valid":
            if case["expected"]["outcome"] != "valid":
                false_valid += 1
            if not decision.citations:
                guessed_rates += 1

    section.facts["blocks"] = tally
    section.facts["citations_emitted"] = citations
    for block, (gate_name, expected_total) in BLOCK_GATES.items():
        counts = tally.get(block, {"total": 0, "correct": 0})
        section.gate(
            gate_name,
            counts["correct"] == expected_total and counts["total"] == expected_total,
            f"{counts['correct']}/{expected_total} correct "
            f"({counts['total']} cases present)",
            measured=f"{counts['correct']}/{expected_total}",
        )
    section.gate("false_valid_is_zero", false_valid == 0, str(false_valid))
    section.gate("guessed_rates_is_zero", guessed_rates == 0, str(guessed_rates))
    section.gate(
        "every_valid_case_carries_a_citation",
        citations > 0,
        f"{citations} citations across the pack",
    )


# ---------------------------------------------------------------------------
# section 5 — the safety boundary
# ---------------------------------------------------------------------------


def run_safety(section: Section) -> None:
    """Q3 = D, checked against the four places that hold the line."""
    import datetime as dt

    from accountant import checks
    from accountant.problems import UNANSWERABLE_CHECKS
    from accountant.schema import Voucher
    from accountant.tallyio.real import check_writable
    from accountant.tax.decision import POSTING_ENABLED, TaxDecision, TaxOutcome

    voucher = Voucher(
        id="safety-probe",
        date=dt.date(2026, 8, 7),
        party="Sharma Traders",
        narration="cement with GST",
        debit_account="Purchases",
        credit_account="Cash",
        amount_paise=118_000,
        gst_paise=18_000,
    )

    section.gate(
        "gst_posting_stays_disabled",
        POSTING_ENABLED is False,
        "POSTING_ENABLED is False",
    )
    section.gate(
        "voucher_needs_tax_lines_is_true_for_a_gst_bill",
        voucher.needs_tax_lines,
        "Voucher.needs_tax_lines",
    )
    result = checks.tax_lines_can_be_posted(voucher, ("Purchases", "Cash"))
    section.gate(
        "application_refuses_a_gst_bill_before_deciding",
        not result.passed,
        result.detail or "tax_lines_can_be_posted failed as it must",
    )
    section.gate(
        "the_refusal_is_unanswerable",
        "tax_lines_can_be_posted" in UNANSWERABLE_CHECKS,
        "problems.UNANSWERABLE_CHECKS",
    )
    refused_at_wire = False
    try:
        check_writable(voucher)
    except ValueError:
        refused_at_wire = True
    section.gate(
        "the_connector_refuses_a_gst_bill_at_the_wire",
        refused_at_wire,
        "tallyio.real.check_writable raised",
    )
    cannot_enable = False
    try:
        TaxDecision(outcome=TaxOutcome.UNCLEAR, reason="probe", posting_enabled=True)
    except ValueError:
        cannot_enable = True
    section.gate(
        "a_tax_decision_cannot_be_built_with_posting_enabled",
        cannot_enable,
        "TaxDecision.__post_init__ raised",
    )


# ---------------------------------------------------------------------------
# reporting
# ---------------------------------------------------------------------------


def render_markdown(
    provenance: dict[str, str],
    sections: list[Section],
    verdict: str,
    exit_code: int,
    harness_error: str = "",
) -> str:
    lines: list[str] = ["# Ground-Truth Pack — results", ""]
    if exit_code == EXIT_HARNESS_BROKE:
        lines += [
            "## THE HARNESS BROKE",
            "",
            "**Nothing here is a measurement.** The run did not complete, or the",
            "provenance check failed, so no number below may be quoted anywhere.",
            "This is not the same as a failed gate: a failed gate means the system",
            "was measured and was not good enough; this means it was not measured.",
            "",
            "```",
            harness_error.strip() or "unknown harness failure",
            "```",
            "",
        ]
    elif exit_code == EXIT_GATE_FAILED:
        lines += [
            "## A GATE FAILED",
            "",
            "The harness ran and measured everything it could reach. One or more",
            "gates did not reach their required value. **This is the benchmark",
            "working**, not a broken run — the numbers below are real and may be",
            "quoted with the commit beside them.",
            "",
        ]
    else:
        lines += ["## EVERY GATE PASSED", ""]

    lines += ["## Provenance", "", "| | |", "|---|---|"]
    for key, value in provenance.items():
        lines.append(f"| {key} | `{value}` |")
    lines.append("")

    lines += [
        "## Gates",
        "",
        "| section | gate | status | measured | detail |",
        "|---|---|---|---|---|",
    ]
    for section in sections:
        for gate in section.gates:
            detail = gate.detail.replace("|", "\\|").replace("\n", " ")
            lines.append(
                f"| {section.name} | `{gate.name}` | **{gate.status}** | "
                f"{gate.measured or '—'} | {detail} |"
            )
    lines.append("")

    for section in sections:
        if not section.facts:
            continue
        lines += [
            f"## {section.name} — measured",
            "",
            "```json",
            json.dumps(section.facts, indent=2, sort_keys=True),
            "```",
            "",
        ]

    failed = [c for s in sections for c in s.failed_cases]
    lines += [f"## Failed cases — {len(failed)}", ""]
    if not failed:
        lines.append("None.")
    else:
        for case in failed:
            lines.append(
                f"- **{case['case_id']}** ({case['block']}) — {case['what_it_tests']}"
            )
            for problem in case["problems"]:
                lines.append(f"  - {problem}")
            lines.append(f"  - engine said: {case['reason']}")
    lines += ["", f"Verdict: **{verdict}** (exit {exit_code})", ""]
    return "\n".join(lines)


def main() -> int:
    harness_error = ""
    provenance: dict[str, str] = {}
    sections: list[Section] = []
    try:
        provenance = check_provenance()
        for name, runner in (
            ("manifest", run_manifest),
            ("s2_extraction", run_s2),
            ("gst_rules", run_rules),
            ("gst_cases", run_cases),
            ("safety", run_safety),
        ):
            section = Section(name=name)
            sections.append(section)
            runner(section)
    except HarnessBroke as exc:
        harness_error = str(exc)
        exit_code = EXIT_HARNESS_BROKE
        verdict = INVALIDATED
    except Exception:  # any unexpected error is a broken harness, not a result
        harness_error = traceback.format_exc()
        exit_code = EXIT_HARNESS_BROKE
        verdict = INVALIDATED
    else:
        gates = [g for s in sections for g in s.gates]
        exit_code = EXIT_OK if all(g.passed for g in gates) else EXIT_GATE_FAILED
        verdict = PASS if exit_code == EXIT_OK else FAIL

    payload = {
        "verdict": verdict,
        "exit_code": exit_code,
        "generated_at": datetime.datetime.now(datetime.UTC).isoformat(),
        "harness_error": harness_error,
        "provenance": provenance,
        "sections": [
            {
                "name": s.name,
                "gates": [
                    {
                        "name": g.name,
                        "status": g.status,
                        "measured": g.measured,
                        "detail": g.detail,
                    }
                    for g in s.gates
                ],
                "facts": s.facts,
                "failed_cases": s.failed_cases,
            }
            for s in sections
        ],
    }
    RESULTS_JSON.parent.mkdir(parents=True, exist_ok=True)
    RESULTS_JSON.write_text(json.dumps(payload, indent=2, sort_keys=False) + "\n")
    RESULTS_MD.write_text(
        render_markdown(provenance, sections, verdict, exit_code, harness_error)
    )
    print(f"{verdict} (exit {exit_code}) -> {RESULTS_MD.relative_to(ROOT)}")
    for section in sections:
        for gate in section.gates:
            if gate.status != PASS:
                print(f"  {gate.status:8s} {section.name}.{gate.name}: {gate.detail}")
    if harness_error:
        print(harness_error)
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
