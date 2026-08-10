# Phase 8 scope — frozen, with every gate and count

Written 2026-08-10. This is the working document for the eight owner answers:
the counts, the gates, the report fields, and the acceptance conditions, in the
form the work needs them.

- The **answers** are in [`docs/OWNER_DECISIONS.md`](../docs/OWNER_DECISIONS.md).
- The **design consequences** are in [`docs/ARCHITECTURE.md` §18](../docs/ARCHITECTURE.md).
- The **statuses** are in [`docs/PROJECT_STATE.md` §41](../docs/PROJECT_STATE.md).

Nothing here overrides those three. Where a number appears in more than one
place it appears because the gate needs it inline, not because it was restated
for emphasis.

**The owner's scope marker, verbatim:**

    PHASE 8 SCOPE = FROZEN-PLAN

**Permitted labels, and nothing else:**

```
PASS · FAIL · BLOCKED · NOT_MEASURED · INVALIDATED · GITHUB_REQUIRED
HUMAN_ACTION_REQUIRED · OWNER_DECISION_REQUIRED · OPTIONAL_HUMAN_INPUT
BLOCKED_ON_HUMAN_EVIDENCE · NOT_IMPLEMENTED · SOURCE_UNVERIFIED
NOT_SELECTED · INCOMPLETE
```

---

## The eight answers

```
Q1 = A     official CBIC and Income Tax Department notifications/circulars
Q2 = C     only codes seen in the company's verified history
Q3 = D     do NOT implement GST posting in Phase 8
Q4 = B     stub extractor only, no production backend selected
Q5 = C     UK government data where it fits, synthetic elsewhere, each labelled
Q6 =       five sequential PRs
Q7 = B     fix the DHSC "Additions NCB PDC" root cause BEFORE enabling four detectors
Q8 = A     explicit system/operator actor labels
```

**These are final and must never be asked again.** The point of writing them
here is that the work starts without another questionnaire.

---

## Q1 · Rule sources and provenance

### Authority hierarchy

| Rank | Source | May be sole authority for a production rule? |
|---:|---|---|
| 1 | Official CBIC notification / circular | Yes |
| 2 | Official Income Tax Department notification / circular | Yes |
| 3 | Official GST Council material | **No — supplementary context only** |
| 4 | Commercial API | **No — not a source and not a dependency** |

GST Council press releases and rate-finder pages may be *recorded* as
supplementary references. They can never be the only thing standing behind a
rule that runs in production.

### The eight fields every rule carries

```
source URL
source title
issuing authority
notification/circular number, if available
retrieval date
effective date
rule version
jurisdiction
```

### Corpus validation gates

| Gate | Required value |
|---|---:|
| uncited production rules | 0 |
| rules with only commercial/API sources | 0 |
| rules with missing retrieval date | 0 |
| commercial API dependencies | 0 |
| runtime tax API calls | 0 |

### When a source cannot be retrieved or verified

```
rule status        = SOURCE_UNVERIFIED
loads in production = no
result shown        = NOT_FOUND or UNCLEAR
automatic posting   = none
```

**Never silently use a stale rate.** An out-of-date rate that posts is worse
than a refusal, because the refusal is visible and the wrong entry is not.

---

## Q2 · Corpus size and scope

### What the corpus is not

- not ~21,000 HSN/SAC entries
- not a top-N
- not a frequency cutoff

### What it is, exactly

```
codes observed in verified company history
+ explicitly approved test fixtures
```

Every unseen code returns `NOT_FOUND`, and is **never guessed from a similar
code**. **A code is never added merely to increase coverage.**

### Report these counts

```
total codes
GST HSN codes
GST SAC codes
TDS sections
Schedule III heads
source-verified count
source-unverified count
unseen-code count during evaluation
```

### Gates

| Gate | Required value |
|---|---|
| loaded production codes with official citations | 100% |
| loaded production codes with retrieval dates | 100% |
| unseen codes returning explicit `NOT_FOUND` | 100% |
| guessed rates | 0 |
| uncited rates | 0 |
| silent fallback to a similar code | 0 |

---

## Q3 · GST posting stays off

**This is a deliberate safety boundary, not a failed test.**

The rules corpus and the evidence model may be built. Automatic GST posting is
not enabled until a later, explicit owner decision.

```
GST posting                              = NOT_IMPLEMENTED
CGST/SGST/IGST split                     = NOT_IMPLEMENTED
place of supply                          = NOT_IMPLEMENTED
GST ledger selection                     = NOT_IMPLEMENTED
successful GST posting with tax lines    = NOT_MEASURED
```

