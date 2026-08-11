# REDACTION — what never reaches an application log

**Written 2026-08-11.** Implementation: [`accountant/redact.py`](../accountant/redact.py).
Proof: [`tests/test_redaction.py`](../tests/test_redaction.py).

This document exists because [`DATA_POLICY.md`](./DATA_POLICY.md) §4 listed
*"no secret is ever logged"* as a claim with a test that **could** exist and did
not. Table B row 3 said a connector key is *"never logged, in any form"*, and
until this work the only thing standing behind that sentence was that nobody had
yet written the logging call that would break it.

---

## 0. The one-sentence version

Application logs, diagnostics and stdout are **redacted at a single seam**.
The audit trail (`action_log`) is **not**, on purpose, and that is an owner
decision — see §3, which is the most important section in this file.

---

## 1. What is redacted, and what shape survives

Redaction replaces a value with a marker. How much of the value's *shape*
survives differs by class, and the line comes from `DATA_POLICY.md` rather than
from taste.

| Class | Becomes | Why that shape |
|---|---|---|
| connector secret (`ACCOUNTANT_CONNECTOR_SECRET`) | `[REDACTED]` | Table B row 3: *"never logged, in any form"*. |
| password, and the scrypt verifier | `[REDACTED]` | Table B row 1: *"Not the password, not the verifier, **not a prefix**."* |
| session token | `[REDACTED]` | Table B row 2: *"the session **id** may be logged. The token never."* |
| session token **fingerprint** | `[REDACTED]` | It identifies a live session, and it confirms a guessed token for anyone holding a candidate. The sha256 of a token is the token's shadow. |
| `Authorization: Bearer …` / `Basic …` | `[REDACTED]` | Same class as the token it carries. |
| credentials inside a URL (`https://user:pass@host`) | `[REDACTED]` | The host survives; the userinfo does not. |
| `os.environ` printed whole | `[REDACTED environment]` | One `log.info("env=%s", os.environ)` publishes every credential the machine holds. |
| vendor / party / supplier / payee / subject / ledger name | `[REDACTED vendor len=14]` | Table B row 6 permits the **length** of entry text where the text itself is refused. Length separates *"the field was empty"* from *"the field was truncated"*, which is the bug people actually chase. |
| amounts — `amount_paise=…`, `₹1,18,000`, `Rs 500`, `INR 500` | `[REDACTED amount]` | **No length.** The digit count *is* the magnitude. `len=9` on a rupee figure publishes the order of magnitude of somebody's invoice — the exact fact being hidden. |

### The two mechanisms, and why one is not enough

1. **By known value** — `redact.learn_secret(value)` / `redact.learn_private(value, label)`.
   The redactor is handed the real string and scrubs it wherever it appears.
   **This is the only thing that catches a secret which leaked into a message
   body**, where it carries no key name with it and nothing about the characters
   says "credential".
2. **By pattern** — `secret=…`, `Bearer …`, a 64+ character hex digest, a URL
   with userinfo, `₹1,18,000`, `vendor='…'`. **This is the only thing that
   catches a value nobody registered** — a password typed into the wrong field,
   or an exception message from a library we do not own.

Either alone leaves a hole the other closes.

### The seam

`RedactingFilter` is installed **once, on the handler**, by
`connector.build_logger`. Not on the logger: `Logger.handle` applies only its
own filters and then walks its *ancestors' handlers*, so a filter sitting on
`accountant.agent` is skipped entirely for a record made by
`accountant.agent.child`. The handler is the object that writes bytes to
somebody's disk, so the handler is where the guard belongs.

The filter formats the message itself and clears `record.args`. That is not
tidiness: `%s` substitution happens in the **formatter**, which runs *after*
every filter, so a filter that scrubbed `record.msg` alone would rewrite the
format string and leave the credential sitting in the arguments to be
interpolated straight into the file. It fills in `exc_text` for the same reason
— a traceback carries `str(exception)`, which is exactly where a credential
lands when a library puts a request body into its error message.

