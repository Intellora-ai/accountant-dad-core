# Phase 9 — do account mappings transfer between organisations?

**Evidence class: PUBLIC_DATA_EVIDENCE.**
Every number below comes from UK central-government spend files, published free
under the Open Government Licence v3.0, retrieved 2026-08-08.

**UK data tests the MECHANISM and the TRANSFER question. It does not prove
performance on Indian customer books.** No number here may be quoted as if it did.

Output hash of the whole experiment:
`e44982a67daea58105e1665d4b339ea1feadfeadbb399283f4b33878b791b016`

> **This hash has changed twice, both times on 2026-08-10, and the finding did
> not change either time.** The most recent move is section 9.2: the Phase 8
> PR-2 root-cause fix stopped `magnitude` counting rows that are not dated
> before an entry into that entry's ceiling, so DHSC's within-department N1
> fell from 33.33% to 19.05% and two lines of the experiment text changed. The
> hash before that move was
> `98d60c6099434db74c4301f9427568562abb0d4fe136f620a6c192e1479701c0`.
>
> **The earlier move, section 9.1.** The hash before it was
> `83cc858f42443fa7aa0753d7ece774337be4cb54d5d8260c1d8dfdfac68f12f4`.
> Owner decision D-05 made a company's legal form part of its identity, so one
> supplier key in the output text became `accenture_uk_ltd` where it used to be
> `accenture_uk`. That is the entire byte difference. 30 pairs, 30 of 30 at
> 0.00% cross-department, and 86.21% best within-department are the same numbers
> before and after. Section 9.1 shows the working.

---

## 1. The answer, in one line

**Mappings do not transfer. Zero of 30 department pairs transferred anything.**

Every pair lost 100% of its within-department accuracy when it moved to another
organisation. The best cross-department score across all 30 pairs was 0.00%.

---

## 2. What was asked, and what was done

The question:

> Does an account mapping learned at one organisation predict the account at a
> different organisation?

The two possible answers, and what each one costs:

| If it… | Then… |
|---|---|
| transfers | charts of accounts are not private dialects. A shared prior is worth building. |
| does not transfer | every customer is a permanent cold start. `memory/` is the only thing that can work. Any pooled model is wasted effort. |

The experiment:

1. Take one department. Cut its rows in half, in published order.
2. Build a memory index from the earlier half, using the shipped
   `accountant/memory/index.py`. Nothing in it was written for this experiment.
   It did change once since the first measurement — D-05, section 9.1 — and the
   result did not move.
3. **Within** = that index predicting the same department's later half.
4. **Cross** = that same index predicting a *different* department's later half.
5. **Gap** = within minus cross. One number per pair. Never pooled.

Only an answer that names the exact published account counts as correct.
"It did not answer" and "it answered wrongly" are counted separately, because
averaging them together hides which one happened.

No new harness was built. `accountant/ingest/` loaded the data,
`accountant/ingest/crossorg.py` ran the comparison, and
`accountant/score/harness.py` scored it, all unmodified.

---

## 3. The corpus

Seven UK departments published spend over £25,000 for November 2025 under the
same statutory duty. The committed slices:

| Dept | Full name | Rows in slice | Loaded | Train | Test | Account labels |
|---|---|---|---|---|---|---|
| MHCLG | Ministry of Housing, Communities and Local Government | 57 | 57 | 28 | 29 | 2 |
| DHSC | Department of Health and Social Care | 41 | 41 | 20 | 21 | 7 |
| DFT | Department for Transport | 47 | 47 | 23 | 24 | 16 |
| DWP | Department for Work and Pensions | 54 | 54 | 27 | 27 | 15 |
| DEFRA | Department for Environment, Food and Rural Affairs | 38 | 38 | 19 | 19 | 11 |
| HMT | HM Treasury | 46 | 46 | 23 | 23 | 20 |
| DBT | Department for Business and Trade | 28 | **0** | — | — | — |

283 rows load. 6 departments take part. 30 ordered pairs.

### The DBT hazard, said out loud

**DBT contributes 0 of 28 rows. All 28 are rejected for empty narration.**

