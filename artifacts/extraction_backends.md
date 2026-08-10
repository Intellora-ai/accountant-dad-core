# Third-party extraction backends — comparison for the owner

**Outcome: `third-party backend selection = OWNER_DECISION_REQUIRED`**

**Summary verdict in the current label set (from 2026-08-10):**

    third-party backend selection = BLOCKED   (owner decision: D-23 first, then the selection)

The permitted values are `PASS · FAIL · BLOCKED · NOT_MEASURED · INVALIDATED ·
GITHUB_REQUIRED`. `OWNER_DECISION_REQUIRED` is the owner's own wording for this
decision and is kept above deliberately — this document does not edit the
owner's open decisions. In the current set it reads **BLOCKED**, and the thing
it is blocked on is a person, not a task: see section 12.

Nothing here selects a backend. Choosing one is a one-way door: it sets a
per-page cost, a data-residency position, and a dependency the product cannot
easily leave. This document lays out what is actually documented, what is not,
and the questions only the owner can answer.

Written 2026-08-10. Every claim below carries a source URL and the date it was
retrieved. Where a vendor does not publish something, the cell says so instead
of guessing.

---

## Read this first — the short version

**16 backends were checked. 11 are real candidates. Here is what matters.**

**The shortlist, if extraction happens at all:**

| # | Backend | Per page | India region | Why it is on the list |
|---|---|---|---|---|
| **1** | **Azure AI Document Intelligence** `prebuilt-invoice` | **$0.01** | **Central India**, same price | Clearest retention statement anywhere (**24h, in-region, documented**), per-field confidence, `en-IN` locale + `INR`, a `Retry-After` header **and** a published backoff schedule. Gap: **no statement on whether it trains on your data.** |
| **2** | **AWS Textract** AnalyzeExpense | **$0.01** (Oregon-quoted) | **Mumbai**; "same price" is **`UNVERIFIED`** — see 9.2 | **The only backend anywhere with named `VENDOR_GST_NUMBER` / `RECEIVER_GST_NUMBER` and PAN fields.** Catch: the **default permits training on your invoices and storing them outside your region** until you file an AWS Organizations opt-out, and Mumbai runs at **1 TPS vs 5**. |
| **3** | **Claude vision + structured outputs** | **≈$0.0046** | **None** | Cheapest, best error contract, contractual **zero-retention** available, and you define the schema — so you can ask for GST fields nobody else provides. Costs: no India region, no numeric per-field confidence. |

**Ruled out, and why:**

- **TallyPrime "Docs by Ira", myBillBook, Vyapar, Vyapar TaxOne (was Suvit), TrulyInvoice** — **none has a public API.** A product with no API cannot be a library, whatever its quality.
- **Google Document AI** — bills **per document, not per page**: **$100 per 1,000 one-page invoices, ~10x** the others. Also **no SLA in India** and a **6 requests/minute** single-region quota.
- **Docsumo** — has the best GST field coverage (GSTIN *and* HSN) but its privacy policy says uploaded documents are *"retained and used for… training of the artificial intelligence."*
- **Rossum** — **$18,000/year** floor. **Nanonets** — ~**$1.31** per one-page invoice, and stores all data in the USA. **Klippa, Affinda** — no public price at all.
- **Self-hosted OCR** — banned from `accountant/extract/` by a test, and it solves the wrong half of the problem. `microsoft/layoutlmv3-base` is additionally **CC BY-NC-SA 4.0, non-commercial**.

**The five findings that should change the decision:**

1. **`accountant/extract/` may import stdlib and `accountant.*` only** — enforced by `tests/test_no_reader.py`. **No vendor SDK can be imported there.** The selection criterion is not "best SDK", it is "plain HTTPS JSON API". This also rules AWS SigV4 awkward and makes Azure's subscription-key header easy.
2. **No vendor publishes a number comparable to the 95-per-100-per-field bar, and none will.** Rossum's famous **0.975 is a configurable auto-accept threshold on a deprecated feature**, not an accuracy — Rossum's own page says real accuracy "may vary field by field". The bar can only be settled by measuring on the owner's own bills.
3. **Not one backend returns a CGST/SGST/IGST split or an HSN code.** AWS gets closest with a GSTIN field. **Every option needs custom GST post-processing.** That cost is vendor-independent and probably exceeds the per-page cost.
4. **A large class of Indian bills may need no OCR at all.** GST e-invoices carry a government-signed IRN and QR code, and the official portal publishes the schema, a sample signed JSON, and a QR verifier. **Scope this before signing any per-page contract** (section 3).
5. **The legal case for keeping data in India is weaker than assumed.** From the official texts: **DPDP s.16 permits cross-border transfer by default** and is probably not in force until 2027; **GST law has no server-location rule**; **RBI localisation applies to payment systems, not accounting data.** The one rule that might bite (Companies (Accounts) Rules 3(5)) could not be retrieved — and as reported it wants a **backup** in India, not processing in India. **One CA task closes this.** Until it is closed, residency is a *trust* choice with a known price, not a constraint (section 10.2).

**And the question above all of these:** `docs/DECISIONS.md` **D-23 is still
`OPEN`**, its stated default is **typed text only**, and `docs/EPIC.md` lists
"OCR of any kind" as out of scope for the entire epic. **If D-23 resolves to
typed text, none of this is needed.**

---

## Words used in this document

Defined once, in plain language, because the rest of the document leans on them.

| Word | What it means |
|---|---|
| **OCR** | Optical Character Recognition. Software that looks at a picture of a page and works out which letters and numbers are in it. It gives you text, not meaning. |
| **Field extraction** | The next step after OCR. Deciding *which* number on the page is the total, and which is the tax. This is the part we actually need. |
| **Backend** | The outside service that does the reading for us. We send it a bill; it sends back fields. |
| **API** | A way for our program to talk to their program directly, with no human clicking anything. If a product has no API, our software cannot use it, no matter how good it is. |
| **Per-field confidence** | A number from the backend saying how sure it is about one field. Useful only if we treat "not sure" as "ask the person". |
| **Data retention** | How long the vendor keeps a copy of your bill after answering. |
| **Zero-retention** | A mode where the vendor keeps nothing after replying. |
| **Data residency** | Which country the data is processed and stored in. |
| **Exit cost** | How much work it is to rip this backend out and use a different one later. |
| **Threshold** | A cut-off you choose. Not a measurement of how often something is right. |

---

## 1. The contract a backend has to fit

Read `accountant/extract/adapter.py` before reading the vendor tables. The
comparison below is against this real contract, not a generic one.

```python
class Extractor(Protocol):
    def extract(self, data: bytes, mime: str, /) -> ExtractedRecord: ...
```

`ExtractedRecord` demands five things, and they are unusually strict:

| Requirement in the code | What it forces on a backend |
|---|---|
| `date, party, total_paise, tax_paise, line_items` | The named fields. A backend that returns raw text only has done maybe half the job; we would have to write the field-picking ourselves, which the package forbids. |
| Amounts are **integer paise** | Money is an exact integer. `_to_paise` returns `None` rather than rounding: `"10.005"` is refused, not silently turned into ₹10.00. A backend that hands back a float, or a pre-rounded 2-decimal string, loses information *before* we can object to it. |
| `per_field_source` must name a source for **every** field | Per-field provenance is mandatory. `__post_init__` raises if any field has no stated source. A backend that returns a whole-document confidence, or no confidence at all, cannot fill this honestly. |
| `NOT_FOUND` is a first-class value | "I could not read this" must be expressible. A backend that guesses instead of abstaining is actively harmful here. |
| `UnavailableExtractor` already exists | The outage path is already designed: every field comes back `not_found: <reason>` and the person types instead. We need the backend's error contract to be **distinguishable** — "the service is down" must not look like "the bill was blank". |

### The constraint most vendor comparisons would miss

`tests/test_no_reader.py` enforces that **`accountant/extract/` may import
stdlib and `accountant.*` and nothing else.** It is an allowlist, and it is
tested:

```
allowed = set(sys.stdlib_module_names) | {"accountant", "__future__"}
```

Consequences, and they are large:

- **No vendor SDK may be imported in this package.** Not `anthropic`, not
  `boto3`, not `google-cloud-documentai`, not `azure-ai-documentintelligence`,
  not even `requests` or `httpx`.
- A backend is therefore only usable here if it can be driven by a **plain
  HTTPS call with a JSON body**, from `urllib.request` — or if the SDK call
  lives outside the package and the result is injected in.
- **Every self-hosted OCR library is excluded from this package by
  construction** (Tesseract, PaddleOCR, docTR all need a dependency). Self-hosting
  is not thereby ruled out for the *product* — but it has to run as a separate
  process behind an HTTP boundary, which is a second thing to operate.

**So the practical selection criterion is not "which vendor has the nicest
Python SDK". It is "which vendor has a documented plain-HTTP JSON API, returns
per-field confidence, and can say *I don't know*."** That reorders the list.

---

## 2. The bar, and why no vendor can be measured against it

The owner's bar: **≥ 95 correct per 100, per field**, on date, party, total
amount, tax amount, line items.

**No vendor publishes a number comparable to that bar. Not one.** Saying so
plainly is more useful than substituting a marketing figure.

What vendors publish instead, and why each fails the bar:

| What you will be shown | Why it is not the bar |
|---|---|
| "99% accuracy", "99.9% accuracy" | Marketing copy. No named test set, no per-field breakdown, no definition of "correct", not reproducible. |
| Character-level OCR accuracy | Measures letters recognised, not fields chosen correctly. A page can be 99% correct at the character level and still put the wrong number in "total". |
| Rossum's **0.975** | A **confidence threshold** — the cut-off above which a field is auto-accepted without a human looking. It is a dial you set, not a measurement of how often the answer is right. It must never be quoted as accuracy. |
| Model cards / leaderboards | Measured on public English/US/EU document sets, not Indian GST purchase bills. |

`docs/EPIC.md` already records the same conclusion from an earlier pass:
*"Zero neutral third-party accuracy benchmarks exist for any product, globally"*
and *"Dext's 99.9% accuracy is field extraction, not account correctness."*

### I went looking for disconfirming evidence