Stdout has its own seam: `accountant/agent/cli.py::_say`. An AST test fails on
any bare `print(` in that module. The startup banner deliberately prints *every*
resolved value, and one day that will include something it should not —
`--cloud-url https://user:hunter2@cloud.example` is a credential inside the one
value the banner is proudest of showing.

No call site is asked to remember anything. This repository's rule is that a
check every caller must remember is a check some caller will forget, and a
redaction call at every log statement is one chance to forget per statement.

---

## 2. What is deliberately left readable

Over-redaction is the failure mode nobody notices until an incident, when the
log turns out to be a wall of `[REDACTED]`. These are kept on purpose:

| Kept | Why |
|---|---|
| **operation ids** `ad_<32 hex>` and **run ids** `run_<32 hex>` | Table B row 9: *"logged everywhere. That is their purpose."* They are the join key between the two audit trails. What protects them is the **word boundary** in the digest rule — `_` is a word character, so `\b` never fires at the start of the hex run inside `ad_ffff…`. See §2.1: the first version of this row said something else and was wrong. |
| **a bare 32-character hex run** — a scrypt salt, or a loose uuid4 hex | Neither is a credential and a reader may need either. This is what the threshold of **64** actually buys: sha256 is 64 and a scrypt verifier at `dklen=64` is 128, so 64 is the length of the shortest digest the rule means to catch, and going lower catches non-digests without catching any digest 64 misses. A salt is public by construction anyway; `salt=…` beside its key name **is** redacted. |
| **Tally host and port** | `DATA_POLICY.md` §3.3: they are a target, not a secret, and loopback binding is the control. A row that cannot say which Tally it came from cannot be evidence about any of them. |
| **`debit_account` / `credit_account`** | Which leg the software chose is the single most useful fact when diagnosing a wrong posting, and row 8 permits field names and provenance. A ledger that is *also* a party name is covered by registering it with `learn_private`. |
| **outcome constants, job ids, exception types, counts** | The whole content of a diagnostic line. |

### 2.1 A correction, kept rather than tidied away

The row above used to read: *"this is why the digest rule requires 64 hex
characters and not 32 — a 32-character threshold would have deleted every
operation id in the system."*

**That was false, and mutation testing on 2026-08-11 is what found it.** The
mutant that lowered the threshold to 32 **survived** the whole suite. The
underscore in `ad_…` and `run_…` is a word character, so `\b` never fires at
the start of the hex run and the boundary anchors protect the ids at either
number. The threshold was doing none of the work the comment credited it with,
and nothing would have failed if somebody had changed it.

Two things came out of that and both are in the tree:

1. the reason for 64 is restated as something measurement supports — it is the
   length of the shortest digest the rule means to catch;
2. `test_a_bare_thirty_two_character_hex_run_is_left_alone` now pins the number,
   so the mutant dies.

It is recorded here because a number defended by a wrong reason is worse than a
number defended by none: the next person reads the reason, believes the number
is load-bearing, and does not check.

### The known limit of the pattern layer

`vendor='Sharma Traders'` is redacted whole. **`vendor=Sharma Traders`,
unquoted, is not** — a key-name rule cannot tell where an unquoted value ends
without eating the rest of the line, so it stops at the space and the surname
survives.

This is stated rather than hidden, and
`test_an_unquoted_two_word_vendor_name_needs_the_learned_layer` asserts both
halves: that the hole is real, and that `learn_private` closes it. In practice
this codebase formats names with `!r` (see every refusal in
`connector.refusal_for`), which is the quoted case.

### The floor on learned values

A value shorter than `redact.MIN_LEARNABLE` (8 characters) is **not** learned
for substring scrubbing. Measured, not guessed: `tests/test_connector.py:816`
builds a real `ConnectorIdentity` whose secret is the single character `"s"`.
Scrubbing that by substring would delete the letter *s* from every line the
suite writes, and a log that has lost a letter is worse than a log that named a
one-character credential. Below the floor the key-name pattern still fires, so
`secret=s` is caught; what is given up is the bare-word case. A credential that
short is not made safe by redaction — it is made safe by not being issued.

---

