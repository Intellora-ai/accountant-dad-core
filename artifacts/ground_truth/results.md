# Ground-Truth Pack — results

## A GATE FAILED

The harness ran and measured everything it could reach. One or more
gates did not reach their required value. **This is the benchmark
working**, not a broken run — the numbers below are real and may be
quoted with the commit beside them.

## Provenance

| | |
|---|---|
| cwd | `/Users/tanveersidhu/ACCOUNTANT` |
| accountant__file__ | `/Users/tanveersidhu/ACCOUNTANT/accountant/__init__.py` |
| python | `3.14.6` |
| commit | `3a7efc75785d951ceb820a22ffd93bcb1de49b29` |
| branch | `cage/safety-layer` |
| worktree | `/Users/tanveersidhu/ACCOUNTANT` |
| dirty | `yes` |

## Gates

| section | gate | status | measured | detail |
|---|---|---|---|---|
| manifest | `ground_truth_manifest_validates` | **PASS** | — | every manifest entry checked out |
| manifest | `ground_truth_hashes_verify` | **PASS** | — | hashes verified by scripts/validate_ground_truth.py |
| manifest | `gst_rule_cases_readable` | **PASS** | 5b2e44fe065b6bb58f81dda423f671783215ad3cef0f6dbfae4628afe6fbdb2c | sha256 5b2e44fe065b6bb5… |
| s2_extraction | `exit1_generated_truth_extraction` | **FAIL** | {"date": 14, "party": 20, "tax_paise": 20, "total_paise": 20} | ladder backend, 80 renderable cases, exact matches per field {'date': 14, 'party': 20, 'total_paise': 20, 'tax_paise': 20}, required 76; WRONG rather than unread, per field, over all 100 cases: {'date': 0, 'party': 0, 'total_paise': 0, 'tax_paise': 0}. Rung that answered: {'ladder': 60, 'pdf_text_layer': 20, 'typed_text': 20}. The application default is 'typed_text', which is NOT the backend scored here. GENERATED_TRUTH from canonical JSON, SYNTHETIC_EVIDENCE, and never evidence about real-world reader accuracy. 40 of the 80 renderable cases reach no rung that can read them at all: 20 DOCX, which no reader here opens, and 20 PNG, whose tier is not wired. docs/EXTRACTION_MEASURED.md carries the split per input type and the count of fields that came back WRONG rather than unread. |
| s2_extraction | `exit2_unrenderable_input_is_explicit` | **PASS** | 0 | 20 unrenderable cases; every named field explicit not_found with a reason. ADAPTER_CONTRACT, never reader accuracy. no silent blank, no fabricated value |
| s2_extraction | `s2_extraction_scored` | **FAIL** | {"date": 14, "party": 20, "tax_paise": 20, "total_paise": 20} | ladder backend, 100 cases, per-field hits {'date': 14, 'party': 20, 'total_paise': 20, 'tax_paise': 20}. This asks whether every field was SPOKEN TO, which two of the five input types cannot be: a DOCX and a pixel-free JPEG reach no rung, and a refusal is the correct answer rather than a hit. |
| gst_rules | `uncited_production_rules_is_zero` | **PASS** | — | 0 |
| gst_rules | `every_rule_has_a_notification_number` | **PASS** | — | 0 |
| gst_rules | `every_rule_has_a_retrieval_date` | **PASS** | — | 0 |
| gst_rules | `every_rule_has_an_effective_date` | **PASS** | — | 0 |
| gst_rules | `every_rule_is_versioned` | **PASS** | — | 0 |
| gst_rules | `no_rule_rests_on_a_source_that_may_not_stand_alone` | **PASS** | — | 0 |
| gst_rules | `no_runtime_tax_api_calls` | **PASS** | — | accountant/rules and accountant/tax import no HTTP client; tests/test_gst_rules_corpus.py asserts it over the AST |
| gst_cases | `intra_state_cases_split_into_cgst_and_sgst_utgst` | **PASS** | 20/20 | 20/20 correct (20 cases present) |
| gst_cases | `inter_state_cases_carry_igst` | **PASS** | 20/20 | 20/20 correct (20 cases present) |
| gst_cases | `missing_evidence_cases_refuse` | **PASS** | 10/10 | 10/10 correct (10 cases present) |
| gst_cases | `unknown_conflicting_or_stale_rules_refuse` | **PASS** | 10/10 | 10/10 correct (10 cases present) |
| gst_cases | `false_valid_is_zero` | **PASS** | — | 0 |
| gst_cases | `guessed_rates_is_zero` | **PASS** | — | 0 |
| gst_cases | `every_valid_case_carries_a_citation` | **PASS** | — | 60 citations across the pack |
| safety | `gst_posting_stays_disabled` | **PASS** | — | POSTING_ENABLED is False |
| safety | `voucher_needs_tax_lines_is_true_for_a_gst_bill` | **PASS** | — | Voucher.needs_tax_lines |
| safety | `application_refuses_a_gst_bill_before_deciding` | **PASS** | — | this bill carries GST of 18000 paise, and Accountant Dad cannot post a tax line yet — posting it would drop the tax and leave a wrong statutory entry, so please enter this one in Tally yourself |
| safety | `the_refusal_is_unanswerable` | **PASS** | — | problems.UNANSWERABLE_CHECKS |
| safety | `the_connector_refuses_a_gst_bill_at_the_wire` | **PASS** | — | tallyio.real.check_writable raised |
| safety | `a_tax_decision_cannot_be_built_with_posting_enabled` | **PASS** | — | TaxDecision.__post_init__ raised |

