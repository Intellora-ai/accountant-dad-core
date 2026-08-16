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

**May talk to: `money`, and nothing else.** Stdlib otherwise. Pure function.

This row read **None** until 2026-08-13. The exception is safe because
`accountant/money.py` is a pure `int -> str` renderer whose only import is
`__future__`, so importing it costs none of what the rule protects — this module
still evaluates with no fixtures, no network, no filesystem and no Tally, and
returns the same verdict on a machine that has never seen an invoice. It buys
the one thing arithmetic in paise cannot give a person: a refusal that reads
*"₹1,199.99 against a stated total of ₹1,200.00, out by 1 paisa"* instead of
*"119999 paise against 120000 paise"*, which the owner's closed INR-grouping
rule requires of every amount a user sees.

**What would make it unsafe:** `money` acquiring any dependency of its own — a
locale, a config file, a store — because that dependency then arrives here
behind an import nobody re-reads.
`tests/test_conservation.py::test_the_control_money_itself_still_depends_on_nothing`
fails on that day, and an allow-list test pins this module's import list to
`money` alone. An undocumented exception is how a layered design becomes a mesh.

## Observability

Counter per law per verdict. **No side effects at all** — the only module in the
system with none.

## Known limit, stated so nobody relies on it

Cannot detect a bill misread *consistently* — every figure scaled by ten, so the
lines still sum and net still plus tax to gross. That is failure mode **F-02**.
Arithmetic cannot see it. This is one guard among several, never the only one.
