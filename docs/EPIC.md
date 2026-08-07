# EPIC — Silent-error detection for bookkeeping entries

Status: DRAFT, not approved
Date: 2026-08-07

---

## UNCONFIRMED ASSUMPTIONS

These were proposed by the assistant and have NOT been confirmed by the owner.
Every one is load-bearing. Correct any that are wrong before implementation.

| # | Assumption | Source |
|---|---|---|
| A1 | Language is Python | assistant proposal, no answer given |
| A2 | No git repo, no GitHub issues, specs live as files | verified: dir was empty, not a repo |
| A3 | N1 = max 10 false alarms per 100 clean entries | derived from 10x-time-saving, not confirmed |
| A4 | N2 = review takes <=10% of read-all time | derived from A3, not confirmed |
| A5 | N3 = >=90% catch per injected error type | assistant proposal, not confirmed |
| A6 | N5 = 0.975 confidence threshold anchor | Rossum published default, not confirmed for us |
| A7 | Project has no name | none given |

---

## Problem statement

**Current state.**
A bookkeeping entry recorded against the wrong account produces no signal.
Debits still equal credits. The trial balance still ties. It reads identically to
a correct entry. Detection rate before audit is effectively zero. No product in
this market compares a new entry against what that business has done before.

**Desired state.**
A wrong entry is surfaced within one working day of being recorded, and the
responsible person examines a flagged subset instead of every entry.

**The gap.**
No comparison exists between a new entry and the company's own history.

Quality checks against the owner's requirements doctrine:
- Solution words: none. No "AI", no "app", no vendor name.
- Both ends measurable: yes. Percentage surfaced before audit; count of entries a
  human must read.
- Verifiable later: yes. Inject known errors, count catches.

---

## Why this, and not the obvious thing

Verified by four independent research passes (2026-08-07). Sources in
`docs/RESEARCH.md` is NOT yet written; citations are in the session transcript.

| Finding | Consequence |
|---|---|
| TallyPrime 7.1 (June 2026) ships "Docs by Ira": PDF/image to draft voucher | Bill extraction is now a vendor feature. Do not build it. |
| myBillBook ships extraction free; Vyapar, Marg, BUSY ship it paid; TrulyInvoice is Rs 599/month unlimited | Extraction is a price war with a free floor. Do not enter. |
| Zoho, Dext, Hubdoc, AutoEntry, QuickBooks, Xero, Ramp, Brex, Bill.com, Vic.ai, Rossum: **none auto-post by default** | Unattended posting is refused by every vendor globally, by choice. |
| Tally MD, on record: "We do not believe this should be a completely unattended process." | The incumbent has publicly committed to leaving this alone permanently. |
| NFRA July 2026 Staff Series: auditor responsibility non-delegable; automation bias named as a diagnosed defect | Regulator has independently named our failure mode. |
| Companies (Accounts) Rules Rule 3(1), effective 1 Apr 2023: unalterable edit log, User ID per change, cannot be disabled | Every correction is a legally mandated, attributed label. The regulation is the data source. |
| Guidance is silent on bulk/API import: "which User ID owns an automated entry" unanswered | Ambiguity, not prohibition. Pushes toward a named human. |
| Dext's "99.9% accuracy" is field extraction, not account correctness | The industry conflates reading with deciding. |
| **Zero neutral third-party accuracy benchmarks exist for any product, globally** | Nobody has ever published a measured number for whether the account was right. |
| Vic.ai, the only vendor claiming autonomy: "The longer clients use Vic.ai, the more autonomy they get" | Autonomy comes from accumulated per-customer history. Confirms the memory design. |

Everyone competes on reading the bill. Nobody competes on catching the wrong
account. That step is unserved, and the incumbent has promised to stay out of it.

---

## Design

Three parts. Two of them use no model at all.

| Part | What | Model needed |
|---|---|---|
| P1 Memory | vendor and phrase to account index, built from the company's own posted history | no |
| P2 Inference | proposes an account only when P1 has no match | yes |
| P3 Detectors | flags entries that deviate from this company's own past | no |

