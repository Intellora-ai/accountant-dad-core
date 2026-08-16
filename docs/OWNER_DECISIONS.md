# OWNER_DECISIONS — Accountant Dad

| | |
|---|---|
| **Purpose** | Owner answers, recorded verbatim, so they are never asked again. This file holds the answer and the detail that must survive with it. It does not hold status — status is in [`PROJECT_STATE.md`](./PROJECT_STATE.md) — and it does not hold design — design is in [`ARCHITECTURE.md`](./ARCHITECTURE.md). |
| **Who may update** | The owner, or Claude on the owner's instruction. **Append only.** An answer already recorded here is not edited, re-litigated, or re-asked. |
| **Relationship to `DECISIONS.md`** | [`DECISIONS.md`](./DECISIONS.md) and `docs/CONTROL_PLANE.yaml` track the numbered `D-nn` decision series. The answers in this file arrived as a `Q1`–`Q8` scope questionnaire, **not** as `D-nn` decisions, and they have deliberately not been given `D-nn` ids. Inventing ids for them would risk a fourth id collision in a project that already has three. |

**Labels used in this file, and nothing else:**

```
PASS · FAIL · BLOCKED · NOT_MEASURED · INVALIDATED · GITHUB_REQUIRED
HUMAN_ACTION_REQUIRED · OWNER_DECISION_REQUIRED · OPTIONAL_HUMAN_INPUT
BLOCKED_ON_HUMAN_EVIDENCE · NOT_IMPLEMENTED · SOURCE_UNVERIFIED
NOT_SELECTED · INCOMPLETE
```

---

## 1. The eight scope answers — 2026-08-10 — FINAL

Answered by the owner. **Final. Never ask these again.** Recorded so the work
can start without another questionnaire.

```
Q1 = A     official CBIC and Income Tax Department notifications/circulars
Q2 = C     only codes seen in the company's verified history
Q3 = D     do NOT implement GST posting in Phase 8
Q4 = B     stub extractor only, no production backend selected
Q5 = C     UK government data where it fits, synthetic elsewhere, each labelled
Q6 =       five sequential PRs
Q7 = B     fix the DHSC "Additions NCB PDC" root cause BEFORE enabling four detectors
Q8 = A     explicit system/operator actor labels
PHASE 8 SCOPE = FROZEN-PLAN
```

The full operational detail behind each answer — every count, every gate, every
report field — is in [`artifacts/phase8_scope.md`](../artifacts/phase8_scope.md).
The design consequences are in [`ARCHITECTURE.md` §18](./ARCHITECTURE.md).
What follows is the part of each answer that must survive with the answer
itself.

---

### Q1 — rule sources

Authority hierarchy, in order:

1. official CBIC notification / circular
2. official Income Tax Department notification / circular
3. official GST Council material — **supplementary context only**
4. **no commercial API**

GST Council press releases and rate-finder pages may be recorded as
supplementary references. They can never be the sole authority for a production
rule.

Every rule carries: source URL · source title · issuing authority ·
notification/circular number if available · retrieval date · effective date ·
rule version · jurisdiction.

Corpus validation gates:

```
uncited production rules                  = 0
rules with only commercial/API sources    = 0
rules with missing retrieval date         = 0
commercial API dependencies               = 0
runtime tax API calls                     = 0
```

If an official source cannot be retrieved or verified: rule status
`SOURCE_UNVERIFIED`, the rule cannot load into production, the result is
`NOT_FOUND` or `UNCLEAR`, and there is no automatic posting.
**Never silently use a stale rate.**

---

### Q2 — corpus size

Not ~21,000 HSN/SAC entries. Not a top-N. Not a frequency cutoff. Scope is
exactly: codes observed in verified company history, plus explicitly approved
test fixtures.

Every unseen code returns `NOT_FOUND` and is **never guessed from a similar
code**.

Report: total codes · GST HSN codes · GST SAC codes · TDS sections ·
Schedule III heads · source-verified count · source-unverified count ·
unseen-code count during evaluation.

Gates:

```
100% of loaded production codes have official citations
100% have retrieval dates
100% of unseen codes return explicit NOT_FOUND
0 guessed rates
0 uncited rates
0 silent fallback to a similar code
```

**A code is never added merely to increase coverage.**

---

### Q3 — GST posting stays off

**This is a deliberate safety boundary, not a failed test.**

The rules corpus and the evidence model may be built. Automatic GST posting is
not enabled until a later explicit owner decision.

```
GST posting                              = NOT_IMPLEMENTED
CGST/SGST/IGST split                     = NOT_IMPLEMENTED
place of supply                          = NOT_IMPLEMENTED
GST ledger selection                     = NOT_IMPLEMENTED
successful GST posting with tax lines    = NOT_MEASURED
```

Never post based on supplier GSTIN alone, company history alone, a guessed
state, or a guessed rate. No partial split. No silent place-of-supply
inference.