DBT publishes the narration column and leaves every cell in it blank — in the
whole real file, not only the committed slice. So DBT cannot be either side of
a pair. It was not silently dropped, and it is not a bad-luck sample: it is what
a real department looks like when the column exists and the data does not.

---

## 4. The result — one gap number per pair

30 ordered pairs. No pooling.

| A (index) | B (tested) | Train rows | Test rows | Within | Cross | **Absolute gap** | Relative gap | Confidently wrong | Supplier recognised | Cross N1 |
|---|---|---|---|---|---|---|---|---|---|---|
| MHCLG | DHSC | 28 | 21 | 86.21% | 0.00% | **+86.21%** | +100.00% | 0 | 0/21 | 0.00% |
| MHCLG | DFT | 28 | 24 | 86.21% | 0.00% | **+86.21%** | +100.00% | 0 | 0/24 | 0.00% |
| MHCLG | DWP | 28 | 27 | 86.21% | 0.00% | **+86.21%** | +100.00% | 0 | 0/27 | 0.00% |
| MHCLG | DEFRA | 28 | 19 | 86.21% | 0.00% | **+86.21%** | +100.00% | 0 | 0/19 | 0.00% |
| MHCLG | HMT | 28 | 23 | 86.21% | 0.00% | **+86.21%** | +100.00% | 0 | 0/23 | 0.00% |
| DHSC | MHCLG | 20 | 29 | 33.33% | 0.00% | **+33.33%** | +100.00% | 0 | 0/29 | 0.00% |
| DHSC | DFT | 20 | 24 | 33.33% | 0.00% | **+33.33%** | +100.00% | 0 | 0/24 | 0.00% |
| DHSC | DWP | 20 | 27 | 33.33% | 0.00% | **+33.33%** | +100.00% | 0 | 0/27 | 0.00% |
| DHSC | DEFRA | 20 | 19 | 33.33% | 0.00% | **+33.33%** | +100.00% | 0 | 0/19 | 0.00% |
| DHSC | HMT | 20 | 23 | 33.33% | 0.00% | **+33.33%** | +100.00% | 0 | 0/23 | 0.00% |
| DFT | MHCLG | 23 | 29 | 50.00% | 0.00% | **+50.00%** | +100.00% | 0 | 0/29 | 0.00% |
| DFT | DHSC | 23 | 21 | 50.00% | 0.00% | **+50.00%** | +100.00% | 0 | 0/21 | 0.00% |
| DFT | DWP | 23 | 27 | 50.00% | 0.00% | **+50.00%** | +100.00% | 0 | 0/27 | 0.00% |
| DFT | DEFRA | 23 | 19 | 50.00% | 0.00% | **+50.00%** | +100.00% | 0 | 0/19 | 0.00% |
| DFT | HMT | 23 | 23 | 50.00% | 0.00% | **+50.00%** | +100.00% | 0 | 0/23 | 0.00% |
| DWP | MHCLG | 27 | 29 | 62.96% | 0.00% | **+62.96%** | +100.00% | 0 | 0/29 | 0.00% |
| DWP | DHSC | 27 | 21 | 62.96% | 0.00% | **+62.96%** | +100.00% | 0 | 0/21 | 0.00% |
| DWP | DFT | 27 | 24 | 62.96% | 0.00% | **+62.96%** | +100.00% | 0 | 0/24 | 0.00% |
| DWP | DEFRA | 27 | 19 | 62.96% | 0.00% | **+62.96%** | +100.00% | **3** | 3/19 | 0.00% |
| DWP | HMT | 27 | 23 | 62.96% | 0.00% | **+62.96%** | +100.00% | **3** | 3/23 | 0.00% |
| DEFRA | MHCLG | 19 | 29 | 5.26% | 0.00% | **+5.26%** | +100.00% | 0 | 0/29 | 0.00% |
| DEFRA | DHSC | 19 | 21 | 5.26% | 0.00% | **+5.26%** | +100.00% | 0 | 0/21 | 0.00% |
| DEFRA | DFT | 19 | 24 | 5.26% | 0.00% | **+5.26%** | +100.00% | 0 | 0/24 | 0.00% |
| DEFRA | DWP | 19 | 27 | 5.26% | 0.00% | **+5.26%** | +100.00% | 0 | 0/27 | 0.00% |
| DEFRA | HMT | 19 | 23 | 5.26% | 0.00% | **+5.26%** | +100.00% | 0 | 0/23 | 0.00% |
| HMT | MHCLG | 23 | 29 | 4.35% | 0.00% | **+4.35%** | +100.00% | 0 | 0/29 | 0.00% |
| HMT | DHSC | 23 | 21 | 4.35% | 0.00% | **+4.35%** | +100.00% | 0 | 0/21 | 0.00% |
| HMT | DFT | 23 | 24 | 4.35% | 0.00% | **+4.35%** | +100.00% | 0 | 0/24 | 0.00% |
| HMT | DWP | 23 | 27 | 4.35% | 0.00% | **+4.35%** | +100.00% | 0 | 0/27 | 0.00% |
| HMT | DEFRA | 23 | 19 | 4.35% | 0.00% | **+4.35%** | +100.00% | 0 | 0/19 | 0.00% |

