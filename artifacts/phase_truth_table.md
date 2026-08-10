# PHASE TRUTH TABLE

Generated 2026-08-10 from `docs/CONTROL_PLANE.yaml` at commit
`53dd60d6c9b0dd9bfb2a1564398c3510cf93a7b8`, plus an uncommitted working tree.

**RE-MEASURED 2026-08-10 against `main` at `da573c1`.** Superseded rows are
marked in place and never deleted. What moved, and why:

| row | was | is | why |
|---|---|---|---|
| **8** | `NOT_STARTED`, 0 / 5 | `PARTIALLY_VERIFIED`, **1 / 5** | Both facts the old row rested on were false when read back. `accountant/rules/` **is** committed, and five Phase 8 PRs are merged into `main`. |
| **7** | `NOT_STARTED`, 0 / 3 | `PARTIALLY_VERIFIED`, **3 / 3** | Copied from the control plane, which is what this table says its statuses come from. **This auditor did not measure phase 7's exits** — see the caveat under Totals. |
| **9** | worst book fails | worst book **still** fails, at **19.05** not 33.33 | Phase 8 PR-2 (`d121574`) narrowed what `magnitude` may treat as prior evidence. The number moved; the verdict did not. |
| totals | 29 of 47 | **33 of 47** | +3 from row 7, +1 from row 8. Recounted by the command below, not by hand. |

A fourth stale record was fixed in `docs/CONTROL_PLANE.yaml` in the same pass and
is noted here because it changes how any count in this project should be read:
**`tests/test_phase5b_readiness.py` was cited as "19 tests"; it is now 39 test
functions / 45 collected.** Auditing that one number showed the file uses the
word "tests" for two different quantities — `def test_` counts and pytest's
collected count, which differ by up to 3.8× on the same file. The full audit is
the `test_count_audit:` block at the foot of the control plane. **Row 5B's
evidence below still says "30 of 30 lifecycles" and similar; those were not
re-measured here.**

**One row per phase. Statuses come from the control plane and nowhere else.**

**Read the "exits met" column before the status column.** "3 of 4" is a more
useful sentence than any single word, and it is the column that tells you what
is actually left.

**Nothing checks this file.** `scripts/validate_project_truth.py` scans seven
tracked documents and `artifacts/` is not among them, which is the mechanical
reason two rows here were able to drift for a whole phase without anything
going red.

---

