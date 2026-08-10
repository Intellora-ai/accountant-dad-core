#!/usr/bin/env python3
"""Write the sixty GST rule cases to artifacts/ground_truth/rules/gst_cases.json.

WHY THE EXPECTED NUMBERS ARE COMPUTED HERE AND NOT BY THE ENGINE
----------------------------------------------------------------
A ground truth produced by the thing it is checking measures nothing. Every
expected amount below is computed with `Decimal(taxable) * Decimal(percent) /
100` and refused unless it is a whole number of paise. The engine under test
multiplies integer paise by integer basis points and divides by 10,000. Two
different arithmetics, written apart, agreeing on sixty cases is evidence; one
arithmetic agreeing with itself is not.

WHAT THE CASES ARE, AND WHAT THEY ARE NOT — owner decision Q5
--------------------------------------------------------------
Every case is labelled `SYNTHETIC_EVIDENCE`. The party names, amounts, GSTINs
and dates are made up. **The rates are not.** Each expected amount is derived
from a rate this repository read out of a CBIC notification, and the case file
carries the notification number beside it.

The jurisdiction codes, names and State/Union-Territory kinds are FIXTURE
EVIDENCE, not product data. The engine compares them; it never validates them
against any register and it never infers the kind. See
`accountant/rules/place_of_supply.py` for why that classification is not shipped
as a table.

Run:
    python scripts/build_gst_rule_cases.py
"""

from __future__ import annotations

import datetime
import json
import pathlib
import sys
from decimal import Decimal
from typing import Any

ROOT = pathlib.Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    # Same reason as `scripts/run_ground_truth.py`: running a script by path puts
    # `scripts/` on sys.path[0], and `import accountant` must not resolve through
    # an editable install to some other tree.
    sys.path.insert(0, str(ROOT))

RULES_DIR = ROOT / "artifacts" / "ground_truth" / "rules"
OUT = RULES_DIR / "gst_cases.json"
CORPUS_OUT = RULES_DIR / "corpus.json"
UNVERIFIED_OUT = RULES_DIR / "unverified_sources.json"

CHART = ["Purchases", "Sundry Expenses", "Cash", "CGST", "SGST", "UTGST", "IGST"]

# -- fixture-declared jurisdictions -------------------------------------------

UT = "union_territory"
STATE = "state"

UNION_TERRITORIES = [
    ("04", "Chandigarh"),
    ("35", "Andaman and Nicobar Islands"),
    ("31", "Lakshadweep"),
    ("26", "Dadra and Nagar Haveli and Daman and Diu"),
    ("38", "Ladakh"),
]
STATES = [
    ("27", "Maharashtra"),
    ("29", "Karnataka"),
    ("24", "Gujarat"),
    ("33", "Tamil Nadu"),
    ("08", "Rajasthan"),
]


def place(code: str, name: str, kind: str) -> dict[str, str]:
    return {"code": code, "name": name, "kind": kind}


def ut(i: int) -> dict[str, str]:
    code, name = UNION_TERRITORIES[i % len(UNION_TERRITORIES)]
    return place(code, name, UT)


def st(i: int) -> dict[str, str]:
    code, name = STATES[i % len(STATES)]
    return place(code, name, STATE)


def gstin(code: str, n: int) -> str:
    """A shape-valid, entirely invented GSTIN whose state code matches `code`."""
    letters = "ABCDE"
    return f"{code}{letters}{1000 + n:04d}F1Z{n % 10}"


# -- rates, quoted from the corpus's own sources ------------------------------
#
# percent per (code, tax) exactly as printed in the notification named beside it.
RATES: dict[str, dict[str, tuple[str, str]]] = {
    "2523": {
        "cgst": ("14", "1/2017-Central Tax (Rate), Schedule IV - 14%, S. No. 18"),
        "utgst": (
            "14",
            "1/2017-Union Territory Tax (Rate), Schedule IV - 14%, S. No. 18",
        ),
        "igst": ("28", "1/2017-Integrated Tax (Rate), Schedule IV - 28%, S. No. 18"),
    },
    "9972": {
        "cgst": ("9", "11/2017-Central Tax (Rate), Sl. No. 16, Heading 9972"),
        "utgst": ("9", "11/2017-Union Territory Tax (Rate), Sl. No. 16, Heading 9972"),
        "igst": ("18", "8/2017-Integrated Tax (Rate), Sl. No. 16, Heading 9972"),
    },
    "9987": {
        "cgst": ("9", "11/2017-Central Tax (Rate), Sl. No. 25, Heading 9987"),
        "utgst": ("9", "11/2017-Union Territory Tax (Rate), Sl. No. 25, Heading 9987"),
        "igst": ("18", "8/2017-Integrated Tax (Rate), Sl. No. 25, Heading 9987"),
    },
}