Rather than assume that finding still held, I searched for independent
per-field invoice benchmarks. Some exist and none qualify. The most cited one
is published by **Businessware Technologies**, an AI development services firm —
a vendor, not a neutral party. On its own page it does not disclose the number
of invoices tested, their country of origin, whether any were Indian or GST
invoices, or whether the dataset is public; it reports no per-field breakdown
and gives no ground-truth or inter-rater methodology, so it cannot be
reproduced. *(Source: businesswaretech.com blog, "Best AI services for
automatic invoice processing", retrieved 2026-08-10.)*

Two things worth the owner's attention anyway:

1. Such third-party figures as circulate for header and line-item field
   accuracy sit in the **78–93%** band — *below* the 95 bar, not above it.
   Weak evidence, but it points the wrong way, and it is the only direction
   the public evidence points.
   **`UNVERIFIED`, added 2026-08-10:** unlike every other number in this
   document, this band carries **no source URL and no retrieval date**. It is
   kept because a figure pointing away from the bar is worth knowing about, and
   labelled because it cannot be traced. Do not quote it in a decision
   document. The paragraph above already disqualifies the one benchmark that
   was traced, so treat 78–93% as hearsay that agrees with the argument — the
   weakest kind of evidence there is.
2. **The bar can only be settled by measuring on your own bills.** That is a
   cost the owner is choosing whether to pay, on top of the per-page price.

---

## 3. The finding that may delete part of the problem

Before choosing how to read a picture of an Indian bill, note that a large
class of Indian B2B bills is **already machine-readable by law**.

Under GST e-invoicing, a supplier above the turnover threshold must report the
invoice to an Invoice Registration Portal (IRP) in a prescribed JSON schema
(GST INV-01). The IRP returns a signed JSON containing an Invoice Reference
Number (IRN) and a QR code, and the supplier must print the IRN and QR on the
invoice.

Official wording from the government e-invoice portal:

> "QR code will be part of the signed JSON, returned by the IRP." … "It will be
> a string (not an image), which the ERP/accounting/billing software shall read
> and convert into QR Code image."

and

> "taxpayers need to print this IRN and QR Code on the invoice"

*Source: e-Invoice Printing: Process, Mandatory Fields, Modes of IRN generation,
`https://einvoice6.gst.gov.in/content/e-invoice-printing-process-mandatory-fields-modes-of-irn-generation/`, retrieved 2026-08-10.*

The official portal `https://einvoice1.gst.gov.in/` publishes an API Sandbox
tool, a QR Code procedure document, a **Sample Signed e-Invoice JSON**, and a
**Verify QR Code App**. *(Retrieved 2026-08-10.)*

| Item | Status |
|---|---|
| A published, official JSON schema for Indian invoices exists | Confirmed |
| The QR code is printed on qualifying invoices and is machine-readable | Confirmed |
| Official sandbox and QR-verification tooling exist | Confirmed |
| The exact list of fields inside the signed QR | **Not stated on the official page I read.** Left blank rather than guessed. |
| The current turnover threshold | **`UNVERIFIED`.** Widely cited as ₹5 crore aggregate annual turnover under **CBIC Notification No. 10/2023 – Central Tax**. The notification exists on the official CBIC portal at `https://taxinformation.cbic.gov.in/view-pdf/1009732/ENG/Notifications`, but **the fetch failed with a TLS certificate error and the operative text was not read.** The figure is kept and labelled, not deleted: it is what everyone cites. It becomes a fact when someone opens that PDF. |

**Why this matters to a one-way-door decision:** for every bill that carries an
IRN and QR, the structured data was created by the supplier's software and
signed by the government. Reading a QR code is not OCR, has no per-page vendor
fee, no data-residency question, and no accuracy ceiling to measure — the data
is either verified by signature or it is not. This does not cover handwritten
kirana bills or small suppliers below the threshold, so it is not a complete
answer. It may be a much cheaper answer for the bills that matter most.

**This should be priced and scoped before any per-page contract is signed.**

---

## 4. Prior recorded position in this repo

The owner should re-read what the project already decided before treating this
as an open shopping exercise.

| Source | What it says |
|---|---|
| `docs/EPIC.md`, "Out of scope for the entire epic" | Lists **"bill photo capture, OCR of any kind"** as out of scope. |
| `docs/EPIC.md` | "TallyPrime 7.1 (June 2026) ships 'Docs by Ira': PDF/image to draft voucher" → *"Bill extraction is now a vendor feature. Do not build it."* |
| `docs/EPIC.md` | "myBillBook ships extraction free; Vyapar, Marg, BUSY ship it paid; TrulyInvoice is Rs 599/month unlimited" → *"Extraction is a price war with a free floor. Do not enter."* |
| `docs/DECISIONS.md` **D-23** (`OPEN`) | "Which input types must work at first launch." Default if unanswered: **typed text only**. And: *"if the answer is typed text only, the extraction phase leaves the critical path entirely."* |

**The upstream question is not "which backend". It is D-23: whether extraction
is on the critical path at all.** A backend choice made before D-23 is answered
is a cost incurred against an undecided requirement.

---

## 5. Backend record — general-purpose vision model (Claude API)

This candidate class was not on the brief's list. It is included because it is
the only class that fits the adapter's stdlib-only constraint with a plain
HTTPS JSON call, and because it has a **documented zero-retention path** — which
most dedicated OCR vendors do not.

All figures below are from official Anthropic documentation, retrieved
**2026-08-10**.

| Field | Record |
|---|---|
| **Name** | Claude API (Messages API) with vision / PDF input |
| **Official URL** | `https://platform.claude.com/docs/en/build-with-claude/vision.md` |
| **Supported input types** | Images: **JPEG, PNG, GIF, WebP** (`image/jpeg`, `image/png`, `image/gif`, `image/webp`). Animations unsupported — only the first frame is used. PDFs: "standard PDF (no passwords/encryption)". **DOCX is not supported** — note that frozen criterion S1 asks for DOCX. |
| **Size / page limits** | Image: **10 MB** base64 per image on the Claude API; max dimensions **8000x8000 px**; max **100 images per request** (200k-context models) or 600 otherwise. PDF: max request **32 MB**, max **600 pages** per request (100 when the context window is under 1M tokens). |
| **API contract shape** | Synchronous HTTP `POST /v1/messages`, JSON in / JSON out. Auth is an `x-api-key` header. **Drivable from `urllib.request` with no SDK** — this is the one that satisfies `test_no_reader.py`. Optional streaming. |
| **Structured fields returned** | **Whatever schema you define.** Not a fixed invoice field list — you supply a JSON Schema via `output_config.format` (structured outputs) and the response is constrained to it. This means `date / party / total_paise / tax_paise / line_items` can be requested *in the shape the adapter already wants*, including asking for amounts as exact strings rather than floats. |
| **GST / tax field support** | **No documented GST-specific model.** There is no official field list naming GSTIN, HSN, CGST/SGST/IGST, because there is no fixed field list at all. This cuts both ways: nothing is pre-built, but nothing prevents you asking for those fields by name. **No official document states Indian GST invoices as a tested use case.** |
| **Confidence and provenance** | **No numeric per-field confidence score is returned.** This is a real gap against `per_field_source`, which requires a stated source for every field. You would have to derive abstention behaviour by prompting (asking the model to return an explicit "not found") rather than reading a vendor-supplied confidence number. Citations exist for documents but are not per-extracted-field confidences. |
| **Price** | Per million tokens (MTok): Opus 5 **$5 in / $25 out**; Sonnet 5 **$2 / $10** introductory through 2026-08-31, then **$3 / $15**; Haiku 4.5 **$1 / $5**. Batch API = **50% off** both. Prompt cache read = 0.1x input; 5-min cache write = 1.25x; 1-hour write = 2x. `inference_geo: "us"` = **1.1x** on all categories. Source: `https://platform.claude.com/docs/en/about-claude/pricing` |
| **Rate limits** | Published per tier. Start tier: Opus 5 / Sonnet 5 / Haiku 4.5 each **1,000 RPM, 2,000,000 input tokens/min, 400,000 output tokens/min**. Build: 5,000 RPM / 5M / 1M. Scale: 10,000 RPM / 10M / 2M. Monthly spend caps: Start **$500**, Build **$1,000**, Scale **$200,000**. Cached input tokens do **not** count toward the input-token limit. Source: `https://platform.claude.com/docs/en/api/rate-limits` |
| **Data retention** | Default: inputs and outputs deleted within **30 days**. **A zero-data-retention (ZDR) arrangement is documented**: *"Under a ZDR arrangement, Anthropic does not store customer prompts or responses at rest after the API response is returned."* Requested via sales, enabled per organization. Source: `https://platform.claude.com/docs/en/manage-claude/api-and-data-retention` |
| **Zero-retention caveats — read these** | The Messages API and **inline PDF support are ZDR-eligible**. But the **Files API is NOT** ("files retained until explicitly deleted") and the **Batch API is NOT** (29-day retention). So the two most obvious cost/convenience optimisations — upload once, and batch at 50% off — each **step outside zero-retention**. Structured outputs are "Yes (qualified)": prompts/outputs not stored, but the JSON schema itself is cached up to 24 hours. Flagged content may be retained up to **2 years** regardless of arrangement. |
| **Data location** | **`inference_geo` accepts only `"us"` and `"global"`. Workspace storage geo: `"us"` is the only value available, and cannot be changed after the workspace is created.** There is **no India region.** Officially: *"Inference geo: Only `"us"` and `"global"` are available."* Source: `https://platform.claude.com/docs/en/manage-claude/data-residency` |
| **Privacy terms** | *"Retained data is never used for model training without your express permission."* Source: as above. |
| **Security controls** | HIPAA-ready arrangement available with a signed BAA (not relevant here, but indicates the control set: encryption, access controls, audit logging). Trust Center: `https://trust.anthropic.com/resources`. No ISO 27001 / SOC 2 claim verified in this pass. |
| **Documented outage / error behaviour** | **The best-documented error contract of anything reviewed.** Official list with types: 400 `invalid_request_error`, 401 `authentication_error`, 402 `billing_error`, 403 `permission_error`, 404 `not_found_error`, 409 `conflict_error`, 413 `request_too_large`, 429 `rate_limit_error` (with a `retry-after` header), 500 `api_error` ("retry with exponential backoff"), 504 `timeout_error`, 529 `overloaded_error`. Every response carries a `request-id`. Rate-limit headroom is exposed in `anthropic-ratelimit-*` headers. Source: `https://platform.claude.com/docs/en/api/errors` |
| **Published uptime SLA** | **Not found** in the documentation reviewed. Left blank. |
| **Exit cost** | **Lowest of any option here.** There is no proprietary field taxonomy to migrate off — you own the JSON Schema. Swapping vendors means rewriting one HTTP call and one prompt, and the adapter's `ExtractedRecord` shape does not change. |
| **Accuracy** | **No official accuracy number exists, and none is claimed.** The docs explicitly warn: *"Claude might hallucinate or make mistakes when interpreting low-quality, rotated, or very small images"* and *"Do not use Claude for tasks requiring perfect precision … without human oversight."* That warning is honest and directly relevant to a 95/100 bar. |

### Per-page cost — my arithmetic, not a vendor price

Vendors quote per page; this class quotes per token, so the two are not
comparable until you do the conversion. Official token formula: an image costs
`ceil(width/28) x ceil(height/28)` visual tokens, capped per model tier.

Assumptions, stated so they can be checked: one A4 page scanned at 200 DPI
(1654 x 2339 px), ~500 tokens of prompt, ~500 tokens of JSON output with line
items. At that size the high-resolution tier hits its **4,784 visual-token cap**;
Haiku 4.5 is standard tier and caps at **1,568**.

| Model | Input tokens | Output tokens | **Cost per page (USD)** | With Batch API (−50%) |
|---|---|---|---|---|
| Opus 5 | 5,284 | 500 | **$0.0389** | $0.0195 |
| Sonnet 5 (intro, to 2026-08-31) | 5,284 | 500 | **$0.0156** | $0.0078 |
| Sonnet 5 (from 2026-09-01) | 5,284 | 500 | **$0.0234** | $0.0117 |
| Haiku 4.5 | 2,068 | 500 | **$0.0046** | $0.0023 |

Add **1.1x** if you pin `inference_geo: "us"`. Remember the batch column costs
you zero-retention eligibility. Convert to rupees at your own FX rate — I have
not assumed one.

---

## 6. Indian SMB accounting products — all five are eliminated, for one reason

Checked: TallyPrime 7.1 "Docs by Ira", myBillBook, Vyapar, Suvit, TrulyInvoice.
Retrieved 2026-08-10.

**None of the five publishes a public, programmatic API that a Python program
could call to extract fields from an invoice.** Every one is an end-user
application. Extraction is reachable only by a human using their desktop app,
mobile app, or web console. No REST endpoints, no API keys, no SDKs, no
developer documentation for extraction, on any of them.

That single fact eliminates all five as backends behind the `Extractor`
Protocol, regardless of price or quality. **A product with no API cannot be a
library.** They remain relevant as *competitors*, and that is how `docs/EPIC.md`
already treats them.

| Product | Price (INR) | Fields claimed | Data location stated? | Public API |
|---|---|---|---|---|
| **TallyPrime 7.1 "Docs by Ira"** | **₹200 + GST per 100 pages**; needs TallyPrime licence + active TSS + Rel 7.1+ | Party name, invoice number, invoice date, amount, "GST", line items. No explicit GSTIN/HSN/CGST-SGST-IGST claim. | **No.** DPA permits processing "in India **or in any other country**". Sub-processors listed only as categories. | **None found** |
| **myBillBook** | ₹291–₹570/mo tier depending; scanning stated as available on free + all paid tiers | Supplier, invoice no., date, items, qty, prices, **GSTIN, CGST/SGST/IGST**, HSN, total | **Partially.** Claims servers "within India", then carves out third-party data centres "outside of our direct control". | **None found** |
| **Vyapar** | **Not established** — pricing pages are JavaScript-rendered and returned no plan data. OCR is Premium-only. | Vendor, item, price, **GSTIN, HSN/SAC**, tax breakdown, totals | **No.** "Secure cloud servers", unnamed. Policy permits access "including outside India". | **None found** |
| **Suvit → now Vyapar TaxOne** (suvit.io 308-redirects to taxone.vyapar.com — Suvit has been acquired/rebranded) | **₹20,000/yr list, ₹10,000/yr promotional**, unlimited | **Not specified on any official page.** Thinnest documentation of the five. | **No.** Retention deferred to an unpublished "Records and Information Management Policy". An India/AWS claim exists only in a staff blog post, not a contract. | **None found** |
| **TrulyInvoice** | **₹599/month**, 2,000 pages/month | Most explicit: party + **GSTIN**, invoice no./date/due date, line items with **HSN**, qty, rate, taxable value, **CGST/SGST/IGST/CESS**, total, round-off | **No country stated at all.** Files "purged after processing according to your data retention preferences" — duration not stated. | **None found** |

### Two corrections to what `docs/EPIC.md` currently records

1. **"TallyPrime 7.1 ships Docs by Ira" understates the cost.** It is a **paid,
   metered plug-in**, not a built-in 7.1 feature: **₹200 + GST per 100 pages**,
   requiring a licence, an active TSS subscription, and separate portal
   registration. It also **cannot import handwritten documents** and handles
   **one transaction per document** (3 MB desktop / 10 MB mobile). Anyone
   assuming "Tally now does OCR out of the box, free" is wrong twice.
2. **Suvit no longer exists as an independent product.** It is now Vyapar
   TaxOne. Any prior research treating them as two separate competitors is stale.

Relevant to the outage contract: Tally's long-standing **XML gateway on port
9000 is not an extraction API.** It writes vouchers whose fields you already
have. It cannot read an invoice image. This matters because the project already
uses that gateway — it is not a route to extraction.

### Accuracy claims from this group

| Product | Claim | Verdict |
|---|---|---|
| Vyapar | "99.9% Data Accuracy" | **MARKETING.** No methodology, no test set, no definition of a correct field. |
| TrulyInvoice | "~96% field-level on clear digital PDFs; 88–90% on handwritten or low-resolution scans" | **MARKETING**, self-reported, no methodology. Notable only because it is shaped like a real measurement rather than a round number — and because **88–90% is openly below the 95 bar**. |
| Tally "Docs by Ira" | "up to 80% of processing time saved" | **MARKETING**, and it is a time claim, not an accuracy claim. |
| Suvit / TaxOne | "Cut 80% of manual entry errors" | **MARKETING**, and an error-*reduction* claim, not an accuracy rate. |
| myBillBook | "Better Accuracy" | **MARKETING**, no number. |

**Nobody in this group publishes an accuracy number that can be checked against
the 95-per-100-per-field bar.**

---

## 7. Specialist extraction vendors — Nanonets, Klippa, Docsumo

All retrieved 2026-08-10 from official documentation, pricing, and legal pages.

| Field | **Nanonets** | **Klippa** (DocHorizon) | **Docsumo** |
|---|---|---|---|
| **Official URL** | `nanonets.com/ocr-api/invoice-ocr` | `klippa.com/en/dochorizon/` | `docsumo.com/solutions/documents/invoices` |
| **API docs** | `docs.nanonets.com` (note: `apidocs.nanonets.com` **no longer resolves**) | `dochorizon.klippa.com/docs/api` | `support.docsumo.com` |
| **Input types** | PDF, PNG, JPG, CSV, XLS, XLSX, TIFF, TXT, DOCX | JPEG, PNG, HEIF/HEIC, AVIF, TIFF, WebP, PDF, RTF, DOC, DOCX, XLS, XLSX, ODT, ODS | .png, .jpeg, .jpg, .pdf, .tiff, .tif |
| **Max file size** | **Not stated** | **20 MB** (images) | **25 MB** |
| **Max pages** | **Not stated** | **50** — "documents with more than 50 pages will time out and fail to process" | **20** — beyond that the doc is accepted but "data extraction occurs only on the first 20 pages" |
| **API shape** | Basic auth (API key as username). Both sync and async endpoints. Multipart or URL. | Header `x-api-key`. Sync primary. **JSON only** — "does not accept `multipart/form-data`"; document as base64, URL, or file_id. | Header `apikey`. **Async only** — upload returns `doc_id`, data fetched separately. Multipart. |
| **Per-field confidence** | **Yes** — `score` key per prediction, float 0–1 | **Partly** — confidence is *OCR/page-quality*, not per-extracted-field | **Yes** — `confidence` key per field |
| **Bounding boxes** | Yes | Yes (`vertices`, 4 corners, plus `page`) | Yes (`position: [x1,y1,x2,y2]`) — **no page-number key documented** |
| **Price** | Only one with published numbers, and it is **not per page**: $0.02/simple block run, $0.10/standard AI block, $0.30/complex AI block; $100/mo = 100 credits. Official docs give "1-page invoice with line items: 1.31 credits" → **≈$1.31 per one-page invoice at list**, derived across two official pages. | **Not public.** "Pricing is based on volume, workflow complexity, and subscription plans." | **Not public.** Free trial 14 days / 1,000 pages. Business and Enterprise undisclosed; **setup fees apply**, amount not stated. |
| **Rate limits** | **No number published.** Retry guidance only: "start with a 30-second delay", then exponential. | Best documented. **Concurrent** caps, not per-window: Financial **50**, most others 15–30. 429 on exceed, with `X-RateLimit-Concurrent-*` headers. | **10 requests/second**. No headers, no retry guidance. |
| **Data retention** | Contract-driven via DPA; retained while subscription is active; deleted on written request. **No zero-retention mode documented.** | **"By default, we do not store any data that is being processed on our servers."** Whether this is a configurable toggle is **not stated** — it is described as the default. Server logs deleted after 1 month. | **"The uploaded document will be retained and used for further research, development and training of the artificial intelligence and will be deleted."** Personal data deleted at latest **2 months** after account closure. |
| **Data location** | **"All customer data is stored in the USA."** AWS + GCP. | **Hosted in EU** (Azure; privacy statement names DigitalOcean, Amsterdam). "Custom server locations in the EU, US, Switzerland, or other locations on demand." | "AWS Cloud, Google Cloud Platform". **No region named.** |
| **India region** | Not stated | Not stated ("other locations on demand" is the only hook) | Not stated |
| **On-premise** | **Conflicting.** Legal security policy: all services cloud, on-prem not mentioned. Marketing page: "Cloud, single-tenant, or on-prem." | Not stated | Not stated |
| **Training on your documents** | **"Customer data is never used for model training"** — but this appears on a **marketing page**, and the legal security policy is silent on training. | **No official statement found**, on four pages checked. What Klippa officially claims is *non-storage*, which is a different promise. | **Explicitly yes** — see retention row. |
| **Security** | SOC 2 Type I & II on request; ISO/IEC 27001 certified; AES-256 at rest | ISO 27001 and ISAE 3000 Type II; ISO 9001; GDPR DPA; published sub-processor list | SOC 2 Type 2; HIPAA-compliant infrastructure; SSO. **ISO 27001 not stated.** |
| **Status page** | `status.nanonets.com` — live, 100.0% over 90 days | `status.klippa.com` — live, per-service | **None found.** `status.docsumo.com` is a bare shell with no services listed. |
| **Error contract** | 200, 206, 400, 401, **402 Exhausted Free API Calls**, 403, 404, 429, 500, 503 + retry guidance | Very granular proprietary code space (X001xxxx general, X002xxxx auth, X003xxxx billing, X004xxxx service, X005xxxx document) with per-code remediation | 2XX, 400, 401, 404, 409, 429, 50X with `error_code` + `message`. **No retry guidance documented.** |
| **Published SLA** | "99.95%+ uptime SLA" — **on a marketing page**, no contractual terms shown | **None published** | **None found** |
| **Line items** | Yes (5 fields) | Yes (**35+** fields) | Yes (6 fields) |
| **GST / India fields** | **Not documented.** Zero occurrences of GST, GSTIN, HSN, CGST, SGST, IGST in the invoice model docs — only generic `total_tax`, `subtax_*`. India-founded does not translate into documented GST support. | **Not documented.** Field vocabulary is EU-shaped: `vat_number`, `eori_number`, `coc_number`. | **Documented — the only one of the three.** `GST/ VAT Number` as a seller and buyer field; **`HSN`** as a line-item column. **But no separate CGST / SGST / IGST fields** — you would reconstruct those from generic tax rows. |
| **Exit cost** | Medium — proprietary field taxonomy, but a conventional REST shape | Medium-high — largest proprietary field surface (35+ line-item fields) to map away from | Medium — async two-step flow to unwind |

### The three things that matter most in this group

1. **Docsumo is the only one that documents Indian tax fields — and the only one
   whose privacy policy says it trains on your documents.** Those two facts sit
   in the same vendor. The best GST fit has the worst confidentiality posture,
   verbatim: *"The uploaded document will be retained and used for further
   research, development and training of the artificial intelligence."*
2. **Klippa has the only documented default-no-storage posture**, but its
   reputation for "not training on your data" is **not backed by any official
   statement** — four pages checked, all silent. Non-storage implies
   non-training in practice; it is not the same commitment, and Klippa has not
   written the second one down. Do not credit it with a promise it has not made.
3. **Nanonets contradicts itself on deployment and on training.** The legal
   security policy says all data is stored in the USA and does not mention
   on-prem; the marketing page claims on-prem and a "zero training guarantee".
   **The legal page is the one with contractual weight.** Get the training
   guarantee into the DPA before relying on it.

### Accuracy claims from this group — all MARKETING

"Reach 99% accuracy" and "near 100% data extraction accuracy" (Klippa);
"95%+ accurate data extraction" (Docsumo enterprise page); "superior accuracy"
(Nanonets, not even numeric). **None appears in API documentation. None is a
per-field measurement. None names a test set, document count, field list, or
method.** They are not comparable to each other and cannot rank the three.

### Not stated by these vendors — recorded as gaps, not guessed

- **Nanonets:** max file size, max page count, numeric rate limit, EU/India residency, contractual SLA terms.
- **Klippa:** any price number, PDF size cap, on-prem availability, model-training policy, uptime percentage.
- **Docsumo:** all pricing above the free tier, ISO 27001 status, data-residency region, on-prem/VPC availability, retry guidance, status page, SLA.

---

## 8. Rossum, Veryfi, Mindee, Affinda

Retrieved 2026-08-10.

### 8.1 The Rossum 0.975 number — settled, with the vendor's own words

The brief flagged this as the only public number of its kind the project has
ever found. **It is a threshold you set. It is not an accuracy anyone measured.**
Rossum's own page says so, and also says the scores are not calibrated.

Source: *"Using AI Confidence Thresholds for Automation in Rossum"*,
`https://knowledge-base.rossum.ai/docs/using-ai-confidence-thresholds-for-automation-in-rossum`, retrieved 2026-08-10.

Verbatim, in the order that matters:

1. Banner at the top of the page:
   > "This feature is deprecated. It won't receive any further updates and is likely to be completely removed in the future."
2. What the score is:
   > "The confidence score indicates the extent to which the AI engine is confident that it got the text and the location of the field correctly."
3. What 0.975 does:
   > "By default, when you enable 'Confident' automation, all data fields captured in the selected queue have a confidence threshold of 0.975. This threshold gives the AI a confidence bar to pass to allow the automatic export of data."
4. The sentence people misread as an accuracy claim:
   > "This means that the 0.975 threshold expresses a requirement for 97.5% accuracy, i.e., that documents sent to accounts payable software automatically should have a 2.5% error rate at maximum."

Read (4) carefully: **"expresses a requirement"** and **"should have… at
maximum"**. That is a bar the operator sets for auto-export, not a result. And
on the same page Rossum says the scores do not track reality:

> "In general, you may find Rossum's AI engine a little pessimistic, with real accuracy higher than hinted by the typical scores, but this may vary field by field."

If 0.975 genuinely meant 97.5% accuracy, that sentence could not be true.

Two further caveats: the threshold is **configurable per queue and per field**,
so 0.975 is only a default; and the whole feature is **deprecated**.

**It belongs in a column headed "auto-accept threshold (default, configurable)".
Never in an accuracy column.**

### 8.2 The four backends

| Field | **Rossum** | **Veryfi** | **Mindee** | **Affinda** |
|---|---|---|---|---|
| **Official URL** | `rossum.ai/` | `veryfi.com/invoice-ocr-api/` | `mindee.com/product/invoice-ocr-api` | `affinda.com/documents/invoice/` |
| **API docs** | `elis.rossum.ai/api/docs/` | `docs.veryfi.com` | `docs.mindee.com` (V2) | `docs.affinda.com` |
| **Status** | Active | Active | V2 current; V1 legacy | **Invoice-only endpoint marked `deprecated: true`**; folded into unified `POST /v3/documents` |
| **Input types** | PDF, PNG, JPEG, TIFF, XLSX/XLS, DOCX/DOC, RTF, HTML, TXT | 20+ incl. pdf, jpg, png, webp, tiff, heic, docx, xlsx, csv, eml, zip | JPEG, PNG, WebP, TIFF, HEIC/HEIF, all non-encrypted PDF | PDF, DOC, DOCX, TXT, RTF, HTML, PNG, JPG, TIFF, ODT, XLS, XLSX |
| **Max size** | 40 MB | **20 MB** (min 250 bytes) | **100 MB** | **20 MB** |
| **Max pages** | Default **32/queue**; max 50,000 fields/doc; min 6pt font, 150 DPI | **15** at once, expandable via support | **Unlimited** on paid (10 on free trial). *Marketing page says "up to 200"; docs win.* | **20** default |
| **API shape** | **Async + polling.** `POST /v1/uploads?queue={id}`, poll annotation. Bearer token, ~162h life | **Sync by default**, async via `async=true`. Two headers (`CLIENT-ID` + `AUTHORIZATION: apikey`), plus HMAC-SHA256 request signature valid 30 min | **Async only** — "no synchronous routes are provided". Enqueue → poll job → results. Jobs time out at **590s**. Header `Authorization`, raw key, **no `Bearer` prefix** | **Both** — `wait` param, default `true`. Bearer token, max 3 keys |
| **Per-field confidence** | **Yes, always** — `rir_confidence` per datapoint | **Opt-in** via `confidence_details=true` | **Opt-in** via `confidence` param — and **returns empty rather than erroring when off** | Yes on **deprecated v2**; **unconfirmed on v3** |
| **Bounding boxes** | Yes — `rir_position`, plus `page_data` | **Opt-in** via `bounding_boxes=true` | **Opt-in** via `polygon`; `locations[]` with zero-based `page` | Yes on v2 (`rectangle` with `pageIndex`) |
| **Price** | **Only one number published: "Starting at $18,000 per year"** (Starter). Business/Enterprise/Ultimate contact-sales. Billed **per page**, including pages with no captured data | **Most transparent.** Free 100 docs/mo. Starter **$500/mo minimum** ("buys you <5k docs"). Per-doc: **invoices $0.16**, receipts $0.08, bank statements $0.25 | Starter **$44/mo** (annual $529), Pro **$116/mo** (annual $1,393), 6,000→300,000 credits. **≈$0.044/credit**; a credit = one **physical page**. Enterprise contact-sales | **No dollar figure anywhere. Contact sales.** Billing unit is pages |
| **Rate limits** | **10 req/s** overall (10/min on translate). 429 + `Retry-After` | **POST document 60 RPS**, GET list 5 RPS. 429 + `retry-after` | **POST 200/min, GET 1200/min.** Concurrency not stated | **20 documents/minute** via priority queue |
| **Data retention** | No numeric default published. `Deleted` and `Purged` statuses; purge is final. Trust page offers "flexibility to define… retention periods". **No documented zero-retention flag, no default period stated** | "Veryfi only stores the data it needs to function properly — for as long as you want Veryfi to function for you." DELETE endpoint exists. "Customizable retention policies" with **no timeframes**. **No zero-retention mode documented** | **Best documented.** Default **12 hours**, configurable **1–24 hours**, auto-deleted on expiry. Near-zero mode: **"Delete extracted data when fetched"** deletes immediately after successful retrieval. **Conflict: the DPA Appendix 1 still says 7 days for async APIs — two official documents disagree** | **Default is indefinite:** "documents uploaded to Affinda will be retained indefinitely". Zero-retention **is documented** — `deleteAfterParse`, but **"Only compatible with requests where wait: True"** (forces synchronous) |
| **Data location** | AWS Frankfurt, Ireland, us-east-1, us-west-1, Tokyo, Osaka | Default **US AWS Oregon**; **EU (Ireland, Germany) on request by email** | Zones: "No Preference", "Europe", "United States". **Zone selection not available on Starter** | Australia, US, EU endpoints — **but the privacy policy says data "may be used, processed or stored anywhere in the world"** |
| **India region** | **No** | **No** | **No** | **No** |
| **On-premise** | Single-tenant with dedicated DB as a commercial option; true on-prem not stated | Not stated ("Dedicated VPC" on GOLD plan) | **Not offered** in any current official doc | "Dedicated tenants", "private cloud deployments" for enterprise; true on-prem not stated |
| **Training on your documents** | Only: "flexibility to define… usage for AI training purposes." Full Terms are Google Drive links, **clause text not verified** | **Permits it, verbatim:** "we may use data you submit…to train, validate, and improve our proprietary machine learning models." Also: "We do not use your data to train third-party or 'generative' AI models" | **Strongest no-training language:** "Mindee will not use the Incoming Data to train its models." Carve-outs: anonymised data for R&D, and a **default-on** right to reuse your **corrections** "for analysis and improvement", with no documented opt-out | **No policy-level statement at all for invoices.** The only "never used to train" line is on the *resume-parser* marketing page |
| **Security** | SOC 2 Type 2; **ISO/IEC 27001:2022**; **ISO/IEC 42001:2023**; TX-RAMP L1 | SOC 2 Type 2; HIPAA + BAA on request; CCPA, PHIPA, ITAR. **ISO 27001 not listed** | SOC 2 claimed in docs + DPA; EU SCCs. **ISO 27001 not stated; no trust centre, no SOC 2 report link** | SOC 2 Type 1 & 2; **ISO/IEC 27001:2022**; HIPAA; trust centre |
| **Error contract** | 400, 401, 403, 404, 405, 409, 429 (+`Retry-After`), 500, 503 | **11 documented codes with per-message guidance**; `retry-after` on 429; no backoff schedule | 400, 401, 402, 403, 404, 422, 429, 500. Retry guidance thin: "Wait a few seconds and try again." **No `Retry-After` documented** | **18 named errors** (`file_too_large`, `password_protected`, `capacity_exceeded`, `parsing_failed_timeout`…). **No HTTP status table, no idempotency key** |
| **Published SLA** | **"99.9% uptime"**; ">90% of documents in under 5 minutes" | **None published.** Support tiers only | Docs publish none. **MSA defines an Availability Rate with a penalty table: 5% of monthly fees for "<99.9% and ≥99%", up to 15%** | **None published.** "Custom SLAs… " is a paid add-on |
| **Status page** | `status.rossum.ai` | `status.veryfi.com` | `status.mindee.com` (100.0% / 90 days) | `status.affinda.com` (API 99.99% / 90 days) |
| **Line items** | Yes (10 fields) | Yes (20+ fields) | Yes (8 fields) | Yes (`tables`) |
| **Exit cost** | **Highest here** — queue/annotation model, async polling, and an annual floor commitment | Medium — conventional REST, per-doc billing, easy to stop | Medium — async-only enqueue/poll to unwind | Medium-high — v2→v3 migration is already forced on you |

### 8.3 GST / India — precise, and the answer is no

| | GST | GSTIN | HSN | CGST/SGST/IGST | India named |
|---|---|---|---|---|---|
| **Rossum** | Only as an **example string** in the Tax Code field description: *"e.g. GST, CGST, DPH, TVA"* | No | No | Only inside that example string | No |
| **Veryfi** | Yes — financial field listed as **"Tax (VAT, GST)"** | **No** — tax IDs are VAT Number, ABN Number, Vendor Registration Number | **Yes — documented line-item field** ("Harmonized System Code/Number") | No | No |
| **Mindee** | Via GSTIN enum only | **Yes, as an enum value only** in `company_registration.type`. It labels a captured number; it does not validate or parse it | No | No | No |
| **Affinda** | **Once**, as a label synonym: "Tax Amount / GST / VAT" | No | No | No | No. Governing law is Victoria, Australia |

**None of the four has real Indian GST support.** Only **Veryfi** documents an
**HSN** field. Only **Mindee** can *label* a registration number as a GSTIN.
**Nobody splits CGST / SGST / IGST** — Mindee's `taxes[]` rows are untyped
`{rate, base, amount}`, so a three-way GST split arrives as three
indistinguishable rows, and Affinda's banking primitives (`bankBsb`,
`bpayBillerCode`) are Australian.