### What each column means

| Column | Meaning |
|---|---|
| Train rows | Rows of A's own history the index learned from. |
| Test rows | Rows of B's later half the index was asked about. Every one counts, including the ones it declined to answer. |
| Within | A's index on A's own later half. |
| Cross | A's index on B's later half. |
| **Absolute gap** | Within minus cross. The headline number for the pair. |
| Relative gap | The gap as a share of the within result. +100% means the whole of it was lost. |
| Confidently wrong | The index gave one clear answer and the answer was wrong. This is the false-alarm number that matters for transfer. |
| Supplier recognised | Test rows where the index had ever seen the supplier. This is coverage. |
| Cross N1 | The score harness's false alarms per 100 clean entries, run with A's chart and A's history against B's entries. **Read section 6 before trusting it.** |

### The one thing to take from the table

The within-department numbers range from 4.35% to 86.21%. That is a twenty-fold
spread across six organisations doing the same job in the same month.
The cross-department number is 0.00% for every single pair.

Pooling would have reported "0 of 715 correct". That happens to be the same
answer here, but it would have thrown away the fact that MHCLG's index is good
at MHCLG and useless everywhere else. That is why the rule is one gap per pair.

---

## 5. Why cross is zero — the mechanism

Two checks, both of which would have shown transfer if transfer existed.

### Check 1: do two departments ever use the same account name?

**No. Not once.**

71 distinct account labels across the six departments. Compared casefolded, so
capitalisation differences do not count as differences.

| Departments sharing an account label | Count |
|---|---|
| All 15 possible department pairings | **0 shared labels** |

Four labels for the same idea, from four different departments:

| Department | How it names IT spend |
|---|---|
| DWP | `EXP - PURCHASE OF GOODS/SERVICES - OTHER ICT COSTS` |
| DEFRA | `EXP - PURCHASE OF GOODS/SERVICES - ICT OUTSOURCING & MAINT/SUPPORT` |
| HMT | `IT Software Services & Maintenance Non VAT Recoverable` |
| DFT | `Business Services` |

Same country. Same statutory duty. Same month. Four private dialects.

### Check 2: when two departments buy from the same supplier, do they file it the same way?

**No. Both times, they disagreed.**

Only two suppliers appear in one department's history and another's test rows.
Both are IT firms. Both were filed differently by the receiving department:

| Supplier | Department A filed it as | Department B filed it as | Agree? |
|---|---|---|---|
| Accenture (UK) Ltd | DWP: `EXP - … - OTHER ICT COSTS` | DEFRA: `EXP - … - ICT OUTSOURCING & MAINT/SUPPORT`, and once `EXP - … - OTHER PROFESSIONAL SERVICES` | No |
| Softcat PLC | DWP: `EXP - … - SUPPORT - ICT OUTSOURCING SOFTWARE LICENCES` | HMT: `IT Software Services & Maintenance Non VAT Recoverable` | No |

**Confident cross-department answers: 6. Correct: 0.**

DEFRA does not even agree with itself: it filed the same supplier under two
different accounts inside one month.

So the zero is not an accident of small overlap. Where the overlap existed, the
mapping was wrong every time.

---

## 6. The trap in the harness numbers

Run the score harness across departments and it reports:

