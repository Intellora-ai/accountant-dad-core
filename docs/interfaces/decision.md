# `Decision` — `accountant/cage/decision.py`

**One job.** Turn a checked observation into post, ask, or block — and build the
`LedgerEntry` on the one outcome that writes.

The only module in the repository allowed to construct a `LedgerEntry`.
`wall.py` names it as the sole answer to *who may write*; this file answers
*when*.

## Inputs

One `Situation`, frozen, **with no defaults on the facts that matter**:

| Field | Type | Required |
|---|---|---|
| `observation` | `Observation` | yes |
| `conservation` | `tuple[ConservationResult, ...]` | yes — all four laws, in `LAWS` order |
| `party_known` | `bool \| None` | yes, **no default** |
| `period_open` | `bool \| None` | yes, **no default** |
| `carries_gst` | `bool \| None` | yes, **no default** |
| `questions_asked` | `int` | yes |
| `debit_account` / `credit_account` | `str` | yes |
| `ambiguous_fields` | `tuple[str, ...]` | defaults to `()` |

The three `bool | None` fields are the design's load-bearing detail. *"The period
is open"* and *"nobody looked up whether the period is open"* are different
facts, and a plain `bool` forces the second to be written as the first. **Every
one of them blocks on `None`.** A default of `period_open=True` would be a fact
nobody checked wearing the costume of one, supplied silently at every call site
that forgot — so a caller who forgets gets a `TypeError` here rather than a post
there.

## Outputs

One `Decided`: `action`, `said` (the sentence a person reads), `reasons` (every
reason, not the first), and `entry`.

**Invariant, enforced in `__post_init__` rather than in `decide`:** `entry` is
present **if and only if** `action is POST`. A blocked decision carrying a
writable entry is one careless attribute access away from posting the thing that
was just refused — a defect that survives review because both halves look fine
on their own.

Also enforced: at least one reason (an outcome nobody can explain is not an
outcome) and a non-empty sentence.

## The bands, owner-set

```
post    >= 0.95  AND every conservation law PASS  AND party known
                 AND period open  AND no hard rule broken
ask     0.70 to just under 0.95, OR any law FAIL at ANY confidence,
                 OR a field readable more than one way
block   under 0.70, OR any hard rule broken
```

**Certainty never outvotes arithmetic.** A confidence score says how legible some
pixels were; a conservation law says whether numbers agree. They are not on the
same scale and do not trade off, so a failing law sends a bill to ASK at
confidence 1.0 exactly as it does at 0.71. This is the single behaviour the whole
cage exists for: `confidence.py` cannot see a value the engine misread
*confidently*, arithmetic can, but only if arithmetic is allowed to win.

## Five hard rules, each of which always blocks

| Rule | Why |
|---|---|
| tax on the bill | owner decision Q3 = D. Writing the bill without its tax line leaves a wrong statutory entry |
| a document law `INDETERMINATE` | "could not check" is not "checked and fine" |
| the period closed | the books for that date are shut |
| the party unknown | a name is never added to somebody's chart of accounts. The person is asked |
| the question budget spent | a product that will not take no for an answer is worse than one that hands the entry back |

## Does NOT

Build a Tally request. Talk to Tally. Persist anything. Touch the network.
Override a failed check for any confidence. Move a threshold.

## Never raises on a situation it was given

A float amount, a verdict it does not recognise, a question count that is not a
number — each is refused in one plain sentence rather than becoming a traceback.

That direction is **measured, not preferred**. This repository already recorded
what the other one costs: an ordinary bill reached a connector that refused it,
the exception propagated, and over HTTP a person got *"Something in Accountant
Dad broke"*. A refusal a person can read is a product; a stack trace is an
outage.

## Depends on

`conservation`, `wall`, `questions` (for `QUESTION_CAP`), `money` for any
rupee figure. All pure or already tested; none touches the network.

## Observability

One audit line per decision: input hash, minimum field confidence, every check
result, outcome, reason, timestamp. Side effects: that line, and constructing a
`LedgerEntry` on POST. Nothing else.

## What it cannot do, said so nobody relies on it

It cannot see a bill misread **consistently** — every figure scaled by ten. Every
law holds, every field is legible, confidence is 1.0, and it posts. That is
failure mode **F-02**, no arithmetic sees it, and nothing here pretends
otherwise.

It also cannot tell whether the party, period and tax facts it was handed are
*true*. It can only tell whether somebody actually looked: `None` means nobody
did, and nobody-looked blocks.
