# `accountant.cage.confidence`

**Main job.** Turn per-word OCR scores into one per-field number.

## Why it is computed rather than received

A paid invoice model hands you a per-field score. A free OCR engine does not —
Tesseract reports a confidence per **word**, 0–100. The bridge is deterministic
arithmetic, not a learned weight: a learned weight is one more thing that can be
quietly wrong, needs its own training data, and cannot be read by a person deciding
whether to trust a refusal.

## The formula

```
field_confidence = min(word_conf)/100  ×  format_valid  ×  consistency
```

| Rule | Why that shape |
|---|---|
| **`min`, not mean** | one misread digit ruins an amount. A mean of 0.99 and 0.40 is 0.70 — reads as *worth asking about* rather than *certainly wrong* |
| **format validity is a HARD multiplier** | a date that will not parse scores exactly `0.0`, not a reduced score. Tesseract can be certain it read `12/34/5678`; that certainty is about pixels, not about whether the thing is a date |
| **consistency is hard too** | if net + tax ≠ gross, both amounts go to `0.0`. Two numbers that contradict each other are not two-thirds right |

`EXACT = 1.0` — a text-layer read was **read, not guessed**. No pixel, no estimate,
nothing to be unsure about.

## Inputs

`word_confidences: tuple[int, ...] | None` — Tesseract's 0–100. `None` or `()` means
nothing was read, which scores `0.0` — not a low score, **no** score.

**`-1` raises.** Tesseract uses it for "no text here" — a marker, not a score. Reading
it as one would let a blank region score as a confident negative.

## Targets

| | |
|---|---|
| Determinism | same input, same output — no clock, no cache, no randomness |
| Latency | measured; **threshold owner-set** |
| Calibration | **measured on the corpus, never assumed** |

## Known limit

Cannot detect a **confidently** misread value — Tesseract reporting 96 on a digit it
got wrong. That is **F-02**, and it is exactly why confidence alone never authorises a
post: the decision layer also requires every conservation law to pass, the party to be
known, and the period to be open.

## The threshold rule

If the corpus run says `0.95` is the wrong band, that is **reported to the owner**.
Thresholds are never retuned here to make a metric pass — `ARCHITECTURE.md:616`
forbids it, because moving a threshold moves the measurement, not the product.

## Dependencies

**None.** Stdlib only.