**Retrieval, two corpora:**

| Corpus | Answers | Public |
|---|---|---|
| Indian accounting law, GST rates, TDS sections, Schedule III heads | is this legal, what rate | yes |
| This company's own posted vouchers | does this look like what they always do | no. Nobody builds this. |

**Confidence gate falls out of P1.** In the index means post. Not in the index
means ask. No invented threshold required for v1.

**Posting mode is a config flag, not architecture.** Unattended versus reviewed is
where the threshold sits. Deferred, deliberately, to whoever pays first.

**Cold start.** The index starts empty, so early entries are all novel and all
produce questions. Each answer writes to the index. Question volume falls as the
index fills. This is also how ground truth is acquired: the clarifying questions
ARE the labelling mechanism.

---

## Hard constraints

| Constraint | Stated by | Consequence |
|---|---|---|
| No access to any other person | owner, three times | Cannot obtain a real company file, cannot obtain accountant review time. All measurement must be self-contained. |
| Must not increase human work | owner | Becomes requirement N2. A review layer that adds reading is a failed review layer. |
| Tally has no cloud API; integration is localhost:9000 | verified, Tally developer docs | Any hosted product needs a local connector. Deferred to #6. |
| Port 9000 has no auth model | verified | Security surface. Named, not solved, in #6. |

---

## Child issues

| # | Title | Priority | Depends on |
|---|---|---|---|
| 1 | Synthetic book generator and error injector | Critical | none |
| 2 | Memory index over posted history (P1) | Critical | 1 |
| 3 | Silent-error detectors and ranked queue (P3) | Critical | 1 |
| 4 | Scoring harness | Critical | 1, 2, 3 |
| 5 | Public real-data ingest | High | none |
| 6 | Tally connector | Deferred | 2, 3, 4 |

## Dependency graph

```
#1 generator ──┬──> #2 memory ──┐
               │                ├──> #4 harness ──> #6 connector (deferred)
               └──> #3 detectors┘

#5 public data ── independent, can start any time, de-risks #1
```

## Sequencing rationale

#1 first because everything downstream consumes its output and nothing can be
measured without it. #2 and #3 are independent of each other and can run in
parallel. #4 last among the core four because it scores the other three. #5 is
independent and exists solely to attack the biggest risk in #1 (see below). #6 is
deferred because it is the only part that can damage real books, and nothing
before it requires write access.

## Definition of done for the epic

1. A synthetic company book of at least one simulated year exists and regenerates
   deterministically from a seed.
2. Errors of the four named types can be injected at a controlled rate.
3. The memory index answers repeat entries without invoking a model.
4. The detectors produce a ranked queue with a one-line stated reason per flag.
5. The harness reports catch rate per error type and false alarms per 100 clean
   entries.
6. At least one real, public, non-synthetic dataset has been scored, or the
   attempt has been documented as failed with reasons.

---

## Top risk, stated at the top and not buried

**What fraction of errors a real accountant actually makes are covered by the four
detector types?**

Unknown. Unmeasurable without real error data. All four research passes confirmed
nobody on Earth publishes it.

If real errors are dominated by a fifth type nobody thought of, catch rate on
injected errors could be 100% and the product still worthless.

No number is given for this. Manufacturing one would be worse than leaving it
blank.

## Second-order risks

1. **Alert fatigue.** Flag too much and the tool is switched off inside a week,
   after which it catches nothing. Mitigated by N1, the ranked queue, the stated
   reason per flag, and dismissal tracking.
2. **The reviewer stops reading unflagged entries.** A miss becomes worse than
   before because the last unaided human check is gone. This is a cost of success,
   not a bug. It must appear in any customer-facing description.
3. **Synthetic self-deception.** Tuning a detector against data you generated
   yourself produces excellent scores and no information. This is why #5 exists.

## Out of scope for the entire epic

GST return filing, TDS computation, payroll, inventory valuation, bank
reconciliation, audit report generation, multi-user concurrency, mobile apps,
bill photo capture, OCR of any kind.
