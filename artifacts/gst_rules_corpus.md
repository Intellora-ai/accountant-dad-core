# The GST rules corpus — what was sourced, what was not, and what that costs

| | |
|---|---|
| **Written** | 2026-08-10 |
| **Branch** | `phase8/gst-rules` |
| **Runner** | `python scripts/run_ground_truth.py` |
| **Corpus** | `accountant/rules/gst_rates.py`, rendered to `artifacts/ground_truth/rules/corpus.json` |
| **Cases** | `artifacts/ground_truth/rules/gst_cases.json`, 60, every one `SYNTHETIC_EVIDENCE` |
| **Owner decisions this obeys** | Q1 = A, Q2 = C, Q3 = D, Q5 = C |

The short version: **six official CBIC notifications were retrieved and read.
Everything that could not be retrieved is named, with its URL and its exact
error, and costs the engine a capability instead of being filled in from
memory.** A corpus of four codes with citations is worth more than forty without.

---

## 1. Coverage

| | |
|---|---:|
| codes used by the case pack | 4 |
| codes sourced from an official notification | 4 |
| codes loaded into the production corpus | 4 |
| codes rejected at load | 0 |
| codes unknown during evaluation (returned `NOT_FOUND`) | 3 |
| rules loaded | 15 |
| rules rejected | 0 |
| GST HSN codes | 2 |
| GST SAC codes | 2 |
| TDS sections | 0 |
| Schedule III heads | 0 |
| source-verified rules | 15 |
| `SOURCE_UNVERIFIED` records | 8 |

**Nothing was rejected**, because nothing uncitable was written. The rejection
path is exercised by `tests/test_gst_rules_corpus.py`, which offers the loader a
rule with each required field removed in turn and asserts it is refused.

The three unknown codes met during evaluation are `8471`, `2524` and the
unparseable string `nonsense`. `2524` is the interesting one: it is one digit
from `2523`, and answering it with cement's twenty-eight per cent is the exact
failure owner decision Q2 = C forbids. The test suite generates **every**
one-digit neighbour of every known code — 100 or more of them — and requires
`NOT_FOUND` for each.

### Why only four codes

Owner decision Q2 = C: the corpus holds the codes the evaluation corpus uses,
plus explicitly approved fixtures, and nothing else. The four are the commodities
and services the repository's own fixtures already trade in — cement, stationery,
repairs and rent. **A code is never added to widen coverage.** The visible cost is
that only two rate bands are exercised, 18% and 28%; widening them would have
meant inventing a reason to hold a code, and that is the thing the decision
forbids.

---

## 2. Every rule that loaded

Fifteen rules: five schedule entries across three taxes. Every one retrieved
**2026-08-10**, jurisdiction `IN`, rule version `1`, effective from
**2017-07-01**, amendment chain checked through **2017-08-17**.

| Code | Entry | Tax | Rate | Notification | Read from |
|---|---|---|---:|---|---|
| 2523 | Portland cement and similar hydraulic cements | CGST | 14% | 1/2017-Central Tax (Rate) | Schedule IV, S. No. 18 |
| 2523 | " | UTGST | 14% | 1/2017-Union Territory Tax (Rate) | Schedule IV, S. No. 18 |
| 2523 | " | IGST | 28% | 1/2017-Integrated Tax (Rate) | Schedule IV, S. No. 18 |
| 4820 | Exercise book, graph book, laboratory note book and notebooks | CGST | 6% | 1/2017-Central Tax (Rate) | Schedule II, S. No. 123 |
| 4820 | " | UTGST | 6% | 1/2017-Union Territory Tax (Rate) | Schedule II, S. No. 123 |
| 4820 | " | IGST | 12% | 1/2017-Integrated Tax (Rate) | Schedule II, S. No. 123 |
| 4820 | Registers, account books … other than note books and exercise books | CGST | 9% | 1/2017-Central Tax (Rate) | Schedule III, S. No. 154 |
| 4820 | " | UTGST | 9% | 1/2017-Union Territory Tax (Rate) | Schedule III, S. No. 154 |
| 4820 | " | IGST | 18% | 1/2017-Integrated Tax (Rate) | Schedule III, S. No. 154 |
| 9972 | Real estate services | CGST | 9% | 11/2017-Central Tax (Rate) | Sl. No. 16, Heading 9972 |
| 9972 | " | UTGST | 9% | 11/2017-Union Territory Tax (Rate) | Sl. No. 16, Heading 9972 |
| 9972 | " | IGST | 18% | 8/2017-Integrated Tax (Rate) | Sl. No. 16, Heading 9972 |
| 9987 | Maintenance, repair and installation (except construction) services | CGST | 9% | 11/2017-Central Tax (Rate) | Sl. No. 25, Heading 9987 |
| 9987 | " | UTGST | 9% | 11/2017-Union Territory Tax (Rate) | Sl. No. 25, Heading 9987 |
| 9987 | " | IGST | 18% | 8/2017-Integrated Tax (Rate) | Sl. No. 25, Heading 9987 |

