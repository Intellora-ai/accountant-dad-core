# Owner work

**One file. This is the only place this list lives.** `docs/PROJECT_STATE.md`
points here rather than repeating it, because tonight this project measured what
duplication costs: `accountant/rules/` was recorded as "verified absent" in three
documents at once, days after it had merged, and the three corroborated each
other into false confidence.

Nothing here blocks coding work. Each item is recorded and work continues around
it.

## THE FOUR LINES ONLY YOU CAN APPLY

**AUTHORISED BY YOU 2026-08-11, AND STILL BLOCKED BY THIS ENVIRONMENT.** You
gave explicit permission for these exact edits. The file tool still answers
*"File is in a directory that is denied by your permission settings"*, and the
shell path is refused by the permission classifier. Your authorisation does not
lift it: it is a harness setting on the directory, not a missing permission from
you. Routing around it would be bypassing the intent of the denial, so it was
not attempted. **These are paste-ready.**

### 1. The secret — DONE

`CLAUDE_AUDIT_TOKEN` exists as a repository secret, created 2026-08-11:
fine-grained, `Administration: read` only, no expiration, scoped to this
repository. Nothing further needed.

### 2. `pr-fast.yml` — REMOVE the audit token (lines 40-42)

It is currently in the workflow-level `env:` block and **it must come out.**
`ci/check_workflow_integrity.py` reports it as
`[WEAKENING] SECRET_ON_PR_PATH`, a finding that **cannot be acknowledged**, and
that single finding is what keeps PR #34 red.

The rule is right. `pr-fast` runs `pytest` over the code in the pull request, so
a secret in that job sits in the environment of arbitrary PR code.

```
BEFORE
  GH_TOKEN: ${{ github.token }}  # Administration:read only. Separate from GH_TOKEN on purpose - the nine live
  # protection tests measure what the DEFAULT identity can do.
  CLAUDE_AUDIT_TOKEN: ${{ secrets.CLAUDE_AUDIT_TOKEN }}

AFTER
  # The DEFAULT identity, so the nine live protection tests actually run. They
  # measure what the token CI runs as can do to branch protection, and the
  # refusal IS the measurement. Unauthenticated, `gh` cannot even ask.
  #
  # NOT the audited identity, and that is not an omission. This workflow is
  # triggered by `pull_request`, and ci/check_workflow_integrity.py refuses any
  # `secrets.*` in a PR-triggered workflow: SECRET_ON_PR_PATH, a WEAKENING that
  # cannot be acknowledged. This job runs pytest over the code in the pull
  # request, so a secret here sits in the environment of arbitrary PR code.
  # CLAUDE_AUDIT_TOKEN lives in watchdog.yml, which has no pull-request trigger.
  GH_TOKEN: ${{ github.token }}
```

`RUN_DESTRUCTIVE_TESTS: "1"` and its comment **stay exactly as they are.** Not a
secret, and the tamper tests need it.

### 3. `pr-fast.yml` line 72 — the lockfile gate

`--frozen` is the one uv flag that guarantees the check is SKIPPED. This step's
own comment already says it IS the gate.

```
BEFORE  (lines 69-72)
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

**This gate has never run once, so `uv.lock` may be stale.** If CI goes red here
on the first run, that is the gate working, not a regression: run `uv lock`,
commit the updated lockfile, push. `uv` is not installed in the agent
environment, so that one command is yours.

The second `--frozen` at line 249 and the five in `full.yml` are **named here
and deliberately not changed** — you authorised one change to one step.

### 4. `watchdog.yml` — a NEW step, where the token is allowed

`watchdog.yml` triggers on `schedule` and `workflow_dispatch` only. No
pull-request trigger, so no PR code ever runs there and the checker permits a
secret. It is also already the branch-protection auditor: its own header says
*"Someone turns off a rule, or adds a bypass actor, and every gate downstream
becomes decoration."*

Step-level, not job-level, so only this step sees the value. Add it inside the
`ruleset-drift` job, after the `audit branch protection` step:

```
      - name: measure bypass_actors
        # The one place in this repository where a secret is permitted: this
        # workflow has no pull-request trigger, so no PR code runs beside it.
        #
        # ci/test_protection.py is the ONLY Python file allowed to read this
        # variable - test_only_this_file_reads_the_audit_token enforces that -
        # so the token is handed to the step that runs it and to nothing else.
        env:
          CLAUDE_AUDIT_TOKEN: ${{ secrets.CLAUDE_AUDIT_TOKEN }}
          RUN_DESTRUCTIVE_TESTS: "1"
        run: uv run pytest ci/test_protection.py -q
