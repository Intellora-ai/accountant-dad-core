# Bug log

Rule 11.3.13: every bug gets a fix, a regression test that would have caught it,
and one line here. Defects found in *existing* code and deliberately left are in
`PROJECT_STATE.md` §49.4 — this file is for bugs in work done here, and for the
one live defect the cage work fixed.

| # | Bug | Caught by | Fix | Test |
|---|---|---|---|---|
| 1 | The AST guard asserting only `wall.py` constructs a `LedgerEntry` was asserting over an **empty set** — `decided()` used `cls(...)`, invisible to a scan looking for `LedgerEntry(`. It would have kept passing after the wall was deleted. | a control test whose only job was to prove the scanner could find any construction | construct by name inside the class; add a second assertion that the set is **exactly** `{wall.py}`, not merely a subset | `test_the_control_the_scanner_can_actually_find_a_construction`, `test_the_ledger_entry_guard_is_not_asserting_over_nothing` |
| 2 | A `PASS` conservation verdict returned an empty sentence, so an audit row could not answer *passed on what numbers* months later when a figure looks wrong. | `test_every_result_carries_a_non_empty_sentence` | `_compare` now returns a sentence on PASS too. **The code changed, not the test** (rule 8.1.5). | same test |
| 3 | Two of my own classifier tests asserted a truncated `%PD` header classifies as `UNSUPPORTED`. Three printable ASCII bytes **are** plain text — "not a PDF" was right, "therefore unsupported" did not follow. | running them | corrected to assert the true claim (`is not FileKind.PDF`), plus a new test using a **binary** prefix where the answer really is a refusal | `test_a_truncated_pdf_header_is_not_treated_as_a_pdf`, `test_a_binary_prefix_of_a_readable_type_is_not_that_type` |
| 4 | `rupees` formatted INR with **western** grouping — ₹10 lakh rendered `1,000,000.00` where Indian convention is `10,00,000.00`. A correctness defect for every Indian user, not a cosmetic one. | flagged during coverage work; ruled on by the owner 2026-08-13 | one `format_inr`, Indian grouping, used at every human-facing site | see the INR tests |

## Not a bug, recorded because it cost time

`.coverage` is gitignored and outlives the code it measured. A partial run leaves
a complete-looking artefact that `coverage report` prints as a confident
percentage. It reported `app.py` at 28% when the file was at 100%. Delete
`.coverage*` before measuring. Full note in `docs/learnings/`.
