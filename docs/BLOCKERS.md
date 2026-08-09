# BLOCKERS — what is stopped, and the one thing that would unstop it

**Authority.** Every blocker id here is declared in
[`docs/CONTROL_PLANE.yaml`](./CONTROL_PLANE.yaml). If this file and the control
plane ever disagree, the control plane is right and this file is the bug.

**Two kinds, and the difference matters.**

| kind | meaning | who can clear it |
|---|---|---|
| `ENVIRONMENT` | something about the machine, the licence tier or the available data | often nobody, until something outside this project changes |
| `OWNER` | a person has to decide, buy, click or authorise | the owner, in one action |

An `ENVIRONMENT` blocker is not an excuse and an `OWNER` blocker is not a
complaint. Both are recorded so that "we could not" never quietly becomes
"we did not need to".

---

## At a glance

| id | kind | one line | what it stops |
|---|---|---|---|
| `B-01` | ENVIRONMENT | `Demo Co` and four ledgers must be made by hand in the TallyPrime window | the live acceptance run has nothing to run against |
| `B-02` | OWNER | no legitimate non-Educational Tally licence | the frozen contract fixture is refused by date |
| `B-03` | ENVIRONMENT | the live acceptance run has never happened | the only producer of `live` evidence |
| `B-04` | ENVIRONMENT | a live Tally gateway was left wedged and nobody recorded a restart | any further live read |
| `B-05` | OWNER | `ANTHROPIC_API_KEY` unset, Claude GitHub App not installed | `claude.yml` skips every run |
| `B-06` | OWNER | `.github/**` edits need an owner yes | one tool still has two install mechanisms |
| `B-07` | OWNER | no external scheduler account or scoped token | nothing can see GitHub dropping its own schedule |
| `B-08` | ENVIRONMENT | no real ledger anywhere here carries a labelled error | the catch rate cannot be measured, and no detector can be verified |

**Three of the eight are the same problem.** `B-01`, `B-02` and `B-03` are one
chain: no licence, so no company, so no live run. Nothing downstream of them can
be proven, and no FakeTally result stands in.

---

## `B-01` · ENVIRONMENT · the test company does not exist

A company named exactly **`Demo Co`** must be created in the TallyPrime window,
with the four ledgers `tests/test_tally_contract.py:46-47` names:

```
Purchases · Sundry Expenses · Cash · Sharma Traders
```

**Why nobody can automate this.** A company **cannot** be created over the XML
gateway. It was attempted and Tally answered:

```
<RESPONSE>Unknown Request, cannot be processed</RESPONSE>
```

**Do not retry it over XML.** Retrying teaches nothing and it is how the gateway
got wedged the last time somebody went looking for a workaround.

- **Stops:** the live validation run (`5-LIVE`), the Tally spine's exit, launch
  gates `LG-09` and `LG-18`.
- **Cleared by:** the owner, in the GUI, in about two minutes.

---

## `B-02` · OWNER · no non-Educational licence

TallyPrime here runs in **Educational mode**, which accepts vouchers dated only
the **1st, 2nd and 31st** of a month.

`tests/test_tally_contract.py:53` posts on `2026-08-07`, so it is refused.

**This is measured, not assumed.** Measured 2026-08-08 against TallyPrime
Release 7.0, Build 27974:

| voucher date | result |
|---|---|
| `2026-08-07` — the contract fixture | **REJECTED** |
| `2026-08-31` — a control | **ACCEPTED** |
| deletion / reversal | **WORKS** |

The control is what makes this a licence limit rather than a bug in our code.
Writing works. Deleting works. Only the date is refused.

**The fixture is never edited to make it pass.** That would delete the only
evidence the restriction exists, and it would make the suite green on a
configuration nobody intends to ship on.

- **Stops:** the Tally spine's exit, `5-LIVE`, launch gates `LG-14`, `LG-18`,
  `LG-19`.
- **Cleared by:** owner decision `D-01` — which is **open and
  self-contradictory in the record**. One place says buy a licence, another
  place, later and labelled an owner decision, says never buy one. Both are
  quoted verbatim in `DECISIONS.md`. Nobody is guessing which is live.

