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

### TLS certificate for the cloud server
The code half is **done** — Task 7, 2026-08-11. `ACCOUNTANT_TLS_CERT` and
`ACCOUNTANT_TLS_KEY` make the web app serve HTTPS at minimum TLS 1.2; setting
exactly one refuses to start rather than falling back to plaintext; the session
cookie gains `Secure` when and only when the connection is actually encrypted.
`docs/TLS.md` has the whole table, including what each misconfiguration does.

The certificate half is **not**, and cannot be from inside this repository. A
certificate is issued to a **domain name**, and no domain, host or certificate
authority exists yet — the same gap as *Deployment target* above. Every test
runs against a self-signed certificate generated into `tmp_path` at run time,
which proves the code path and proves nothing about a browser trusting it.

This also closes the entry `docs/AUTH.md` recorded as pending: the cookie's
missing `Secure` flag. It was **never actually written down here** despite two
places saying it was — `docs/AUTH.md:200` and the comment in
`accountant/web/app.py::_send_with_session` both pointed at this file for an
item that was not in it. Recorded now, and both pointers corrected. That is the
same duplication failure the header of this file was written about.

**Needed:** a domain, then a certificate for it (Let's Encrypt is free and
automatable), then the two paths as deployment configuration. Until then the
server runs plain HTTP on loopback and says so loudly at every start.

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
### The lockfile gate — AUTHORISED 2026-08-11, and BLOCKED BY THIS ENVIRONMENT

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
        # ci/gates.toml, and until now it checked nothing. uv's reference:
        # --frozen skips the check, --locked requires the lockfile to be up to
        # date.
        run: uv sync --extra dev --locked
```

Only the `pr-fast` job, because that is the only job the gate names. No other
`uv sync` step is touched. No gate added or removed, no threshold moved.

**Status: the owner authorised this change on 2026-08-11. It could not be
applied.** `.github/` is refused twice over in this environment — the file tool
denies the directory, and the shell path is refused by the permission
classifier. Working around either would be bypassing the intent of the denial,
so it was not attempted.

**Needed: apply the four lines above by hand, or add a permission rule for
`.github/`.** Nothing else about it is undecided.

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

### DEFECT J1 — CLOSED 2026-08-11, recorded so nobody redoes it

Found by Task 16, the end-to-end journey file, at step 6. **Fixed the same day**
in `accountant/web/app.py::Handler._identify`; the reasoning, the mutants and
the seven new tests are in `docs/AUTH.md` under "DEFECT J1".

What it was, in one sentence: `Principal.require` had a passing unit test and
**no caller** anywhere in `accountant/`, so a session issued to one customer was
authenticated against another customer's open books and let through.

Why it is worth remembering rather than deleting: *a unit test of a guard proves
the guard works and says nothing about whether the guard is installed.* Every
piece of the journey had passing tests while the pieces did not connect. The
guard is now called unconditionally at the one seam every route passes through,
and an AST test asserts both that the call exists and that it is not inside a
condition.

**One thing this created, and it is not optional.** `ACCOUNTANT_TENANT` is now
**required in production** and names the customer a process serves. Unset means
every request is refused, which is deliberate — unset meaning "any tenant may
enter" is the defect itself reintroduced as a default.

**Needed:** set `ACCOUNTANT_TENANT` in whatever runs this, alongside
`ACCOUNTANT_COMPANY`. They are two halves of one statement: which books, and
whose. `docs/DEPLOY.md` lists it with the rest.

### The home path on a public repository — DECIDED 2026-08-11, option B

Owner decision, closed. Recorded so it is not re-opened.

    the two DOCS files      corrected. `$PWD` instead of a fixed home path.
    the seven ARTEFACTS     UNCHANGED, deliberately. 16 occurrences remain.

**A correction to the count I gave when asking.** I said eight files, six of
them evidence. It is **nine** files, **seven** of them evidence — one more
artefact than I reported. The decision is unaffected either way: every file
under `artifacts/` was left exactly as measured.

`docs/RUNBOOK_PHASE5_ACCEPTANCE.md` and `docs/CLAUDE_CONTEXT.md` are
INSTRUCTIONS, not records — they tell a person which command to run. `$PWD` is
the same instruction without naming whose machine it was written on, and it
still works when pasted, which a fixed path would not on anybody else's laptop.
That is why they could be changed and the artefacts could not.

The artefacts are EVIDENCE. A reproducibility manifest naming a path nobody can
check is a weaker artefact, not a safer one, and rewriting recorded evidence to
tidy a cosmetic leak is the thing this project has a rule against.

**No guard test was added.** One would stop a 25th occurrence, and it was not
part of the decision. Recorded here rather than added, so the choice is visible
rather than taken quietly.

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

**What Task 11 measured, 2026-08-11.** The server is now
`ThreadingHTTPServer`: one thread per connection, so two people no longer queue
behind each other and a hung Tally call no longer takes the product down for
everybody. `MemoryStore` opens its connection with `check_same_thread=False`
and every method that uses it holds one `threading.RLock`
(`accountant/memory/store.py::MemoryStore.__init__` records the two routes not
taken and why).

Be clear about what that buys, because the honest answer is smaller than it
sounds:

- **What is now parallel:** everything outside the database. The slow part of a
  request is the Tally round trip, and it holds no lock at all.
- **What is still serial:** every SQL statement in the process. The lock costs
  us parallel *reads*, which SQLite would have allowed; SQLite already
  serialised every *write* by locking the whole file, so no write throughput was
  given up.
- **What is still one:** the process. Two web workers against one SQLite file
  is exactly what this entry defers, and nothing in Task 11 changes it.

**The trigger to revisit:** when read latency under load, or a second worker,
becomes the constraint. Neither is measured today, and one Python lock around a
database this small is not the bottleneck for a handful of accountants.

**Needed:** nothing yet. This is recorded so the ceiling is a known number
rather than a surprise.

### Connector audit token
`CLAUDE_AUDIT_TOKEN` — a fine-grained token with `Administration: read` and
nothing else, as a repository secret.

Without it `ci/test_protection.py::test_bypass_actors_are_still_empty` cannot
run: `GITHUB_TOKEN` receives a ruleset body with `bypass_actors` **absent**, and
actionlint v1.7.12 proves `administration` is not a workflow permission scope at
all, so no `permissions:` block can grant it. The other nine protection tests
pass. Details in `artifacts/gate_integrity_blocked.md`.