### 8.4 Accuracy claims — all marketing

| | Claim | Label |
|---|---|---|
| Rossum | "92.5% average accuracy across use cases and industries"; per-customer case studies (92.6%, 90%, 71% STP) | **MARKETING** — case studies, no methodology, no test set, no definition of "correct" |
| Veryfi | "Extract 99%+ accurate data from any document type"; "99.9%" in one vertical | **MARKETING** |
| Mindee | "Accuracy of our invoice API is generally above 95% for most fields", computed weekly on a dataset "spanning 50+ countries" | **MARKETING** — dataset unnamed and unpublished. Closest to a method statement of any vendor here, and still not checkable |
| Affinda | "over 99% accuracy"; case-study figures of 95/82/76/60/30% | **MARKETING** — and the case-study figures are manual-work reduction and STP rates, **not extraction accuracy** |

### 8.5 Five things to know before committing to any of these

1. **Confidence scores are opt-in on Veryfi, Mindee, and effectively Affinda v3.**
   For a pipeline that must route low-confidence fields to a person — which is
   what `per_field_source` exists for — this is the load-bearing feature.
   Mindee charges **unpublished extra credits** for it and returns *empty*
   rather than erroring when it is off. That is a silent-failure trap.
2. **Two vendors contradict themselves in their own paperwork.** Mindee: DPA
   says 7-day retention, MSA and docs say 1–24 hours. Affinda: residency page
   names three regions, privacy policy says "anywhere in the world".
