# `accountant.cage.conservation`

**Main job.** Check quantities that must be equal. Nothing else.

## Inputs

| Field | Type | Required | Constraint |
|---|---|---|---|
| `debit_paise` / `credit_paise` | `int \| None` | yes | whole paise; `None` means not read |
| `line_paise` | `tuple[int, ...] \| None` | yes | `None` = not read; `()` = read, there were none |
| `total_paise`, `net_paise`, `tax_paise`, `gross_paise` | `int \| None` | yes | whole paise |
| `balance_before_paise`, `balance_after_paise` | `int \| None` | yes | whole paise |

A `float` or a `bool` **raises**. `bool` is an `int` in Python and `True == 1`, so a
flag passed where an amount belonged would otherwise balance a one-paisa entry.

## Outputs

`tuple[ConservationResult, ...]` — **exactly four, always, in `LAWS` order**.

| Invariant | |
|---|---|
| length | always 4, never "the ones that applied" |
| order | fixed; a caller may index it and a log line reads the same every run |
| `said` | never empty, on any verdict — an audit row saying only "pass" cannot answer *passed on what numbers* |

`Verdict` is `PASS` / `FAIL` / **`INDETERMINATE`**. The third is the load-bearing one:
the law could not be evaluated because a number it needs was never read. **The caller
blocks on it.**

## Does NOT

Call the network · read Tally · look at confidence · ever pass a law it could not
compute · stop at the first failure.

## Targets

| | |
|---|---|
| Correctness | 100% of proposals see all four laws; a **1-paisa** mismatch is FAIL |
| Latency | measured and reported; **threshold is owner-set, not set here** |
| Throughput | not a constraint — pure arithmetic, no I/O |

## Failure modes

| Trigger | Behaviour | Logging |
|---|---|---|
| a field is `None` | `INDETERMINATE`, which **blocks** | law name + which figure was missing |
| line items absent | that law `INDETERMINATE`, the other three still evaluated | same |
| a non-`int` amount | **raises** rather than coercing | at the call site |

## Dependencies

**None.** Stdlib only. Pure function.

## Observability

Counter per law per verdict. **No side effects at all** — the only module in the
system with none.

## Known limit, stated so nobody relies on it

Cannot detect a bill misread *consistently* — every figure scaled by ten, so the
lines still sum and net still plus tax to gross. That is failure mode **F-02**.
Arithmetic cannot see it. This is one guard among several, never the only one.