## 3. What is deliberately **NOT** redacted: the audit trail

> **`action_log` in `accountant/memory/store.py` keeps the vendor and keeps the
> amount. This is an owner decision, and it is not an oversight.**

That table is the record of what this software did to a real business's
**statutory books**. `DATA_POLICY.md` Table B row 10 says of it *"it **is** the
log"*; row 8 says amounts *"appear in the local action log by design, never in a
cloud log"*. An audit row that cannot say which party and how much is not an
audit row — it is a timestamp.

Redacting it would destroy the evidence it exists to hold, in the one place the
customer's own accountant would look after a scare.

**How the separation is enforced, rather than promised:**

- `accountant/memory/` imports nothing from `accountant.redact`, and
  `test_nothing_in_the_memory_package_imports_the_redactor` reads the **import
  graph** (AST, not substring — the store's own docstring contains the word) and
  fails the day somebody adds it.
- `record_action` is a SQL `INSERT`. It never becomes a `logging.LogRecord`, so
  the filter cannot reach it even by accident.
- `test_the_audit_log_is_untouched_even_after_the_redactor_learns_the_vendor`
  teaches the process-wide redactor the vendor name **and then** writes an audit
  row, proving redaction is a property of the logging seam and not of the string.

The cloud-side copy is a different question and a narrower table:
`DATA_POLICY.md` row 10 already says the cloud keeps *"a thinner one with no
amounts and no ledger names"*. That thinning happens where the cloud row is
built, not here, and the cloud does not exist yet.

---

## 4. How to add a new secret

**If it has a known value at runtime** — a credential, a key, a party name:

```python
from accountant import redact

redact.learn_secret(value)  # credential: nothing survives
redact.learn_private(name, "vendor")  # private: the length survives
```

Register it at the **narrowest place the value cannot avoid passing through**,
not at the place you happen to be editing. The working example is
`ConnectorIdentity.__post_init__`: there is no way to hold a connector secret
without constructing one, so the redactor learns it whether or not anybody
remembered. A `learn_secret(...)` line in `cli.main` would have been one line
somebody deletes while refactoring startup, after which the guard is silently
off and every test still passes.

**If it is recognisable by shape or by the key name beside it** — add a rule to
`_PATTERNS` in `accountant/redact.py`:

1. Write the compiled pattern next to its neighbours, with a comment saying what
   real string it is aimed at.
2. Add it to `_PATTERNS` **in the right position**. The table runs top to
   bottom; narrow rules go before broad ones, or a broad rule eats the input the
   narrow one needed.
3. Decide the shape — `HIDDEN` for a credential, `shaped(value, label)` for
   something private but not replayable — and write the reason down. "Because it
   felt safer" is how a log becomes undiagnosable one rule at a time.
4. Add a test to `tests/test_redaction.py` with **both** halves: the secret is
   gone, **and** a named diagnostic value on the same line survived.
5. Check it against §2. If your rule would eat an operation id, a run id or a
   host and port, it is too wide.

**Always add the control.** The first test in `tests/test_redaction.py` plants
the credential in a handler built *without* the filter and proves it lands on
disk. Without that, every "the secret is not in the file" assertion can pass
because the secret was never going to be there.

---

## 5. What this does not cover

- **The cloud.** Nothing in `CLOUD_ARCHITECTURE.md` is built. When it is, it
  gets its own seam; this module is importable from anywhere because it takes no
  `accountant` import, but nothing wires it in yet.
- **The web app.** `accountant/web/app.py` builds no `logging` logger today —
  the only logger the product constructs is `connector.build_logger`. The day it
  builds one, it calls `redact.guard(handler)`, and the day it does not is the
  day this document is wrong.
- **Third-party log output.** There are no runtime dependencies, so there is
  nothing else writing lines. That is a property of the dependency list, and it
  changes if the dependency list changes.
- **Data already written.** Redaction applies to lines written from now on. It
  does not go back and clean a log file that already holds a credential; the fix
  for that is rotating the credential, which is what `DATA_POLICY.md` row 3
  means by *"destroyed on revocation, on both sides"*.