---

### Q4 — stub extractor only

`StubExtractor` for contract and safety tests. `UnavailableExtractor` stays
supported.

```
real extraction accuracy   = NOT_MEASURED
S2                         = NOT_MEASURED
production backend         = NOT_SELECTED
adapter contract           = measurable
real-reader S2             = NOT_MEASURED
five-input-type real extraction = INCOMPLETE
```

**A stub returning `not_found` cannot satisfy the real extraction-quality
exit.** Extraction is not to be claimed complete. No customer bill goes to a
third party without explicit approval.

---

### Q5 — evaluation corpus

Every case individually labelled one of:

```
SYNTHETIC_EVIDENCE · THIRD_PARTY_PUBLIC_EVIDENCE
REAL_ANONYMISED_EVIDENCE · HELD_OUT_CUSTOMER_LIKE_EVIDENCE
```

Synthetic may test mechanics, schema, provenance and adversarial behaviour —
**never described as real-bill accuracy evidence.**

Counts:

```
5 input types x 20 = 100 input cases
4 detectors   x 25 = 100 detector cases
```

Required:

```
100/100 input cases labelled
100/100 detector cases labelled
100/100 have expected outputs
100/100 have evidence provenance
```

Without real bills: real-bill accuracy `NOT_MEASURED`, `S2` `NOT_MEASURED`.
**Never fill the gap by calling generated documents real.**

---

### Q6 — five sequential PRs

Never one 5,000-line PR.

```
PR-1  five input-type contracts and fixtures
PR-2  four detector expansion and measurements
PR-3  rules corpus and source provenance
PR-4  UI provenance
PR-5  full reversal history
```

Each merges and is confirmed in `origin/main` before the next begins
integration.

---

### Q7 — root cause before the feature

Reproduce the baseline first, verifying every number from the intended
worktree:

```
current aggregate false-alarm rate  = 6.29
all-detector result                 = 36.36
DHSC "Additions NCB PDC" contributes 6 of 9 false alarms
```

Then: isolate that account · determine why it creates false alarms · fix the
root cause **not the threshold** · add a regression test for that account ·
re-run the detector corpus · enable all four · measure N1.

Gate `N1 <= 10`. Acceptance:

```
4/4 detectors active in test mode
4/4 have tests
4/4 have provenance
0 crashes
0 silently skipped results
N1 <= 10
```

If N1 > 10 with all four: detector exit `FAIL`, the phase is not complete, fix
the root cause and re-measure.

**A feature flag may be used during development only — it can never be used to
claim the all-four exit while production runs one detector.**

---

### Q8 — actor labels

Exactly `accountant_dad` for system-generated actions and `operator` for
actions answered through the UI.

```
authenticated user identity = NOT_IMPLEMENTED
actor provenance            = coarse-grained system/operator
```

**Do not claim `operator` is a real authenticated identity.**

Use the existing `action_log`; add no authentication dependency.

Every reversal event carries seven fields: previous state · new state · reason ·
actor · timestamp · company/document scope · evidence.

Test:

```
20/20 events preserve all seven
0 overwritten
0 missing actors
0 missing timestamps
0 missing scopes
0 missing reasons
```

---

## 2. Approved assumptions

```
provenance in UI      = the existing draft screen displays detector/rule,
                        source URL, evidence and explanation per decision
four detectors        = vendor_switch, first_use, magnitude, gst_anomaly
full reversal history = extends the existing action_log
five input types      = text, PDF, PNG, JPG, DOCX
web implementation    = existing stdlib http.server unless separately approved
```

**If implementation inspection contradicts an assumption: do not silently change
scope. Record the contradiction, mark the affected exit `BLOCKED`, and continue
independent work.**

All five were checked against the repository on 2026-08-10 and all five hold.
The one qualification found is recorded in `ARCHITECTURE.md` §18.8: the existing
`action_log` has no `actor` column and no previous-state column, so four of the
seven reversal fields exist today and two do not. Extending the table is a
schema change, not an authentication dependency.

---

## 3. Human-required actions

The table is in [`ARCHITECTURE.md` §16.4](./ARCHITECTURE.md) and the statuses in
[`PROJECT_STATE.md` §41.2](./PROJECT_STATE.md). It is not restated here.

Two things about it that belong with the decisions rather than with the table:

- **None of the five blocks the buildable work.** Schema, rules corpus, detector
  tests, UI provenance and reversal history are all unblocked. Each of the five
  blocks only the exit named in its own `Blocks` column.
- **`H-03` and `H-04` are the same real-world actions as `B-01` and `B-02`**, and
  the id `H-03` separately names a different item in `ARCHITECTURE.md` §16.1.
  Both id sets are kept and cross-referenced. No third id was invented and
  nothing was renumbered. See `ARCHITECTURE.md` §16.5.

---