## manifest — measured

```json
{
  "gst_cases_path": "artifacts/ground_truth/rules/gst_cases.json",
  "gst_cases_sha256": "5b2e44fe065b6bb58f81dda423f671783215ad3cef0f6dbfae4628afe6fbdb2c"
}
```

## s2_extraction — measured

```json
{
  "corpus_label": "SYNTHETIC_EVIDENCE",
  "exit1_exact_per_field": {
    "date": 14,
    "party": 20,
    "tax_paise": 20,
    "total_paise": 20
  },
  "exit1_renderable_cases": 80,
  "exit1_required": 76,
  "exit1_wrong_examples": [],
  "exit1_wrong_per_field": {
    "date": 0,
    "party": 0,
    "tax_paise": 0,
    "total_paise": 0
  },
  "exit2_unrenderable_cases": 20,
  "exit2_unsafe": [],
  "s2_application_default": "typed_text",
  "s2_backend": "ladder",
  "s2_backend_is_the_application_default": false,
  "s2_by_input_type": {
    "DOCX": {
      "date": {
        "exact": 0,
        "refused": 20,
        "wrong": 0
      },
      "party": {
        "exact": 0,
        "refused": 20,
        "wrong": 0
      },
      "tax_paise": {
        "exact": 0,
        "refused": 20,
        "wrong": 0
      },
      "total_paise": {
        "exact": 0,
        "refused": 20,
        "wrong": 0
      }
    },
    "JPG": {
      "date": {
        "exact": 0,
        "refused": 20,
        "wrong": 0
      },
      "party": {
        "exact": 0,
        "refused": 20,
        "wrong": 0
      },
      "tax_paise": {
        "exact": 0,
        "refused": 20,
        "wrong": 0
      },
      "total_paise": {
        "exact": 0,
        "refused": 20,
        "wrong": 0
      }
    },
    "PDF": {
      "date": {
        "exact": 14,
        "refused": 6,
        "wrong": 0
      },
      "party": {
        "exact": 20,
        "refused": 0,
        "wrong": 0
      },
      "tax_paise": {
        "exact": 20,
        "refused": 0,
        "wrong": 0
      },
      "total_paise": {
        "exact": 20,
        "refused": 0,
        "wrong": 0
      }
    },
    "PNG": {
      "date": {
        "exact": 0,
        "refused": 20,
        "wrong": 0
      },
      "party": {
        "exact": 0,
        "refused": 20,
        "wrong": 0
      },
      "tax_paise": {
        "exact": 0,
        "refused": 20,
        "wrong": 0
      },
      "total_paise": {
        "exact": 0,
        "refused": 20,
        "wrong": 0
      }
    },
    "text": {
      "date": {
        "exact": 0,
        "refused": 20,
        "wrong": 0
      },
      "party": {
        "exact": 0,
        "refused": 20,
        "wrong": 0
      },
      "tax_paise": {
        "exact": 0,
        "refused": 20,
        "wrong": 0
      },
      "total_paise": {
        "exact": 0,
        "refused": 20,
        "wrong": 0
      }
    }
  },
  "s2_cases_scored": 100,
  "s2_per_field": {
    "date": 14,
    "party": 20,
    "tax_paise": 20,
    "total_paise": 20
  },
  "s2_rung_that_answered": {
    "ladder": 60,
    "pdf_text_layer": 20,
    "typed_text": 20
  },
  "truth_label": "GENERATED_TRUTH"
}
```

## gst_rules — measured