| id | name | status | exits met | evidence | blocker | next action |
|---|---|---|---|---|---|---|
| **0** | repository and safety | `PASSED` | **2 / 2** | Re-verified 2026-08-10 by `gh api`, not by memory. Old repo `pushed_at 2026-08-06T19:55:12Z`, head `924d0e0…` — both byte-identical to the baseline. New repo public, created `2026-08-07T11:38:55Z`. | — | none |
| **1** | CI foundation | `PASSED` | **4 / 4** | 20 gates in `ci/gates.toml`, locked by `ci/gate_names.lock`, 18 contract tests. 8 deliberate failures observed blocking; red merge refused (`405`); direct push refused; 3 ruleset writes refused (`403`). | — | re-run at HEAD — the evidence is from commit `4cc290f` |
| **2** | the Tally spine | `BLOCKED_ENVIRONMENT` | **0 / 1** | The build is done and has run against a real TallyPrime 7. The **exit** has never run: Educational mode refuses the `2026-08-07` fixture. Measured — that date rejected, `2026-08-31` accepted, deletion works. | `B-02` | owner answers `D-01` |
| **3** | the typed vertical slice | `PARTIALLY_VERIFIED` | **2 / 4** | The two testable exits pass — no path posts a Not-valid or Unclear entry, and an existing company with an empty index is a bootstrap failure. The two live exits are proven against FakeTally only. | `B-03` | needs `B-01` and `B-02` first |
| **4** | the no-match safety path | `PASSED` | **4 / 4** | All four exits are code assertions, so unit tests are the right evidence and no real Tally is needed. Exit 4 was **proven false first**, then the funding guess was deleted rather than patched. | — | none |
| **5** | idempotency and reversal — the implementation | `PASSED` | **4 / 4** | `ci/acceptance.py` reports 15 of 15 conditions. Operation-ID identity across all five artefacts, bulk reversal over 8 + 7 states, the `N = 10` conservation proof, the acceptance command. FakeTally and simulator. | — | none |
| **5-LIVE** | the same proof against a real Tally | `BLOCKED_ENVIRONMENT` | **0 / 1** | **None exists and none is claimed.** `python -m ci.acceptance_cli` has never been run. The command refuses to label itself live while the licence read returns `UNKNOWN`. | `B-03` | `B-01`, then `B-02` |
| **5B** | operational readiness — a release **gate**, not a feature | `PARTIALLY_VERIFIED` | **5 / 6** | 3 of 3 differing runs, 30 of 30 lifecycles, clean-room wheel install, restart and recovery, complete evidence bundles. All against FakeTally and the simulator, which is by design. **The sixth exit is the gate's own entry condition** — the live reversal proof — and it is not met. | `B-03` | none it can act on. It waits on `5-LIVE`. |
| **6** | the first detector | `PARTIALLY_VERIFIED` | **4 / 5** | `PENDING_VERIFICATION` — an agent is re-checking. On record: the detector fires, names its evidence, leaks no account name, and a dismissal is logged durably (16 tests, FakeTally over HTTP). Against it, and measured: `vendor_switch` never reads its history parameter, and the per-batch cap was never passed from the web app until commit `a19a100`. | — | take the verification agent's result |
| **7** | the extraction adapter | `PARTIALLY_VERIFIED` | **3 / 3** | *Corrected 2026-08-10; was `NOT_STARTED`, 0 / 3, "none of the three exits has been attempted".* Taken from the control plane, which this table declares as its only source of statuses. **Not independently measured by this auditor** — reported as copied, not as verified. | — | owner answers `D-23` first — if launch is typed-text only, this is not on the critical path |
| **8** | widen to the frozen criteria | `PARTIALLY_VERIFIED` | **1 / 5** | *Corrected 2026-08-10; was `NOT_STARTED`, 0 / 5, "`accountant/rules/` does not exist".* **That was false.** `git ls-files accountant/rules/` lists six modules — `__init__.py`, `effective_dates.py`, `gst_rates.py`, `hsn_sac.py`, `place_of_supply.py`, `provenance.py` — merged in `7db7f45`, and five Phase 8 PRs are on `main` (`d98adc3`, `d121574`, `7db7f45`, `b96f01b`, `1b52bbe`). The exit that is now met is the rules one, measured twice: a rule with no `source` raises `TypeError` at construction, and flipping one rule to `SOURCE_UNVERIFIED` takes `RuleCorpus.build` from 15 loaded / 0 rejected to 14 / 1. **Still true from the old row:** the production detector set is still `SLICE_4_DETECTORS`, one detector (`accountant/pipeline.py:365`, `:811`). | — | fix the DHSC root cause, then enable four detectors |
| **9** | the proof track | `PARTIALLY_VERIFIED` | **4 / 5** | Everything it was asked to **report**, it reports. What it reports is not all passing. Same seed gives byte-identical output; the coverage table gives `UNCOVERED` as a number; 30 department pairs are compared. But the false-alarm rate fails on the worst book — **19.05 on DHSC** against a target of 10, re-measured 2026-08-10 *(was 33.33; PR-2 moved the number, not the verdict)* — and two of the three frozen numbers have never been measured on real data. | — | `D-22` is answered — **B**, both numbers reported, a failing department is never hidden |
| **10** | operational hardening | `NOT_STARTED` | **0 / 3** | Deferred on purpose. The external nightly monitor needs owner credentials; removing the second `actionlint` install mechanism needs an owner yes for a `.github` edit; auto-fix is not built. | `B-06` | owner clears `B-06` and `B-07`, or says leave them |

---

## Totals