```

The other job's `GH_TOKEN: ${{ github.token }}` at line 60, and the audit step's
at line 115, are **not touched**.

### What these four unblock

```
2 (token out of pr-fast)  ->  SECRET_ON_PR_PATH clears, and PR #34 can go green
4 (token into watchdog)   ->  bypass_actors is measured somewhere it is legal to
                              measure it, instead of nowhere
3 (--locked)              ->  the lockfile gate starts checking, first time ever
```

**One editing session, four things fixed.**

---

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

### Production reader selection — CHOSEN AND BUILT, ACCOUNT AND VERIFICATION STILL YOURS

**Launch scope (D-23): typed-text entry and uploaded documents (PDF/PNG/JPG)
enabled at launch. Azure backend implemented; real-invoice verification required
before general availability.** Recorded in full at `docs/DECISIONS.md`, D-23.

The decision as recorded, verbatim:

> D-23 (2026-08-11): First launch supports typed-text entry and uploaded documents (PDF/PNG/JPG) via Azure Document Intelligence. Azure backend is implemented; real-invoice verification is required before general availability.

**Vendor: Azure Document Intelligence**, chosen 2026-08-11 for its Central India
region and the clearest retention terms of the three compared. Registered as
`azure`; it is the default. Implementation is on PR #61.

**Azure extractor status: `UNVERIFIED_VENDOR_SHAPE` until real invoices are
tested.** No request has yet reached Azure in production. Parser is based on
documented shape; tests use synthetic responses. Real-invoice verification
required before general availability.

That label is not modesty and it is not a formality. The parser and the test
fixtures were written by the same author from the same documentation, so a green
suite proves they agree with each other. Nothing so far can tell you they agree
with Azure. **Do not let anyone write "verified" until a real bill has gone
through end to end.**

**What is left for you, in order:**

1. **Create the Azure Document Intelligence resource** and take its two values.
   Nothing here can do that — it needs a card.
2. **Set `ACCOUNTANT_AZURE_ENDPOINT` and `ACCOUNTANT_AZURE_KEY`** wherever the
   app runs. `docs/DEPLOY.md` lists both with their failure modes. The key is a
   credential: injected at run time, never in the image.
3. **Run the real-invoice verification below** before you call it GA.

**Until you do, nothing is broken.** With no credentials an uploaded document is
refused with a sentence naming both variables, a typed bill still works exactly
as before, and nothing is guessed. There is no fallback, deliberately: a reader
that quietly degrades to guessing is the failure the extraction package exists
to prevent.

#### Real-invoice verification — REQUIRED BEFORE GA

1. Configure real credentials in a test environment.
2. Upload **10–20 real invoices** (PDF/PNG/JPG) — actual customer bills or
   representative samples.
3. Verify extracted fields against the paper: vendor, date, amount, GST
   breakdown. Verify error handling too: malformed files, unsupported formats.
4. Record the results in `docs/AZURE_VERIFICATION.md` — how many tested, the
   accuracy rate, every issue found and what was done about it.
5. Only then change the status here from `UNVERIFIED_VENDOR_SHAPE` to
   `VERIFIED_ON_REAL_INVOICES`.

**Until step 5 is done, the launch is beta or early access, not GA.** That is
not a marketing choice; it is what the evidence currently supports.

**Two numbers here were chosen by the implementer, not by you**, and are
recorded so you can overrule them: a 60-second overall deadline and a 1-second
poll interval for Azure's asynchronous analysis
(`accountant/reader/azure.py`). Both are constructor arguments, so changing them
does not mean editing that file.

`artifacts/extraction_backends.md:3` recorded this as
`OWNER_DECISION_REQUIRED`; the decision is made, and that line is history rather
than a live block.

**The seam is now BUILT — Task 9, 2026-08-11.** Before that day,
`grep -rn "multipart\|enctype\|type=file" accountant/` returned nothing: there
was no way to hand this product a document at all. There is now.

What shipped:

- `POST /upload` in `accountant/web/app.py`, reachable from a file input on the
  home page. Authenticated like every other route; a maximum of **100 MB**
  refused with `413` on the declared length **before the body is read**; an
  allow-list of `application/pdf`, `image/jpeg`, `image/png` refused with `415`;
  a malformed body answered with `400` and a sentence rather than a crash.
- `accountant/web/multipart.py`, a strict stdlib parser. `cgi` was removed in
  Python 3.13 and `.python-version` says 3.14, so there was nothing to call and
  `dependencies = []` had to stay empty.
- `accountant/extract/placeholder.py::PlaceholderReader`, registered as
  `no_reader`. Every field comes back explicit `not_found` carrying *"no
  document reader is configured"*. It never guesses, never blanks, never claims
  a name it is not, and never carries the uploaded bytes back out.
- The upload goes through `Runtime.extractor` — the **same** seam typed text
  uses — so the answer flows into the existing decision path and the person is
  told plainly. Nothing is written to disk and nothing is logged: the durable
  row records the decision, never the document.

**One limitation of that 100 MB, ruled 2026-08-13 and deferred, not forgotten.**
In your own words:

> "Uploads up to 100 MB are currently read fully into memory per request. This
> is acceptable for a single local user. Before multi-tenant hosting, this must
> be changed to streaming with concurrency limits."

**MANDATORY before any multi-tenant hosting or public deployment** — not an
optional improvement, just deferred past this MVP. It needs two things together:
streaming into the multipart parser instead of one whole body in memory, and a
cap on how many uploads may be in flight at once. The full note lives in
[`ARCHITECTURE.md`](./ARCHITECTURE.md) §4.8, beside the web application it
constrains. **No code change now.**

**What swapping in a real vendor costs, exactly.** Three edits, all inside
`accountant/extract/`:

1. a class satisfying the `Extractor` Protocol that calls the vendor through an
   **injected** transport — `service.ServiceExtractor` is that shape already and
   needs only a `ServiceCall`. It cannot import a vendor SDK:
   `tests/test_no_reader.py` allows stdlib and `accountant.*` and nothing else,
   so the criterion is *plain HTTPS JSON API*, not *best SDK*.
2. one line in `registry._READY` giving it a name.
3. `registry.DEFAULT_BACKEND` set to that name.

Nothing outside that package changes, and that is measured rather than promised:
`tests/test_adapter_contract.py` counts concrete-backend references outside
`accountant/extract/` off the AST and the count is `{}`.

**Still true, and not fixed by any of the above:** `S2 = NOT_MEASURED`. Nothing
read anything, the question rate for uploaded documents is not zero and is not
measured, and the placeholder's output is **not extraction evidence**. Until a
vendor is chosen, every uploaded document returns explicit `not_found` and the
person is asked to type the entry instead.

Measured options are in `artifacts/extraction_backends.md`. Azure Document
Intelligence and AWS Textract are both $0.01/page; Azure has the clearest
retention terms and a Central India region, AWS has explicit GST fields.

**Needed:** pick one, create the account, add the endpoint and key. That is a
person, not a task — no code change is blocked on anything else.

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
**No automated test in this repository touches a real TallyPrime**, and that is
still true. `tests/test_tally_contract.py:63` yields `FakeTally`;
`tests/test_real_tally.py` drives a simulator and says so in its own header.

**Corrected 2026-08-12.** This entry used to end *"`LICENSED_REALTALLY`
evidence: none, anywhere."* That is no longer true. `PROJECT_STATE.md` §47
records a run against a licensed TallyPrime — the owner's `TANVEER SIDHU`
company, ledgers created over XML, a Purchase voucher posted and read back. It
was a script (`mvp_real_tally.py`), not a test, so the first sentence stands and
the third did not.

It is **not** an acceptance pass. Four of the fifteen conditions in
`RUNBOOK_PHASE5_ACCEPTANCE.md` were touched, one of those four failed, and
condition 14 (`trial_balance_restored`) is failing in those books today: the
script ran twice and left a duplicate ₹1,000 voucher. Condition by condition in
that runbook's PART J. The duplicate guard that prevents a repeat is in code and
tested; **removing the existing duplicate is an owner action** — a deletion in
TallyPrime cannot be undone and those are real books.

**Needed, and settled as permanent 2026-08-12:** `Demo Co` and four ledgers
(`Purchases`, `Sundry Expenses`, `Cash`, `Sharma Traders`) created **in the
TallyPrime GUI**, with the HTTP gateway switched on. The XML gateway refuses
company creation with `<RESPONSE>Unknown Request, cannot be processed</RESPONSE>`
because company creation is an administrative flow and is not on the documented
integration surface at all — not because a workaround is missing. No XML
workaround will be attempted; retrying wedged a live gateway once already. See
`RUNBOOK_PHASE5_ACCEPTANCE.md` §A.0.1. Then a Windows VM reachable from CI if
those tests are ever to run automatically.

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

### Destructive tests are opt-in — DONE, landed separately

Merged on its own so it did not have to wait on a secret. A plain `pytest` no
longer asks GitHub to delete anything; `RUN_DESTRUCTIVE_TESTS=1` opts in.

**Needed: nothing here.** Setting `RUN_DESTRUCTIVE_TESTS=1` in CI is listed
under the lockfile entry above, because it is the same blocked `.github` edit.


### An acknowledgement now expires with its content — BUILT, WAITING ON YOU

The fingerprint was `CODE:location`, so an acknowledgement said *"somebody
looked at the header of `pr-fast.yml` once"* and went on saying it for ever.
Measured: `contents: read` swapped for `write-all`, nothing else touched, no ack
added — and the checker returned **PASS**, consuming an ack written for a
different change.

It is now `CODE:location:hash-of-the-acknowledged-content`, over all six
acknowledgeable finding types. An ack dies the moment its content changes.

**Why it has not landed.** It ships with `ci/check_workflow_integrity.py`, which
correctly flags `ci/test_protection.py`'s module-level `pytest.mark.skipif` as
`PROTECTION_TEST_SKIPPABLE` — a module-level skip is how those tests once passed
on every hosted run without calling GitHub. Removing that skip needs the live
tests to actually run in CI, which needs `CLAUDE_AUDIT_TOKEN`.

So the chain is: **secret → workflow line → the skip can go → the checker can
land.** One action of yours unblocks all four.

**Needed: create `CLAUDE_AUDIT_TOKEN` and apply the two workflow lines** (this
entry and the lockfile entry above give both verbatim). Nothing else about it is
undecided, and no further code is waiting to be written.


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

### Connector audit token — STEP 1 BUILT, STEP 2 IS ONE LINE YOU MUST APPLY

`bypass_actors` is withheld from `GITHUB_TOKEN`. Reading it needs repository
`Administration: read`, which exists only as a fine-grained
personal-access-token permission — actionlint v1.7.12 rejects `administration`
as a workflow `permissions:` scope, so no `permissions:` block can grant it.
That is why `test_bypass_actors_are_still_empty` reports `NOT_MEASURED` and not
a verdict.

**STEP 1 — BUILT 2026-08-11.** `ci/test_protection.py` reads
`CLAUDE_AUDIT_TOKEN` from the environment and runs every `gh` call as that
identity. One reader, tested: the token is forwarded, a blank one does not
clobber a working `GH_TOKEN`, the value is never printed, no other file reads
it, and an AST test proves `gh()` actually *uses* the environment it computes —
that last one exists because a mutant deleted `env=` and nothing noticed.

**STEP 2 — YOURS.** Two parts, neither of which can be done from here.

1. Create the repository secret. Fine-grained token, **`Administration: read`
   and nothing else**, named `CLAUDE_AUDIT_TOKEN`. Never send the value to
   anybody, including me; the mechanism above never needs to see it.
2. Pass it to the job. In `.github/workflows/pr-fast.yml`, the `env:` block
   currently reads:

```
  GH_TOKEN: ${{ github.token }}