Full URLs are on every rule in `accountant/rules/gst_rates.py` and in
`artifacts/ground_truth/rules/corpus.json`.

### 4820 is a conflict, and the conflict is in the source

Notification 1/2017 prints heading 4820 **twice**, in two schedules, at two
rates. At four digits the code cannot tell exercise books from account books. The
corpus reports the conflict and refuses; it does not pick the commoner one. The
conflict was not manufactured for the test suite — it is on the page.

### Where the intra-State and inter-State split comes from

From the operative paragraph of each notification, read verbatim off the
retrieved documents: the CGST and UTGST notifications levy on the "intra-State
supply", the IGST ones on the "inter-State supply". The corpus needs no separate
authority for that, because the authority is the notification it already cites.

---

## 3. Sources that could not be retrieved — every one `SOURCE_UNVERIFIED`

| URL | Error, exactly | What it would have supported |
|---|---|---|
| `https://taxinformation.cbic.gov.in/view-pdf/1010211/ENG/Notifications` | `unable to verify the first certificate` | the current rate schedule |
| `https://taxinformation.cbic.gov.in/view-pdf/1010100/ENG/Notifications` | `unable to verify the first certificate` | notification 9/2025-Central Tax (Rate) |
| `https://www.cbic.gov.in/resources/htdocs-cbec/gst/notfctn-9-2025-cgst-rate-english.pdf` | `HTTP 500 Internal Server Error` | the same, from the main CBIC host |
| `https://www.cbic.gov.in/resources/htdocs-cbec/gst/notfctn-1-2017-cgst-rate-english.pdf` | `HTTP 500 Internal Server Error` | a second copy of 1/2017-CTR |
| `https://cbic-gst.gov.in/pdf/IGST-Act-Updated-30092020.pdf` | `HTTP 404 Not Found` | IGST Act ss. 7, 8, 10, 12 |
| `https://cbic-gst.gov.in/hindi/igst-act.html` | `HTTP 404 Not Found` | the same, from the Acts index |
| State Government SGST rate notifications | not attempted — outside the Q1 = A hierarchy | the SGST half of a State intra-State supply |
| `https://courier.cbic.gov.in/ECCS/…9_2025-INTEGRATED TAX (RATE)….pdf` | retrieved, then **rejected** | the post-2025 IGST schedule |

**The last row is the one worth reading twice.** That file *is* served from a
`cbic.gov.in` host and it *does* contain notification 9/2025. Its pages are
headed "The Institute of Chartered Accountants of India — GST & Indirect Taxes
Committee" and it is marked "[UPDATED] [As corrected by corrigendum, dated
18-9-2025]". It is a third-party consolidation of the notification, not the
notification as issued. **No rate was taken from it.** A government hostname is
not the same as a government document, and a corpus that cannot tell the
difference will eventually cite the wrong one.

### What the gaps cost

| The gap | What the engine does instead |
|---|---|
| no post-2022 rate notification | a supply dated after **2017-08-17** gets `beyond_amendment_check` and the decision is UNCLEAR |
| no SGST source | an intra-State supply inside a **State** gets `source_unverified` and the decision is UNCLEAR |
| no IGST Act sections | the place of supply must be **stated**; it is never derived from the nature of the transaction |

All three are refusals. All three are visible. **None of them is a 2017 rate
quietly applied to a 2026 invoice**, which is what treating an absent end date as
"valid forever" would have produced.

---

## 4. The four case blocks

| Block | Required | Measured |
|---|---|---|
| intra-State, CGST + SGST/UTGST correct | 20/20 | **20/20** |
| inter-State, IGST correct | 20/20 | **20/20** |
| missing place of supply, refused | 10/10 | **10/10** |
| unknown, conflicting or stale rule, refused | 10/10 | **10/10** |
| guessed rates | 0 | **0** |
| guessed states | 0 | **0** |
| unsafe posts | 0 | **0** |
| false VALID results | 0 | **0** |
| uncited production rules | 0 | **0** |
| runtime tax API calls | 0 | **0** |