| Measure | Within department | Across departments |
|---|---|---|
| Entries scored | 143 | 715 |
| False alarms | 9 | **0** |
| N1 status | 1 of 6 departments FAIL | **PASS on all 30 pairs** |
| Entries the system calls VALID | 92 of 143 (64%) | **0 of 715 (0%)** |
| Entries turned into a question for the human | 51 of 143 (36%) | **715 of 715 (100%)** |

**Zero false alarms across departments is not a good result. It is silence.**

Every active detector needs to recognise the supplier before it can fire. Across
organisations it recognises nothing, so it says nothing, so it raises no false
alarm and N1 reads a perfect 0.00%.

A metric that scores silence and correctness the same cannot detect a cold
start. N1 must never be quoted on its own for a transfer claim.

The number that is not silent: **100% of cross-department entries land on the
human's desk as a question.** That is the real cost of a cold start, and it is
the number to watch.

---

## 7. I tried to prove myself wrong

Confirming evidence is cheap. Here is what I went looking for that would have
overturned the conclusion, and what I found.

| What would have shown transfer works | What was found |
|---|---|
| Two departments sharing an account label | 0 of 71 labels shared. |
| A shared supplier filed the same way twice | 2 shared suppliers, 0 agreements. |
| A shared naming scheme across departments | **Found one, and it is weak.** DWP and DEFRA both use top-level prefixes `EXP -` and `PPE -` — the same ERP family. But the leaf accounts still differ, DWP→DEFRA still scored 0.00%, and the one supplier they share was still filed differently. A shared prefix is not a shared account. |
| A cross-department result beating a within one | None. The report format prints a negative gap with its sign, and a test pins that it can, so a transfer win could not have been hidden. It did not happen. |
| More data closing the gap | **Cannot be ruled out.** See limitations. |

---

## 8. Limitations — read these before quoting anything

| # | Limitation | Status |
|---|---|---|
| 1 | The corpus is committed slices, 283 rows of 16,011 published. On the full files, supplier overlap would rise, and the cross number could rise with it. | **NOT_MEASURABLE** on the full files — only the slices are committed. |
| 2 | Six departments, one month, one country. Six is six. | Stated, not fixed. |
| 3 | This is UK public-sector data. It tests the mechanism and the transfer question. It **does not prove performance on Indian customer books**. | Permanent. No Indian transaction-level ledger data is published. |
| 4 | The index keys on **supplier**, not on narration text. A narration-based or model-based method might transfer where this one does not. This experiment does not test that. | **NOT_MEASURABLE** here. |
| 5 | N2 (review time) needs R and D, self-timed inputs nobody has supplied. | **NOT_MEASURABLE**. No default was invented. |
| 6 | N3 (catch rate) needs injected errors. Nobody injects errors into a real government ledger. | **NOT_MEASURABLE**. The harness reports FAIL on absent evidence, not a vacuous PASS. |
| 7 | DBT contributes nothing. 0 of 28 rows load, all rejected for empty narration. | Stated in section 3, not hidden. |
| 8 | UK council data is not used and must not be: about 2,600 publishers, no schema stability, a live `EFEFCTIVE` header typo in a real Rochdale file. | Out of scope by decision. |

Limitation 1 is the one that could most change the answer, and it is the one to
attack next: **load the full published files and re-run.** Note what it would
have to overturn. Supplier overlap rising does not help unless the overlapping
suppliers are filed the *same way*, and both times we observed that, they were
not.

---

## 9. Reproducibility

Full detail in `artifacts/phase9_reproducibility_manifest.json`.

