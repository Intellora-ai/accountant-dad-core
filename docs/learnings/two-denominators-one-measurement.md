# The same extraction result has two correct denominators

- The text-layer reader's accuracy on the 20 corpus PDFs is quoted two ways, and
  **both are right**:

  ```
  four fields   date 14 + party 20 + total 20 + tax 20 = 74 of 80   (4 x 20)
  five fields   the same, plus line_paise 18 of 20     = 92 of 100  (5 x 20)
  ```

- The harness scores **four** fields, so it reports 74/80 against its required
  76. Module docstrings describing what the reader reads quote **92/100**,
  because `line_paise` is a field the reader genuinely produces.
- **2026-08-13:** an agent read `92/100` in three docstrings, compared it to the
  harness's 74/80, and reported the docstrings as wrong. They were not. It had
  the sense to leave them alone rather than "fix" them, which is the only reason
  a correct number survived.

**Applies:** any figure of the form *n of m* in this repo. Before correcting one,
find what `m` is counting. A mismatch between two quoted numbers is more often
two denominators than one error.

**Does not apply:** where the denominator is stated and still disagrees. That is
a real defect.

**The general form:** a ratio without its denominator named is not a
measurement, it is a rumour. When quoting one, say what it is out of and over
how many documents.