Expected amounts were computed in `scripts/build_gst_rule_cases.py` with `Decimal`
arithmetic and refused unless whole in paise. The engine multiplies integer paise
by integer basis points. Two arithmetics, written apart, agreeing on sixty cases.

---

## 5. Six mutations, six kills

Each guard was removed or inverted, the suite was run, and the tree was restored
from git. A guard nothing can violate is a guard nobody can trust.

| # | Mutation | Result | First tests that caught it |
|---|---|---|---|
| M1 | a rule loads without a source URL | **RED** | `test_a_rule_with_a_missing_source_field_is_rejected_at_load[url]` |
| M2 | a rule loads without a retrieval date | **RED** | `test_a_rule_with_a_missing_source_field_is_rejected_at_load[retrieval_date]` |
| M3 | an unknown code falls back to a similar code's rate | **RED**, 3 failures | `test_no_neighbour_of_a_known_code_inherits_its_rate`, `test_a_case_with_an_unknown_conflicting_or_stale_rule_refuses`, `test_no_case_in_the_pack_produces_a_false_valid` |
| M4 | an intra-State supply is calculated as IGST | **RED**, 27 failures | `test_an_intra_state_case_splits_into_cgst_and_sgst_or_utgst` and the whole intra-State block |
| M5 | a missing place of supply is inferred from the supplier | **RED**, 8 failures | `test_a_case_with_no_usable_place_of_supply_refuses`, `test_a_gstin_beside_a_supplier_state_still_cannot_supply_the_place_of_supply` |
| M6 | the posting boundary is removed so a GST bill posts | **RED**, 34 failures | `test_a_gst_bill_without_tax_lines_cannot_be_valid`, `test_a_gst_bill_over_http_writes_nothing_and_moves_no_paise`, `test_a_connector_refusal_cannot_happen_after_the_application_said_valid` |

M6 is the important one. Removing the two Phase 7 guards — the check before the
decision and the refusal at the wire — takes 34 tests down, not one. The boundary
is held in more than one place and every one of them is load-bearing.

---

## 6. Posting

Owner decision Q3 = D, unchanged and enforced in four places, none of which this
work touched:

    accountant/schema.py            Voucher.needs_tax_lines
    accountant/checks.py            tax_lines_can_be_posted
    accountant/problems.py          UNANSWERABLE_CHECKS
    accountant/tallyio/real.py      check_writable

Plus two new ones inside the engine: `tax.decision.POSTING_ENABLED` is False, and
a `TaxDecision` raises if it is constructed with posting enabled. Neither
replaces the four above; they make the intent local so a future reader inherits
it rather than having to remember it.

**The engine can compute a bill the product refuses to post, and both are
correct.** `tests/test_gst_posting_boundary.py` pins exactly that: the same
₹1,000 of cement in Chandigarh comes back as ₹140 CGST and ₹140 UTGST with a
citation, and the voucher carrying that tax is still refused by both the
application and the connector.

| | |
|---|---|
| GST calculation engine | MEASURED |
| GST rules engine | MEASURED |
| GST simulated ledger mapping | MEASURED |
| GST real-Tally posting | HUMAN_EVIDENCE_REQUIRED |
| GST production posting | DISABLED |

The first three are recorded as measured **only because** the gates in section 4
passed on this branch, from this worktree, with the imported package verified to
come from it.

---

## 7. What is honestly not done

- **The corpus is not current.** It holds 2017 rates because those are the ones
  that could be retrieved. It refuses anything dated later rather than pretending
  otherwise. Fixing this needs one working fetch from `taxinformation.cbic.gov.in`.
- **There is no SGST rate for any State.** Not an oversight — no CBIC document
  carries one, and owner decision Q1 = A admits no other kind of source.
- **The place of supply is never derived.** Sections 10 and 12 of the IGST Act
  are not implemented, because the CBIC copy of the Act returned 404 twice.
- **Rounding is not implemented.** A tax that is not whole paise is refused
  rather than rounded, because no retrieved document says how to round it.
- **The jurisdiction State/Union-Territory classification is fixture evidence**,
  supplied on each case, not a table the product ships. The engine compares it and
  never infers it.
