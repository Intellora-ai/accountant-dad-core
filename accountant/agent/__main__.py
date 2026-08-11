"""`python -m accountant.agent`. Routing only; the entry point is cli.main."""

from __future__ import annotations

import sys

from accountant.agent.cli import main

if __name__ == "__main__":
    sys.exit(main())
