"""GST calculation, ledger mapping, and the decision that ties them to evidence.

**Nothing in this package posts anything, and nothing in it can be made to.**
Owner decision Q3 = D: the engine is built, automatic GST posting is not enabled.
The Tally write path is untouched — `accountant/checks.py::tax_lines_can_be_posted`
still turns any voucher carrying `gst_paise` into UNCLEAR, and
`accountant/tallyio/real.py::check_writable` still refuses it at the wire. This
package computes and cites; it never authorises.
"""