HAPPY_CODES = ["2523", "9972", "9987"]

WINDOW_START = datetime.date(2017, 7, 1)
WINDOW_END = datetime.date(2017, 8, 17)


def in_window(step: int) -> str:
    """A date inside the checked window, spread across all 48 days of it."""
    span = (WINDOW_END - WINDOW_START).days
    return (WINDOW_START + datetime.timedelta(days=(step * 7) % (span + 1))).isoformat()


def exact_paise(taxable_paise: int, percent: str) -> int:
    """Whole paise or a build-time failure. The builder never rounds either."""
    value = Decimal(taxable_paise) * Decimal(percent) / Decimal(100)
    if value != value.to_integral_value():
        raise SystemExit(
            f"case builder refuses {percent}% of {taxable_paise} paise: "
            f"{value} is not a whole number of paise"
        )
    return int(value)


def zero_expectation(
    outcome: str, reason_contains: str, **extra: Any
) -> dict[str, Any]:
    base: dict[str, Any] = {
        "outcome": outcome,
        "supply_kind": None,
        "cgst_paise": 0,
        "sgst_paise": 0,
        "utgst_paise": 0,
        "igst_paise": 0,
        "total_tax_paise": None,
        "total_including_tax_paise": None,
        "ledgers": [],
        "reason_contains": reason_contains,
    }
    base.update(extra)
    return base


# -- block 1: twenty intra-State supplies -------------------------------------


def intra_state_cases() -> list[dict[str, Any]]:
    cases: list[dict[str, Any]] = []
    for i in range(20):
        where = ut(i)
        code = HAPPY_CODES[i % len(HAPPY_CODES)]
        taxable = 100_00 * (i + 1) + 100_00  # whole rupees, ₹200 .. ₹2,100
        cgst_pct, cgst_ref = RATES[code]["cgst"]
        utgst_pct, utgst_ref = RATES[code]["utgst"]
        cgst = exact_paise(taxable, cgst_pct)
        utgst = exact_paise(taxable, utgst_pct)
        cases.append(
            {
                "case_id": f"gt-rules-intra-{i + 1:02d}",
                "block": "intra_state",
                "evidence_class": "SYNTHETIC_EVIDENCE",
                "what_it_tests": (
                    f"an intra-State supply of {code} wholly inside {where['name']} "
                    "splits into CGST and UTGST at the notified rates"
                ),
                "supply_date": in_window(i),
                "hsn_sac": code,
                "taxable_paise": taxable,
                "supplier": where,
                "place_of_supply": where,
                "place_of_supply_stated_on_document": True,
                "supplier_gstin": gstin(where["code"], i) if i % 2 == 0 else None,
                "chart_of_accounts": list(CHART),
                "rate_authority": [cgst_ref, utgst_ref],
                "expected": {
                    "outcome": "valid",
                    "supply_kind": "intra_state",
                    "cgst_paise": cgst,
                    "sgst_paise": 0,
                    "utgst_paise": utgst,
                    "igst_paise": 0,
                    "total_tax_paise": cgst + utgst,
                    "total_including_tax_paise": taxable + cgst + utgst,
                    "ledgers": ["CGST", "UTGST"],
                    "reason_contains": "intra-State supply",
                },
            }
        )
    return cases


# -- block 2: twenty inter-State supplies -------------------------------------


def inter_state_cases() -> list[dict[str, Any]]:
    cases: list[dict[str, Any]] = []
    for i in range(20):
        # Rotate the three shapes an inter-State supply comes in, so the block
        # is not twenty copies of State-to-State with different numbers.
        if i % 3 == 0:
            supplier, destination = st(i), ut(i)
        elif i % 3 == 1:
            supplier, destination = ut(i), st(i + 1)
        else:
            supplier, destination = st(i), st(i + 2)
        code = HAPPY_CODES[i % len(HAPPY_CODES)]
        taxable = 100_00 * (i + 3)  # ₹300 .. ₹2,200
        igst_pct, igst_ref = RATES[code]["igst"]
        igst = exact_paise(taxable, igst_pct)
        cases.append(
            {
                "case_id": f"gt-rules-inter-{i + 1:02d}",
                "block": "inter_state",
                "evidence_class": "SYNTHETIC_EVIDENCE",
                "what_it_tests": (
                    f"a supply of {code} from {supplier['name']} to "
                    f"{destination['name']} is inter-State and carries IGST only"
                ),
                "supply_date": in_window(i + 3),
                "hsn_sac": code,
                "taxable_paise": taxable,
                "supplier": supplier,
                "place_of_supply": destination,
                "place_of_supply_stated_on_document": True,
                "supplier_gstin": gstin(supplier["code"], i) if i % 2 == 1 else None,
                "chart_of_accounts": list(CHART),
                "rate_authority": [igst_ref],
                "expected": {
                    "outcome": "valid",
                    "supply_kind": "inter_state",
                    "cgst_paise": 0,
                    "sgst_paise": 0,
                    "utgst_paise": 0,
                    "igst_paise": igst,
                    "total_tax_paise": igst,
                    "total_including_tax_paise": taxable + igst,
                    "ledgers": ["IGST"],
                    "reason_contains": "inter-State supply",
                },
            }
        )
    return cases


