# Deployment

**Nothing in this document deploys anything.** On 2026-08-11 there is no cloud
account, no host, no domain and no registry. Owner decision, already made:
build the artefacts against a **placeholder** registry and host so the
decisions are argued now rather than on the day somebody buys a server.

`docs/OWNER_WORK.md`, "Deployment target", is the one place the outstanding
owner work lives. This file describes the artefacts and what they will and will
not do.

## What exists

| File | What it is |
|---|---|
| `Dockerfile` | The production image for the web app. Two stages; only the second ships. |
| `.dockerignore` | What never reaches the Docker daemon. |
| `pyproject.toml` + `uv.lock` | The dependency list and the exact resolved versions. The image installs from these two and from nothing else. |
| `scripts/deploy` | Build, tag and push to a registry **you** name. |
| `tests/test_deploy_artefacts.py` | Reads the files above as data and fails when a property below stops being true. |

The CI deploy job is **not** in this list. `.github/` is owned elsewhere and
was not touched.

## What is a placeholder

| | |
|---|---|
| Registry | `ACCOUNTANT_REGISTRY`. **No default.** `scripts/deploy` refuses when it is unset. |
| Tenant | `ACCOUNTANT_TENANT`. **No default, and no invented example.** The image declares it *empty*; empty means every request is refused 403. |
| Host | None. Nothing in this repository knows a host name, and no line invents one. |
| Domain | None. |
| Reading service | **None, and no variable configures one.** `accountant/extract/registry.py` takes its backend from `configure()` in code, deliberately — there is nothing here for an operator to set, which is why no row for one appears in the table below. |
| Credentials | None, anywhere. Not in the image, not in the script. |

Every example registry in this repository is under `.invalid`, which RFC 2606
reserves so that it can never resolve. That is deliberate: a working example is
the fastest way to push somebody's image to a registry they never chose.

## What the image installs

Until 2026-08-13 the answer was **nothing**, and that was correct:
`dependencies = []`. D-30 gave the project `pypdf`, `pytesseract` and `Pillow`,
and this file did not notice for two days — so the image carried code that
could not import. `accountant/extract/textlayer.py` imports `pypdf` at module
level and `freeocr.py` imports `pytesseract` and `PIL`.

```
stage 1   pip install uv==0.12.2
          COPY pyproject.toml uv.lock
          uv sync --locked --no-dev --no-install-project   ->  /app/.venv
stage 2   COPY --from=dependencies /app/.venv /app/.venv
          ENV PATH="/app/.venv/bin:$PATH"
```

Four properties, each of them a check in `tests/test_deploy_artefacts.py`:

1. **Exact versions, not names.** `--locked` installs what `uv.lock` resolves,
   and refuses outright when `uv.lock` and `pyproject.toml` have drifted apart.
   Nothing in the image is a version the tests never ran against.
   `pip install pypdf` would install the right package and whatever version the
   index served that morning.
2. **Runtime only.** CI runs `uv sync --extra dev --locked` to get pytest, ruff,
   pyright and eleven other tools. The image asks the same lockfile without the
   extra. `--no-dev` is redundant today — `dev` is an extra — and stays for the
   day somebody makes it a dependency group and uv's default flips to
   installing it.
3. **The project itself is not installed.** `--no-install-project`. The package
   is copied and run under `PYTHONPATH=/app`, exactly as before.
4. **uv does not ship.** It stays in stage one with the lockfile. What crosses
   into the image is a virtualenv.

### There is no `requirements.txt`, and nothing here needs one

`pyproject.toml` names the three dependencies and `uv.lock` pins every version
and hash beneath them, transitive ones included. A `requirements.txt` would be
a **third** list of the same facts, generated from the second, with nothing
checking it stayed equal to either — and its failure is silent: the image
installs an old pin, the tests go on running against the lock, and the two
disagree until something breaks in front of a customer. uv reads the lockfile
inside the build, so the install list exists for the seconds it takes to use it
and is never a file anybody can edit or forget.

