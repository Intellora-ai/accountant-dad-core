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
| `moment` | `Moment` | yes, **no default** |
| `pdf_repaired` | `bool \| None` | yes, **no default** |
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
ask     0.70 to just under 0.95, OR a field readable more than one way
block   under 0.70, OR any hard rule broken — and a law that FAILED
                 is one of them, at ANY confidence
```

**A conservation FAIL blocks, and that reversed on 2026-08-13.** It used to
`ASK`, and the band list above used to say so. Owner decision, verbatim:
*"Conservation FAIL → BLOCK, always. This is now a hard rule."* Nothing a person
can answer makes 45,000 + 74,999 equal 1,20,000, so the question spent one of
five and ended in the same refusal. The sentence is the owner's own: *"The
numbers in this bill do not add up. Please check the original and upload a
correct version."*, followed by the failing law's own line so the person has the
two figures to reconcile against the bill.

**Certainty never outvotes arithmetic.** A confidence score says how legible some
pixels were; a conservation law says whether numbers agree. They are not on the
same scale and do not trade off, so a failing law refuses a bill at confidence
1.0 exactly as it does at 0.71. This is the single behaviour the whole cage
exists for: `confidence.py` cannot see a value the engine misread *confidently*,
arithmetic can, but only if arithmetic is allowed to win.

## Three laws are about the bill. The fourth is about the books.

`debits_equal_credits`, `lines_sum_to_total` and `net_plus_tax_equals_gross` ask
whether the numbers **on the piece of paper** agree. That has an answer before
anything is written, so `INDETERMINATE` on any of them blocks at either moment.

`balance_delta_equals_entry` asks whether the **books** moved by exactly the
entry — it compares the ledger balance before with the balance after, and before
a write there is no after. Its honest pre-write verdict is `INDETERMINATE` on
every bill, every time. Blocking on it made auto-post unreachable except by
handing the law a *predicted* after-balance, which makes it compare a number
against itself: a check that cannot fail wearing the face of one that passed.

So the caller states which moment it is, and pre-write an `INDETERMINATE` fourth
law is expected rather than blocking. **The exemption is narrow in three ways,
and each is why it is safe:**

| Narrow in | What still blocks |
|---|---|
| one law | `DOCUMENT_LAWS` is *derived* from `conservation.LAWS`, so a law added there blocks by default rather than becoming exempt by omission |
| one moment | `AFTER_THE_WRITE`, an `INDETERMINATE` fourth law blocks — there it means nobody read the register back |
| one verdict | a pre-write **FAIL** still refuses the post. "Not yet knowable" is exempt; "known to be wrong" never is |

`Moment` has no default and is never inferred from whether a balance arrived: a
balance absent because it cannot exist yet and one absent because the caller
forgot are the same `None`.

## A repaired file caps the outcome at ASK — a ceiling, not a rule

Owner decision, 2026-08-13, verbatim: *"If the PDF had to be repaired: in the
decision layer, if conservation checks and all other rules pass, allow confirm
(ask), but do NOT auto-post. If anything else is uncertain or fails, block with
a plain sentence."*

`pdf_repaired=True` adds one more reason to ask. It is deliberately **not** an
early return and **not** a ninth hard rule: a ceiling lowers the best
available outcome from POST to ASK and changes nothing else, so a repaired file
that is also wrong about something still blocks. Written as `return ASK` it
would have *overturned* those blocks, which is the opposite of the second half
of the same sentence.

**`None` does not mean "nobody looked" here, and it is the only field in
`Situation` where it does not.** The other three `bool | None` facts are things
about the customer's books somebody has to go and look up. This one is a fact
about our own processing, which the caller always knows:

| Value | Means | Effect |
|---|---|---|
| `True` | the bytes had to be mended before anything could be read | ceiling: ASK at best |
| `False` | it is a PDF and it did not need repairing | none |
| `None` | not a PDF, or nothing to repair | none |
| anything else | nobody can tell | **blocks** — a value nobody can read is not evidence that nothing was repaired |

It still has **no default**, and here the reason is sharper than for the other
fields: the safe-*looking* default is the dangerous one. `None` grants the full
post, so a default would hand every caller that forgot exactly the permission
the field exists to withhold.

## Eight hard rules, each of which always blocks

Listed in the order `_blocking` evaluates them, which is the order a person
reads them in on screen.

| Rule | Why |
|---|---|
| a law `FAIL` | owner decision, 2026-08-13, and it **reversed** what this module did: a failed law used to ASK. Nothing a person can answer makes 45,000 + 74,999 equal 1,20,000 |
| tax on the bill | owner decision Q3 = D. Writing the bill without its tax line leaves a wrong statutory entry |
| the tax flag and the tax figure disagree | `carries_gst` is the caller's; `tax_paise` is on the reading in the same argument. Two statements about one fact, and neither is trusted over the other — when they disagree, nothing is posted |
| checked ≠ written | the amount the laws were run on is not the amount the entry would be for. See *The number checked and the number written* below; this is the one the rest of the page depends on being true |
| a document law `INDETERMINATE` | "could not check" is not "checked and fine". **A separate rule from the first one**, deliberately: they share an outcome and not a sentence, because one means send a readable copy and the other means the figures disagree with each other |
| the period closed | the books for that date are shut |
| the party unknown | a name is never added to somebody's chart of accounts. The person is asked |
| the question budget spent | a product that will not take no for an answer is worse than one that hands the entry back |

## The number checked and the number written are the same number

Every claim on this page is *"the arithmetic was checked before anything was
written"*, and that sentence is only true if the amount checked and the amount
written are one amount. They arrive from two places: the verdicts in
`Situation.conservation` are computed by the **caller** from the caller's
figures, and the entry is built from `observation.total_paise`. Until 2026-08-13
nothing compared them — measured, laws passing on 1,00,000 paise authorised a
write of 1,00,00,000 paise and returned POST.

So `Situation` carries `checked_paise`, no default, and `decide` refuses any
bill where it is not the amount that would be written. **The laws are not re-run
here**: that would take a responsibility this module does not own, and a check
that computes its own evidence cannot be contradicted by anybody — another check
that cannot fail. The caller states what it checked; this compares two
statements and believes neither on its own. Equality is exact, like
`conservation._compare`: a one-paisa tolerance would absorb the misread digit
this is most likely to catch.

## The eight are the business rules. They are not every refusal.

**Measured, not counted by eye: `decide` and `_blocking` hold 24 distinct
block-producing branches.** A branch is a `return` or a `reasons.append` that
puts a new refusal sentence into a BLOCK, each arm of a conditional counted
separately because the two arms carry two different sentences about two
different facts. The scan is
`tests/test_interface_contract_pages.py::_block_branches`, and it derives the
set of functions it walks from `_blocking` itself, so a helper added there is
counted rather than exempt by omission.

Eight of the 24 are the hard rules above. **A reader who takes that table for
the complete list of what refuses a bill is wrong about two thirds of it.** The
other sixteen:

| Family | How many | What it is |
|---|---|---|
| malformed or absent input | 13 | a field that is not the type it must be, or a fact nobody looked up — a verdict that is not a `Verdict`, a question count that is not a number, `carries_gst=None`, an `observation` that is not an `Observation`. Every one fails **closed** |
| both sides name one ledger | 1 | a typo, and no answer to any question makes it not one — `_account_blocks` |
| confidence under `ASK_FLOOR` | 1 | the band stated above: too unsure to be worth spending a question on |
| the wall's own refusal | 1 | `LedgerEntry.decided` raising `ValueError` — caught here and turned into a sentence rather than a traceback |

They are unnamed because there is nothing to name: each is a single branch whose
sentence is a module constant, and they are read in order in `_blocking`. That
they are unnamed is not that they are minor — it is the *"never raises"* rule
below doing its work. Malformed input becomes a refusal a person can read
instead of an outage.

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
