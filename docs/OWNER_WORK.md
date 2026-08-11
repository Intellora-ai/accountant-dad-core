# Owner work

**One file. This is the only place this list lives.** `docs/PROJECT_STATE.md`
points here rather than repeating it, because tonight this project measured what
duplication costs: `accountant/rules/` was recorded as "verified absent" in three
documents at once, days after it had merged, and the three corroborated each
other into false confidence.

Nothing here blocks coding work. Each item is recorded and work continues around
it.

## Owner / Manual Work

### Deployment target
No cloud account, host or domain exists. Task 12 builds a Dockerfile, a deploy
script and a CI job against a **placeholder** registry and host. Nothing deploys
anywhere until an account exists.

**Needed:** a host, a container registry, a domain, and the credentials as
repository secrets.

### Production reader selection
No document reader is selected. `D-23` is open.
`artifacts/extraction_backends.md:3` — *"third-party backend selection =
OWNER_DECISION_REQUIRED"*.

Task 9 builds the upload routes and a **placeholder** reader behind the existing
`Extractor` seam, so one config change swaps in a real vendor later. Until then
every uploaded document returns explicit `not_found` and `S2 = NOT_MEASURED`.

Measured options are in `artifacts/extraction_backends.md`. Azure Document
Intelligence and AWS Textract are both $0.01/page; Azure has the clearest
retention terms and a Central India region, AWS has explicit GST fields.

**Needed:** pick one, create the account, add the endpoint and key.

### Tally licensing model
Educational mode accepts vouchers dated only the 1st, 2nd and 31st of a month —
measured 2026-08-08 against TallyPrime 7.0 Build 27974: `2026-08-31` accepted,
`2026-08-07` rejected. The frozen contract fixture posts on the 7th and is never
edited to fit.

Standing decision 2026-08-08, Option 2: **Tally stays in Educational mode.** No
licence is to be purchased, activated, bypassed or simulated. Phase 2 is
therefore `ENVIRONMENT-LIMITED`.

**Needed:** a decision on whether customers must hold their own licence, and
what the product says when they do not. A decision closes this as firmly as a
purchase would.

### Real-Tally testing infrastructure
No test in this repository touches a real TallyPrime.
`tests/test_tally_contract.py:63` yields `FakeTally`; `tests/test_real_tally.py`
drives a simulator and says so in its own header. `LICENSED_REALTALLY` evidence:
**none, anywhere.**

**Needed:** `Demo Co` and four ledgers (`Purchases`, `Sundry Expenses`, `Cash`,
`Sharma Traders`) created **in the TallyPrime GUI** — the XML gateway refuses
company creation with `<RESPONSE>Unknown Request, cannot be processed</RESPONSE>`
and retrying it wedged a live gateway once already. Then a Windows VM reachable
from CI if those tests are ever to run automatically.

### GST scope for launch
Owner decision `Q3 = D`: GST posting is **not** implemented and that is
deliberate, not a defect. `POSTING_ENABLED = False`, and two independent guards
refuse a GST bill — one in the application, one at the wire.

Calculation, place-of-supply and ledger selection **are** implemented; only
posting is off.

**Needed:** which GST features are in scope at launch.

### Connector distribution
`docs/CONNECTOR.md` describes a program that runs on the customer's Windows
machine. How they obtain it, install it, and receive updates is undecided. It is
unsigned, so Windows SmartScreen will warn on first run.

**Needed:** a distribution method, and a decision on code signing.

### Authentication gaps that need a decision, not code
Task 2 built sessions, tenancy and the two modes. Four things it did NOT build,
each because the answer is a product decision or needs an account that does not
exist:

- **No `Secure` flag on the session cookie.** A browser withholds a `Secure`
  cookie over plain HTTP, which would break the loopback development server. It
  goes on with Task 7, when TLS is in front of the cloud server.
- **No password reset.** Sending mail needs a provider, an account and a domain,
  none of which exist. The login page deliberately carries no "forgot password"
  link rather than a dead one.
- **No sign-up route.** Users are created by calling `MemoryStore.create_user`.
  Whether customers self-register or are created by you is a product decision.
- **No rate limit on `/login`.** The refusal is constant-time against a
  stopwatch and identical for an unknown email and a wrong password, so nothing
  can be enumerated — but nothing yet slows a machine trying a million
  passwords.

