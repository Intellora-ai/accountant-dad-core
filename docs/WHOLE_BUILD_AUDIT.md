# What six agents cannot see from inside one file

**Written by:** the agent holding the whole build, 2026-08-15.
**Verified against:** a clean worktree at `0cf2411`, `git worktree add … 0cf2411`.
**Nothing in this file changed any source.** Every number below has the command
that produced it.

Six agents are each deep in one file. Each one is right about its own file.
This document is about what happens **between** them, and about one thing
nobody is looking at.

---

## The short version

1. The cage refuses **100 out of 100** real PDFs, under the kindest world facts
   it is possible to hand it honestly.
2. The **18** of those 100 that the cage can fully check are the **18** it
   accuses of being arithmetically wrong. All 18 are correct. The mistake is
   ours.
3. That mistake is one line pairing two numbers that do not go together, and
   **no current agent owns that line**.
4. Agent B's change, landed on its own, makes the text-layer reader **raise on
   every real PDF**. It needs a matching change in agent D's file.
5. The two guards I was asked to check — the 5-question cap and the jargon
   guard — **both still hold**. No action needed on either.

---

# 1 · The interaction

## 1.1 What each agent is doing, and what it does to the others

| Agent | File | What it changes | What it does to somebody else |
|---|---|---|---|
| A | `pipeline.py`, `web/app.py` | wires the cage into `evaluate` | makes every refusal below **visible to a real person** |
| B | `extract/adapter.py` | `net_paise` joins `ExtractedRecord.FIELDS` | **breaks agent D's reader** until D states a source |
| C | `extract/pagereader.py` | positional guess at confidence 0.5 | feeds a **guessed number** into arithmetic that then blames the bill |
| D | `extract/textlayer.py` | strip a 3-byte BOM before `%PDF-` | more documents reach 1–3 above |

## 1.2 Agent B breaks agent D's file. Measured.

`ExtractedRecord.__post_init__` (`accountant/extract/adapter.py:213-218`) raises
if any name in `FIELDS` has no stated source. `FIELDS` is at
`accountant/extract/adapter.py:211`.

The text-layer reader **already reads a net figure** — it filled `net_paise` on
21 of 100 PDFs — but it **never states a source for it**. So the moment
`net_paise` joins `FIELDS`, every text-layer read raises.

```
$ PYTHONPATH=. .venv/bin/python -c "
  ExtractedRecord.FIELDS = (...,'net_paise'); TextLayerReader().extract(GT-0021.pdf)"

textlayer post-B RAISES: ValueError: incomplete record: no source stated for net_paise
```

This is not a merge conflict. Both files are individually correct. The
requirement lives in B's file and the obligation lives in D's, and neither
agent can see the other.

**What has to be true:** every construction site that B fixes must include the
readers — `textlayer.py`, `freeocr.py`, `ladder.py`, `placeholder.py` — not
only the test fixtures.

## 1.3 A field in `FIELDS` also silently removes auto-post

`gate._tiers` (`accountant/cage/gate.py:224-245`) walks **`FIELDS`** and
collects the source of every one, read or not. An unread field arrives as
`not_found: …`, which is on no allowlist, so `_may_auto_post`
(`accountant/cage/decision.py:1069`) returns False and
`_TIER_NOT_CLEARED_TO_POST` caps the bill at ASK
(`accountant/cage/decision.py:1187`).

Measured, with `FIELDS` patched in-process to include `net_paise`:

| bill | tiers reported | outcome |
|---|---|---|
| perfect text-layer bill, **no printed pre-tax figure** | `('pdf_text_layer', 'not_found: …')` | **block** |
| same bill where the pre-tax figure **is** printed | `('pdf_text_layer',)` | **post** |

So B's change is not neutral for bills that simply do not print a subtotal. It
adds a second reason to refuse them. That is the correct direction for safety
and it is worth **stating out loud**, because B cannot see it from inside
`adapter.py`.

## 1.4 Agent C's 0.5 guess makes us accuse the customer

A positional guess scored 0.5 is below `ASK_FLOOR` (0.70), so it blocks. That
part is right and safe.

But the guessed total is a **real integer**, so the conservation laws now run on
it. If the guess is wrong, a law FAILS, and a FAIL is a hard block with the
owner's own sentence — which blames the document.

Measured on one document carrying all four agents' changes at once:

```
action = block   (4 sentences)
[1] There is something on this bill I could not check at all…
[2] The numbers in this bill do not add up. Please check the original and
    upload a correct version. That is the ₹12,000.00 bill from Sharma Traders.
[3] The line items on this bill do not add up to its total: they come to
    ₹1,200.00 against a stated total of ₹12,000.00, out by ₹10,800.00.
[4] I am less than 70 out of 100 sure about what this bill says…
```

Sentence 2 says **your bill is wrong**. Sentence 4 says **I could not read your
bill**. Both are on the same screen, and only sentence 4 is true. The person
acts on sentence 2, because it is first and it tells them what to do.