## 4. OPEN — expense-note posting — `OWNER_DECISION_REQUIRED`

Recorded 2026-08-16 on the owner's instruction. **Nothing here is a decision.**
It is the question, the evidence on both sides, and what the code does today
while the question is open.

### The question

May a `NON_INVOICE_EXPENSE_NOTE` — a person typing
`paid Sharma Traders 4200 for cement` — post on its own, or must it be held for
a person to confirm?

### Why it is open rather than answered

Two owner statements exist and they do not settle each other.

```
2026-08-13  typed_text was added to AUTO_POST_ALLOWED_TIERS on the owner's
            ruling. Rationale recorded in cage/decision.py: the one-entry
            allowlist stopped the product posting anything, and
            demo_safety_cage.py went from `posted 3` to `posted 0` because
            every input in it is a typed sentence. That measurement is what
            the ruling was made on. It is a ruling about READING TIERS.

2026-08-16  Step 1, quoted verbatim in
            artifacts/problem1_document_type_policy.md:
              "If no separate expense-note posting policy exists, the safe
               result is: NON_INVOICE_EXPENSE_NOTE -> REVIEW_REQUIRED or
               BLOCKED. Never silently allow it into the invoice posting
               path."
            It is a ruling about DOCUMENT KINDS, and it is conditional on
            whether a policy exists.
```

The 2026-08-16 document answered that condition itself — *"No separate
expense-note posting policy exists today"* — so it did not treat the tier
ruling as one. Two further places in the repository say the choice is still
outstanding:

- `docs/CAGE_FINDINGS.md`: *"Not done, and it is the owner's call ... I did not
  choose. Choosing it by writing code would be setting a number the owner did
  not give."*
- `docs/CAGE_FINDINGS.md`: *"the choice between 'the tests are stale' and 'the
  cage is too strict for a typed sentence' is still the owner's and has not
  been made."*

### What the code does today, measured

```
paid Sharma Traders 4200 for cement   ->  outcome = valid   (it posts)
```

This is the state after the cage work the owner accepted on 2026-08-16. That
change removed two refusals that were not true of an expense note — a date it
does not have, and a tier stamped by fields nobody read. It did not set out to
answer this question, and the answer it produces has not been ruled on.

**It is not silent.** The draft, its reasons and its operation id are written to
the action log and shown on the page exactly as any other posting is. What is
missing is an owner ruling, not an audit trail.

### The two answers, and what each costs

```
HOLD FOR REVIEW   matches the 2026-08-16 wording. Costs: every typed entry
                  needs a person, which is the workflow demo_safety_cage.py
                  measured as `posted 0` and which the 2026-08-13 ruling was
                  made to end.

MAY POST          matches the 2026-08-13 ruling and what runs today. Costs: a
                  typed sentence carries no document to check against, so the
                  only guards on it are the person who typed it and the
                  non-document laws.
```

### Not done, deliberately

No production behaviour was changed to open or close this. Per the owner's
instruction of 2026-08-16: where no explicit decision exists, mark it and leave
the code alone.

### ANSWERED 2026-08-16 — Option 3, the input source decides

The owner ruled. The question above is closed.

```
a clearly typed one-line expense entry   may reach VALID after normal validation
an expense note EXTRACTED FROM A DOCUMENT must be REVIEW_REQUIRED
ambiguous, conflicting or unsafe evidence remains BLOCKED
document-derived data keeps every safety check unweakened
```

WHAT THE RULING TURNS ON, stated because the two cases look identical on the
page: not the shape of the sentence, but WHERE THE CHARACTERS CAME FROM. Both
carry one amount, one party and no line items. What differs is whether a
machine guessed. A person typing a sentence read nothing, so nothing could be
misread; a reader lifting the same sentence off a page produces a field that
could be wrong, and with no line items and no net there is no arithmetic left
to catch it with. Confidence alone is not evidence enough to write somebody's
books from.

IMPLEMENTED as a split in `decision.DocumentType`:

```
TYPED_EXPENSE_NOTE        person typed it   -> may reach POST
NON_INVOICE_EXPENSE_NOTE  read off a page   -> capped at ASK
```

`decision._needs_a_person` applies the cap, beside the repair ceiling and the
tier ceiling and for the same reason: a ceiling can only lower POST to ASK and
can never overturn a block. `pipeline.evaluate` chooses the kind from
`per_field_source["total_paise"]`, which is the only place that knows whether a
person or a reader produced the characters.

MEASURED, AND IT IS NOT WHAT ANYONE EXPECTED. This ruling was believed to be
what the 147 failing tests were waiting on. It is not. Full suite before the
change: 147 failed / 5177 passed. After: 147 failed / 5182 passed - the same
147 test identities, zero resolved, the five new passes being this ruling's own
tests. The 147 have some other cause and the question above was never their
blocker. That belief is corrected here rather than quietly dropped.