If some tool ever genuinely needs one, it is printed from the lockfile rather
than kept:

```bash
uv export --locked --no-emit-project -o requirements.txt
```

### The OCR engine is not installed

`tesseract` is a system binary, not a wheel — `pytesseract` is only a wrapper
that shells out to it. The image does not install it. The `Dockerfile` carries
the reasoning in full; in short: nothing in this repository builds the rung
that would call it, `apt` cannot be pinned to anything this repository
measured, and the app already refuses cleanly without it rather than crashing.
**CI does install it** (`docs/CI_OCR_INSTALL.md`), because a suite without the
binary skips the OCR tests and a skipped test measures nothing. Same binary,
two questions, two answers.

Typed entry and born-digital PDFs are unaffected either way.

## What this image cannot do yet

Read this before planning a deployment around it. Two of these are blocking.

### 1. It refuses to start without a reachable TallyPrime — BLOCKING

`serve()` calls `connect()` before it binds a socket. If TallyPrime is not
reachable at `ACCOUNTANT_TALLY_HOST:ACCOUNTANT_TALLY_PORT`, or the company is
not open, it prints `REAL TALLY REQUIRED` and exits 1.

That is correct behaviour and it is not a defect in the image — a server that
serves pages it cannot fulfil is worse. But a cloud host has no TallyPrime.
Tally runs on the customer's Windows machine, which is the entire reason the
connector dials out (`docs/CONNECTOR.md`), and **the cloud side of the connector
protocol does not exist yet**: `/connector/register`, `/connector/jobs` and
`/connector/result` are not built.

So today `docker run` on this image exits 1, every time, in any cloud. The
image is publishable and reviewable. It is not yet runnable.

### 2. The server binds `127.0.0.1` — BLOCKING

`serve(host: str = "127.0.0.1", port: int = 8000)`, and `python -m accountant.web`
takes the defaults. Inside a container that means the port answers only
processes in that container. Nothing outside can reach it.

This is why the Dockerfile has **no `EXPOSE`**: an `EXPOSE` line would document
a port that answers nobody. `tests/test_deploy_artefacts.py` reads `serve()`'s
default host out of the source, so the day the bind address becomes
configurable the test fails and says to add the line.

**What has to change:** `serve()` needs its bind address and port from the
environment, failing closed the way every other resolved value in
`config_from_environment()` does. That is a code change with its own tests, and
it is not in this task's scope.

### 3. SQLite means one writer

`docs/OWNER_WORK.md`, "PostgreSQL migration". SQLite locks the whole file, so
this image must not be scaled to two replicas writing the same volume. One
container, one volume.

### 4. Nothing has been pulled, and nothing has been installed

No Docker is available where these files were written, and no `docker build`
has been run — deliberately, not as an oversight. Three things are therefore
**asserted and not verified**: that `python:3.14.6-slim` exists, that
`uv==0.12.2` installs into it, and that `uv sync --locked` resolves inside it.
Two commands settle all three, and the second is the one that matters:

```bash
docker manifest inspect python:3.14.6-slim
docker build -t accountant-dad:check .
```

Until somebody runs the second, "the image installs its dependencies" means
what the tests can read out of the file, not what a build did.

## Document upload is enabled at launch

**Document upload is enabled at launch (see D-23 in `docs/DECISIONS.md`).**

> D-23 (2026-08-11): First launch supports typed-text entry and uploaded documents (PDF/PNG/JPG) via Azure Document Intelligence. Azure backend is implemented; real-invoice verification is required before general availability.

**That decision names a vendor this repository no longer contains, and this
document went on describing it for two days.** Measured 2026-08-13: no `.py`
file under `accountant/` mentions Azure, and the two variables this table used
to carry for it — `ACCOUNTANT_AZURE_ENDPOINT` and `ACCOUNTANT_AZURE_KEY` — are
read by no code and declared by no image. Both rows are **deleted** below. An
operator who set them would have changed nothing and been told by this file
that they had. Reconciling D-23 itself is owner work, not a document edit:
`docs/OWNER_WORK.md`.