---

## `B-03` · ENVIRONMENT · the live acceptance run has never happened

```
RealTally acceptance test: REQUIRED, NOT YET RUN
```

The command is `python -m ci.acceptance_cli`. It prints backend identity,
company identity, backup identity, licence mode, write-enabled status, the exact
voucher set, the expected trial-balance movement, the cleanup plan and the
reconciliation plan — and touches nothing without `--yes`.

**It is the only producer of the `live` evidence class.** Until it passes,
everything that depends on a real Tally reports `BLOCKED_ENVIRONMENT`, and the
machine cannot fake it: `ci/acceptance_cli.py` refuses the `LICENSED_REALTALLY`
label while the licence read returns `UNKNOWN`, which it does by design.

- **Cleared by:** `B-01` and `B-02`, in that order. Neither can be worked
  around.

---

## `B-04` · ENVIRONMENT · a live Tally gateway was left wedged

On 2026-08-09 a live TallyPrime HTTP gateway stopped answering. TCP kept
accepting connections; no request was ever answered again.

**Nothing in this repository records whether it has been restarted since.**

`ci/educational_slice.py` has therefore never produced a passing run — Tally
became unresponsive before the first execution finished. Its status is
**NOT YET MEASURED** and no number from it is claimed anywhere.

- **Cleared by:** the owner closing TallyPrime and opening it again, then
  reopening the company. There is no remote path.
- **The standing rule this produced:** never probe a Tally you cannot restart.
  The request shape that caused it is not built anywhere, and the permitted
  shapes are a whitelist.

---

## `B-05` · OWNER · the Claude workflow is registered but never runs

`ANTHROPIC_API_KEY` is not set and the Claude GitHub App is not installed.
`.github/workflows/claude.yml` is registered and every run reports
`conclusion: skipped` — the `if:` guard doing its job.

**Nothing else is stopped by this.** It is recorded so a wall of skipped runs is
not mistaken for a wall of failures.

- **Cleared by:** the owner adding the secret and installing the app, or
  deciding to leave it off. Either is a fine answer.

---

## `B-06` · OWNER · `.github/**` is read-only without an owner yes

`.github/workflows/full.yml` still installs `actionlint` through Docker, while
`pr-fast` uses a pinned, SHA-256-verified binary. One tool, two install
mechanisms, and the Docker path adds a container-registry dependency the binary
does not have.

The cost is small — about 25 seconds on a nightly — which is exactly why it was
never fixed.

- **Stops:** the operational-hardening exit "the same tool has exactly one
  install mechanism".
- **Cleared by:** the owner approving one `.github` edit.

---

## `B-07` · OWNER · nothing can see the scheduler fail

The watchdog runs **on the scheduler it is watching**. If GitHub drops scheduled
runs, it drops the watchdog with them, and nothing reports the silence.

This is structural, not a defect. A monitor inside a system cannot observe that
system's total absence.

Observed: the 02:00 UTC slot fired 87 minutes late and the 03:00 slot 70 minutes
late. Both succeeded. **A drop would look identical to silence.**

- **Cleared by:** the owner creating an external scheduler account and a token
  scoped to dispatch plus read-runs. Claude's token is `Actions: read` and
  returns `403` on dispatch, so this cannot be worked around from inside.

---

## `B-08` · ENVIRONMENT · there is no answer key

No real ledger this project can obtain carries a **labelled** accounting error.

That single fact is why:

- the catch rate cannot be measured on real data;
- verified detector coverage is zero and can only stay zero;
- no detector has ever been shown to catch a real error.

The absence is pinned by a test rather than left as prose:
`tests/test_ingest.py::test_the_score_harness_fails_n3_on_real_data_because_there_is_no_answer_key`.

- **Cleared by:** a real book with labelled errors, or a real customer's own
  corrections used as labels.
- **It may never clear.** The owner has recorded three times that there is no
  access to any other person. If that holds, this is a **product-strategy fact,
  not an engineering backlog item**, and it deserves to be treated as one.
