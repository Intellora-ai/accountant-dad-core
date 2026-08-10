# UI provenance — PR-4 of the owner's five

Branch `phase8/ui-provenance`, from `f22eace`. Measured 2026-08-10.

Every number here was produced by running the code, over real HTTP, against the
worktree this branch lives in. None was copied from a document.

**Evidence class: `SYNTHETIC_EVIDENCE`.** All twenty cases are lines written in
`tests/test_ui_provenance.py` against a fabricated company. Q5 permits synthetic
cases to test mechanics, schema, provenance and adversarial behaviour. They are
not real-bill accuracy evidence and are not described as any.

---

## 1. The requirement, and the result

The owner-approved assumption, `docs/OWNER_DECISIONS.md` §2, verbatim:

> provenance in UI = the existing draft screen displays detector/rule, source
> URL, evidence and explanation **per decision**

Measured off the rendered HTML by `tests/test_ui_provenance.py`:

```
20 decisions
20/20 show detector or rule
20/20 show source URL
20/20 show evidence
20/20 show explanation
```

**One qualification, and it is the important line in this document.**
`20/20 show source URL` means the slot is present and explicitly filled on every
decision. It does **not** mean twenty citations. **0 of 20** carry a URL, because
`accountant/rules/` — the corpus that would supply one — is PR-3 of the five and
is not merged. All twenty read:

```
NOT_AVAILABLE — accountant/rules/ not merged
```

That is the honest rendering. A blank cell would read as "this rule has no
source", which is a different and much worse claim, and a plausible-looking URL
would be an invented citation.

---

## 2. What is on the screen

The draft screen grew one block, below both existing tables and below the
question, inside the same decision card:

```html
<div data-provenance="decision" data-provenance-rule-kind="rule"
     data-provenance-slots="4">
<h2>Why this decision, and where it came from</h2>
<table>
<tr data-provenance-slot="detector_or_rule" data-slot-state="recorded">…</tr>
<tr data-provenance-slot="source_url"       data-slot-state="not_available">…</tr>
<tr data-provenance-slot="evidence"         data-slot-state="recorded">…</tr>
<tr data-provenance-slot="explanation"      data-slot-state="recorded">…</tr>
</table></div>
```

| slot | where the value comes from |
|---|---|
| `detector_or_rule` | `Decision.question_problem_id`, else every `Problem.id` on the draft, else `decision_order` — the rule in `accountant/decide.py` that decides an entry with nothing wrong with it |
| `source_url` | a `source_url` carried by the `Decision` or any `Flag`. Nothing carries one today |
| `evidence` | the check count, every failed check by name and detail, every fired detector by name and reason **including the ones the display cap hid**, and the memory/live-ledger disagreement if there is one |
| `explanation` | `Decision.reason` |

`data-provenance-rule-kind` is `detector` when the named driver is one of the
draft's own flags and `rule` otherwise. It is derived from the flags on the
draft rather than from a detector list, so a rename in `accountant/detect/`
cannot break a page.

### Three states, never a blank

| state | rendered | means |
|---|---|---|
| `recorded` | the value | the draft carries this source |
| `not_recorded` | `NOT_RECORDED` | the draft does not carry it |
| `not_available` | `NOT_AVAILABLE — accountant/rules/ not merged` | the half of the system that would supply it is not here |

A blank cell and a field with no source look identical on a screen, and telling
those two apart is the whole reason this block exists. So there is no fourth
state.

---

## 3. The twenty decisions, as measured

Twenty HTTP requests, two companies, sequential. Sixteen against the demo
company in `tests/test_web.py`; four against the stale-ledger company in
`tests/test_first_detector.py`, which is the only route from the screen to
`vendor_switch` and therefore the only way to render a decision whose driver is
a detector rather than a rule.

