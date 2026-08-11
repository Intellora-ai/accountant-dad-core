# The connector

The program that runs on the customer's Windows machine, beside TallyPrime.

## Why it exists

TallyPrime listens on `localhost:9000` on the machine it runs on, and that port
has **no authentication beyond network reachability**
(`accountant/tallyio/real.py:1876-1882`). A cloud server cannot open a socket to
a port on somebody's laptop — and nobody should want it to, because the only way
to make that work is to ask every customer to expose an unauthenticated
accounting API to the internet.

So the direction is inverted. The connector runs where Tally runs and **dials
out**.

```
 browser  ──inbound──►  cloud  ◄──outbound──  connector  ──loopback──►  Tally
                               (connector opens it)      (never leaves the box)
```

**Port 9000 is never listened on, never forwarded, and never reachable from
outside the machine.** The only inbound connection anywhere in the system is a
browser reaching the cloud. `tests/test_connector.py::test_no_module_in_this_package_binds_a_socket`
reads the package's AST and fails if any module ever gains a listener.

## What it refuses, before Tally is touched

| Outcome | When |
|---|---|
| `REFUSED_WRONG_TENANT` | the job names a tenant this connector does not belong to |
| `REFUSED_WRONG_COMPANY` | the job names a company this connector was not paired to |
| `ALREADY_EXECUTED` | this operation id has already been run here |
| `REFUSED_UNKNOWN_KIND` | the job asks for something this connector does not do |

All four are decided **before** a Tally client is opened, so a wrong-tenant job
cannot even cause a connection attempt against somebody's books
(`test_a_refused_job_never_reaches_tally`).

Tenant is checked before company on purpose: a job from another tenant names a
company that is not ours to reason about, so the tenant answer is the honest one.

## The two outcomes that are not synonyms

```
TALLY_UNAVAILABLE   Tally could not be reached. The job did NOT run.
                    A retry is safe.
FAILED              Tally was reached, understood the job, and errored.
                    The job WAS attempted. A retry is not automatically safe.
```

Collapsing these into one "error" is how a system ends up retrying something
that already happened.

## Duplicate protection, and why the ordering is load-bearing

A polling protocol drops replies. That is normal, not exceptional: the connector
runs the job, the network dies before it can report, and the cloud — never
having heard an answer — offers the same job again.

So the operation id is recorded **after Tally answers and before the cloud is
told**:

```
open client  →  call Tally  →  Tally answers  →  RECORD  →  report to cloud
                                                  ▲
                              a dropped reply after this point comes back
                              as ALREADY_EXECUTED instead of running twice
```

Getting that order wrong breaks in both directions. Record earlier and a job
that reached Tally and failed is marked done for ever, stranding it silently.
Record later and a dropped reply duplicates a statutory entry.

Both directions are pinned:
`test_a_job_that_reached_tally_and_failed_is_not_recorded_as_done` and
`test_a_dropped_reply_does_not_execute_the_job_a_second_time`. The first one was
written because moving the record one line earlier **survived every other test
in the file**.

The record is a file, not a set in memory, because the case it exists for
includes a connector that was restarted between doing the work and reporting it.

## What it does not do

It decides nothing. It does not validate an entry, choose an account, ask a
question, or judge whether a voucher should exist. Those live in the cloud,
where the memory index and the rules corpus are. A connector that formed
opinions would be a second place where accounting decisions are made.

**It also cannot write.** `accountant/agent/cli.py` constructs its client with
an empty `RecordedBackups`, and `READ_KINDS` lists only reads. The write path
reaches Tally through `pipeline.post` and its gate stack, and
`tests/test_runtime_backend.py` exists to keep that door singular.

It never speaks XML either. It holds a `TallyClient` and calls methods on it, so
correction C3 is unchanged: `accountant/tallyio/` remains the only package that
knows Tally's wire format exists.

## Running it

```
ACCOUNTANT_CONNECTOR_SECRET=... python -m accountant.agent \
    --connector-id  connector-1 \
    --tenant-id     tenant-alpha \
    --company       "Demo Co" \
    --cloud-url     https://cloud.example.com \
    --tally-host    localhost \
    --tally-port    9000 \
    --state-dir     data
```

`--once` takes at most one job and exits — for checking a new pairing.

**The secret is never a command-line argument.** Arguments are visible in the
process table to every user on the machine and land in shell history. It comes
from `ACCOUNTANT_CONNECTOR_SECRET` or `--secret-file PATH`, and it is never
printed and never logged (`test_the_secret_is_never_written_to_the_log`).

`--cloud-url` must be `https://`. `https_cloud_call` refuses plaintext rather
than warning about it, because the request body carries that secret.

## The log

Rotating, bounded at `(backups + 1) × max_bytes` = **4 MB** by default. An
unattended program on a customer's laptop must not fill their disk, and "it
rotates" without a measured ceiling is the promise every unbounded log ever
made. `test_the_log_rotates_and_does_not_grow_without_bound` writes 2,000 lines
and asserts the total.

## What is not built yet

```
the cloud side of this protocol   /connector/register, /connector/jobs,
                                  /connector/result do not exist yet
connector revocation              no way to disable a connector centrally
heartbeat                         the cloud cannot tell a stopped connector
                                  from a disconnected one
write jobs                        deliberate, see above
distribution                      how a customer gets this onto their machine
                                  is owner work — docs/OWNER_WORK.md
```

## Evidence class

Everything proven about this connector today is **`FAKETALLY`**. Every test runs
against `FakeTally` or a deliberate failure double. Nothing here is evidence
about a real TallyPrime, and no result in this file may be relabelled as such.
