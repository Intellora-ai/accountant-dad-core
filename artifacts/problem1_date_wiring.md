# Step 2 — wiring `dates.py` into the live reader

Branch `cage/safety-layer`, from `cfcdf1d`. Written 2026-08-15.

## The one line that was wrong

`accountant/extract/freeocr.py:815`, `_read_date`:

```python
if not looks_like_a_date(text):
    return None, (
        f"{text!r} is not a real date written the way this system "
        "reads dates, which is year-month-day"
    )
return datetime.date.fromisoformat(text), ""
```

`looks_like_a_date` is `date.fromisoformat` — **ISO and nothing else**. Every
other written form was refused, and the refusal sentence said the date was not
year-month-day. That was true and useless: it was equally true of a date the
reader could have read.

`accountant/extract/dates.py` — 12 written forms, 58 tests, green — existed and
was imported by **`tests/test_dates.py` and nothing else**. It was built and
never connected.

MEASURED on a real corpus document: the page prints `16-11-2023`, the engine
read those characters at confidence 95 with the label matched, and the reader
answered "not a real date". 55 of 62 documents lost their date this way.

## What it says now

```python
reading = read_date(text, locale=DateLocale.UNKNOWN)
if reading.value is None:
    return None, reading.why
return reading.value, ""
```

## The locale is `UNKNOWN`, and that is the whole safety argument

`DateLocale.INDIAN` was tried first. It reads `11/08/2026` as the 11th of
August by convention. `UNKNOWN` refuses it, because 11/08 is a real day under
**both** orders and choosing one is inventing evidence rather than reading it.

Measured both ways on the 62-document ground-truth corpus:

| locale | dates correct | resting on the convention |
|---|---|---|
| `INDIAN` | 33 | 11 |
| `UNKNOWN` | **24** | **0** |

The 11 are not lost. They are **refused** — a question for a person instead of
a value nobody checked. A miss costs a question; a silently chosen date is a
wrong number in the books.

`INDIAN` also required rewriting `test_a_date_in_a_form_this_system_does_not_
read_is_refused_not_guessed`, whose docstring says *"Picking one would be
inventing the evidence."* A test that says that is not a stale fixture, and
making it pass by widening the reader would be the change marking its own
homework.

If a locale is ever wanted it belongs where the company is known, passed from
the tenant's settings — not hard-coded at the one place in the codebase that
cannot see whose bill it is holding.

## Every date read is settled by arithmetic, not by convention

Of the 24 dates now read correctly:

| how the order was settled | count |
|---|---|
| one number is over 12, so it cannot be a month (`16-11-2023`, `23-10-2014`) | 23 |
| day and month are the same number, so both orders give the same day (`07-07-2026`) | 1 |
| **assumed from a convention** | **0** |

The corpus is not all Indian. `real-voxel51-05` prints `11/26/2018`, a US
month-first bill; 26 is not a month, so arithmetic settles it and the reader
read 26 November 2018 correctly with nothing assumed.

## Result

| | before | after |
|---|---|---|
| correct, all 5 fields | 50 | **74** |
| incorrect | 0 | **0** |
| false positive | 0 | **0** |
| correctly unresolved | 34 | 34 |
| missed | 226 | 202 |

| field | before | after |
|---|---|---|
| `invoice_date` | 0 correct, 55 missed | **24 correct, 31 missed** |
| `total` | 30 | 30 |
| `tax` | 20 | 20 |
| `party` | 0 | 0 |
| `invoice_number` | 0 | 0 |

## Tests proving the LIVE path

Not `dates.py` unit tests — the spec forbids marking this complete from those.

- `tests/test_freeocr.py` (153 with `test_dates.py`, all green) exercises
  `_read_date`, the live function, through `FreeReader.extract`.
- `test_a_date_in_a_form_this_system_does_not_read_is_refused_not_guessed`
  still asserts `record.date is None` for `11/08/2026`. Only the reason
  assertion moved, from the old ISO wording to the two things the new refusal
  must contain — **both readings named**, so the question shows a person the
  actual choice instead of asking them to re-derive it.
- The corpus measurement above runs `read_page` + `_scored`, the real upload
  path, over 62 documents.

## A comparator bug found and fixed, the third of its kind

The first measurement after wiring reported **33 dates INCORRECT**. All 33 were
exact matches:

```
expected '23-10-2014', read datetime.date(2014, 10, 23)
```

The reader returns a `datetime.date`; the ground truth stores what the page
printed. `_same_date` compared the two as strings. This is the third time a
broken comparator has claimed the reader invented values — money was the first
(50 slots), and it is the more dangerous direction: it argues for "fixing" a
reader that was already right. Checked before reporting, both times.

`_same_date` now parses both sides to `datetime.date`, tries day-first before
month-first so an ambiguous date can never be silently reinterpreted, and
carries the whole account in its docstring.

## Effect on the suite: net zero, measured by A/B not by assumption

Both arms run in full, HEAD's reader against the new reader, same tests:

```
HEAD reader     174 failed
new reader      174 failed
```

Exactly one test newly failed and was rewritten:
`tests/test_pagereader.py::test_a_date_that_is_not_iso_is_found_and_then_
refused_as_a_value`. Its stated reason was *"`freeocr._read_date` is
`date.fromisoformat` and nothing else"* — an accurate description of the code
and the exact rule the owner's requirement overrides. `13/05/2026` has a 13, so
arithmetic settles it and it now reads.

It was replaced by **two** tests, not one, so the widening cannot quietly take
the safety half with it:

- `test_a_non_iso_date_reads_when_arithmetic_settles_its_order` — `13/05/2026`
  reads as 13 May 2026, at confidence **0.5**, because this fake page prints no
  DATE label and the positional fallback found it. Below `ASK_FLOOR`. It cannot
  post and cannot spend a question.
- `test_a_date_both_orders_could_read_is_still_refused_not_picked` —
  `11/08/2026` is still refused. This one fails the moment someone passes a
  locale into `_read_date` to lift the corpus number.

A first draft of the reading test asserted the raw characters appear in the
evidence. They do not, on that path: a REFUSAL quotes the characters because
the reason is about them, while a READ names how the field was found. Corrected
against the actual output rather than argued.

## Not fixed here, and why

`tests/test_no_reader.py::test_no_module_outside_the_allow_list_names_the_work_
of_reading` fails on `nearby.py` (`CLEARLY_CLOSER_PIXELS`, `max_pixel_distance`,
`pixel_distance`). `nearby.py` is untouched by this change and the failure is
pre-existing — one of the known 173. It is a real architecture violation and
belongs to Step 6's classification, not to a date change.
