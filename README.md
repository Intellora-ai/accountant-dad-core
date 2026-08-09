# Accountant Dad

A safe, reversible decision-and-posting layer in front of Tally.

You type or drop in a bill. It works out the fields, checks them against **your
own company's Tally history** and against Indian accounting rules, asks a plain
question when it is unsure, and posts into **your** Tally when it is confident.

Tally stays the book of record. We never hold your books. Delete this software
tomorrow and your accounts are untouched and complete.

## What it decides, in this order

| | | |
|---|---|---|
| **Not valid** | Something no answer could fix | Tells you. Posts nothing. |
| **Unclear** | Something a person could clear up | Asks one plain question, then reconsiders. |
| **Valid** | Every check passes, memory agrees, no detector fires | Posts, then tells you what it wrote. |

First match wins. There is **no confirmation step** — the system's own judgement
decides. It asks when it needs to understand, not for permission.

## The rules it holds itself to

- **A question never contains a ledger account name.** Not "Purchases or Repairs?"
  but "Was this stuff you'll sell on, or fixing something you already own?"
  Enforced by test against the company's chart of accounts.
- **Never a guess.** An unseen vendor produces a question, never a fallback
  account. No "Suspense", no "Sundry Expenses", no anything.
- **Every value carries its source.** A field with no provenance is, by our own
  definition, a hallucination.
- **Money is integer paise, everywhere.** A float in a money field is a
  correctness bug, not a style choice.
- **Every write carries a unique operation ID**, and is read back afterwards.
  HTTP 200 is not proof a voucher exists. Reversal is by operation ID, never by
  amount — two vouchers with the same amount and narration are normal.
- **A retry cannot post twice.** Duplicate operation IDs are rejected.
- **At most 5 questions per entry**, none overlapping. Then the entry is saved
  as a draft for a person to finish, which is an honest end to a conversation
  that stopped making progress — not a failure.

## Measured, not claimed

| | |
|---|---|
| Tests | 891 |
| Test suite runtime | 0.06s (without the web tests) |
| Line coverage | 95% |
| Mutation score | 94% of 267 mutants |
| Lint (ruff) | 0 |
| Types (pyright strict, source **and** tests) | 0 |
| Security (bandit, blocks at LOW) | 0 |
| Runtime dependencies | **0** |

Mutation testing requires `COVERAGE_CORE=pytrace`. Python 3.12+ defaults
coverage to the `sysmon` core, which does not support dynamic contexts;
`pytest-gremlins` uses those to map tests to lines, so on the default core the
mapping is silently incomplete and the score under-reports badly.

## Run it

```bash
uv sync --extra dev
python -m accountant.web              # http://127.0.0.1:8000

It connects to TallyPrime FIRST. If Tally is not running, or the company is not
open, or its HTTP server is off, it refuses in the terminal and exits 1 rather
than serving pages that cannot work.
```

Every gate, locally, before you push:

```bash
./scripts/guards
```

## Status

The Tally connector is a Protocol with two implementations. `FakeTally` is for
tests. **`RealTally` is built** — XML over HTTP to Tally's port 9000 — and has
been read from and written to against a real TallyPrime 7. The web app imports
neither directly; it asks a factory, so "which Tally are we talking to" has one
answer and one place to enforce it. If Tally is unreachable the app refuses to
start rather than serving pages that cannot work.

Three kinds of evidence, never merged:

| class | proves | cannot prove |
|---|---|---|
| FakeTally | our logic is right | anything about TallyPrime |
| Educational-mode compatibility | a real Tally accepted our XML on a permitted date | that the unchanged contract fixture passes |
| RealTally live | the product works on real books | — |

The live class is **not yet obtained.** Tally here runs in Educational mode,
which accepts vouchers only on the 1st, 2nd and 31st, and the contract fixture
posts on the 7th. That fixture is not edited to work around it. See
[`docs/PROJECT_STATE.md`](docs/PROJECT_STATE.md) §24 and §25.

Our own data — the memory index and the append-only action log — is in SQLite
and survives a restart. Their books are never ours; they stay in Tally.

Not yet built: PDF/image input, and the Indian rules corpus beyond a small
phrasebook.

## Licence

None yet.

<!-- CI first measurement: this PR exists to make pr-fast report a real runtime. -->