**What ships instead needs no account, no endpoint and no credential.** A
born-digital PDF is read through its own text layer by `pypdf`, in this
process. Typed-text entry is untouched by any of this. The picture rung is not
wired — nothing in this repository builds it — so a photographed bill is
refused in words rather than guessed at, and the `Dockerfile` explains at
length why the OCR engine is therefore not installed in the image.

**No environment variable selects an extraction backend, on purpose.**
`accountant/extract/registry.py` gives the reasoning beside `configure()`. That
is why the table below has no row for one: there is nothing to set.

Accuracy is not claimed here and is not measured by any of these files —
`docs/EXTRACTION_MEASURED.md` is where that number lives, and what it is
measured against matters more than the number.

## Environment variables the running container needs

The image sets `ACCOUNTANT_DB` to a real value and `ACCOUNTANT_TENANT` to an
**empty** one. Everything below is the operator's to supply.

| Variable | Set in the image? | If it is missing |
|---|---|---|
| `ACCOUNTANT_DB` | **Yes**, `/app/data/app.db` | Falls back to `data/app.db` *relative to the working directory* — a database on the container's disposable filesystem, which is the exact defect commit `69191e2` fixed. Set in the image so it cannot happen. |
| `ACCOUNTANT_TENANT` | **Declared, empty** | **Every request is refused 403, deliberately.** `served_tenant()` treats unset and empty identically and refuses rather than admitting everybody, so a deployment that forgets this variable is broken on the first request instead of quietly serving one customer's books to another. Reads and writes alike. There is **no default**: unset meaning "any tenant may enter" is defect J1 reintroduced. |
| `ACCOUNTANT_TALLY_HOST` | No | Defaults to the built-in host and the container exits 1 with `REAL TALLY REQUIRED` — see blocker 1 above. |
| `ACCOUNTANT_TALLY_PORT` | No | Defaults to 9000. A value that is not a number is a **refusal**, not a fallback: a typo must not connect to a different port. |
| `ACCOUNTANT_COMPANY` | No | Defaults to the built-in company name. A wrong company is refused at startup by `runtime()`, in the terminal, before a socket is bound. |
| `ACCOUNTANT_BACKED_UP_COMPANIES` | No | Empty, and **every write is refused**. This fails closed on purpose: nobody posts into books they have not said they have a backup of. Reads still work. |
| `ACCOUNTANT_POSTING_ENABLED` | No | **Defaults to ON — writes are permitted.** It fails OPEN on purpose: it is not the security boundary, and a flag that had to be *set* for writes to work would mean a deployment that forgot it posts nothing while reporting success. Set `0`, `false`, `no` or `off` and every write is refused `POSTING_DISABLED` with nothing sent to Tally; reads are unaffected. A value it does not recognise (`maybe`) reads as the **default**, so a typo cannot silently switch posting off. |
| `ACCOUNTANT_SAFE_MODE` | No | **Defaults to ON — a write marked destructive is refused `CONFIRM_REQUIRED`** unless the caller passed `confirm=True`. Set `0`/`false`/`no`/`off` and destructive writes proceed without that second statement, which is the wrong value on any machine holding a real business's books. An unrecognised value reads as the **default**, so a typo leaves the guard on. |
| `ACCOUNTANT_LOG_DIR` | No | Defaults to `logs/` **relative to the working directory** — `/app/logs` in this image, which is *not* the declared volume and dies with the container. It is where `accountant/tallyio/audit.py` writes the JSONL record of what was sent to Tally. Point it inside `/app/data` for any process that writes one. |
| `ACCOUNTANT_XML_LOG_DIR` | No | Defaults to `<log dir>/xml`, so it moves with the row above unless set. It holds the raw request and response XML — the evidence of exactly what was sent. Same disposable-filesystem warning. |
| `ACCOUNTANT_TLS_CERT` | No | With **both** this and the key unset the server serves **plain HTTP** and says so loudly at startup. Nothing in this image terminates TLS. |
| `ACCOUNTANT_TLS_KEY` | No | Set **one** of the pair and **nothing binds at all**: `TlsMisconfigured` names the missing one and refuses. Half-configured TLS must not degrade to plaintext — an operator who set the certificate believes the traffic is encrypted, and a server that quietly serves HTTP anyway leaves them holding that belief. |
| `LOCAL_DEV_MODE` | **No, and it must stay that way** | Unset means authentication is **required**. That is the point. Set to `1` it skips authentication entirely, every request runs as tenant `local-dev`, and anybody who can reach the port can read and write those books. A test fails if this variable ever appears in the Dockerfile. |
| `ACCOUNTANT_CONNECTOR_SECRET` | No, and it does not belong here | This is the **connector's** secret. The connector runs on the customer's Windows machine, not in this image. |