### Never post on any of these

- supplier GSTIN alone
- company history alone
- a guessed state
- a guessed rate

No partial split. No silent place-of-supply inference.

### What the repository already enforces

`tests/test_real_tally.py::test_a_gst_voucher_is_refused_rather_than_silently_stripped`
raises rather than writing a voucher that carries `gst_paise`, with the reason
recorded in the test itself: this connector builds no tax lines, and writing the
voucher anyway would post a wrong statutory entry that looks fine.

So the boundary is not only a plan. It is already a test, and the decision above
keeps it that way.

---

## Q4 · Extraction — stub only

| Item | Status |
|---|---|
| real extraction accuracy | `NOT_MEASURED` |
| S2 | `NOT_MEASURED` |
| real-reader S2 | `NOT_MEASURED` |
| production backend | `NOT_SELECTED` |
| adapter contract | measurable |
| five-input-type real extraction | `INCOMPLETE` |

`StubExtractor` serves the contract and safety tests. `UnavailableExtractor`
stays supported. Both exist today — `accountant/extract/adapter.py:161` and
`accountant/extract/adapter.py:205`.

**A stub returning `not_found` cannot satisfy the real extraction-quality
exit.**

Extraction is not to be described as complete. **No customer bill goes to a
third party without explicit approval** — the backend choice is `H-01`, an open
owner decision.

---

## Q5 · Evaluation corpus and labelling

### One label per case, no case unlabelled

```
SYNTHETIC_EVIDENCE
THIRD_PARTY_PUBLIC_EVIDENCE
REAL_ANONYMISED_EVIDENCE
HELD_OUT_CUSTOMER_LIKE_EVIDENCE
```

Synthetic cases may test mechanics, schema, provenance and adversarial
behaviour. **They are never described as real-bill accuracy evidence.**

### Counts

```
5 input types x 20 = 100 input cases
4 detectors   x 25 = 100 detector cases
```

### Required

| Requirement | Value |
|---|---|
| input cases labelled | 100/100 |
| detector cases labelled | 100/100 |
| cases with expected outputs | 100/100 |
| cases with evidence provenance | 100/100 |

### Without real bills

```
real-bill accuracy = NOT_MEASURED
S2                 = NOT_MEASURED
```

**Never fill the gap by calling generated documents real.** Supplying a
labelled real or anonymised corpus is `H-02`, and it is optional — it unblocks
real-bill accuracy and nothing else.

---

## Q6 · Five sequential PRs

```
PR-1  five input-type contracts and fixtures
PR-2  four detector expansion and measurements
PR-3  rules corpus and source provenance
PR-4  UI provenance
PR-5  full reversal history
```

Each merges and is **confirmed in `origin/main`** before the next begins
integration. Never one 5,000-line PR.

---

## Q7 · Root cause before the feature

### Reproduce the baseline first

Every number verified from the intended worktree before anything is built on
it — the rule that came out of the invalidated cross-organisation measurement,
where both sides imported the unchanged main checkout from `/tmp` and the
comparison could only come out at zero.

```
current aggregate false-alarm rate  = 6.29
all-detector result                 = 36.36
DHSC "Additions NCB PDC" contributes 6 of 9 false alarms
```

All three reconcile with what the repository already records:
`artifacts/detector_gate.md:67` gives the aggregate as 9 of 143 (6.29);
`artifacts/detector_gate.md:186` gives the all-detector figure as 52 of 143
(36.36); and `artifacts/detector_gate.md:144` records that removing
`Additions NCB PDC` takes the aggregate from 9 of 143 to 3 of 143, which is the
6-of-9 contribution.

### Order of work, and the order is the decision

```
1. isolate the account
2. determine why it creates false alarms
3. fix the root cause, NOT the threshold
4. add a regression test for that account
5. re-run the detector corpus
6. enable all four detectors
7. measure N1
```

### Gate and acceptance

Gate: `N1 <= 10`.

| Acceptance condition | Required |
|---|---|
| detectors active in test mode | 4/4 |
| detectors with tests | 4/4 |
| detectors with provenance | 4/4 |
| crashes | 0 |
| silently skipped results | 0 |
| N1 | `<= 10` |

If N1 > 10 with all four active: **detector exit `FAIL`**, the phase is not
complete, fix the root cause and re-measure.

**A feature flag may be used during development only. It can never be used to
claim the all-four exit while production runs one detector.**

The production set today is `SLICE_4_DETECTORS`, which is `(vendor_switch,)` —
one detector — at `accountant/detect/detectors.py:206`.

