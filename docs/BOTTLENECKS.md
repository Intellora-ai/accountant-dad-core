# BOTTLENECKS — Accountant Dad

**What this file is.** The register of things that are currently costing more
than they should, each with the evidence that established it and the smallest
mechanism that will notice if it comes back.

**What this file is not.** It is not a gate, not a ratchet, and not a blocking
rule. Nothing here is enforced by CI. The owner explicitly forbade adding a
quality-decay ratchet, a 21st gate, or any new blocking rule, and this file
adds none. See [`PROJECT_STATE.md` §14](./PROJECT_STATE.md#14-quality-decay-ratchet--deferred).

Design lives in [`ARCHITECTURE.md`](./ARCHITECTURE.md). Project history and
run-level evidence live in [`PROJECT_STATE.md`](./PROJECT_STATE.md). This file
links to both rather than restating either.

---

## How an entry is written

Every entry carries exactly these nine fields:

```
bottleneck | evidence | root cause | fix | guard/test/monitor | owner | status | last measured | revisit trigger
```

**The guard is chosen by the class of defect, smallest mechanism first.** A
mechanism larger than the defect is process with no owner.

| Class of defect | Smallest mechanism that catches it |
|---|---|
| code defect | a test |
| workflow defect | a workflow guard |
| permission defect | a live negative test |
| performance defect | a timing measurement / regression budget |
| external reliability | an independent monitor |
| product quality | a held-out scoring regression |
| documentation defect | a consistency check |

Every entry names its class and the mechanism it used.

---

# Part A — CURRENT EVIDENCE

Measured. Nothing in Part A is a plan.

---

## A1 — Detectors cover 2 of 12 published real error types

- **class** product quality → **held-out scoring regression**

| Field | Value |
|---|---|
| **bottleneck** | The four detectors catch **changes from history**. Real audit errors are **standing practices**. A wrong head used consistently never changes, so nothing fires. |
| **evidence** | `accountant/taxonomy` — published real error types **12**, covered by current detectors **2** (`capital_expenditure_as_revenue`, `revenue_expenditure_as_capital`), **UNCOVERED 10**. `first_use`, `magnitude` and `gst_anomaly` map to **nothing** in the published record — `taxonomy.detectors_targeting_no_error_type()` returns all three. Sources: 5, each with a URL and a retrieval date. |
| **root cause** | The four detectors were written before anyone read what auditors actually find. The design premise — "surprising relative to this company's own history" — cannot see an error that has always been there. |
| **fix** | Proof work per uncovered type before any new detector is written. `PROPOSALS` in `accountant/taxonomy/coverage.py` holds one proposal per `UNCOVERED` type. **Do not write ten detectors off this table** — a proposal is a hypothesis, not a requirement. |
| **guard/test/monitor** | Held-out scoring regression. The coverage table reports `UNCOVERED` as a single number (`taxonomy.uncovered_count()`), so the gap is a value that can move, not prose. |
| **owner** | Claude for the table and the proposals · **tanveersidhu** for which uncovered types are worth building |
| **status** | **OPEN — 10 of 12 uncovered** |
| **last measured** | 2026-08-08 |
| **revisit trigger** | `uncovered_count()` changes · a new audit source is added to `taxonomy/sources.py` · any proposal is promoted to a detector |

**Backlog, explicit.** These ten are proof work, not shipped capability:

```
balance_under_wrong_balance_sheet_head      object_head_incompatible_with_major_head
expenditure_exceeds_sanctioned_provision    parked_in_suspense_head
expenditure_netted_against_receipt          receipt_classified_as_wrong_type
expense_under_wrong_statement_head          related_party_not_identified
tax_credit_claimed_where_not_admissible     wrong_expense_head_within_same_section
```

---

## A2 — N1 fails by 2.8x

- **class** product quality → **held-out scoring regression**

| Field | Value |
|---|---|
| **bottleneck** | False alarms per 100 clean entries = **27.59** against a target of **≤ 10**. |
| **evidence** | **27.59 per 100.** Target ≤ 10 (frozen, [`PROJECT_STATE.md` §6](./PROJECT_STATE.md#n1n5)). **FAIL by 2.8x.** This is the **first N1 ever measured on real data** — every prior N1 statement in this project was an unmeasured target. |
| **root cause** | Not established. The working hypothesis is the same one A1 records: the detectors fire on change, and on real data change is normal. Stated as a hypothesis because no experiment has separated it from the alternatives. |
| **fix** | Not yet designed. **Do not tune a threshold to make the number pass** — that moves the measurement, not the product. The honest next step is to attribute the 27.59 to individual detectors before changing any of them. |
| **guard/test/monitor** | Held-out scoring regression. `accountant/score/harness.py` reports N1 as an explicit `PASS` or `FAIL`; `N1_MAX_FALSE_ALARMS_PER_100` is the frozen target and is not a tuning knob. |
| **owner** | Claude |
| **status** | **OPEN — FAILING** |
| **last measured** | 2026-08-08 |
| **revisit trigger** | any detector changes · any threshold inside a detector changes · a second N1 is measured on a different book |

---

## A3 — Educational-mode date restriction blocks the 15 contract tests

- **class** permission defect → **live negative test**

| Field | Value |
|---|---|
| **bottleneck** | TallyPrime is running in **EDUCATIONAL** mode, which rejects voucher dates outside the **1st, 2nd and 31st** of a month. The 15 client-fixture tests in `tests/test_tally_contract.py` post on `2026-08-07`, so they **cannot run unmodified** against the real client. |
| **evidence** | Measured 2026-08-08 against TallyPrime Release 7.0, Series A Release 7.0.0, Build 27974: `2026-08-07` **REJECTED**, `2026-08-31` **ACCEPTED**. `tests/test_tally_contract.py:39` — `date=datetime.date(2026, 8, 7)`. 15 of the file's 21 tests take the `client` fixture. |
| **root cause** | A licence restriction in Tally, not a defect in this codebase. **Educational mode does NOT block deletion** — that theory was tested and disproven, so the reversal work was never blocked by it. |
| **fix** | A non-Educational TallyPrime licence. **Owner-blocked.** Rewriting the test dates to fit the restriction is rejected: it would make the suite green on a configuration nobody intends to ship on, and would delete the only evidence that the restriction exists. |
| **guard/test/monitor** | **Live negative test** — post on a non-permitted date against the running Tally and assert the refusal. The restriction then stays a measured fact rather than a memory, and the test flips the moment a real licence is installed. |
| **owner** | **tanveersidhu** — licence purchase |
| **status** | **OPEN — owner-blocked** |
| **last measured** | 2026-08-08 |
| **revisit trigger** | a non-Educational licence is installed · Tally is upgraded past Release 7.0 |

**The person asking is the bottleneck here.** This is a purchase decision hidden
behind a wall of engineering that cannot substitute for it.

---

## A4 — `trial_balance()` includes a derived figure

- **class** code defect → **test**

| Field | Value |
|---|---|
| **bottleneck** | `trial_balance()` returns `Profit & Loss A/c`, which is **Tally's derived closing figure, not a posting**. The raw sum of the returned dict is therefore not zero. |
| **evidence** | Measured 2026-08-08 on a real company: the two real ledgers cancel **exactly**; the dict as a whole does not sum to zero because the derived head is in it. |
| **root cause** | Tally reports derived heads in the same collection as posted ledgers, and the connector passes the collection through unfiltered. |
| **fix** | Not yet chosen. Either exclude derived heads from the returned dict, or tag them so a caller can. **Reversal is not affected** — reversal compares the same dict before and after, which is equality, not a sum. |
| **guard/test/monitor** | A test asserting that the **posting-only** sum is zero on a real export. It does not exist yet. |
| **owner** | Claude |
| **status** | **OPEN** |
| **last measured** | 2026-08-08 |
| **revisit trigger** | **before any code depends on the trial balance summing to zero.** Nothing does today; the first thing that tries is the trigger. |

---

## A5 — `full.yml` still installs actionlint through Docker

- **class** workflow defect → **workflow guard**

| Field | Value |
|---|---|
| **bottleneck** | Two installation mechanisms exist for one tool. The Docker path also adds a container-registry dependency that the checksum-verified binary does not have. |
| **evidence** | Measured: actionlint's own work ≈ **1s**; GitHub rebuilding the Docker action's container ≈ **25s**; `pr-fast` **~21s → ~50s**. `pr-fast.yml:90` now runs the pinned, SHA-256-verified native binary (`./scripts/install-actionlint`) and `pr-fast` returned to 26–31s. **`full.yml:169` still uses `rhysd/actionlint@914e7df21a07ef503a81201c76d2b11c789d3fca`.** |
| **root cause** | The fix was applied to the path where the cost was felt and stopped there. 25s on a nightly costs nothing, so the second mechanism was never removed. |
| **fix** | Replace the `full.yml` step with `./scripts/install-actionlint`, the same mechanism `pr-fast` uses. |
| **guard/test/monitor** | **Workflow guard** — one install mechanism per tool, asserted across `.github/workflows/`. `ci/check_stubs.py` already walks these files for SHA pins and timeouts, so the check has a home and needs no new job. |
| **owner** | **tanveersidhu** — `.github/**` needs an owner yes and has not been given one |
| **status** | **DEFERRED — not done** |
| **last measured** | 2026-08-08 |
| **revisit trigger** | the next approved `.github` edit · a registry outage failing the nightly · the nightly's `workflow-checks` job exceeding its 10-minute timeout |

---

## A6 — No off-platform monitor for a dropped nightly

- **class** external reliability → **independent monitor**

| Field | Value |
|---|---|
| **bottleneck** | The watchdog runs on the scheduler it is watching. If GitHub drops scheduled runs, it drops the watchdog with them, and nothing reports the silence. |
| **evidence** | GitHub documents that scheduled runs may be delayed and that *"some queued jobs may be dropped"* under load. Observed: the 02:00 UTC slot fired at 03:27:17Z (87 minutes late) and the 03:00 slot at 04:10:49Z (70 minutes late) — both succeeded. Delay is documented behaviour; a drop would look identical to silence. |
| **root cause** | Structural, not a defect. A monitor inside a system cannot observe that system's total absence. |
| **fix** | An external scheduler that dispatches `workflow_dispatch` with a `heartbeat_id`, plus an off-platform check that the matching run appeared **and** completed. Full plan in [`PROJECT_STATE.md` §12](./PROJECT_STATE.md#12-nightly-scheduling). |
| **guard/test/monitor** | **Independent monitor**, hosted off GitHub. That independence is the whole mechanism; anything in-repo reproduces the bottleneck. |
| **owner** | **tanveersidhu** — needs an external scheduler account and a token scoped to dispatch + read runs only. Claude's token is `Actions: read` and returns `403` on dispatch. |
| **status** | **DEFERRED — needs owner credentials** |
| **last measured** | 2026-08-08 |
| **revisit trigger** | one nightly slot passes with no run recorded · the repository approaches 60 days of inactivity, at which point GitHub disables scheduled workflows on public repositories |

---

## A7 — Windows guest-agent work is invisible in Session 0

- **class** external reliability → **independent monitor**

| Field | Value |
|---|---|
| **bottleneck** | Anything launched through the UTM guest-agent channel runs where the owner cannot see it, and reports success either way. |
| **evidence** | The channel is `utmctl exec` and `utmctl file push\|pull`. The agent runs as **SYSTEM in SESSION 0**, so a GUI it launches is **invisible on the user's desktop** while the call still returns success. |
| **root cause** | Windows isolates services in Session 0 by design. The exit code reports that the process started, not that a human can see it. |
| **fix** | A scheduled task created with **`/IT`** runs in the interactive session and is visible. Use the guest agent for files and headless commands; use `/IT` for anything the owner must watch. |
| **guard/test/monitor** | **Independent monitor** — confirm the interactive session, never the exit code. A Session 0 launch succeeds while showing nothing, so the exit code is the one signal that cannot detect this. |
| **owner** | Claude |
| **status** | **UNDERSTOOD — mechanism known, no recurrence since** |
| **last measured** | 2026-08-08 |
| **revisit trigger** | any future "it ran but I can't see it" report from the VM |

---

## A8 — Documentation drift is unchecked

- **class** documentation defect → **consistency check**

| Field | Value |
|---|---|
| **bottleneck** | Both docs described a repository that no longer existed. Nothing compares a documented path against the filesystem. |
| **evidence** | Found 2026-08-08 and corrected in this update: `ARCHITECTURE.md` §4.10 listed `accountant/score/`, `accountant/ingest/` and `accountant/taxonomy/` as **absent** — all three exist. `PROJECT_STATE.md` §1 recorded `main @ 4cc290f`, 11 commits — the repository is at **`f7bf5d9`, 16 commits**. `PROJECT_STATE.md` §8 recorded the real Tally connector as **NOT STARTED** with the evidence `grep … → nothing` — `accountant/tallyio/real.py` is 63.5 KB and has run against a real Tally. Full list in [`PROJECT_STATE.md` §23](./PROJECT_STATE.md#23-documentation-drift-corrected). |
| **root cause** | Both documents are hand-maintained, and the fastest-moving facts in them — which packages exist, which commit is HEAD — are exactly the ones nothing verifies. |
| **fix** | This update corrects every drifted claim found. The general fix is mechanical: every path a doc calls **present** must exist, and every path it calls **absent** must not. |
| **guard/test/monitor** | **Consistency check** over the `present` / `absent` markers in `ARCHITECTURE.md` §4. It is the smallest mechanism because those markers are already a machine-readable convention — the check reads them, it does not need them invented. **NOT BUILT.** |
| **owner** | Claude |
| **status** | **FIXED for the drift found · the check itself is NOT BUILT** |
| **last measured** | 2026-08-08 |
| **revisit trigger** | the next time a doc and the repository disagree · any new package added under `accountant/` |

---

# Part B — RESOLVED

Kept because each one records a fact about TallyPrime that is not in the
official documentation and would otherwise be rediscovered the expensive way.

---

## B1 — Tally emits invalid XML

- **class** code defect → **test**

| Field | Value |
|---|---|
| **bottleneck** | One reserved ledger name made the entire chart of accounts unparseable. |
| **evidence** | `<PARENT TYPE="String">&#4; Primary</PARENT>` — a reference to **U+0004**, which XML 1.0 forbids. The ledger was `Profit & Loss A/c`. One name, whole document lost. |
| **root cause** | `sanitise()` stripped raw control characters but not **numeric character references** to them. Worse, the bare-ampersand pass would have rewritten `&#4;` to `&amp;#4;` — turning an unparseable document into a **parseable lie**. |
| **fix** | Strip illegal numeric character references in `sanitise()`, **before** the bare-ampersand pass. `accountant/tallyio/real.py:347`. |
| **guard/test/monitor** | Unit test. |
| **owner** | Claude |
| **status** | **FIXED** |
| **last measured** | 2026-08-08 |
| **revisit trigger** | any Tally upgrade · any parse failure whose first 200 characters contain a `&#` sequence |

---

## B2 — The `<CMPINFO>` voucher **count** was read as a voucher

- **class** code defect → **test**

| Field | Value |
|---|---|
| **bottleneck** | An **empty** company looked like a corrupt export. |
| **evidence** | Every Tally response carries `<CMPINFO>…<VOUCHER>0</VOUCHER>` — a **count**, not a voucher. Scanning the whole document for the tag `VOUCHER` picked up that counter. |
| **root cause** | The parser searched the whole document for a tag name instead of the region that holds data. |
| **fix** | Scope voucher parsing to **`BODY/DATA`**. `accountant/tallyio/real.py:1110`. |
| **guard/test/monitor** | Unit test — **the fixture now emits the real `CMPINFO` shape**, so the test can reproduce the original failure. A fixture that omits the header cannot fail this way and would be a test that proves nothing. |
| **owner** | Claude |
| **status** | **FIXED** |
| **last measured** | 2026-08-08 |
| **revisit trigger** | any Tally upgrade · any new response region parsed by tag name rather than by path |

---

## B3 — Reversal: seven delete shapes failed

- **class** code defect → **test**

| Field | Value |
|---|---|
| **bottleneck** | The whole safety promise of the product. Without reversal there is no reversible mistake, only permanent damage to a real business. |
| **evidence** | Seven shapes measured against a live TallyPrime 7.0: without `REMOTEID` → `Cannot delete unnamed object: VOUCHER!`; with `REMOTEID` → `Voucher does not exist!`; `ACTION="Alter"` plus `<ISDELETED>Yes</ISDELETED>` → **silently ignored**, `altered=0 deleted=0 errors=0`. A silent success is the worst of the three. |
| **root cause** | Two facts, neither obvious. **(1)** Tally identifies a voucher for Alter/Cancel/Delete by a **`TAGNAME`/`TAGVALUE` attribute pair** — a TDL method name and its value — **not by child tags**. **(2)** `REMOTEID` is a **sync-lineage** field. Tally stamps it on export so it looks like a handle, but a locally-imported voucher has no remote-index entry, so matching on it can only ever fail. |
| **fix** | `<VOUCHER DATE="2-Apr-2026" TAGNAME="Master ID" TAGVALUE="3" ACTION="Delete" VCHTYPE="Journal"></VOUCHER>`. `accountant/tallyio/real.py:760` — `DELETE_TAGNAME = "Master ID"`. **Note the two date formats: the `DATE` attribute is `dd-MMM-yyyy`; the `DATE` child tag is `yyyyMMdd`.** `REMOTEID` was removed from the delete envelope on 2026-08-08. |
| **guard/test/monitor** | Unit test on the envelope shape, plus the end-to-end proof in [`PROJECT_STATE.md` §21](./PROJECT_STATE.md#21-tally--first-real-evidence). |
| **owner** | Claude |
| **status** | **FIXED — proven end to end** |
| **last measured** | 2026-08-08 |
| **revisit trigger** | any Tally upgrade · any change to the delete envelope · the first delete against a **non-Educational** licence |

Official sources, both fetched 2026-08-08:
- <https://help.tallysolutions.com/article/DeveloperReference/integration-capabilities/case_study_1.htm>
- <https://help.tallysolutions.com/article/DeveloperReference/faq/6191.html>

---

## B4 — Cross-organisation transfer does not exist

- **class** product quality → **held-out scoring regression**

| Field | Value |
|---|---|
| **bottleneck** | Whether a pooled model across customers is worth building. It is not. |
| **evidence** | **16,011 real UK central-government rows, 30 department pairs.** Within-department best **53.08%**. Cross-department **0.00% on 29 of the 30 pairs**. |
| **root cause** | Not a defect. Each organisation's expense vocabulary is its own, so there is no shared vendor→account mapping to learn. |
| **fix** | None needed — this is an **answer**, and it removes work rather than adding it. Memory is **company-local only**. Every customer is a **permanent cold start**. A pooled model is wasted effort. Recorded as a design rule in [`ARCHITECTURE.md` §4.3](./ARCHITECTURE.md#43-memory-index--accountantmemoryindexpy--present). |
| **guard/test/monitor** | Held-out scoring regression — `accountant/ingest/crossorg.py` reports every ordered pair, and refuses a cross-organisation claim built on fewer than 3 pairs. |
| **owner** | Claude |
| **status** | **MEASURED — question answered** |
| **last measured** | 2026-08-08 |
| **revisit trigger** | a cross-department pair ever exceeds 0% by a margin that is not one shared vendor · a customer dataset with a genuinely shared chart appears |

**The consequence is a product invariant, not a note.** Because every customer
is a cold start, an **existing** company must have its **own Tally history
bootstrapped before the first proposal is shown**. An empty memory for an
existing company is a **product failure**, not a neutral state — the system
would ask about vendors the company has posted to for years. Stated as an
invariant in [`ARCHITECTURE.md` §4.3](./ARCHITECTURE.md#43-memory-index--accountantmemoryindexpy--present)
and carried into the MVP completion checklist in
[`ARCHITECTURE.md` §11](./ARCHITECTURE.md#11-mvp-completion-checklist).

---

## B5 — UK central government is not schema-stable

- **class** code defect → **test**

| Field | Value |
|---|---|
| **bottleneck** | A loader written against one department's published file fails on the next department's. |
| **evidence** | The narration column appears as `Narrative`, `Description`, `Item Text`, `Publication Description`, `Invoice Cost Centre Description` and **`PO Catergory Description `** — that misspelling and that trailing space are both in Defra's published file. DfT publishes its amount column as the literal header **`" £ "`**. **DBT publishes its narration column and leaves all 199 cells EMPTY.** |
| **root cause** | There is no enforced schema on published spend data. Each department writes its own headers. |
| **fix** | Header alias tables in `accountant/ingest/spend.py`. A present-but-empty column is **reported**, never silently treated as absent — DBT's 199 empty cells are the case that makes the difference visible. |
| **guard/test/monitor** | A test per real published shape, over the seven department fixtures in `accountant/ingest/fixtures/` (`dbt`, `defra`, `dft`, `dhsc`, `dwp`, `hmt`, `mhclg`). Real files, not invented ones — an invented fixture cannot contain somebody else's typo. |
| **owner** | Claude |
| **status** | **HANDLED** |
| **last measured** | 2026-08-08 |
| **revisit trigger** | a new department is added · any published month fails to load |

---

# Part C — FUTURE PROPOSALS

**Not evidence. Not commitments. Nothing here has been measured, approved or
scheduled.** Kept separate from Parts A and B so a proposal can never be read as
a finding.

| Proposal | Why it might be worth doing | What would have to be true first |
|---|---|---|
| Attribute the N1 = 27.59 figure per detector before changing any detector | 27.59 is one number over four detectors; it may be one detector's fault | somebody decides A2 is the next thing worked on |
| Build the `present`/`absent` consistency check described in A8 | it is the only guard in this file that would have caught its own entry | a second documentation drift occurs, or a new `accountant/` package is added |
| Exclude or tag derived heads in `trial_balance()` (A4) | the sum is currently not a usable invariant | the first caller needs the sum, rather than before/after equality |
| Promote one `taxonomy` proposal to a real detector | 10 of 12 error types are uncovered | one uncovered type is shown to occur in a real book, with the occurrence observed rather than assumed |
| Replace the `full.yml` Docker actionlint (A5) | one tool, one mechanism | an owner yes for a `.github` edit |
| External nightly dispatch and off-platform monitor (A6) | the current watchdog cannot detect its own scheduler failing | an owner-created scheduler account and a scoped token |

**Deliberately not proposed**, and not to be re-raised without the trigger named
in [`ARCHITECTURE.md` §10](./ARCHITECTURE.md#10-deliberately-outside-this-architecture):
a quality-decay ratchet · a 21st gate · any new blocking rule · a second
mutation engine · an in-house document reader.