No secret of any kind is baked into a layer. A secret in an image layer is a
secret in every registry, cache and backup that image ever touches.

**This table is checked, not maintained.** `tests/test_deploy_artefacts.py`
reads every `ENV_*` constant and every literal handed to `os.environ.get` under
`accountant/`, and fails in both directions: a variable the code reads and this
table does not list, and a row here for a variable no code reads. The second
direction is the one that had actually broken — it is what deleted the two
Azure rows.

### Neither of the two write flags is set anywhere, and both default to ON

`ACCOUNTANT_POSTING_ENABLED` and `ACCOUNTANT_SAFE_MODE` are the two variables
that decide whether this software writes into somebody's books, and until
2026-08-13 neither appeared in any document. Both are read by
`accountant/tallyio/writedoor.py`.

They fail in **opposite** directions on purpose:

- **Posting fails open.** Start a container with neither variable set and it
  **will** post, subject to the permits in `writedoor.ALLOWED_WRITES` and to
  the authentication and tenant checks that run long before a write door is
  reached. Posting is not gated on a variable because a deployment that forgot
  one would then report success and write nothing — the failure this repository
  keeps finding.
- **Safe mode fails closed.** Anything a permit marks destructive needs an
  explicit `confirm=True` as well, so "authorised" and "authorised like this"
  stay two separate statements.

**Set wrong, exactly:** `ACCOUNTANT_POSTING_ENABLED=0` means every write is
refused and reads keep working — a safe, visible, reversible state.
`ACCOUNTANT_SAFE_MODE=0` is the dangerous one: it does not enable anything new,
it removes the confirmation from writes that were already allowed. Misspell
either value (`ACCOUNTANT_SAFE_MODE=flase`) and the flag takes its **default**
rather than reading as off, so a typo cannot disarm a guard.

## Whose books this process serves — `ACCOUNTANT_TENANT`

**Set it in every environment that runs this app.** Local dev, Docker, a
launcher, CI, whatever eventually becomes production. There is one exception and
it is `LOCAL_DEV_MODE=1`, where the served tenant is the constant `local-dev`
and there are no customers to keep apart.

One process serves one company — `runtime()` binds it at startup and refuses on
any disagreement — so it also serves exactly one customer, and this variable
names them. A session belonging to anybody else is refused **403** by
`_identify` before a handler runs, however valid that session is.

**The failure mode, exactly:** unset (or empty) means *every* request is refused
403, reads included. The refusal names the variable. That is not a bug to work
around — it is defect J1's fix, and the alternative, unset meaning "any tenant
may enter", is the defect itself reintroduced as a default.

**The image declares it empty rather than omitting it.** Empty and absent are
identical to the code: `served_tenant()` reads
`os.environ.get(ENV_TENANT, "").strip()`. They are not identical to a person.
`docker inspect` and `docker history` show a declared variable and cannot show
one nobody wrote down, so the image tells the operator what it needs. No
placeholder that *looks* like a tenant id appears anywhere — a value that reads
as real is a value somebody deploys.