# -- block 3: ten supplies with the place of supply missing or unusable --------


def missing_place_of_supply_cases() -> list[dict[str, Any]]:
    day = in_window(2)
    chandigarh = place("04", "Chandigarh", UT)
    maharashtra = place("27", "Maharashtra", STATE)

    specs: list[tuple[str, dict[str, Any], str]] = [
        (
            "no place of supply at all",
            {"supplier": chandigarh, "place_of_supply": None, "stated": False},
            "the place of supply is missing",
        ),
        (
            "a place of supply that the document does not state",
            {"supplier": chandigarh, "place_of_supply": chandigarh, "stated": False},
            "never inferred",
        ),
        (
            "no supplier State",
            {"supplier": None, "place_of_supply": chandigarh, "stated": True},
            "which State or Union Territory the supplier is in",
        ),
        (
            "a supplier GSTIN and nothing else — the case this rule exists for",
            {
                "supplier": None,
                "place_of_supply": None,
                "stated": False,
                "gstin": gstin("04", 1),
            },
            "which State or Union Territory the supplier is in",
        ),
        (
            "a supplier State with a GSTIN, but no place of supply",
            {
                "supplier": chandigarh,
                "place_of_supply": None,
                "stated": False,
                "gstin": gstin("04", 2),
            },
            "not used to fill this in",
        ),
        (
            "a GSTIN whose state code contradicts the stated supplier State",
            {
                "supplier": chandigarh,
                "place_of_supply": chandigarh,
                "stated": True,
                "gstin": gstin("27", 3),
            },
            "disagrees with itself",
        ),
        (
            "a GSTIN that is not shaped like a GSTIN",
            {
                "supplier": chandigarh,
                "place_of_supply": chandigarh,
                "stated": True,
                "gstin": "04ABCDE1234",
            },
            "not shaped like a GSTIN",
        ),
        (
            "a place of supply with a blank code",
            {
                "supplier": chandigarh,
                "place_of_supply": place("", "", UT),
                "stated": True,
            },
            "the place of supply is missing",
        ),
        (
            "a supplier with a blank code",
            {
                "supplier": place("", "", STATE),
                "place_of_supply": maharashtra,
                "stated": True,
            },
            "which State or Union Territory the supplier is in",
        ),
        (
            "both sides absent",
            {"supplier": None, "place_of_supply": None, "stated": False},
            "which State or Union Territory the supplier is in",
        ),
    ]

    cases: list[dict[str, Any]] = []
    for i, (what, spec, contains) in enumerate(specs):
        # A contradiction or an unreadable registration number is a defect in
        # the document rather than an absence, so those two are NOT_VALID; the
        # other eight are questions and stay UNCLEAR.
        outcome = "not_valid" if "GSTIN" in what and "contradict" in what else "unclear"
        if "not shaped like a GSTIN" in contains:
            outcome = "not_valid"
        cases.append(
            {
                "case_id": f"gt-rules-nopos-{i + 1:02d}",
                "block": "missing_place_of_supply",
                "evidence_class": "SYNTHETIC_EVIDENCE",
                "what_it_tests": what,
                "supply_date": day,
                "hsn_sac": "2523",
                "taxable_paise": 100_000,
                "supplier": spec["supplier"],
                "place_of_supply": spec["place_of_supply"],
                "place_of_supply_stated_on_document": spec["stated"],
                "supplier_gstin": spec.get("gstin"),
                "chart_of_accounts": list(CHART),
                "rate_authority": [],
                "expected": zero_expectation(outcome, contains),
            }
        )
    return cases


# -- block 4: ten unknown, conflicting or stale rules -------------------------