| Item | Value |
|---|---|
| Determinism verdict | **REPRODUCIBLE** |
| Runs | 10 — every seed twice |
| PYTHONHASHSEED values | 0, 1, 12345, 99991, random |
| Identical output hashes | 10 of 10 |
| Output sha256 | `e44982a67daea58105e1665d4b339ea1feadfeadbb399283f4b33878b791b016` |
| Output sha256 before the Phase 8 PR-2 fix | `98d60c6099434db74c4301f9427568562abb0d4fe136f620a6c192e1479701c0` — see 9.2 |
| Output sha256 before D-05 | `83cc858f42443fa7aa0753d7ece774337be4cb54d5d8260c1d8dfdfac68f12f4` — see 9.1 |
| Corpus sha256 | `830b987153adc8c999a1bb247ccb33ae4d8e4d5db4429883553bc1f710a8bd01` |
| Taxonomy sha256 | `e8a8a1d75545033d3114f3c7c0563a76b938b8443910defd6917112e38369df7` |
| Configuration sha256 | `04abf19a3291d7e5e3dda912eae95d7ffd1c75de37f90fc581829259534b1236` |
| Code measured at commit | `2e11cea` |
| Python | 3.14.6 (CPython) |
| OS | macOS-26.4.1-arm64-arm-64bit-Mach-O, arm64 |
| Tools | pytest 9.1.1, ruff 0.16.1, coverage 7.15.4, pyright 1.1.411 |
| Network | none — every byte came from a committed fixture |

Two runs in one process share a hash seed, so they cannot catch set or dict
iteration order leaking into the output. Five seeds, one of them random, can.
All ten agreed exactly.

To re-run:

```
PYTHONHASHSEED=0 COVERAGE_CORE=pytrace .venv/bin/python -c \
  "from tests.test_cross_organisation import experiment_text; print(experiment_text(), end='')"
```

The measurement code lives in `tests/test_cross_organisation.py`, which is
committed. There is no scratch script, so anybody with the repository can
reproduce the hash.

### 9.1 The output hash changed on 2026-08-10. The finding did not.

If you saw an earlier copy of this report, it quoted a different hash. Here is
the whole of what happened, so nobody has to take the new number on trust.

| | |
|---|---|
| Hash before | `83cc858f42443fa7aa0753d7ece774337be4cb54d5d8260c1d8dfdfac68f12f4` |
| Hash after this move | `98d60c6099434db74c4301f9427568562abb0d4fe136f620a6c192e1479701c0` |
| Reason, in one line | Owner decision D-05 made a company's legal form part of its identity, so `normalise_vendor` stopped stripping it. |

D-05 (2026-08-10) rules that "Ltd", "PLC" and the rest are part of who was paid,
not noise to be folded away. `accountant/memory/index.py` now keeps them:

```
normalise_vendor("Accenture (UK) Ltd")
  before D-05   ->  accenture_uk
  after  D-05   ->  accenture_uk_ltd
```

That key is printed once in the experiment text — in the shared-supplier block
of section 5, check 2. So the text's bytes changed, so its sha256 changed.

**One token, in one place.** Take the current 147-line experiment text, put
`accenture_uk` back where `accenture_uk_ltd` now stands, and the sha256 is
`83cc858f42443fa7aa0753d7ece774337be4cb54d5d8260c1d8dfdfac68f12f4` again —
the old hash, exactly. Every other byte is untouched, which is another way of
saying every number in this report is untouched.

Side by side:

| Measurement | Before D-05 | After D-05 |
|---|---|---|
| Ordered pairs | 30 | 30 |
| Pairs at 0.00% cross-department | 30 of 30 | 30 of 30 |
| Best cross-department accuracy | 0.00% | 0.00% |
| Best within-department accuracy | 86.21% (MHCLG) | 86.21% (MHCLG) |
| Largest gap | +86.21% | +86.21% |
| Shared suppliers, and agreements between them | 2, and 0 | 2, and 0 |

**The new hash was re-earned, not edited in.** All ten runs — five seeds, twice
each — were executed again on that branch, and each one independently reported
`98d60c60…`. None of the eleven hashes in the manifest was copied from another.
Each run also asserted, before producing any text, that `accountant` had been
imported from this worktree and not from another checkout; a run that failed
that check would have raised rather than been recorded.

What did **not** change, and so was not re-measured: the corpus hash, the
taxonomy hash, the configuration hash, the Python version, the OS, and the tool
versions. The fixtures were not touched. D-05 changed code, not data.

---

## 10. Sources

Open Government Licence v3.0 —
https://www.nationalarchives.gov.uk/doc/open-government-licence/version/3/
Retrieved 2026-08-08. Month: 2025-11.