```

   Add one line beside it:

```
  CLAUDE_AUDIT_TOKEN: ${{ secrets.CLAUDE_AUDIT_TOKEN }}
```

   `GH_TOKEN` stays. It is what the other nine live protection tests run as,
   and they are measuring what the DEFAULT identity can do — replacing it would
   destroy that measurement.

**Until step 2 is applied the constant does nothing**, and that is the honest
state: the mechanism is complete and the wire is not connected. `.github/` is
denied at the permission layer in this environment — see the lockfile entry
above.

**When it is applied**, `bypass_actors` becomes readable, the `NOT_MEASURED`
outcome turns into a real verdict on its own, and the strict-xfail pair around
it fails loudly until somebody removes the now-passing half. Nothing else needs
changing.

### `period_open` has no source — RULED 2026-08-13, CLOSED, and deferred

**This is no longer an open question. Nothing is asked of you here.** It is a
known limitation of this MVP, recorded so nobody re-opens it, and the ruling is
in your own words:

> "Period check is currently off the live path because Tally open/closed bounds
> are not read. This is a known limitation for this MVP. A future task will read
> SVFROMDATE/SVTODATE from Tally and enable this gate on the live pipeline."

**No further action, and nothing is being built.** The future task is named
above and is not started. Do not wire the period check onto the live pipeline as
part of some other change.

The background, unchanged, so the limitation is understandable rather than just
asserted:

`period_open` has no source. Tally knows the financial-year bounds and refuses
an out-of-range date at the write door; nothing reads them beforehand. Until
something does, the gate is passed `None` and those entries block. Closing it
means a Tally read (`SVFROMDATE`/`SVTODATE`, noted at `tallyio/reports.py:45`)
that needs a live Tally to verify.

Closing it is also what lets `accountant/cage/gate.py` move from the reader path
onto the pipeline path. It is not the only thing that has to land first — no
reader produces per-field confidence yet, and three of the four conservation
laws have no inputs on that path — but it is the one that needs a live Tally
rather than more code.

### A bill whose own numbers contradict each other — ASK or BLOCK? NOT BLOCKING

**Nothing posts either way.** Only the label and the sentence differ, so this is
not holding anything up. It is written down because two parts of the product
disagree and neither should quietly win.

`demo_safety_cage.py` and `accountant/cage/decision.py` label two cases the
opposite way round:

| the bill | the demo, following your criterion | `decide()` |
|---|---|---|
| its own numbers contradict each other — line items out by a paisa, net + tax ≠ gross, debits ≠ credits | BLOCK | ASK |
| it has no party on it at all | ASK | BLOCK |

**The reading I believe is right, and why.** Your five hard rules — each of
which always blocks — are: tax on the bill, a law INDETERMINATE, the period
closed, the party unknown, and the question budget spent. A conservation law
that **FAILED** is not one of the five. Your band list then says ASK covers "any
conservation law FAIL at any confidence", explicitly. So `decide()` returning
ASK for a bill that does not add up is your own rule applied literally, and the
unknown party blocking is your fourth hard rule applied literally. On that
reading `decide()` is right on both rows and the demo is following the wording
of the acceptance criterion rather than the wording of the rules.

**The reading that says the demo is right.** "Refuse every bad one" is the
acceptance criterion in your own words, and a bill whose arithmetic contradicts
itself is bad in the plainest sense — nothing a person answers makes 45,000 +
74,999 equal 120,000. On that reading ASK spends one of the five questions on
something no answer can fix, which is the same objection
`checks.py::tax_lines_can_be_posted` records under `problems.UNANSWERABLE_CHECKS`.

**What is not being done.** Neither side has been changed to match the other.
The disagreement is named in `demo_safety_cage.DIVERGENCE`, printed on every
demo run, and asserted in `tests/test_demo_i54.py`, so a test fails the day
either side moves without the other.

**If you want it settled:** one line changes in `decision.py::_asking` (move a
failed law from the ask list to the block list) plus its test, or one line
changes in the demo's `DIVERGENCE`. Say which, and it is a small edit either
way.