**Needed:** a mail provider (reset), a decision on self-registration, and
whichever of these you want before the first real customer.
### The lockfile gate is declared and does not run — ONE LINE
`ci/gates.toml` declares the `lockfile` gate as `uv lock --check`, threshold 0,
required, active. No workflow runs that command. What runs instead, in
`.github/workflows/pr-fast.yml`, is `uv sync --extra dev --frozen`, under a
comment claiming `--frozen` is that gate.

It is the opposite flag. From uv's own CLI reference:

```
--frozen   "Instead of checking if the lockfile is up-to-date, uses the
            versions in the lockfile as the source of truth."
--locked   "Requires that the lockfile is up-to-date. If the lockfile is
            missing or needs to be updated, uv will exit with an error."
```

`--frozen` guarantees the check is SKIPPED. A pull request may therefore change
`pyproject.toml`, leave `uv.lock` stale, and go green — with CI resolving a
dependency set nobody recorded.

**The change, in full. Nothing else in the diff.**
`.github/workflows/pr-fast.yml`, job `pr-fast`, step
`sync dependencies from the lockfile`, lines 63-66:

```
BEFORE
      - name: sync dependencies from the lockfile
        # --frozen fails if uv.lock does not match pyproject.toml, which is the
        # `uv lock --check` gate.
        run: uv sync --extra dev --frozen

AFTER
      - name: sync dependencies from the lockfile
        # --locked, NOT --frozen. This step IS the `lockfile` gate declared in
        # ci/gates.toml, and until now it did not check anything. uv's
        # reference: --frozen skips the check, --locked requires the lockfile
        # to be up to date.
        run: uv sync --extra dev --locked
```

Only the `pr-fast` job, because that is the only job the gate names. No other
`uv sync` step is touched. No gate is added or removed and no threshold moves.

**Why it is not already done:** `.github/` is denied at the permission layer in
this environment, so it cannot be edited from here.

`tests/test_gate_contract.py::test_the_lockfile_gate_is_actually_enforced` pins
it as a strict xfail, paired with a passing test recording what runs today. The
day the workflow changes, the xfail turns green and fails loudly until it is
removed — so the fix cannot land and leave a test that proves nothing.

### The second dead gate is dead ON PURPOSE — no action wanted
`cached-mutation` also never executes, and it should not. `ci/gates.toml`
records it as PARKED on 2026-08-08 with the measurement: a cached
pytest-gremlins verdict carries no `selected_tests`, because the mutant was not
re-executed, so every cached mutant is indistinguishable from one that nothing
ran against, and `ci/check_mutation.py` correctly reports `FAIL_INCOMPLETE`.
Observed twice on real runs.

The standing owner rule is "mutation-result cache only if proven correct" — and
it is not. The cache restore and save steps remain in both workflows, so
re-enabling is a one-flag change if a cached verdict ever carries its own
mapping evidence. `full-mutation` and `mutation-accounting` still run and still
enforce the 90 threshold.

**Needed: nothing.** Listed only so "2 of 20 gates never execute" is not read
as two defects when it is one.

### Legal
Privacy policy, terms of service, billing, refunds, and a support process. The
product will hold vendor names and amounts from real books.

**OWNER / LEGAL REVIEW REQUIRED.** No legal advice is offered here.

### Incident response and support SLAs
Undefined. What a customer does when a voucher posts wrongly, who answers, and
how fast.

### PostgreSQL migration
Optional, and deliberately deferred. SQLite on a file is the decision for now.
It supports unique constraints and migrations and needs no server. It will not
support two web workers writing at once — SQLite locks the whole file — so
Task 11's concurrency has a ceiling until this is revisited.

### Connector audit token
`CLAUDE_AUDIT_TOKEN` — a fine-grained token with `Administration: read` and
nothing else, as a repository secret.

Without it `ci/test_protection.py::test_bypass_actors_are_still_empty` cannot
run: `GITHUB_TOKEN` receives a ruleset body with `bypass_actors` **absent**, and
actionlint v1.7.12 proves `administration` is not a workflow permission scope at
all, so no `permissions:` block can grant it. The other nine protection tests
pass. Details in `artifacts/gate_integrity_blocked.md`.
