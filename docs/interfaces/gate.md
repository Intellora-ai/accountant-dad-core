# `Gate` — `accountant/cage/gate.py`

**One job.** Turn a `Draft` plus the world-facts its caller actually knows into a
`Situation`, ask `decision.decide`, and hand back the answer.

It weighs nothing. It holds no threshold. It is the adapter, and the reason it is
a module rather than a few lines inline is that a few lines inline is exactly
where a defaulted fact gets written.

## Inputs

A `Draft`, plus keyword arguments:

| Argument | Default | Note |
|---|---|---|
| `party_known` | **none** | keyword-only, no default |
| `period_open` | **none** | keyword-only, no default |
| `carries_gst` | **none** | keyword-only, no default |
| `net_paise` | `None` | safe to default — see below |
| `balance_before_paise` / `balance_after_paise` | `None` | same |

The asymmetry is deliberate and is the whole point of the file. **Forgetting a
`None`-defaulted number fails closed** — unread becomes `INDETERMINATE` becomes a
block. Forgetting a defaulted *world fact* would fail **open**, because
`period_open=True` reads as "somebody checked". So the three that could fail open
have no defaults at all, and a caller who forgets gets a `TypeError`.

`Draft` is imported under `TYPE_CHECKING` only, so the cage never depends on the
pipeline at run time.

## Outputs

One `Decided`, straight from `decision.decide`. The gate adds nothing to it and
takes nothing away.

## What it does when building the `Observation`

- A field the record read gets `confidence.EXACT`. Text-layer reads are read, not
  guessed — the constant is used, never a literal `1.0`.
- A field that is absent gets `value=None, confidence=0.0` carrying the record's
  own stated reason. A blank reason is replaced with a stated one rather than
  becoming a `ValueError` on a person's screen.
- Money that is not whole paise — a float, a `bool` — is left **unread**. Not
  coerced, not raised on. `checks.amount_is_integer_paise` exists because that
  value really arrives.
- Unread line items are passed as `None`, never `()`. Those mean different things
  to `conservation.run` and the difference is the point.
- `net_paise` is **never** computed as total − tax. A law checking a number
  against its own inputs is a law that always passes.

## Does NOT

Decide anything. Derive a world fact. Touch Tally, the network, or the
filesystem. Persist. Move a threshold.

## Guarded by

An AST scan asserting the set of modules calling `cage.decision.decide` is
**exactly** `{accountant/cage/gate.py}` — not a subset. Three controls: that the
scanner finds a real call under both import forms, that it is **not** fooled by
`accountant/decide.py`'s own unrelated `decide` (a bare-name matcher would report
the pipeline as a cage caller), and that it walked a directory that exists.

The controls are not decoration. This build has already shipped one AST guard
that asserted over an **empty set** and passed — and would have kept passing
after the thing it guarded was deleted.

## Not on the live write path, and this is measured

`accountant/pipeline.py` does **not** import this module, and a test asserts it
does not, with the reason in its docstring.

Wiring it there was tried. A one-line experiment against a 3,820-pass green
baseline produced **50 failures**. Three of the four conservation laws have no
inputs before a write — nothing fills `line_items`, no reader produces a net
amount, and there is no after-balance yet — and on the typed-text path `date` is
always `not_found`, so `lowest_confidence` is 0.0 before conservation even runs.
It was not a strict gate. It was an off switch.

`ARCHITECTURE.md` §19.4 settles it: an advisory layer is **added** to a
deterministic one and never subtracts. The path it would have replaced is already
covered by the eight checks in `checks.py`, the write-ahead row, and a write door
that already refuses an out-of-financial-year date in plain words.

The gate moves onto that path when a reader produces a real `Observation` —
Steps 13 and 14 — and when `period_open` acquires a source. Both are recorded in
`docs/OWNER_WORK.md`.

## Depends on

`decision`, `conservation`, `wall`, `confidence`. Four, against a limit of five.
No cycles.
