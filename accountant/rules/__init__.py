"""The GST rules corpus: official rates, effective dates, and where they came from.

Owner decision Q1 = A. Two sources may stand alone behind a production rule —
an official CBIC notification or circular, and an official Income Tax Department
notification or circular. GST Council material is supplementary context and can
never be the only thing behind a rule. **No commercial API, at build time or at
runtime.** Nothing in this package opens a socket; every rate here was fetched
once, by hand, at build time, and the retrieved text is what the citations point
at.

Owner decision Q2 = C. The corpus holds exactly the codes the evaluation corpus
uses. Not twenty-one thousand. Not a top-N. Not a frequency cutoff. An unseen
code returns NOT_FOUND and is never answered with a similar code's rate.

Owner decision Q3 = D. Nothing in this package or in `accountant/tax/` posts
anything. The rates are computed and shown; the Tally write path is unchanged and
still refuses every voucher that carries tax.
"""