---

## Q8 · Actor labels

### The two labels

```
accountant_dad   system-generated actions
operator         actions answered through the UI
```

```
authenticated user identity = NOT_IMPLEMENTED
actor provenance            = coarse-grained system/operator
```

**Do not claim `operator` is a real authenticated identity.** Use the existing
`action_log`. Add no authentication dependency. Approving an identity subsystem
is `H-05`, and it blocks authenticated actor identity and nothing else.

### The seven fields on every reversal event

```
previous state · new state · reason · actor · timestamp
company/document scope · evidence
```

### Test

| Requirement | Required |
|---|---|
| events preserving all seven fields | 20/20 |
| overwritten | 0 |
| missing actors | 0 |
| missing timestamps | 0 |
| missing scopes | 0 |
| missing reasons | 0 |

### What the schema holds today, measured

`action_log` at `accountant/memory/store.py:123` has eleven columns:

```
company_key · ts · action · outcome · reason · run_id
backend · operation_id · voucher_id · vendor_id · detail
```

Mapping the seven required fields onto them:

| Required field | Column today |
|---|---|
| reason | `reason` |
| timestamp | `ts` |
| company/document scope | `company_key` + `voucher_id` |
| new state | `outcome` |
| evidence | `detail` |
| **actor** | **absent** |
| **previous state** | **absent** |

**Two of the seven do not exist yet.** Adding them is a schema change to
`action_log`, which is compatible with the decision — a column is not an
authentication dependency — but "use the existing `action_log`" cannot be read
as "leave it unchanged". Recorded rather than worked around.

---

## Approved assumptions, and what checking them found

```
provenance in UI      = the existing draft screen displays detector/rule,
                        source URL, evidence and explanation per decision
four detectors        = vendor_switch, first_use, magnitude, gst_anomaly
full reversal history = extends the existing action_log
five input types      = text, PDF, PNG, JPG, DOCX
web implementation    = existing stdlib http.server unless separately approved
```

Checked against the repository on 2026-08-10:

| Assumption | Checked against | Holds? |
|---|---|---|
| four detectors | `ALL_DETECTORS`, `accountant/detect/detectors.py:205` | yes, exactly those four names |
| full reversal history extends `action_log` | `accountant/memory/store.py:123` | table exists; two of seven fields missing, see Q8 |
| five input types | `docs/ARCHITECTURE.md:426` and `S1` in `docs/PROJECT_STATE.md` §6 | yes, the same five |
| stdlib `http.server` | `accountant/web/app.py:35` imports `HTTPServer` | yes |
| provenance in UI | the draft screen exists in `accountant/web/app.py` | not verified field-by-field; treated as scope to build, not as an existing capability |

**If implementation inspection contradicts an assumption: do not silently change
scope. Record the contradiction, mark the affected exit `BLOCKED`, and continue
independent work.**

---

## Human-required actions

| ID | Item | Status | Blocks | Required action |
|---|---|---|---|---|
| H-01 | Approve production extraction backend | OWNER_DECISION_REQUIRED | Real-reader S2 | Select backend after cost/privacy/residency review |
| H-02 | Supply real or anonymised bills | OPTIONAL_HUMAN_INPUT | Real-bill accuracy only | Provide labelled corpus if real-bill accuracy is required |
| H-03 | Create Demo Co in TallyPrime | HUMAN_ACTION_REQUIRED | LICENSED_REALTALLY only | Create company and four ledgers in GUI |
| H-04 | Provide licensed Tally evidence | HUMAN_ACTION_REQUIRED | LICENSED_REALTALLY only | Supply verified live-run evidence |
| H-05 | Approve authenticated actor identity | OWNER_DECISION_REQUIRED | Authenticated actor identity only | Approve identity subsystem if required |

**None of these blocks the buildable work.** They do not block schema, rules
corpus preparation, detector tests, UI provenance implementation, or
reversal-history implementation. Each blocks only the exit named in its own
`Blocks` column.

### The id overlap — recorded, not reconciled

```
H-03 (this table) == B-01   create Demo Co and four ledgers in the GUI
H-04 (this table) == B-02   provide the licensed environment / evidence
H-03 (this table) != H-03 in docs/ARCHITECTURE.md section 16.1
```

The id `H-03` names two different items: here, the GUI action that is also
`B-01`; in `ARCHITECTURE.md` §16.1, confirming the live evidence class after
`B-01` and `B-02`. Both are kept, both ids are shown, **no third id was
invented and nothing was renumbered.** This is the project's third id
collision; the first two are recorded the same way rather than tidied away.
