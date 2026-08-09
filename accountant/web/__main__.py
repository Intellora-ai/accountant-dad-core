"""`python -m accountant.web` — the command a person actually types.

Without this file that command fails with

    No module named accountant.web.__main__; 'accountant.web' is a package
    and cannot be directly executed

which tells somebody who is not a Python programmer nothing at all. The working
form was `python -m accountant.web.app`, one word longer and only discoverable
by reading the README. Both now work and do the same thing.

The entry point itself lives in `app.serve()`. This module only routes to it, so
there is one startup path and not two — a second, subtly different way to start
the process is exactly how "it works on mine" happens.
"""

from __future__ import annotations

from accountant.tallyio.factory import RealTallyRequired
from accountant.web.app import serve

if __name__ == "__main__":
    try:
        serve()
    except RealTallyRequired as exc:
        # `exc` ALREADY begins "REAL TALLY REQUIRED: no operation performed."
        # Prefixing it again printed that sentence twice in a row, which reads
        # like a bug in the very message meant to reassure somebody that
        # nothing happened. Re-raised as-is.
        #
        # Non-zero exit, so a launcher, a shell script or a packaged .exe can
        # tell "never started" apart from "stopped". Printing and exiting 0
        # would make a failed start look like a clean shutdown.
        raise SystemExit(str(exc)) from exc
