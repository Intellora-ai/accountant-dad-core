"""Couriers. Code that carries a document to somebody else's reading service.

WHY THIS PACKAGE EXISTS SEPARATELY FROM `accountant/extract/`
-------------------------------------------------------------
`accountant/extract/` is an adapter, and `tests/test_no_reader.py` enforces that
it imports nothing which reaches another program, a socket or the network — its
transport is INJECTED. That guard is why a person can read the whole extraction
package and know that no request leaves the machine from any line of it.

A vendor was selected on 2026-08-11, and a real vendor needs somebody to open
the socket. That somebody lives here: one small module, the only thing
in the tree that speaks to a reading service, small enough to review in one
sitting.

The transport was written under `accountant/extract/` first and the guard
refused it, naming `urllib`. The guard was right and the file moved. Nothing was
weakened to make room for it.

THE SAME RULE STILL APPLIES HERE
--------------------------------
This package is not a reader either. No OCR, no layout analysis, no field
detection, no guessing. `tests/test_reader_transport.py` enforces that on this
package the way `tests/test_no_reader.py` enforces it on the other, so moving
the transport out added a guard rather than escaping one.
"""
