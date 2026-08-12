# Integration log

Rule 11.4: one entry per join, ≤10 bullets, findable by step name (`integrate-C`).
Rule 11.2: **no big-bang integrations.** Each part passes its own tests alone
first, then joins one at a time, and every earlier group re-runs at every join.

The parts, in join order:

| Letter | Part | Landed |
|---|---|---|
| A | `cage/classify.py` | 2026-08-13 |
| B | `cage/conservation.py` | 2026-08-12 |
| C | `cage/wall.py` — `Observation` + `LedgerEntry` | 2026-08-12 |
| D | `cage/confidence.py` | 2026-08-13 |
| E | `cage/lying.py` — the lying model | in flight |
| F | fake reader | in flight |
| G | `cage/state.py` — the state machine | in flight |
| H | `cage/decision.py` — the human gate | in flight |
| I | `extract/textlayer.py` — `pypdf` | in flight |
| J | `extract/freeocr.py` — Tesseract | in flight |

---

## integrate-B — conservation alone

- **Added:** `accountant/cage/conservation.py`, four laws, pure, zero imports
  beyond the standard library.
- **Before:** 3,376 tests green.
- **After:** 3,407 green. 31 new, all micro, all under 100 ms.
- **Mutation:** 4 mutants, each turning one law into always-`PASS`. 4 killed.
- **Issue:** a `PASS` verdict returned an empty sentence, so an audit row could
  not say *passed on what numbers*. A test caught it before the join.
- **Resolution:** the code changed, not the test (rule 8.1.5).
- **Decision:** proceed.

## integrate-C — the wall joins conservation

- **Added:** `accountant/cage/wall.py`. `Observation` is what we think;
  `LedgerEntry` is what we will write; only `accountant.cage.decision` may
  build the second.
- **Before:** 3,407 green. **After:** 3,430 green.
- **Mutation:** 5 mutants across the caller check, the `bool` rejection, the
  confidence bounds and `lowest_confidence`. 5 killed.
- **Issue — the important one.** The AST guard proving only `wall.py`
  constructs a `LedgerEntry` was asserting over an **empty set**: `decided()`
  used `cls(...)`, which a scan for `LedgerEntry(` cannot see. It passed, and
  would have kept passing after the wall was deleted.
- **Detected by:** a control test whose only job was to prove the scanner could
  find any construction at all. It could not.
- **Resolution:** construct by name inside the class, and add a second
  assertion that the found set is **exactly** `{wall.py}` rather than merely a
  subset of it. Both tests now fail if either half regresses.
- **Decision:** proceed. This is failure mode F-16 — a guard that exists but is
  not installed — caught in the cage's own guard, which is the strongest
  available evidence that the paired-guard rule is worth its cost.

## integrate-D — confidence joins the wall

- **Added:** `accountant/cage/confidence.py`. `min(word_conf)/100 ×
  format_valid × consistent`.
- **Before:** 3,430 green. **After:** 3,454 green.
- **Mutation:** 5 mutants — `min`→`max`, `min`→mean, dropping each hard
  multiplier, and widening the range check to admit Tesseract's `-1`. 5 killed.
- **No issues.** The module is pure arithmetic over integers and has no I/O to
  go wrong.
- **Note:** joins `Observation` only by *shape*. Nothing imports the other; the
  reader is what will carry a score from one to the other, and it does not exist
  yet. So this join proves the arithmetic, not the wiring — stated plainly
  rather than counted as more than it is.
- **Decision:** proceed.

## integrate-A — the classifier joins everything so far

- **Added:** `accountant/cage/classify.py`. Magic bytes over declared MIME,
  always. 3 readable signatures, 12 named-but-unreadable, plus HEIC/WebP/AVIF
  whose marker sits at offset 4.
- **Before:** 3,454 green. **After:** 3,481 green.
- **Mutation:** 4 mutants — trusting the declared MIME over the bytes, dropping
  the NUL check, dropping the UTF-8 check, and returning a readable kind on a
  prefix match. 4 killed.
- **Issue:** two of my own tests were wrong. They asserted a truncated `%PD`
  header classifies as `UNSUPPORTED`. Three printable ASCII bytes **are** plain
  text — "not a PDF" was right, "therefore unsupported" did not follow.
- **Resolution:** corrected to the claim that actually matters (a prefix is
  never the real thing), plus a new test using a *binary* prefix where the
  answer really is a refusal. The docstring records that the test was wrong,
  because a silently corrected test teaches nobody anything.
- **Note:** `classify` deliberately has **no `filename` parameter**. An
  extension decides nothing here, and a parameter that is accepted and ignored
  invites a caller to believe it matters. Deleted before it acquired a caller
  (rule 11.3.6).
- **Decision:** proceed.

---

## What no join has proven yet

Stated here rather than left to be inferred from a green suite:

- **Nothing has read a real file.** A, B, C and D are joined by shape and by
  the suite passing, not by a byte of a real bill moving through them. That
  happens at integrate-I.
- **`decision.py` does not exist**, so the wall's guard currently proves that
  *nobody at all* builds a `LedgerEntry` outside `wall.py`. That is the correct
  state for now and becomes the real claim at integrate-H.
- **`s2_extraction` is still 0/76.** It moves when a reader lands, and not
  before. Named Limitation 4 — an exit criterion, not pre-work.