**This is the contradiction the question asked about.** It is not a stack of
five unrelated refusals — the sentences are individually well written and the
order is fixed and deliberate. The defect is narrower and worse: **a sentence
that blames the customer sits above a sentence that admits it was our fault.**

## 1.5 The two guards I was asked to check — both hold

| Guard | Where | Verdict |
|---|---|---|
| 5-question cap | `accountant/questions.py:24`, imported by `accountant/cage/decision.py:201`, enforced at `:999` | **HOLDS.** One constant, one import, so the two layers cannot drift. Verified: `same object = True`. |
| jargon guard | `accountant/questions.py:32`, applied to cage sentences by `tests/test_decision.py:1287-1313` | **HOLDS.** The cage's sentences are checked for leaked ledger names by the same `mentions_any` the questions use. |

Focused run in the clean worktree:

```
$ pytest tests/test_gate.py tests/test_questions.py tests/test_the_wall.py \
         tests/test_question_determinism.py -q
145 passed, 1 xfailed in 11.41s
```

---

# 2 · The defect nobody is on

## 2.1 The one line

`accountant/cage/gate.py:412-413`:

```python
line_paise=seen.line_paise,
total_paise=amount,          # amount is the GROSS total
```

`conservation.lines_sum_to_total` (`accountant/cage/conservation.py:209`) then
asks: **do the line items add up to the total?**

On any bill that carries tax, they do not, and they never will. Line items add
up to the **net**. The tax is added afterwards. The law is being asked to
compare a pre-tax figure against a post-tax figure.

## 2.2 What it does, measured

Command:

```
$ PYTHONPATH=. .venv/bin/python scratchpad/corpus_cage.py \
    data/real_invoices data/real_invoices_indian artifacts/ground_truth/documents
```

Run over 100 real PDFs, using only `pdf_text_layer` — the tier the owner
cleared for auto-post — and granting **the most generous world facts that can
honestly be granted**: the party is a known ledger, the books are open, no GST,
no questions asked, and the net figure the reader found handed straight to the
law that needs it.

```
PDFs read: 100
records where all four laws are answerable: 18

cage verdict:
    100  block
      0  ask
      0  post

first sentence, by frequency:
     82  There is something on this bill I could not check at all…
     18  The numbers in this bill do not add up. Please check the original…
```

Then, on those 18:

```
$ PYTHONPATH=. .venv/bin/python -c "…"
fully-answerable documents      : 18
sum(line items) == net_paise    : 18
sum(line items) == total_paise  : 0
shortfall is EXACTLY the tax    : 18
```

**Eighteen out of eighteen.** The lines always equal the net. They never equal
the total. The "discrepancy" is the tax amount, exactly, every time.

## 2.3 The worked example

`artifacts/ground_truth/cases/GT-0021.json` declares itself
`"category": "clean"`. The reader reads it perfectly — every field matches the
case file's own `expected` block:

```
date 2026-09-21 · party 'BALFOUR BEATTY VINCI JV - HS2 (N2)'
net 495.00 · tax 89.10 · total 584.10 · line items [495.00]
```

495.00 + 89.10 = 584.10. The bill is correct. The read is correct. The cage
says:

```
block
[1] The numbers in this bill do not add up. Please check the original and
    upload a correct version. That is the ₹584.10 bill from BALFOUR BEATTY…
[2] The line items on this bill do not add up to its total: they come to
    ₹495.00 against a stated total of ₹584.10, out by ₹89.10.
[3] This bill includes GST. GST posting is switched off, so this cannot be
    posted automatically. Please enter this one in Tally yourself.
```

Sentence 3 is correct and tells the person what to do. Sentences 1 and 2 are
false, and they come first, because `_failed_laws_block` runs at
`accountant/cage/decision.py:1142` and `_world_blocks` at `:1148`.

**The person is told to go and correct a correct bill.**

## 2.4 Why this matters more than a false block

The owner's rate is 100 false blocks to 1 silent wrong post. Eighteen false
blocks is cheap and I am not arguing with the rate.

This is a different thing. It is a **false accusation**, made with total
confidence, using the owner's own hand-written sentence, about a document that
is fine. And the arithmetic error is entirely on our side of the line.

It also breaks the idea the whole build rests on. `ARCHITECTURE.md` says
conservation laws need no labelled data and give the same verdict anywhere. That
is true of the law. It is not true of **what we feed the law**. A law wired to
the wrong operand is not a law, it is a wrong answer with a proof attached.

## 2.5 Attacking my own conclusion

I went looking for the reason this finding might be worth less than it looks.
I found one, and it is real:

**All 18 documents come from one place.** They are
`artifacts/ground_truth/documents/GT-00xx.pdf`, generated by
`scripts/build_ground_truth.py`. So this is 18 samples from **one generator**,
not 18 independent bills. The 18/18 is weaker than it reads.

Two things survive that, and I think they are enough:

- **The disagreement is internal to this repository.** Our own generator says
  these bills are clean. Our own cage says they do not add up. Both are ours,
  they live in different directories, and no single-file agent can see both.