3. **Veryfi's privacy policy permits training on your documents** for its own
   models, despite marketing ("no humans in the loop", "we never sell your
   data") that reads otherwise. Note also that the Shield page claims **no HITL
   at all** while the security page hedges to "no humans in the loop *gaining
   unauthorized access*" — a materially weaker statement.
4. **Affinda's default is indefinite retention**, and the opt-out
   (`deleteAfterParse`) forces synchronous calls.
5. **Only Veryfi and Mindee let you build a cost model from public pages.**
   Rossum publishes a single **$18,000/year** floor; Affinda publishes nothing.

### 8.6 The cheapest test that settles the accuracy question

Since no vendor publishes a usable benchmark, **the free tier is the benchmark.**
Run one identical set of real Indian GST purchase bills through each and score
the fields yourself: Veryfi 100 docs/month free; Mindee 200 credits / 14 days;
Affinda 200 credits / 2 weeks; Rossum free trial. This is the cheap experiment
that fails in the same place as the expensive one.

---

## 9. The hyperscalers — and the only India regions in this whole document

Everything in this section I fetched and read myself, from official vendor
documentation, on **2026-08-10**. Where a figure is missing, it is missing
because the page did not render or was truncated, and it says so.

**This is where the data-residency answer changes.** Every dedicated OCR vendor
in sections 7 and 8, and the vision-model option in section 5, has **no India
region**. **All three hyperscalers have one** — and each charges a different,
non-obvious price for using it.

### 9.1 Google Document AI — Invoice Parser

| Field | Record |
|---|---|
| **Official URL** | `https://docs.cloud.google.com/document-ai/docs/processors-list` |
| **Locations** | Multi-region: `us`, `eu`. Single-region ("limited support"): **`asia-south1` (Mumbai)**, `asia-southeast1` (Singapore), `australia-southeast1` (Sydney), `europe-west2` (London), `europe-west3` (Frankfurt), `northamerica-northeast1` (Montréal). |
| **India region for the Invoice Parser specifically** | **YES.** The processor list states the Invoice Parser supports: *"asia-south1, asia-southeast1, australia-southeast1, eu, northamerica-northeast1, us"*. I checked the processor-level list, not just the general region list, because "limited support" means capabilities vary by processor. **The Invoice Parser is one of the processors that runs in Mumbai.** |
| **Data location** | *"You must specify either a regional or multi-regional location for data storage and document processing."* |
| **Per-field confidence** | **Yes** — every entity carries a `confidence` float. Example from the docs: `{"type": "invoice_date", "confidence": 0.9938466, ...}` |
| **Normalized values — the detail that matters for this adapter** | Entities carry `normalizedValue` with **typed** output, e.g. `"dateValue": {"year": 2020, "month": 1, "day": 1}`. That maps onto `ExtractedRecord.date` without string-parsing a date format. Money fields normalise similarly. This is a genuinely better fit for a contract that demands exact typed values than a vendor returning display strings. |
| **Bounding boxes** | Yes — `textAnchor` with `textSegments`, plus `pageAnchor` |
| **Fields returned** | Invoice identifiers; total, net and tax amounts; currency; payment terms; supplier and receiver names, addresses, contacts, **tax IDs**, websites; delivery dates; **VAT breakdowns**; freight; PO references. **Line items: yes** — structured `line_item` entries with `line_item/amount`, `line_item/quantity`, description, unit price. |
| **GST / GSTIN / HSN** | **Not documented as named fields.** The field list has generic "tax IDs" and "VAT breakdowns". A supplier GSTIN would likely land in a generic tax-ID field, and **HSN has no home at all**. No official page names India or GST as a supported use case. |
| **Price — and this is the headline** | **$0.10 per "count", where 1 count = up to 10 pages.** It is billed **per document, not per page**. A one-page GST invoice therefore costs **$0.10 → $100 per 1,000 invoices**, roughly **10x AWS and Azure**. No free tier, no volume break. Not billed for 4xx/5xx. *(Widely-quoted "$30/1,000 pages" figures are the Custom Extractor / Form Parser row, not the Invoice Parser.)* |
| **Rate limits** | 120 online requests/min per project per processor type in `us`/`eu` — but **only 6 requests/min for single-region endpoints**, which includes Mumbai. 5 concurrent batch jobs per project. |
| **SLA — and the India catch** | 99.9% for online and batch, with 10/25/50% credits. **But the SLA defines a Covered Service as one "configured with a multi-region endpoint."** Running in `asia-south1` means **no SLA at all**. |
| **Data retention** | Batch: stored "encrypted with an ephemeral key, meaning that no human has access to it… typically deleted immediately after the processing, with a failsafe Time to live (TTL) of one day." **Online: "processed in memory, encrypted in flight, and not persisted to disk."** No named zero-retention toggle, because online is effectively no-disk-persistence by default. |
| **Training on your documents** | **The clearest "no" of the three:** *"At Google Cloud, we never use customer data to train our Document AI models."* |
| **Security** | VPC Service Controls, CMEK, Access Transparency, data residency controls, Deny policy. ISO 27001/27017/27018, SOC 2, SOC 3, PCI DSS, FedRAMP High, HIPAA. |
| **Size / page limits** | Online 40 MB / **15 pages**; batch 1 GB / 200 pages / 5,000 files per request. *(The pricing page says >10 pages is unsupported synchronously while the processor list says 15 — Google contradicts itself.)* |
| **Errors** | **No Document AI-specific error-code page exists.** Standard Google API error model only. Weaker than Azure or Anthropic for building a retry contract. |
| **Exit cost** | Medium — proprietary entity/normalizedValue shape, but conventional REST and the field semantics are ordinary. |

### 9.2 AWS Textract — AnalyzeExpense

| Field | Record |
|---|---|
| **Official URL** | `https://docs.aws.amazon.com/general/latest/gr/textract.html` |
| **India region** | **YES — `ap-south-1` (Asia Pacific, Mumbai)**, endpoints `textract.ap-south-1.amazonaws.com` and `textract.ap-south-1.api.aws`. |
| **Price** | **AnalyzeExpense: $10.00 per 1,000 pages** for the first 1M pages/month, then **$8.00 per 1,000** — i.e. **$0.01/page**. (For contrast: Detect Document Text $1.50/1,000; Analyze Document Forms $50.00/1,000.) Prices are quoted for US West (Oregon); **the page does not state whether they vary by region** — verify for Mumbai before budgeting. |
| **Rate limits — and the India penalty** | This is the catch, and it is significant. Synchronous **AnalyzeExpense: 5 TPS in us-east-1 and us-west-2, but only 1 TPS in Asia Pacific (Mumbai)**. Async `StartExpenseAnalysis`: 5 TPS in Virginia/Oregon, **1 TPS in Mumbai**. `GetExpenseAnalysis`: 5 TPS everywhere. Max simultaneous async jobs: **600 in Virginia/Oregon, 100 in Mumbai**. **Choosing India costs you 5x the throughput ceiling.** At 1 TPS that is a hard ceiling of 86,400 pages/day before quota increases. |
| **Price in Mumbai** | **`UNVERIFIED`, added 2026-08-10 — and it contradicts the row above.** The claim as written was "Identical to US — no India premium: $0.01/page first 1M/month, $0.008/page above; free tier 100 pages/month for 3 months." The **Price** row two lines up says the published figures are quoted for US West (Oregon) and that *"the page does not state whether they vary by region — verify for Mumbai before budgeting."* Both cannot be true. Nothing in this document shows a Mumbai price actually read from a region-selected pricing page. The number is kept, struck as unverified, and **not** replaced by a guess. Same caution applies to the "India price premium: None" cells for AWS in sections 9.4 and 11.5, which restate it. **Closing it costs one page load:** open the Textract pricing page with the region selector set to Asia Pacific (Mumbai). |
| **Size / page limits** | Sync: 10 MB, and **PDF/TIFF is 1 page only**. Async: JPEG/PNG 10 MB; PDF/TIFF **500 MB / 3,000 pages**; **S3 object only** (no direct bytes). JobId valid 7 days. |
| **Per-field confidence** | **Yes** — `Confidence` 0–100 on Type, LabelDetection and ValueDetection |
| **Bounding boxes / provenance** | **Yes** — `Geometry{BoundingBox, Polygon, RotationAngle}` on every detection, plus `PageNumber` and full `Blocks[]` |
| **GST / GSTIN / HSN — the standout** | **AWS is the only backend in this entire document with named GST fields.** `VENDOR_GST_NUMBER`, `RECEIVER_GST_NUMBER`, and Indian PAN: `VENDOR_PAN_NUMBER`, `RECEIVER_PAN_NUMBER`. **But it stops there — no `CGST`, `SGST`, `IGST` or `HSN` field types exist.** You get the GST registration number, not the tax split or product codes a GST return needs. India is not named as a supported use case in prose, and no Indian language is supported. |
| **Fields** | 45 summary fields incl. `INVOICE_RECEIPT_DATE`, `INVOICE_RECEIPT_ID`, `VENDOR_NAME`, `RECEIVER_NAME`, `TOTAL`, `SUBTOTAL`, `TAX`, `AMOUNT_DUE`, `PAYMENT_TERMS`. Line items: `ITEM, QUANTITY, PRICE, UNIT_PRICE, PRODUCT_CODE, EXPENSE_ROW`. **Line items: yes.** |
| **Retention / training — the biggest risk item here** | Verbatim from the AI services opt-out policy: *"AWS AI services may use and store customer content for service improvement, such as fixing operational issues, evaluating service performance, debugging, **or model training**. For this purpose, **we might store such content in an AWS Region outside of the AWS Region where you are using the service.**"* Textract is opt-out-eligible **via an AWS Organizations opt-out policy**. **Default behaviour therefore defeats the whole point of choosing `ap-south-1`** — without filing that policy, Indian invoices may be used for training and stored outside India. Documented zero-retention exists only for Custom Queries adapter training, not for AnalyzeExpense calls. |
| **Errors** | `AccessDeniedException`(400), `BadDocumentException`(400), `DocumentTooLargeException`(400), `InvalidParameterException`(400), `InvalidS3ObjectException`(400), `ProvisionedThroughputExceededException`(400), `UnsupportedDocumentException`(400), `InternalServerError`(500), `ThrottlingException`(500) |
| **SLA** | Credit table: <99.9% and ≥99% → 10%; <99% → 25%; <95% → 100%. **The Service Commitment sentence itself contains no percentage** — 99.9% is only inferable from where credits begin. Last updated 2022-05-05. |
| **Security** | HIPAA eligible, SOC, ISO, PCI (PCI workloads must opt out via Support). SSE-S3 / SSE-KMS with customer-managed CMK. **PrivateLink VPC endpoint** `com.amazonaws.<region>.textract`, supports all API actions. |
| **Exit cost** | Medium — AWS-specific response shape and SigV4 signing. **SigV4 is hard to implement from `urllib.request` alone**, which matters given the stdlib-only rule in section 1. |

### 9.3 Azure AI Document Intelligence — prebuilt-invoice

| Field | Record |
|---|---|
| **Official URL** | `https://learn.microsoft.com/en-us/azure/ai-services/document-intelligence/language-support/prebuilt` |
| **India support at the model level** | **The strongest documented India signal of any backend in this document.** The `prebuilt-invoice` supported-languages table lists **English (`en`) → "United States (`us`), Australia (`au`), Canada (`ca`), United Kingdom (-uk), India (-in)"**, and the supported-currency table lists **`INR` — Indian Rupee (`in`)**. India is named, explicitly, in the model's own documentation. No other vendor does this. |
| **GST / GSTIN / HSN** | **Still not named.** India as a *locale* and INR as a *currency* is not the same as GSTIN and HSN as *fields*. Do not conflate them. |
| **Input types** | PDF; images JPEG/JPG, PNG, BMP, TIFF, HEIF. (Office DOCX/PPTX/XLS are supported by Read and Layout but **not by prebuilt models**.) |
| **Size / page limits** | Free (F0): **4 MB**, **2 pages**. Standard (S0): **500 MB**, **2,000 pages**. Neither is adjustable. |
| **Rate limits** | S0 defaults, all adjustable by support ticket: **Analyze 15 transactions/sec**, Get operations **50/sec**, model management 5/sec, list 10/sec. F0 is 1/sec across the board. |
| **Documented outage / error behaviour** | Good. **429 (Too many requests)** is documented per-quota, the `analyze` response carries a **`retry-after` header**, and Microsoft publishes explicit backoff guidance: *"a 2-5-13-34 pattern of delays between requests"* and *"we recommend not calling the get analyze response more than once every 2 seconds"*. That is a contract an adapter can actually implement against. |
| **India region availability** | **Yes — Central India**, confirmed on the pricing region selector (`central-india: 10.0`, enabled). South India / West India / India South Central are listed but **disabled**. So India residency is available, but on **one region only** — worth noting for disaster recovery. |
| **Price** | **$10.00 per 1,000 pages, flat**, for all prebuilt models including Invoice. **No volume tier break.** **`central-india` is priced the same as the US** — no India premium. F0 free tier: 500 pages/month. US Gov +25%. |
| **API shape** | **Async only.** `POST …/documentModels/prebuilt-invoice:analyze` returns **202** with `Operation-Location` and a `Retry-After` header; you then poll GET. Body takes `base64Source` or `urlSource`. |
| **Per-field confidence** | **Yes** — `confidence` per field and per word |
| **Bounding boxes / provenance** | **Yes** — `boundingRegions` with `polygon`, plus `spans` (offset/length) |
| **Data retention — the clearest of the three** | FAQ, verbatim: *"**Does Document Intelligence store my data? Yes, briefly.** For all features, Document Intelligence temporarily stores data and results in Azure Storage in the same region as the request. Your data is then deleted **24 hours** from the time that you submit an analyze request."* You can delete earlier via the **Delete Analyze Result API**. **No zero-retention mode** — shortening the 24h window is the only lever. |
| **Data location** | *"The incoming data is processed in the same region where the Document Intelligence resource was created."* |
| **Training on your documents** | **NOT STATED.** No explicit "we do / do not use your data to train models" sentence was found on any official Microsoft page for this service. The 24-hour deletion policy *implies* it but never says it. **This is a genuine gap, not a "no"** — and it is the thing to put in writing before signing. |
| **Fields** | Microsoft has **moved the field list off learn.microsoft.com** to a GitHub sample repo schema. It includes `InvoiceId, InvoiceDate, DueDate, VendorName, VendorAddress, VendorTaxId, CustomerName, CustomerTaxId, SubTotal, TotalDiscount, TotalTax, InvoiceTotal, AmountDue, PaymentTerm, PaymentDetails[]{IBAN, SWIFT, …}, TaxDetails[]{Amount, Rate}`, and `Items[]{Amount, Date, Description, Quantity, ProductCode, Tax, TaxRate, Unit, UnitPrice}`. **Line items: yes, and the richest of the three — it carries per-line `Tax` and `TaxRate`.** |
| **GST — none, but a telling detail** | `GST`, `GSTIN`, `HSN`, `CGST`, `SGST`, `IGST`, `PAN` all return **0 hits** in the schema. But the schema *does* carry **`KVKNumber`**, a Netherlands-specific business registration ID — which proves Microsoft adds country-specific fields when it chooses to. **It simply has not added an India one.** |
| **SLA** | **Not retrieved.** Both candidate SLA URLs 404 and redirect to a JavaScript licensing library. A search result claimed 99.9% with 10/25% credits; the primary source was not read, so it is **not reported as verified**. |
| **Security** | At rest: *"Data is encrypted and decrypted using FIPS 140-2-compliant 256-bit AES encryption."* CMK supported for resources created after 2020-05-11. Private Link / private endpoints, VNet service endpoints, firewall + IP allowlist, configurable default-deny public access. **Per-service certification list not confirmed** — only platform-wide Azure compliance pages were found. |
| **Exit cost** | Medium — Azure-specific resource/key model and its own response schema. Auth is a simple subscription-key header, which is **stdlib-friendly** (unlike AWS SigV4). |

### 9.4 What the hyperscalers change

**All three run in India. Each charges a different hidden price for it.**

| | **Google Document AI** | **AWS Textract** | **Azure Document Intelligence** |
|---|---|---|---|
| India region | `asia-south1` Mumbai | `ap-south-1` Mumbai | **Central India only** |
| Price per 1,000 one-page invoices | **$100** ($0.10/document, 1 count = up to 10 pages) | **$10** ($0.01/page) | **$10** ($10.00/1,000 pages, flat) |
| India price premium | None — **`UNVERIFIED`**, no regional price was read for Google either | **None** — **`UNVERIFIED`**, see the "Price in Mumbai" row in 9.2; the pricing page quotes Oregon and does not state whether prices vary by region | **None** — sourced: the pricing region selector shows `central-india: 10.0`, the same as the US |
| **The India catch** | **No SLA at all** — the SLA covers multi-region endpoints only. And single-region quota is **6 requests/min** vs 120. | **1 TPS vs 5** (5x throttle), 100 concurrent async jobs vs 600 | Only **one** India region — no in-country DR pair |
| Retention | Online: not persisted to disk. Batch: 1-day TTL failsafe. | **Default permits use for training and storage outside your region** unless you file an AWS Organizations opt-out | **24 hours, in-region, documented**, with a Delete API |
| Trains on your data? | **"we never use customer data to train our Document AI models"** | **Yes by default** — opt-out required | **Not stated.** Genuine gap |
| GST fields | None | **`VENDOR_GST_NUMBER`, `RECEIVER_GST_NUMBER`, PAN** | None (but ships a Netherlands `KVKNumber`) |
| India locale | **No Indian locale at all** | No Indian language | **`en-IN` listed, `INR` supported** |
| Error contract | **No service-specific error page** | 9 named exceptions | 9 HTTP + 9 inner codes, `Retry-After`, published backoff pattern |

**The four things that matter:**

1. **Google is 10x the price of the other two for one-page invoices**, because it bills per document, not per page. At any real volume that difference dominates every other consideration. It is also the only one that **forfeits its SLA** in India and throttles single-region endpoints to **6 requests/minute**.
2. **AWS is the only backend anywhere in this document with named GST fields** — `VENDOR_GST_NUMBER`, `RECEIVER_GST_NUMBER`, and Indian PAN. That is a real advantage for a GST product.
3. **But AWS's default undoes the reason you chose India.** Its AI services policy permits using your content for model training and **storing it in a region outside the one you are using**, unless you file an AWS Organizations opt-out policy. Choosing `ap-south-1` without that policy buys you the illusion of residency, not residency.
4. **Even AWS stops short.** `GST_NUMBER` is the registration number. **No backend anywhere in this document returns a CGST/SGST/IGST split or an HSN code.** Every option needs custom GST post-processing. Budget for it regardless of vendor.

---

## 10. Self-hosting, and the Indian legal position

### 10.1 Self-hosting is excluded from this package by construction

This is settled by the repo, not by research. `tests/test_no_reader.py` allows
`accountant/extract/` to import **stdlib and `accountant.*` only**. Tesseract,
PaddleOCR, docTR and every other OCR library requires a dependency. **None of
them can live in this package.**

Self-hosting is not thereby ruled out for the *product* — but it would have to
run as a **separate process behind an HTTP boundary**, which is a second system
to build, secure, monitor and operate. That is a materially larger commitment
than calling an API, and it should be costed as one.

There is a second, more fundamental problem with the open-source route:

| What the tool gives you | What the adapter needs |
|---|---|
| Tesseract, PaddleOCR, docTR are **OCR engines**: they return text and, at best, boxes | `ExtractedRecord` needs **fields** — *which* number is the total, *which* is the tax |
| The field-picking is left to you | **`accountant/extract/` is forbidden from doing field detection.** That is the entire point of "we write an adapter, never a reader" |

So the open-source route does not just add operational burden — it puts the
work on the wrong side of the boundary the project has deliberately drawn.

### 10.1a The open-source options, verified

| Tool | Licence (SPDX) | Outputs invoice **fields**? | Per-field confidence | GPU needed |
|---|---|---|---|---|
| **Tesseract** | **Apache-2.0** | **No** — text only (plain text, hOCR, PDF, TSV, ALTO, PAGE) | Per **word** (`conf` column in TSV), not per field | No |
| **docTR** (Mindee) | **Apache-2.0**, clean | **No — and `kie_predictor` is a trap.** Every pretrained detector has exactly one class, `"words"`. Multi-class needs you to label and train | Two per **word** (`confidence`, `objectness_score`) | No |
| **PaddleOCR** | **Apache-2.0 at the top level — but see below** | **No** (in the Apache parts). Layout labels are `text`/`table`/`doc_title`, i.e. geometry, not `invoice_total` | Per text-line and per layout region; **none at field level** | Optional |

**Two PaddleOCR traps worth naming explicitly:**

1. **The KIE (key information extraction) component — the exact part that does
   field extraction — is licensed CC BY-NC-SA 4.0, not Apache-2.0.** Verbatim
   from its README: *"The content of this project itself is licensed under the
   Attribution-NonCommercial-ShareAlike 4.0 International (CC BY-NC-SA 4.0)."*
   **The one piece you would actually want is the one piece you may not use
   commercially.**
2. **PP-ChatOCRv4 sends your documents to Baidu's cloud by default** — it
   requires an LLM API key and defaults to `"base_url": "https://qianfan.baidubce.com/v2"`.
   **That is disqualifying for a "nothing leaves the machine" baseline**, and it
   is easy to miss.

**Invoice-field models — three of the most-recommended are non-commercial:**

| Model | Licence | Commercial? | Named invoice fields | Per-field confidence |
|---|---|---|---|---|
| `microsoft/layoutlmv3-base` / `-large` | **`cc-by-nc-sa-4.0`** | **NO** | No | Not documented |
| `microsoft/layoutlmv2-base-uncased` | **`cc-by-nc-sa-4.0`** | **NO** | No | Not documented |
| `impira/layoutlm-invoices` | **`cc-by-nc-sa-4.0`** | **NO** | Yes | Yes |
| `microsoft/layoutlm-base-uncased` (v1) | `mit` | Yes | No | Not documented |
| `naver-clova-ix/donut-base-cord-v2` | `mit` | Yes | **Receipt fields only** — `menu`, `sub_total`, `total`. **No vendor, no invoice number, no invoice date, no tax field.** Training used "64 A100 GPUs (~2.5 days)" | Not documented |
| **`impira/layoutlm-document-qa`** | **`mit`** | **Yes** | **Yes, via questions** | **Yes** |

**🚨 The near-miss trap:** `impira/layoutlm-invoices` (**non-commercial**) and
`impira/layoutlm-document-qa` (**MIT**) have near-identical model cards. Picking
the wrong one is a licence breach that looks like a typo.

**The one permissive option that actually returns fields** is
`impira/layoutlm-document-qa` — MIT, no fine-tuning, and the only permissive
model here with documented per-field confidence. Its own card shows
`nlp(<invoice>, "What is the invoice number?")` → `{'score': 0.9943977, 'answer': 'us-001'}`.
Open risk: it was fine-tuned on DocVQA, whose commercial terms are not published.

**Bottom line on self-hosting:** Tesseract, docTR and Apache-PaddleOCR give you
text and boxes and leave **100% of the field logic to you** — on the wrong side
of the adapter/reader boundary. Per-field confidence, which
`per_field_source` needs, exists in **exactly one** permissive option.

### 10.2 The Indian legal position — from the official texts

Three of the four questions are now answered from primary sources. The fourth
is the one that matters most and could not be retrieved.

**1. DPDP Act 2023, Section 16 is a BLOCKLIST, not a whitelist.**

Source: *THE DIGITAL PERSONAL DATA PROTECTION ACT, 2023 (No. 22 of 2023)*,
official MeitY PDF, `https://www.meity.gov.in/static/uploads/2024/06/2bf1f0e9f04e6fb4f8fef35e82c42aa5.pdf`,
retrieved 2026-08-10. Verbatim:

> **16.** (1) The Central Government may, by notification, restrict the transfer of personal data by a Data Fiduciary for processing to such country or territory outside India as may be so notified.
>
> (2) Nothing contained in this section shall restrict the applicability of any law for the time being in force in India that provides for a higher degree of protection for or restriction on transfer of personal data by a Data Fiduciary outside India…

**This corrects a widespread error.** Many summaries say transfer is permitted
*only* to countries notified as adequate. **That was the 2019 Bill. The enacted
text is the opposite:** transfer is permitted by default, and the Government
*may* restrict it to specifically notified countries. No such restricting
notification was found. **DPDP imposes no blanket bar on sending data abroad.**

**Timing caveat:** the DPDP Rules 2025 were notified 14 November 2025 with
*"an eighteen-month period for phased compliance"* (official PIB document),
which lands around May 2027. Section 16 is **probably not yet in force**, but
the section-by-section commencement notification could not be retrieved from an
official source. Treat as probable, not verified.

**2. Are GST purchase invoices "personal data"? Genuinely ambiguous — and the
convenient answer is wrong.**

- s.2(t): *"'personal data' means any data about an individual who is identifiable by or in relation to such data"*
- s.2(j) defines the Data Principal as *"the individual to whom the personal data relates"* — an **individual**, not a company.
- **But** s.3(a)(ii) applies the Act to data collected *"in non-digital form and digitised subsequently"* — **scanning a paper invoice brings it in scope.**

A pure company-to-company invoice is arguably not personal data. **Real invoice
piles are not pure**: sole proprietors and partnerships trading under a personal
name, named contacts, named consignees, signatories, e-way-bill driver names.
**Do not accept "GST invoices aren't personal data, so DPDP is irrelevant."**

`Assumption: a meaningful fraction of a real invoice corpus contains personal data · Confidence: 85% · Check: sample 100 real invoices and count how many name an individual.`

**3. GST record retention is 72 months — and the clock does not start where you
think.**

Source: **CGST Act 2017, Section 36**, official CBIC portal,
`https://taxinformation.cbic.gov.in/content/html/tax_repository/gst/acts/2017_CGST_act/active/chapter8/section36_v1.00.html`, retrieved 2026-08-10:

> **Section 36. Period of retention of accounts.—** Every registered person… shall retain them until the expiry of **seventy-two months from the due date of furnishing of annual return** for the year pertaining to such accounts and records…

Plus a proviso: if a party to an appeal, revision, proceedings or investigation,
records must be kept **one year after final disposal**, or the 72 months,
**whichever is later**.

**72 months = 6 years, but measured from the annual-return due date** (31 December
following the financial year), so in practice ~6 years 9 months past the end of
the FY. **The proviso means litigation can extend it indefinitely — a retention
design needs a legal-hold flag, not just a timer.**

**4. Does any Indian rule require accounting/GST data to be stored inside India?**

| Rule | Verdict |
|---|---|
| **GST law — CGST s.35(1) + Accounts and Records Rules, Rule 2** | **NO server-location requirement.** s.35(1) requires records "at his principal place of business" but expressly permits keeping them "in electronic form in such manner as may be prescribed"; the prescribed manner is a **backup + on-demand production** duty: *"Proper electronic back-up of records shall be maintained and preserved…"* and *"shall produce, on demand, the relevant records… in hard copy or in any electronically readable format."* **No words requiring the server to sit in India.** |
| **RBI payment-data localisation** (RBI/2017-18/153, 6 April 2018) | **Real, but does NOT apply.** *"All system providers shall ensure that the entire data relating to payment systems operated by them are stored in a system only in India."* Addressed to authorised payment systems and banks under the Payment and Settlement Systems Act 2007. **An accounting tool reading purchase invoices is not a payment system provider. Do not let this be cited as an accounting-data rule.** |
| **⚠️ Companies (Accounts) Rules 2014, Rule 3(5)** | **NOT RETRIEVED — and this is the load-bearing one.** `mca.gov.in` returns HTTP 403 to every automated request. The consistently-reported claim: companies keeping books in electronic mode must keep a **backup on servers physically located in India**, changed from "periodic" to **"daily"** by the 2022 Fourth Amendment. Every readable source for this was a law-firm blog, so **it is not asserted here.** |

**Note the precise shape of the one rule that may bite**, if it is accurate: it
binds **companies under the Companies Act** (not proprietorships or
partnerships), it requires a **backup in India**, and it does **not prohibit
processing abroad**. *A backup-in-India obligation is satisfied by a nightly
copy to an Indian bucket — not by refusing to use a cloud API.*

**Owner action to close it:** have the CA pull Rule 3 of the Companies (Accounts)
Rules 2014 as currently amended, or open
`https://www.mca.gov.in/content/mca/global/en/acts-rules/ebooks/notifications.html`
in a normal browser and download the 5 August 2022 notification.

### 10.3 The uncomfortable summary on residency

On the official texts that could actually be read: **DPDP s.16 permits
cross-border transfer by default and is probably not in force until 2027; GST
law has no server-location rule; RBI localisation does not apply.** The one
genuine India-storage rule is a **Companies Act backup requirement** that could
not be verified — and even as reported, it asks for a backup in India, not for
processing to stay in India.

**Most things called constraints are assumptions in costume.** Data residency
in this project has not been shown to be a legal constraint. It may still be the
right *trust* position — but it should be argued and priced as a choice, and
this document now shows the price: about **$0.01/page instead of $0.0046/page**,
plus a 5x throughput cut on AWS. If self-hosting is the answer, the honest
arguments for it are **cost, latency and lock-in — not compliance.**

---

## 11. The five cross-cutting answers

Indian SMB products (section 6) are excluded from these tables — with no public
API they are not candidates. "Not retrieved" means I did not read it; it is a
gap to close, not a "no".

### 11.1 Which backends process Indian GST invoices as a *documented* use case

**None of them.** Not one documents GSTIN, HSN, and a CGST/SGST/IGST split as
first-class fields. Ranked by how close they get:

| Backend | GSTIN | HSN | CGST/SGST/IGST split | India named | Verdict |
|---|---|---|---|---|---|
| **AWS Textract** | **Yes — `VENDOR_GST_NUMBER`, `RECEIVER_GST_NUMBER`**, plus Indian **PAN** fields | No | No | Not in prose; no Indian language | **Closest of all** — the only named GSTIN field types anywhere here |
| **Docsumo** | **Yes** (`GST/ VAT Number`, seller and buyer) | **Yes** (line-item column) | No | No | **Only backend with both GSTIN and HSN** — but see its training clause |
| **Veryfi** | No (VAT/ABN/registration only) | **Yes** (documented line-item field) | No | No | Close on HSN only |
| **Azure DI** | No | No | No | **Yes** — `en-IN` locale **and** `INR` currency, in the model's own docs | Best *localisation*, nothing on *fields*. Ships a Netherlands `KVKNumber` but no India equivalent |
| **Mindee** | Enum value only — labels a captured number, does not validate or parse | No | No | No | Cosmetic |
| **Rossum** | No | No | Only inside an example string: *"e.g. GST, CGST, DPH, TVA"* | No | Cosmetic |
| **Affinda** | No | No | No | No (governing law: Victoria, Australia) | Cosmetic |
| **Google Document AI** | Generic `supplier_tax_id` / `receiver_tax_id` only | **No home at all** | No | **No Indian locale at all** | Generic only |
| **Nanonets** | No | No | No | No | None — despite being India-founded |
| **Klippa** | No | No | No | No | None — field vocabulary is EU-shaped |
| **Claude vision** | No fixed list — you define the schema, so you can *ask* for them | Same | Same | No | **Different shape of answer:** nothing pre-built, nothing prevented |

**The honest summary: for a GST product, every backend needs GST work on top.**
The choice is between reconstructing GST fields from generic tax rows (most
vendors) or specifying them yourself in a schema (the vision-model option).

### 11.2 Which state where data is stored, and for how long

| Backend | Location stated? | India region? | Retention stated? |
|---|---|---|---|
| **Azure DI** | **Yes** — "processed in the same region where the resource was created" | **YES — Central India** (only India region enabled) | **Yes — 24 hours**, in-region, plus a Delete Analyze Result API. **Clearest retention statement of any backend here.** |
| **Google Document AI** | **Yes** — you choose the location | **YES — `asia-south1` Mumbai, confirmed for the Invoice Parser** | **Yes** — online "not persisted to disk"; batch deleted after processing with a **1-day TTL failsafe** |
| **AWS Textract** | **Yes** — regional endpoints… | **YES — `ap-south-1` Mumbai** | **…but the default undercuts it:** AWS "might store such content in an AWS Region **outside of the AWS Region where you are using the service**" for service improvement including model training, unless you file an Organizations opt-out |
| **Claude API** | **Yes, and the answer is no India** — `inference_geo` accepts only `"us"` and `"global"`; workspace geo `"us"` only, unchangeable after creation | **No** | **Yes — 30 days default** |
| **Nanonets** | Yes — **"All customer data is stored in the USA."** | No | Contract/DPA-driven; **no default period stated** |
| **Klippa** | Yes — EU (Azure; Amsterdam named), custom locations on demand | No | Default is **no storage**; logs 1 month |
| **Docsumo** | **No region named** (AWS/GCP only) | No | **Retained and used for AI training**; personal data deleted ≤2 months after account closure |
| **Rossum** | Yes — Frankfurt, Ireland, us-east-1, us-west-1, Tokyo, Osaka | No | **No default period published** |
| **Veryfi** | Yes — US Oregon default; EU on email request | No | "As long as you want Veryfi to function for you" — **no timeframe** |
| **Mindee** | Yes — Europe / United States zones (not on Starter) | No | **12h default, 1–24h configurable** — but the DPA says 7 days. **Two official documents disagree.** |
| **Affinda** | Endpoints in AU/US/EU — **but the privacy policy says data "may be used, processed or stored anywhere in the world"** | No | **Indefinite by default** |

**Only Google and AWS give a confirmed India region. Only Mindee and Anthropic
publish a specific default retention period.**

### 11.3 Which allow a zero-retention mode

| Backend | Zero-retention | Detail and catch |
|---|---|---|
| **Claude API** | **Yes, documented** | *"Anthropic does not store customer prompts or responses at rest after the API response is returned."* Requested via sales, per organization. **Catch: the Files API and the Batch API are both excluded**, so "upload once" and "50% off" each step outside it. Flagged content may be kept 2 years regardless. |
| **Affinda** | **Yes, documented** | `deleteAfterParse` — but *"Only compatible with requests where wait: True"*, forcing synchronous calls. Default is **indefinite** retention, so this is opt-out, not opt-in. |
| **Mindee** | **Near-zero** | *"Delete extracted data when fetched"* deletes immediately after successful retrieval. Otherwise 1–24h. Undermined by the DPA/MSA contradiction. |
| **Klippa** | **Effectively, by default** | *"By default, we do not store any data that is being processed on our servers."* **Not described as a configurable toggle** — it is stated as the default, which is a weaker guarantee than a contractual mode. |
| **Nanonets** | **No** | Contract-driven retention only |
| **Docsumo** | **No — the opposite** | Documents "retained and used for… training of the artificial intelligence" |
| **Rossum** | **Not documented** | Trust page offers "flexibility to define… retention periods"; no zero-retention flag |
| **Veryfi** | **No documented mode** | "Customizable retention policies" with no timeframes |
| **Google Document AI** | **Effectively, for online requests** | *"processed in memory, encrypted in flight, and not persisted to disk"* for online; batch has a 1-day TTL failsafe. No named toggle — it is the default behaviour. Pairs with the strongest no-training statement of the three: *"we never use customer data to train our Document AI models."* |
| **Azure DI** | **No** | 24-hour in-region retention is documented and cannot be set to zero; the only lever is calling the **Delete Analyze Result API** earlier. Training policy **not stated at all**. |
| **AWS Textract** | **No — and the default is the opposite** | Content may be used for service improvement **including model training** and stored **outside your region**, unless you file an AWS Organizations opt-out policy. Zero-retention is documented only for Custom Queries adapter training, not for AnalyzeExpense. |

### 11.4 Which have an outage/error contract the adapter can rely on

This matters because `UnavailableExtractor` already exists and needs to
distinguish **"the service is down"** from **"the bill was blank"**. Ranked:

| Rank | Backend | Why |
|---|---|---|
| **1** | **Claude API** | Full status→type list (400/401/402/403/404/409/413/429/500/504/529), `retry-after`, `request-id` on every response, live rate-limit headroom headers, explicit "retry with exponential backoff" guidance. **Best contract reviewed.** No published uptime SLA found. |
| **2** | **Azure DI** | 429 documented per-quota, `retry-after` header on the analyze response, and an explicit published backoff pattern (*"a 2-5-13-34 pattern of delays"*, *"not… more than once every 2 seconds"*). Implementable as written. |
| **3** | **Veryfi** | 11 documented codes with per-message guidance; `retry-after` on 429. No backoff schedule. |
| **4** | **Rossum** | Standard codes + `Retry-After`, status page, and a published **"99.9% uptime"** plus a latency claim (>90% of docs under 5 min). |
| **5** | **Nanonets** | 10 codes including a distinctive **402 Exhausted Free API Calls**, plus explicit backoff (30s, exponential). SLA claim is marketing-page only. |
| **6** | **Klippa** | Very granular proprietary code space with per-code remediation text — but **no backoff numbers** and no SLA. |
| **7** | **Mindee** | Codes present; retry guidance thin (*"Wait a few seconds and try again"*), **no `Retry-After` documented**. Uniquely, the MSA carries an availability **penalty table** (5% of monthly fees below 99.9%). |
| **8** | **Affinda** | 18 named domain errors but **no HTTP status table and no idempotency key** — hard to build a reliable retry against. No SLA. |
| **5=** | **AWS Textract** | 9 named exceptions (`BadDocumentException`, `DocumentTooLargeException`, `UnsupportedDocumentException`, `ProvisionedThroughputExceededException`, `ThrottlingException`…). Note `ThrottlingException` is a **500**, not a 429 — a retry layer written for HTTP conventions will mis-handle it. SLA credit table exists but **the commitment sentence contains no percentage**. |
| **9** | **Docsumo** | Codes only. **No retry guidance, no working status page, no SLA.** |
| **10** | **Google Document AI** | **No Document AI-specific error-code page exists** — only the standard Google API error model. And in India there is **no SLA at all**, because the SLA covers multi-region endpoints only. **Weakest outage contract of anything reviewed.** |

Azure's entry deserves emphasis for this project specifically: it is the only
backend that publishes both a `Retry-After` header **and** a concrete backoff
schedule (*"a 2-5-13-34 pattern of delays"*), which is what
`UnavailableExtractor` needs in order to distinguish a transient outage from a
permanent failure and hand the person a stated reason.

### 11.5 Cheapest credible option, most defensible option, and why they differ

| | **Cheapest credible** | **Most defensible** |
|---|---|---|
| **Option** | Claude Haiku 4.5 vision, structured outputs to your own schema | **Azure AI Document Intelligence `prebuilt-invoice`, in Central India** |
| **Cost per page** | **≈$0.0046** (my arithmetic from the official token formula and price list) | **$0.01** ($10.00 per 1,000 pages, flat, **same price in Central India as in the US**) |
| **India residency** | **No.** `inference_geo` offers only `"us"` and `"global"` | **Yes** — and *"the incoming data is processed in the same region where the resource was created"* |
| **Retention** | 30 days default; **documented zero-retention available** (but not with the Files or Batch APIs) | **24 hours, in-region, documented**, with a Delete Analyze Result API. No zero-retention mode. |
| **Trains on your data?** | **"Retained data is never used for model training without your express permission"** | **Not stated.** Genuine gap — get it in writing |
| **Per-field confidence** | **No numeric score.** Abstention must be prompted for | **Yes** — per field *and* per word |
| **Error contract** | **Best reviewed** — full status→type map, `retry-after`, `request-id`, backoff guidance | **Second best** — `Retry-After` header plus a published backoff schedule (*"2-5-13-34"*) |
| **Fits the stdlib-only rule** | **Yes** — plain HTTPS + JSON from `urllib.request` | **Yes** — simple subscription-key header |
| **GST fields** | None pre-built; you specify them in your own schema | **None** — though `en-IN` and `INR` are supported, and per-line `Tax`/`TaxRate` help |
| **Line items** | Whatever you define | **Richest of the hyperscalers** — per-line `Tax` and `TaxRate` |

**Why they differ — the trade in one sentence:** the cheapest option buys schema
freedom, a contractual zero-retention path, and the best error contract for
about half a US cent a page, but it sends Indian purchase bills to the United
States and gives you no per-field confidence number; the most defensible option
keeps the bills in Central India, deletes them in 24 hours, and hands you
per-field confidence and an implementable retry contract for roughly **twice the
price** — which at any plausible volume is a rounding error.

**Two runners-up worth naming, for opposite reasons:**

- **AWS Textract AnalyzeExpense** — same $0.01/page (that figure is quoted for
  Oregon; the Mumbai price is **`UNVERIFIED`**, see 9.2), same India region, and the
  **only backend anywhere here with named `VENDOR_GST_NUMBER` / `RECEIVER_GST_NUMBER`
  and PAN fields.** On features it is arguably the best India fit. It is not the
  most defensible because its **default permits using your invoices for model
  training and storing them outside your region**; residency only becomes real
  after you file an AWS Organizations opt-out policy. It also throttles to
  **1 TPS in Mumbai versus 5 in Virginia**. If the owner is willing to file that
  policy and live with 1 TPS, this becomes the strongest candidate.
- **Google Document AI** — **rule it out on price and SLA, not on capability.**
  It bills **per document, not per page**: $0.10 per invoice is **$100 per 1,000**,
  about **10x** the other two. In India it also carries **no SLA** (multi-region
  endpoints only) and a **6 requests/minute** single-region quota. Its
  no-training statement is the clearest of the three, which is not enough to
  offset a 10x price and no SLA.

**A caution about "cheapest".** At realistic volumes the per-page price is not
the real cost. At $0.0046/page a 1,000-bill month costs **$4.60**; at $0.01/page
it costs **$10.00**. The difference is five dollars and forty cents. What
actually dominates is the labelling run in Q3 and the engineering to reconstruct
the CGST/SGST/IGST split and HSN codes that **no backend provides**.
**Choosing on per-page price is optimising a non-bottleneck.** The bottleneck is
the measurement nobody has done and the GST post-processing everybody needs.

---

## 12. Questions only the owner can answer

These are ordered. Each one can make the ones below it unnecessary — do not
start at the bottom.

### The three that come before any vendor choice

**Q1. D-23: is extraction on the critical path at all?**
`docs/DECISIONS.md` D-23 is still `OPEN`, and its stated default is **typed text
only**. `docs/EPIC.md` lists "OCR of any kind" as out of scope for the entire
epic and says "Do not build it" and "Do not enter". If the answer to D-23 is
"typed text only", **every table in this document is moot** and no money is
spent. Nobody but the owner can answer this.

**Q2. Should the GST e-invoice QR route be scoped before any per-page contract
is signed?**
For invoices above the turnover threshold, the structured data already exists,
was created by the supplier's software, and is signed by the government. That
route has no per-page fee, no data-residency question, and no accuracy number
to measure. It does not cover handwritten or below-threshold bills. **What share
of the target customer's purchase bills carry an IRN and QR?** Only the owner
knows the customer. If the answer is "most of them", the extraction problem is
much smaller than this document assumes.

**Q3. Who pays to measure the 95 bar, and on whose bills?**
No vendor publishes a comparable number and none will. The bar can only be
settled by labelling a real sample of the owner's own Indian purchase bills and
counting per field. **How many bills, gathered by whom, labelled by whom?** This
is a real cost that sits on top of every per-page price below, and it is the
only thing that can turn "sounds fine" into a number.

### The commercial and legal ones

**Q4. What is the acceptable per-page cost, and at what monthly volume?**
The credible options span roughly **$0.0046 to $1.31 per page** — a factor of
about **285** — and that ignores Rossum's **$18,000/year** floor, which is a
different shape of commitment altogether. That range cannot be narrowed without
a volume. Nothing else in this document becomes decidable until the owner states
an expected pages-per-month figure and a ceiling.

**Q5. Must Indian customers' purchase bills stay in India — and is that a legal
requirement or a trust position?**
Residency **is** achievable: all three hyperscalers run in an India region, and
on Azure and AWS it costs **nothing extra per page**. Nothing else in this
document offers India at all.

But section 10.2 shows the legal case is **weaker than it looks**. From the
official texts: **DPDP s.16 permits cross-border transfer by default** (the
Government *may* restrict to notified countries; none found) and is probably not
in force until 2027; **GST law has no server-location rule**, only backup and
on-demand production; **RBI localisation covers payment systems, not accounting
data.** The one rule that may bite — **Companies (Accounts) Rules Rule 3(5)** —
could not be retrieved (mca.gov.in returns 403), and even as reported it requires
a **backup in India**, not processing in India.

So the owner must answer two separate questions: *is this a legal obligation*
(**one CA task closes it** — pull Rule 3 as currently amended), and *if not, is
it a trust position we choose to pay for?* The price of the choice is now known:
roughly **$0.01/page instead of $0.0046/page**, plus a 5x throughput cut if the
answer is AWS. **Do not let an unverified rule make this decision by default.**

**Q6. Is "the vendor may train its models on our customers' bills" acceptable,
or disqualifying?**
Docsumo says in its privacy policy that it does exactly this. Others are silent,
which is not the same as "no". If this is disqualifying, say so now — it removes
vendors from the list rather than complicating a later negotiation.

**Q7. Is a vendor who will not publish a price acceptable?**
Klippa and Docsumo are both contact-sales. That means no cost model before a
sales cycle, and no ability to compare. Is the owner willing to run those sales
conversations, or should the shortlist be restricted to vendors with public
prices?

**Q8. Is an unattended posting path ever intended?**
`docs/EPIC.md` records that **no vendor globally auto-posts by default**, and
that Tally's MD has publicly committed against it. If extraction output will
always be reviewed by a person before posting, then per-field confidence matters
far more than raw accuracy, and a backend that abstains honestly beats one that
scores higher and guesses. This changes which column of the tables matters.