| # | case | outcome | kind | detector or rule | source URL | evidence | explanation |
|--:|---|---|---|---|---|---|---|
| 1 | `valid_sharma` | valid | rule | `decision_order` | not_available | recorded | recorded |
| 2 | `valid_sharma_again` | valid | rule | `decision_order` | not_available | recorded | recorded |
| 3 | `valid_kumar` | valid | rule | `decision_order` | not_available | recorded | recorded |
| 4 | `valid_city_power` | valid | rule | `decision_order` | not_available | recorded | recorded |
| 5 | `valid_landlord` | valid | rule | `decision_order` | not_available | recorded | recorded |
| 6 | `unseen_gupta_asks` | unclear | rule | `which_account` | not_available | recorded | recorded |
| 7 | `unseen_mehta_asks` | unclear | rule | `which_account` | not_available | recorded | recorded |
| 8 | `unseen_bansal_asks` | unclear | rule | `which_account` | not_available | recorded | recorded |
| 9 | `conflicted_verma_asks` | unclear | rule | `which_account` | not_available | recorded | recorded |
| 10 | `gst_refused_sharma` | not_valid | rule | `tax_lines_can_be_posted` | not_available | recorded | recorded |
| 11 | `gst_refused_kumar` | not_valid | rule | `tax_lines_can_be_posted` | not_available | recorded | recorded |
| 12 | `unreadable_text` | unclear | rule | `amount_is_positive` | not_available | recorded | recorded |
| 13 | `no_party_named` | unclear | rule | `party_is_named` | not_available | recorded | recorded |
| 14 | `very_large_amount` | valid | rule | `decision_order` | not_available | recorded | recorded |
| 15 | `funding_question` | unclear | rule | `funding_is_named` | not_available | recorded | recorded |
| 16 | `posted_after_two_answers` | valid | rule | `decision_order` | not_available | recorded | recorded |
| 17 | `chart_gap_asks` | unclear | rule | `accounts_exist` | not_available | recorded | recorded |
| 18 | `detector_vendor_switch` | unclear | **detector** | `vendor_switch` | not_available | recorded | recorded |
| 19 | `detector_dismissed` | unclear | **detector** | `vendor_switch` | not_available | recorded | recorded |
| 20 | `chart_gap_asks_again` | unclear | rule | `which_account` | not_available | recorded | recorded |

Coverage of the shapes: 6 valid, 12 unclear, 2 not_valid · 8 distinct drivers ·
2 detector-driven · 3 routes (`/entry`, `/answer`, `/dismiss`).

Twenty identical VALID entries would have reported 20/20 on all four slots and
proved nothing about a refusal, a question or a detector, which is why the
census is a spread rather than a repetition.

---

## 4. What was assumed about `accountant/rules/`

It does not exist on `origin/main`. It could not be imported and its contract
could not be read, only designed against. The assumption is deliberately narrow:

> **A rule's official source URL arrives on the object that already reaches this
> screen — the `Decision` or the `Flag` — as an attribute named `source_url`.**

`app.rule_source_url` reads exactly that, in that order, and falls through to the
`NOT_AVAILABLE` marker when nothing carries one. That fall-through is every
decision today.

The seam is exercised rather than promised:
`test_the_source_url_slot_renders_a_carried_url_when_one_finally_exists` builds a
`Decision` subclass carrying `source_url` and asserts the slot renders it, and
`test_every_provenance_value_goes_through_the_escaper` asserts it is escaped —
because a URL fetched from outside this repository is untrusted, and the moment
the corpus lands is the worst moment to find out the cell was never escaped.

If the corpus lands with a different carrier, one function changes and nothing
else on this screen does.

---

## 5. The guards, proved load-bearing

Each mutant injected into `accountant/web/app.py`, the suite run, the mutant
reverted. Tests run:
`test_ui_provenance` · `test_web` · `test_first_detector` · `test_questions` ·
`test_flag_cap` · `test_stale_memory_conflict` — 176 passed clean.

| mutant | result | first test to catch it |
|---|---|---|
| the source URL slot renders empty instead of an explicit marker | RED | `test_no_provenance_cell_is_ever_empty` |
| the evidence slot is dropped from the template entirely | RED | `test_all_twenty_decisions_show_the_slot[evidence]` — 9 tests fail |
| the explanation is rendered for some decisions but not others | RED | `test_all_twenty_decisions_show_the_slot[explanation]` — 6 tests fail |
| a provenance value is rendered unescaped | RED | `test_a_hostile_detector_reason_reaches_the_page_escaped` |
| an account name leaks into a question string | RED | `test_provenance_did_not_put_a_ledger_account_name_inside_a_question` |
| the detector/rule name is shown but its evidence is not | RED | `test_every_decision_shows_evidence_and_shows_it_recorded` |