| status | phases |
|---|---|
| `PASSED` | 4 — 0, 1, 4, 5 |
| `PARTIALLY_VERIFIED` | 6 — 3, 5B, 6, **7**, **8**, 9 |
| `BLOCKED_ENVIRONMENT` | 2 — 2, 5-LIVE |
| `NOT_STARTED` | 1 — 10 |

*Superseded 2026-08-10, kept: `PARTIALLY_VERIFIED` 4 — 3, 5B, 6, 9 · `NOT_STARTED`
3 — 7, 8, 10.*

**CAVEAT ON ROW 7, stated rather than buried.** This re-measurement was scoped to
two claims about phase 8 and to the N1 figures. Phase 7's move from
`NOT_STARTED` 0 / 3 to `PARTIALLY_VERIFIED` 3 / 3 is **copied from the control
plane**, because this table's stated rule is that statuses come from there and
nowhere else. It is **not** an independent verification, and it should not be
read as one.

**Exit clauses across all thirteen rows: 33 met of 47** *(superseded, kept: 29 of
47 — +3 from row 7, +1 from row 8)*. Counted from the control plane, not by hand.
Re-run 2026-08-10 and it returns `33 of 47`:

```bash
.venv/bin/python - <<'EOF'
import re, pathlib
t = pathlib.Path('docs/CONTROL_PLANE.yaml').read_text()
blk = t[t.index('\nphases:\n'):t.index('\nmetrics:\n')]
print(sum(len(re.findall(r'^        met: true$', r, re.M)) for r in blk.split('\n  - id: ')[1:]),
      'of',
      len(re.findall(r'^      - text:', blk, re.M)))
EOF
```

---

## The three things this table makes obvious

**1 · One chain holds up four rows.** Phases 2, 3, 5-LIVE and 5B are all waiting
on the same thing: a licence, then a company created by hand, then one live run.
That is **one owner decision and two minutes of clicking**, not four engineering
problems. It is the highest-leverage item on the board by a wide margin.

**2 · The uncomfortable row is 9, not 2.** Phase 2 is blocked by something
external and everyone knows it. Phase 9 is the one that measured the product and
came back with: nothing verified as covered, the catch rate unmeasurable, and a
false-alarm rate that fails on the worst real book. **A licence fixes phase 2.
Nothing on the current plan fixes phase 9**, because the missing thing is a
labelled real-error dataset that this project has recorded three times it cannot
obtain.

*Still true 2026-08-10, and worth pinning down.* DHSC improved from 33.33 to
**19.05** and is still **1.91 times over** a target of 10. The improvement came
from a root-cause fix, not a threshold change — `N1_MAX_FALSE_ALARMS_PER_100`
was not touched. A second slice fails for an unrelated reason: **DBT has 0 clean
entries**, so it is unmeasurable rather than passing, and the gate says so out
loud. Two slices fail, for two different reasons.

**3 · ~~Two rows are `NOT_STARTED` and might never need to start.~~** *Superseded
2026-08-10, kept.* This read: "Phase 7 only matters if launch needs more than
typed text — that is open decision `D-23`. Phase 8 sits behind it. Answering one
question could delete both from the critical path, which is cheaper than building
them." **It is now out of date, and the reason is instructive.** Both rows moved
off `NOT_STARTED` while the table still said they had not begun, so the cheap
option it described was being weighed against a board that had already changed.
Phase 8 shipped five PRs. What remains true is narrower and worth keeping:
`D-23` still governs whether launch needs more than typed text, and the one row
that is genuinely `NOT_STARTED` is **10**, which is deferred on purpose.

---

## What is deliberately not in this table

- **Test counts, coverage and mutation scores.** They are engineering health,
  not phase progress. They are in `artifacts/launch_baseline.md`.
- **Anything cloud.** Cloud work sits behind decision `D-08`, which is a gate on
  *starting*, and it has not fired. There is no cloud phase and inventing one
  would imply progress that does not exist.
- **Dates and estimates.** Nobody has been asked for one and none has been
  invented.