- MHCLG — https://assets.publishing.service.gov.uk/media/695296452054b690f7cd3d13/MHCLG_Spend_over__25k_November_2025.csv
- DHSC — https://assets.publishing.service.gov.uk/media/6a1ffb3559fb7a60f827f6d9/DHSC-spending-over-25000-november-2025-revised.csv
- DFT — https://assets.publishing.service.gov.uk/media/698da3d57da91680ad7f42e8/dft-spending-over-25000-november-2025.csv
- DWP — https://assets.publishing.service.gov.uk/media/6984b79f2df808759a7bd74f/transparency-for-publication-november-2025.csv
- DEFRA — https://assets.publishing.service.gov.uk/media/697a075233bc3750e7652fcd/November_Over__25K_Transparency.csv
- HMT — https://assets.publishing.service.gov.uk/media/697a080f005d288bf850dea7/HMT_spending_over_25_000_for_Nov_25.csv
- DBT — https://assets.publishing.service.gov.uk/media/69a0223d532c9ad91ebbcd39/dbt-spending-over-25k-november-2025.csv

---

## 11. What this means for the product

**Every customer is a cold start. `memory/` is the thing that works. A pooled
model trained on other people's books is wasted effort.**

What follows from that:

| Decision | Direction |
|---|---|
| A shared prior across customers | **Do not build it.** 0 of 30 pairs transferred. Nothing to pool. |
| `accountant/memory/` | **This is the product.** It is the only thing that scored above zero. |
| Day one for a new customer | Plan for 100% questions, then decay. Do not promise accuracy on day one. |
| The onboarding metric | How fast questions fall as the customer's own history grows. Not accuracy on a first import. |
| Selling on "our model learned from 10,000 books" | **Do not.** The measurement says it would not help this customer. |
| N1 as a transfer metric | **Do not use it alone.** It reads 0.00% PASS on a system that knows nothing. |

One honest sentence:

> An account mapping learned at one organisation is worth exactly nothing at
> another, so the product's value has to come from how fast it learns one
> customer's own book — not from what it learned from anyone else's.

---

**Label: PUBLIC_DATA_EVIDENCE.** UK central-government data. Tests the mechanism
and the transfer question. Does not prove performance on Indian customer books.

### 9.2 The output hash changed again on 2026-08-10. The finding did not.

The second move, and the reason it is a smaller thing than a new hash looks.

| | |
|---|---|
| Hash before | `98d60c6099434db74c4301f9427568562abb0d4fe136f620a6c192e1479701c0` |
| Hash now | `e44982a67daea58105e1665d4b339ea1feadfeadbb399283f4b33878b791b016` |
| Reason, in one line | Phase 8 PR-2 stopped `magnitude` counting rows that are not dated before an entry into that entry's ceiling. |

`accountant/detect/detectors.py:prior_amounts` is the whole of the change. Six
of the nine false alarms in `artifacts/detector_gate.md` were one DHSC account,
`Additions NCB PDC`, and three of those six were raised against a history dated
`2025-11-03` — the same day as the entry being judged. A payment made on the
third is not evidence about the range a payment made on the third falls outside
of, so those three are gone.

**Two lines, measured by unified diff of the 147-line experiment text:**

```
-  DHSC   entries  21   false alarms  7   N1    33.33%   FAIL   questions  14/21
+  DHSC   entries  21   false alarms  4   N1    19.05%   FAIL   questions  13/21
-  all    questions 51/143 within department
+  all    questions 50/143 within department
```

Every other byte is identical.

Side by side:

| Measurement | Before the fix | After the fix |
|---|---|---|
| Ordered pairs | 30 | 30 |
| Pairs at 0.00% cross-department | 30 of 30 | 30 of 30 |
| Best cross-department accuracy | 0.00% | 0.00% |
| Best within-department accuracy | 86.21% (MHCLG) | 86.21% (MHCLG) |
| Largest gap | +86.21% | +86.21% |
| Departments failing N1 inside their own department | 1 of 6 (DHSC) | 1 of 6 (DHSC) |

The transfer finding is untouched. DHSC still fails N1 on its own book; it
fails by less.

**The new hash was re-earned, not edited in.** All ten runs — five seeds, twice
each — were executed again on this branch, and each one independently reported
`e44982a6…`.