```json
{
  "codes": [
    "2523",
    "4820",
    "9972",
    "9987"
  ],
  "hsn_codes": [
    "2523",
    "4820"
  ],
  "rejections": [],
  "rules_loaded": 15,
  "rules_rejected": 0,
  "sac_codes": [
    "9972",
    "9987"
  ],
  "schedule_iii_heads": 0,
  "source_unverified": 8,
  "tds_sections": 0,
  "unverified_sources": [
    {
      "attempted_on": "2026-08-10",
      "error": "unable to verify the first certificate",
      "url": "https://taxinformation.cbic.gov.in/view-pdf/1010211/ENG/Notifications",
      "would_have_supported": "the current GST rate schedule. Rate notifications issued after 2022 are published only on this host, so the corpus cannot see any rate change after 2022 and refuses every supply dated later than 2017-08-17 rather than returning a rate that may have moved."
    },
    {
      "attempted_on": "2026-08-10",
      "error": "unable to verify the first certificate",
      "url": "https://taxinformation.cbic.gov.in/view-pdf/1010100/ENG/Notifications",
      "would_have_supported": "notification 9/2025-Central Tax (Rate), which the CBIC listing pages record as superseding notification 1/2017-Central Tax (Rate)"
    },
    {
      "attempted_on": "2026-08-10",
      "error": "HTTP 500 Internal Server Error",
      "url": "https://www.cbic.gov.in/resources/htdocs-cbec/gst/notfctn-9-2025-cgst-rate-english.pdf",
      "would_have_supported": "notification 9/2025-Central Tax (Rate) from the main CBIC host"
    },
    {
      "attempted_on": "2026-08-10",
      "error": "HTTP 500 Internal Server Error",
      "url": "https://www.cbic.gov.in/resources/htdocs-cbec/gst/notfctn-1-2017-cgst-rate-english.pdf",
      "would_have_supported": "a second independent copy of notification 1/2017-Central Tax (Rate). The cbic-gst.gov.in copy was retrieved instead and is what the rules above cite."
    },
    {
      "attempted_on": "2026-08-10",
      "error": "HTTP 404 Not Found",
      "url": "https://cbic-gst.gov.in/pdf/IGST-Act-Updated-30092020.pdf",
      "would_have_supported": "sections 7, 8, 10 and 12 of the IGST Act, 2017 \u2014 the statutory definition of inter-State and intra-State supply, and the derivation of the place of supply from the nature of the transaction. Without it the engine requires the place of supply to be STATED and never derives it; see accountant/rules/place_of_supply.py."
    },
    {
      "attempted_on": "2026-08-10",
      "error": "HTTP 404 Not Found",
      "url": "https://cbic-gst.gov.in/hindi/igst-act.html",
      "would_have_supported": "the same IGST Act sections, from the CBIC Acts index"
    },
    {
      "attempted_on": "2026-08-10",
      "error": "not attempted: an SGST rate is notified by a State Government, not by CBIC or the Income Tax Department, so it falls outside the authority hierarchy fixed by owner decision Q1 = A",
      "url": "STATE GOVERNMENT SGST RATE NOTIFICATIONS \u2014 no CBIC source exists",
      "would_have_supported": "the SGST half of an intra-State supply made in a State. The corpus therefore carries no SGST rate at all, and an intra-State supply in a State comes back UNCLEAR. Intra-State supplies in a Union Territory are computable, because UTGST is notified by the Central Government and 1/2017-Union Territory Tax (Rate) is a CBIC document."
    },
    {
      "attempted_on": "2026-08-10",
      "error": "retrieved, then REJECTED: the file is served from a cbic.gov.in host but its pages are headed 'The Institute of Chartered Accountants of India \u2014 GST & Indirect Taxes Committee' and the document is marked '[UPDATED] [As corrected by corrigendum, dated 18-9-2025]'. It is a third-party consolidation of the notification, not the notification as issued, so it is not rank-1 authority and no rate was taken from it.",
      "url": "https://courier.cbic.gov.in/ECCS/advisory/2025/NOTIFICATION%20NO.%209_2025-INTEGRATED%20TAX%20(RATE)%20-1759486719.pdf",
      "would_have_supported": "the post-2025 IGST rate schedule"
    }
  ]
}
```

## gst_cases — measured

```json
{
  "blocks": {
    "bad_rule": {
      "correct": 10,
      "total": 10
    },
    "inter_state": {
      "correct": 20,
      "total": 20
    },
    "intra_state": {
      "correct": 20,
      "total": 20
    },
    "missing_place_of_supply": {
      "correct": 10,
      "total": 10
    }
  },
  "case_count": 60,
  "citations_emitted": 60,
  "evidence_classes": [
    "SYNTHETIC_EVIDENCE"
  ]
}
```

## Failed cases — 0

None.

Verdict: **FAIL** (exit 1)