```bash
docker run --rm \
  -e ACCOUNTANT_TENANT=the-tenant-id-that-owns-these-books \
  -e ACCOUNTANT_COMPANY='Their Company Name' \
  -v accountant-data:/app/data \
  registry.example.invalid/your-namespace/accountant-dad:<commit>
```

`ACCOUNTANT_COMPANY` and `ACCOUNTANT_TENANT` are two halves of one statement:
which books, and whose.

`scripts/deploy` does **not** refuse when this is unset, and that is a decision.
The registry is a build input — nothing can be pushed without it. The tenant is
a run-time input, and gating the build on it would claim the image is built per
customer. It is not: one image serves every customer. The script says so in its
closing message instead.

## The volume

```
VOLUME ["/app/data"]     ACCOUNTANT_DB=/app/data/app.db
```

The two must name the same directory, and a test asserts they do.

The image creates `/app/data` owned by uid 10001 **before** declaring the
volume, because Docker seeds a new named volume from the image's ownership at
that mount point. Declare the volume first and the seed is a root-owned
directory the non-root process cannot write — which surfaces as a database
error on the first run rather than an obvious one at build time.

**A bind mount ignores all of that** and takes the host directory's ownership.
If you bind-mount, the owner must:

```bash
chown -R 10001:10001 /path/on/host
```

## The healthcheck

```
HEALTHCHECK ... CMD python -c "...urlopen('http://127.0.0.1:8000/health')..."
```

`/health` is the app's own readiness gate. It needs no session — `do_GET`
answers it before `_identify` runs, and the company check is deliberately
exempt, because a readiness endpoint that needs Tally to answer cannot report
that Tally is not answering.

It returns **503 until the runtime is connected and bootstrapped**. `urlopen`
raises on 503, the exit status is non-zero, and the container reports
unhealthy. "Not ready" and "unhealthy" are the same statement here: readiness
means safe to receive work.

`127.0.0.1`, not `localhost` — `localhost` can resolve to `::1` first, where
nothing is listening, and the check would fail for a reason unrelated to the
app.

## Running the script

```bash
ACCOUNTANT_REGISTRY=registry.example.invalid/your-namespace ./scripts/deploy
```

With the variable unset it prints `REFUSED: no operation performed.`, names the
variable, and exits 2 having built nothing.

The tag is the short commit, twelve characters, with `-dirty` appended when the
working tree is not clean. A dirty tree does not block the build — refusing
would make the script unusable exactly when somebody is debugging a deployment
— but the tag never claims the image matches a commit when it does not.

One immutable tag is pushed. **No `:latest`.** A moving tag is how a host ends
up running a build nobody selected.

## What the owner must supply

1. A cloud account and a host.
2. A container registry, and the value of `ACCOUNTANT_REGISTRY`.
3. The tenant id of the customer this deployment serves, and the value of
   `ACCOUNTANT_TENANT` wherever the container is started. Without it the server
   refuses every request 403. Nothing in this repository invents one.
4. A domain, and TLS for it. Nothing in this repository terminates TLS.
5. Registry credentials as repository secrets, for the CI deploy job that is
   owned elsewhere.
6. A backup policy for the volume at `/app/data`. It holds the append-only
   action log, which is the record of what this software did to a real
   business's books. There is no other copy.

## Evidence class

`NOT MEASURED`. No image has been built, pulled, run or pushed. Every check in
`tests/test_deploy_artefacts.py` is a check on the **text** of these files, plus
one behavioural check that runs `scripts/deploy` against a recorded stand-in for
the `docker` CLI. Nothing here is evidence that a container starts.

That applies to the dependency install added on 2026-08-13 without exception.
The tests prove the Dockerfile *instructs* an exact, lockfile-pinned install
and that `uv.lock` resolves exactly what `pyproject.toml` declares. They do not
prove a wheel was fetched, a virtualenv was built or an import succeeded — no
`docker build` was run, and the file was fixed rather than the pipeline.