def bad_rule_cases() -> list[dict[str, Any]]:
    chandigarh = place("04", "Chandigarh", UT)
    maharashtra = place("27", "Maharashtra", STATE)
    karnataka = place("29", "Karnataka", STATE)
    good_day = in_window(4)

    specs: list[dict[str, Any]] = [
        {
            "what": "an HSN code the corpus has never seen",
            "code": "8471",
            "date": good_day,
            "supplier": chandigarh,
            "pos": chandigarh,
            "taxable": 100_000,
            "chart": CHART,
            "outcome": "unclear",
            "contains": "not in the rules corpus",
        },
        {
            "what": (
                "a code one digit away from a known one — 2524 must NOT be "
                "answered with 2523's twenty-eight per cent"
            ),
            "code": "2524",
            "date": good_day,
            "supplier": maharashtra,
            "pos": karnataka,
            "taxable": 100_000,
            "chart": CHART,
            "outcome": "unclear",
            "contains": "no similar code is used in its place",
        },
        {
            "what": (
                "heading 4820, which the notification prints twice at two "
                "different rates — the corpus refuses rather than choosing"
            ),
            "code": "4820",
            "date": good_day,
            "supplier": chandigarh,
            "pos": chandigarh,
            "taxable": 100_000,
            "chart": CHART,
            "outcome": "unclear",
            "contains": "does not choose between them",
        },
        {
            "what": "a supply dated before the notification came into force",
            "code": "2523",
            "date": "2017-06-15",
            "supplier": chandigarh,
            "pos": chandigarh,
            "taxable": 100_000,
            "chart": CHART,
            "outcome": "unclear",
            "contains": "takes effect on 2017-07-01",
        },
        {
            "what": (
                "a supply dated today — past the last day the amendment chain "
                "was checked, so the rate may be stale and is not used"
            ),
            "code": "2523",
            "date": "2026-08-10",
            "supplier": chandigarh,
            "pos": chandigarh,
            "taxable": 100_000,
            "chart": CHART,
            "outcome": "unclear",
            "contains": "may be stale",
        },
        {
            "what": (
                "a supply dated one day after the amendment check — the boundary "
                "itself, not a date far away from it"
            ),
            "code": "9987",
            "date": "2017-08-18",
            "supplier": maharashtra,
            "pos": karnataka,
            "taxable": 100_000,
            "chart": CHART,
            "outcome": "unclear",
            "contains": "may be stale",
        },
        {
            "what": (
                "an intra-State supply inside a State, where the SGST half has "
                "no source that may stand alone under Q1 = A"
            ),
            "code": "2523",
            "date": good_day,
            "supplier": maharashtra,
            "pos": maharashtra,
            "taxable": 100_000,
            "chart": CHART,
            "outcome": "unclear",
            "contains": "holds no SGST rate for any code",
        },
        {
            "what": (
                "a tax that is not a whole number of paise, with no official "
                "rounding rule to appeal to"
            ),
            "code": "9987",
            "date": good_day,
            "supplier": maharashtra,
            "pos": karnataka,
            "taxable": 1,
            "chart": CHART,
            "outcome": "unclear",
            "contains": "not a whole number of paise",
        },
        {
            "what": "a company whose chart of accounts has no IGST ledger",
            "code": "2523",
            "date": good_day,
            "supplier": maharashtra,
            "pos": karnataka,
            "taxable": 100_000,
            "chart": ["Purchases", "Cash", "CGST", "SGST", "UTGST"],
            "outcome": "unclear",
            "contains": "no ledger called IGST",
        },
        {
            "what": "a taxable amount of zero",
            "code": "2523",
            "date": good_day,
            "supplier": chandigarh,
            "pos": chandigarh,
            "taxable": 0,
            "chart": CHART,
            "outcome": "not_valid",
            "contains": "nothing to tax",
        },
    ]

    cases: list[dict[str, Any]] = []
    for i, spec in enumerate(specs):
        cases.append(
            {
                "case_id": f"gt-rules-badrule-{i + 1:02d}",
                "block": "bad_rule",
                "evidence_class": "SYNTHETIC_EVIDENCE",
                "what_it_tests": spec["what"],
                "supply_date": spec["date"],
                "hsn_sac": spec["code"],
                "taxable_paise": spec["taxable"],
                "supplier": spec["supplier"],
                "place_of_supply": spec["pos"],
                "place_of_supply_stated_on_document": True,
                "supplier_gstin": None,
                "chart_of_accounts": list(spec["chart"]),
                "rate_authority": [],
                "expected": zero_expectation(spec["outcome"], spec["contains"]),
            }
        )
    return cases


