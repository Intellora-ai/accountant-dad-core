# A stale `.coverage` file reads exactly like a real measurement

- `.coverage` is gitignored, so it survives branch switches and outlives the code
  it measured. A partial run — `--testmon`, a single file, an interrupted job —
  leaves a complete-looking artefact behind.
- `coverage report` reads that file and prints a confident percentage. Nothing in
  the output says the data is old or partial.
- **Measured 2026-08-12:** an artefact from the previous day reported
  `accountant/web/app.py` at **28%**. A fresh full run measured **100%**. The
  stale file listed module-level assignments as unexecuted, which is impossible
  if the module imported at all — that impossibility is the tell.
- Two agents and a plan were built on the wrong number before anyone re-measured.

**Applies:** any time you are about to act on a coverage figure. Delete
`.coverage*` first, then run, then read.

**Does not apply:** `coverage.xml` written by CI in the same job — that one cannot
outlive its run.