- **The other 82 PDFs prove nothing either way**, because not one of them can
  answer the law at all. There is no real bill in this repository that
  contradicts the finding, and none that confirms it. The honest position is
  that the law has **never once been shown to be right on a real bill**.

## 2.6 What I am NOT doing

I am not changing the line. Two reasons.

- It is a source change on a file no agent owns, and the owner has not asked
  for it.
- The fix is **not obvious**, and choosing it is an owner decision, not a
  cleanup. At least three options exist:
  1. compare lines against the **net** — correct for taxed bills, and equal to
     today's behaviour for untaxed ones, since net equals gross there;
  2. compare **lines plus tax** against the total — same arithmetic, different
     sentence when it fails;
  3. return INDETERMINATE when a tax figure is present and no net was read —
     safest, and it refuses more.

Option 1 passes all 18 today. That is a measurement, not a recommendation.

## 2.7 What about defect E1?

E1 (`accountant/tallyio/real.py:1179`, xfail-strict at
`tests/test_error_responses.py:1221`) was offered as a candidate. I checked it
and it is **already bounded, and the bound holds**.

An HTML 404 does read as a company with no ledgers. But `bootstrap`
(`accountant/memory/bootstrap.py:278-291`) calls `list_companies()` **first**
and refuses when the company is not in the list. An empty list never contains
the company, so bootstrap returns INCOMPLETE, which makes every lookup
`MEMORY_NOT_READY`. The empty-history path E1 threatens is closed before it
opens.

It is closed **by accident** — by arithmetic on an empty list, not by anybody
deciding the answer was not Tally — and `tests/test_error_responses.py:1263`
already says exactly that. So E1 is a real defect, correctly pinned, correctly
bounded, and **not the highest-value thing available**. The conservation
mis-pairing is, because that one is live, wrong on every checkable document, and
nobody is looking at it.

---

# 3 · The honest launch answer

What would have to be true to post **one** real bill correctly, in order.
Not a plan. A list of preconditions and where each one stands today.

| # | Precondition | State |
|---|---|---|
| 1 | A file arrives and is routed by what it **is**, not what the browser claims | **NOT DONE.** `accountant/cage/classify.py` is imported by no source module. Fails closed today, so it costs false blocks, not wrong posts. |
| 2 | A reader gets figures off a real bill | **PARTLY.** 20 of 100 PDFs yield a total; 21 yield a net; 18 yield line items. |
| 3 | Every field carries a stated source and a stated confidence | **DONE** for four fields. **IN FLIGHT** for `net_paise` (agent B), and it needs agent D's file too — see §1.2. |
| 4 | The four conservation laws are fed the right numbers | **NO. This is §2.** `lines_sum_to_total` is fed the gross where it needs the net. Wrong on 18 of 18. |
| 5 | The cage is on the live path | **IN FLIGHT** (agent A, `pipeline.py:793`). Correctly written: it may narrow and never widen (`pipeline.py:198`). |
| 6 | Somebody can tell whether the books are open for a date | **NOT DONE.** Nothing in this repository reads it. `period_open=None` blocks, which is the right answer to "nobody looked". |
| 7 | The party can be recognised without inventing a ledger | **WEAK.** `party_known` is `party in accounts` — false for every supplier that is not already a ledger. Hard rule 7 then blocks. Correct per the owner, and it blocks most real bills. |
| 8 | A voucher reaches a licensed TallyPrime | **BLOCKED.** `B-01`, `B-02`, `B-03` — one chain: no licence, so no company, so no live run. Two minutes of owner clicking plus one purchase decision. |
| 9 | One real bill has actually gone into a real Tally and been read back | **NEVER HAPPENED.** n = 0. The 95% upper bound on the silent-wrong-post rate is 100%. |

**The order matters and item 4 is out of order.** Items 8 and 9 are the ones
everybody talks about, and they are owner-blocked. Item 4 is not blocked by
anybody, is wrong today, is measured, and sits upstream of everything else.
Fixing it changes what the product says to a person this week. Nothing else on
this list does.

**The bottleneck is not the Tally licence.** With items 4, 6 and 7 as they
stand, a licensed Tally would receive **zero** vouchers — the cage blocks all
100 documents before the write door is reached. Buying the licence today buys
an empty pipe.

---

## Commands, so anybody can re-run this

```bash
git worktree add /tmp/wt-audit 0cf2411
cd /tmp/wt-audit

# the corpus, through the only auto-post-eligible reader, most generous facts
PYTHONPATH=. …/.venv/bin/python scratchpad/corpus_cage.py \
  data/real_invoices data/real_invoices_indian artifacts/ground_truth/documents

# which law fails, and by how much
PYTHONPATH=. …/.venv/bin/python scratchpad/corpus_why.py <same three paths>

# the guards
…/.venv/bin/python -m pytest tests/test_gate.py tests/test_questions.py \
  tests/test_the_wall.py tests/test_question_determinism.py -q
```

The probe scripts live in the session scratchpad, not in the repository. They
read files and print; they write nothing.