def build() -> dict[str, Any]:
    blocks = {
        "intra_state": intra_state_cases(),
        "inter_state": inter_state_cases(),
        "missing_place_of_supply": missing_place_of_supply_cases(),
        "bad_rule": bad_rule_cases(),
    }
    cases = [case for block in blocks.values() for case in block]
    ids = [c["case_id"] for c in cases]
    if len(set(ids)) != len(ids):
        raise SystemExit("duplicate case ids")
    return {
        "schema": "accountant-dad/gst-rule-cases/1",
        "built_by": "scripts/build_gst_rule_cases.py",
        "evidence_class": "SYNTHETIC_EVIDENCE",
        "note": (
            "Party names, amounts, dates, GSTINs and jurisdiction records are "
            "invented. The RATES are not: every expected amount is derived from a "
            "rate read out of the CBIC notification named in `rate_authority`, and "
            "was computed with Decimal arithmetic in the builder rather than by the "
            "engine under test. Jurisdiction codes, names and State/Union-Territory "
            "kinds are fixture evidence; the engine compares them and never infers "
            "them."
        ),
        "counts": {name: len(block) for name, block in blocks.items()},
        "cases": cases,
    }


def corpus_snapshot() -> dict[str, Any]:
    """The loaded corpus, flattened, so a reviewer can read it without Python.

    This is a RENDERING of `accountant/rules/gst_rates.py`, not a second copy of
    the rates. `tests/test_gst_rules_corpus.py` fails if the two drift, which is
    the only thing that makes a rendering safe to publish.
    """
    from accountant.rules.gst_rates import official_corpus

    corpus = official_corpus()
    return {
        "schema": "accountant-dad/gst-rule-corpus/1",
        "rendered_from": "accountant/rules/gst_rates.py",
        "counts": {
            "codes_used_by_the_case_pack": len(corpus.codes),
            "rules_loaded": len(corpus.loaded),
            "rules_rejected": len(corpus.rejected),
            "sources_unverified": len(corpus.unverified),
        },
        "codes": list(corpus.codes),
        "rules": [
            {
                "rule_id": rule.rule_id,
                "code": rule.code.value,
                "code_kind": rule.code.kind.value,
                "description": rule.description,
                "tax_type": rule.tax_type.value,
                "rate_percent": str(Decimal(rule.rate_basis_points) / 100),
                "rate_basis_points": rule.rate_basis_points,
                "effective_from": rule.window.effective_from.isoformat()
                if rule.window.effective_from
                else None,
                "effective_to": rule.window.effective_to.isoformat()
                if rule.window.effective_to
                else None,
                "amendments_checked_through": (
                    rule.window.amendments_checked_through.isoformat()
                    if rule.window.amendments_checked_through
                    else None
                ),
                "notification_number": rule.source.notification_number,
                "source_url": rule.source.url,
                "source_title": rule.source.title,
                "issuing_authority": rule.source.issuing_authority,
                "document_reference": rule.source.document_reference,
                "retrieval_date": rule.source.retrieval_date.isoformat()
                if rule.source.retrieval_date
                else None,
                "authority_rank": rule.source.authority_rank.value,
                "rule_version": rule.rule_version,
                "jurisdiction": rule.jurisdiction,
                "status": rule.status.value,
            }
            for rule in corpus.loaded
        ],
    }


def unverified_snapshot() -> dict[str, Any]:
    """The gaps, rendered the same way and for the same reason."""
    from accountant.rules.gst_rates import official_corpus

    return {
        "schema": "accountant-dad/gst-unverified-sources/1",
        "rendered_from": "accountant/rules/gst_rates.py",
        "note": (
            "Sources that were asked for and did not arrive, with the exact error. "
            "None of them contributed a rate. Each one costs the engine a stated "
            "capability rather than being replaced by a remembered number."
        ),
        "sources": [
            {
                "url": u.url,
                "attempted_on": u.attempted_on.isoformat(),
                "error": u.error,
                "would_have_supported": u.would_have_supported,
                "status": u.status.value,
            }
            for u in official_corpus().unverified
        ],
    }


def main() -> None:
    RULES_DIR.mkdir(parents=True, exist_ok=True)
    payload = build()
    OUT.write_text(json.dumps(payload, indent=2, sort_keys=False) + "\n")
    CORPUS_OUT.write_text(json.dumps(corpus_snapshot(), indent=2) + "\n")
    UNVERIFIED_OUT.write_text(json.dumps(unverified_snapshot(), indent=2) + "\n")
    print(f"wrote {OUT} with {len(payload['cases'])} cases {payload['counts']}")
    print(f"wrote {CORPUS_OUT} and {UNVERIFIED_OUT}")


if __name__ == "__main__":
    main()
