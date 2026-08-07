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
| Tests | 208 |
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
python -m accountant.web.app          # http://127.0.0.1:8000
```

Every gate, locally, before you push:

```bash
./scripts/guards
```

## Status

The Tally connector is defined as a Protocol and exercised by contract tests
against an in-memory fake. **The real connector — XML over `localhost:9000` —
is not built yet**, because Tally is Windows-only and the VM does not exist.
The same contract tests run unchanged against it when it does.

Not yet built: PDF/image input, the Indian rules corpus beyond a small
phrasebook, SQLite persistence (state is in memory and is lost on restart).

## Licence

None yet.
