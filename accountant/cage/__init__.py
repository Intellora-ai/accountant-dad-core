"""The safety cage: what stands between a guess and a customer's books.

WHY THIS PACKAGE EXISTS
-----------------------
Before this package, one type carried a field from the moment it was guessed to
the moment it was written into TallyPrime. That single fact is the root cause of
most of the failure list: a misread amount, a wrong party, a flipped sign and a
nonsense entry all reach the books by the same route, because nothing in the
type system distinguishes "what we think the bill says" from "what we are about
to write".

So this package holds two types and one gate between them, plus the checks that
need no model and no labelled data:

    conservation   quantities that must be equal. Debits and credits. Lines and
                   total. Net plus tax and gross. They hold or they do not.

WHAT THIS PACKAGE DOES NOT DO
------------------------------
It does not read files, talk to Tally, or decide what a bill means. It is the
wall, not the rooms on either side of it.
"""

from __future__ import annotations