The middle two are the ones that matter. Both are **missing slots**, not
malformed ones — the mutant deletes the row rather than emptying it, which is
the `dropped_flags` failure repeating, and both are caught by more than one
independent test.

The reason a missing slot is detectable at all is that
`tests/test_ui_provenance.py` carries **its own literal copy** of the four slot
names. A test that looped over `app.PROVENANCE_SLOTS` would compare the page to
itself and pass just as happily with three entries in the tuple.

---

## 6. The two traps this was written against

**A blank slot is indistinguishable from a field with no source.** Handled by
having no blank state at all, and by
`test_no_provenance_cell_is_ever_empty`, which treats whitespace as blank.
`test_a_decision_with_no_evidence_says_NOT_RECORDED_rather_than_nothing` pins
the marker directly, on a draft constructed to have no evidence — that state
cannot be reached over HTTP, because `pipeline.evaluate` always runs eight
checks, and "unreachable" is exactly the reasoning that let `dropped_flags` sit
unrendered for a whole phase.

**Provenance text must not bleed into a question.** No question string may
contain any account name from the company's chart of accounts; the chosen
account is shown after answering, never inside the question. This block
legitimately carries account names — a `vendor_switch` reason naming the ledger
a vendor usually goes to *is* the evidence — so the block is rendered in the
audit region at the bottom of the card, after both tables and well clear of
`<p class=ask>`. Two tests, in both directions:

- `test_provenance_did_not_put_a_ledger_account_name_inside_a_question` — no
  chart name inside the question region, across all twenty decisions
- `test_the_provenance_block_is_not_rendered_inside_the_question` — position,
  not content, so a block placed inside the question cannot pass merely by
  happening to name no account

The pre-existing account-name and jargon tests were re-run unchanged and stay
green: `tests/test_web.py`, `tests/test_first_detector.py`,
`tests/test_questions.py`, `tests/test_stale_memory_conflict.py`.

---

## 7. What did not change

| claim | measured |
|---|---|
| no web framework, no template engine, no JS, no CSS framework | `dependencies = []` unchanged; stdlib `http.server` unchanged |
| `accountant/web/app.py` names no concrete extraction backend | AST scan, derived from `accountant/extract/`: `{}` |
| the `Runtime.extractor` seam and `registry.default_extractor()` | untouched |
| `FLAG_CAP = 3` and the overflow line | untouched; suppressed flags now also appear in the evidence record, which is what the owner's "never lose concerns from the audit/evidence record" asks for |
| the decision order, the checks, the detectors, the write path | untouched — this PR renders, it does not decide |

Files changed: `accountant/web/app.py`, `tests/test_ui_provenance.py`,
`artifacts/phase8_ui_provenance.md`. Nothing else.

---

## 8. What this does not prove

- **That any rule has a real citation.** 0 of 20 decisions carry a source URL.
  That claim belongs to PR-3 and the slot says so rather than pretending.
- **Anything about a real TallyPrime.** The backend is `FakeTally` injected
  through `app.configure()`, over real HTTP on an ephemeral port.
- **That real bills produce these decisions.** Extraction is stub-class work
  under Q4; real extraction accuracy stays `NOT_MEASURED`.
- **That twenty is the right number of decisions.** It is the number the owner
  named. The census covers eight distinct drivers and all three outcomes, which
  is the property that makes twenty worth counting.

---

## 9. Reproducing it

```
COVERAGE_CORE=pytrace .venv/bin/python -m pytest -q -p no:cacheprovider \
    tests/test_ui_provenance.py
```

Full suite on this branch: **2321 passed, 5 xfailed**.
Baseline on `origin/main` at `f22eace`: **2295 passed, 5 xfailed**. Net +26, all
of them in `tests/test_ui_provenance.py`.

Branch coverage of `accountant/web/app.py`: 97%, with no uncovered line inside
anything this PR added.

Every measurement was taken with the provenance assertion in force:

```python
from pathlib import Path
import accountant

assert str(Path(accountant.__file__).resolve()).startswith(str(Path.cwd().resolve()))
```
