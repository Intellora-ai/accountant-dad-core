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

### The OCR engine is installed — owner decision, 2026-08-13

> **The default Docker image includes Tesseract OCR so that photo uploads can be
> processed. This increases image size and build time; this cost is accepted for
> the MVP.**

`tesseract` is a system binary, not a wheel — `pytesseract` is only a wrapper
that shells out to it. Until 2026-08-13 this image did not install it, and this
section said so. **That was reversed**, and the measurement is why:

```
PATH=/usr/bin:/bin   (no tesseract binary — what this image was)
registry.default_extractor() on a corpus PNG
  -> all four fields unread, each: "not_found: the text reading program
     is not installed on this machine"
```

`DEFAULT_BACKEND` is `ladder` and `app.py` routes every uploaded image to it, so
a photograph read **zero of four fields** in this image, always. The owner's
reason, in full: *"This is required because the MVP requirement is: a user can
upload a photo and get fields read. A Docker image where photos are dead by
design does not satisfy that requirement."*

```
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      tesseract-ocr \
      tesseract-ocr-eng \
 && rm -rf /var/lib/apt/lists/*
```

| Constraint | How it is held |
|---|---|
| **Two packages, no third** | `tesseract-ocr` is the engine; `tesseract-ocr-eng` is the English trained data. A third package fails a test. |
| **English only** | `tesseract-ocr-eng` is a hard dependency of `tesseract-ocr` on Debian and is named anyway, because *which languages this image reads* is a decision and not apt's to make. Any other `tesseract-ocr-<lang>`, and `tesseract-ocr-all`, fail a test. |
| **No recommends chain** | `--no-install-recommends`. Its absence fails a test. |
| **No package lists left behind** | `apt-get update` writes tens of megabytes into `/var/lib/apt/lists`. A layer is immutable, so deleting them in a *later* `RUN` leaves the bytes in the image under a whiteout — update, install and delete are one instruction, and a split fails a test. |

**The cost that did not go away: apt pins nothing.** `uv sync --locked` installs
the exact wheel versions the suite ran against. These two Debian packages are
whatever the index serves on the morning of the build. No version is written in
the `Dockerfile` because none has been *read* from an index, and inventing one
in a file whose job is to be exact would be a fabricated fact — the same rule
that keeps the base image a tag rather than an invented digest. What contains it
is that the list is short and every property above is asserted.

**CI installs the same binary for a different reason**
(`docs/CI_OCR_INSTALL.md`): a suite without it skips the OCR tests, and a
skipped test measures nothing. Same package, two questions, and since today the
same answer.

Typed entry and born-digital PDFs are unaffected either way — those go through
`pypdf`, or through no reader at all.

**The image was built and a photograph was read inside it, 2026-08-13.** What
the two packages actually cost, now that the numbers exist rather than being
accepted sight-unseen:

| | |
|---|---|
| `tesseract-ocr` + `tesseract-ocr-eng` layer | **107 MB** uncompressed — the single largest layer in the image, larger than the interpreter's own build layer (45 MB) and four times the virtualenv (25.8 MB) |
| Build time for that one `RUN` | **12.0 s** of a 39 s cold build |
| Engine that arrived | `tesseract 5.5.0` / `leptonica 1.84.1`, from Debian trixie |
| Languages present | `eng` and `osd`, and nothing else — `tesseract --list-langs` inside the image confirms the constraint the table above asserts |

The engine is on `PATH` at `/usr/bin/tesseract` and `pytesseract` finds it:
`pytesseract.get_tesseract_version()` returns `5.5.0` inside the container. The
apt install did not silently no-op.

See "Evidence class" at the foot of this document for the per-field readings.

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

### 4. Nothing has been pushed, and no host has run it

This item used to read "Nothing has been pulled, and nothing has been
installed". **That stopped being true on 2026-08-13**: the image was built on
both architectures, the base image was pulled, the wheels were installed and a
photograph was read inside a container. "Evidence class" at the foot of this
document carries the numbers.

What is still **asserted and not verified**:

- **No registry has this image.** `scripts/deploy` has never pushed. Every
  registry named in this repository is under `.invalid` and cannot resolve.
- **No host has run it.** Blockers 1 and 2 above are why, and neither was
  weakened by the build — running the image with its default `CMD` exits **1**
  with `REAL TALLY REQUIRED`, which is blocker 1 happening rather than being
  predicted.
- **apt pins nothing, and now there is a number attached.** The build on
  2026-08-13 got `tesseract 5.5.0` from Debian trixie. Nothing in the
  `Dockerfile` holds it there. The next build may get another, and the
  measurement below shows that the engine version *moves the confidence
  score* — see "The one disagreement" in the evidence class.

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
process. Typed-text entry is untouched by any of this. The picture rung **is**
wired — `pagereader.py` supplies the reader, `DEFAULT_BACKEND` is `ladder`, and
`app.py` routes every uploaded image to it — and since 2026-08-13 the image
carries the engine that rung shells out to, for the reason given above. What no
file here claims is that it reads a photographed bill *well*: the corpus numbers
are poor and are stated in `docs/EXTRACTION_MEASURED.md`.

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

`MEASURED — BUILT AND RUN, NOT DEPLOYED`. Measured 2026-08-13 on Docker
29.5.2 / Docker Desktop, 10 CPU, 8 GB, Apple Silicon. This section used to read
`NOT MEASURED`, correctly, because no Docker existed where these files were
written. One is available now, so the costs the owner accepted in writing above
are numbers rather than acceptances.

Everything below was **observed**. Nothing in it is read out of a file.

### The build

| | linux/arm64 (native) | linux/amd64 (emulated) |
|---|---|---|
| Result | **success**, exit 0 | **success**, exit 0 |
| Cold wall-clock | **39 s** | **40 s** |
| Image size, to transfer | **93,414,173 B ≈ 93.4 MB** | **94,226,116 B ≈ 94.2 MB** |
| Image size, unpacked on disk | **389 MB** | — |

Cold means the base image was pulled inside that 39 s and no build cache
existed; this repository had never been built. Both architectures were built
because the machine is Apple Silicon and any cloud host is overwhelmingly
likely to be amd64 — an image that only builds on the developer's laptop is a
deployment defect that a single-arch build cannot see.

Where the 39 s and the 93.4 MB go, from `docker history` and the build log:

| Layer | Size | Time |
|---|---|---|
| `apt-get install tesseract-ocr tesseract-ocr-eng` | **107 MB** | 12.0 s |
| base `python:3.14.6-slim` interpreter build layer | 45 MB | (pulled) |
| `COPY /app/.venv` — pypdf, pytesseract, Pillow | 25.8 MB | 1.0 s to resolve |
| `COPY accountant/` | 3.96 MB | 0.0 s |
| `pip install uv==0.12.2` (stage 1, does not ship) | — | 13.9 s |
| base image pull | — | 4.5 s |

**Tesseract is the largest single thing in this image.** 107 MB for an engine
that read one field of four off the corpus photograph. That is the owner's
accepted cost, stated as the number rather than as the acceptance.

The base tag resolved to a **real digest**, which the `Dockerfile` header says
is the next step and correctly declined to invent:

```
python:3.14.6-slim  ->  sha256:7bec7ddcddeff7975d6ba9b4be7dd6f6b2f55e7491539145e2978f7f97ce9144   (index)
                        sha256:34bf30c914ac17d2a0f7ecf94866e54380669d618ae4673672445516603ad8d7   (linux/arm64)
                        sha256:b921fe7e7522f828d45197a47656ec465a9b15689b27fa8e1fba2864fca5b967   (linux/amd64)
```

### The binary is there

```
$ docker run --rm accountant-dad:check tesseract --version
tesseract 5.5.0
 leptonica-1.84.1
  libgif 5.2.2 : libjpeg 6b (libjpeg-turbo 2.1.5) : libpng 1.6.48 : libtiff 4.7.0 :
  zlib 1.3.1 : libwebp 1.5.0 : libopenjp2 2.5.3

$ docker run --rm accountant-dad:check sh -c 'command -v tesseract; tesseract --list-langs'
/usr/bin/tesseract
List of available languages in "/usr/share/tesseract-ocr/5/tessdata/" (2):
eng
osd
```

Checked because an image whose `apt-get` silently no-opped would still build.
It did not: the engine is on `PATH`, `pytesseract.get_tesseract_version()`
returns `5.5.0` from inside the container, and the only trained data present is
English plus orientation detection — the "English only" constraint above,
verified rather than asserted.

The wheels are the ones the suite ran against: `pypdf 6.15.0` and
`Pillow 12.3.0` inside the container, **identical to the host virtualenv**.
`uv sync --locked` did what the four properties above claim.

The container runs as `uid=10001(accountant) gid=10001(accountant)`.

### The reading, per field

Three corpus documents, through `registry.default_extractor().extract(...)` —
the exact call `accountant/web/app.py` makes on an upload. `DEFAULT_BACKEND`
resolved to `ladder` inside the container. **arm64 and amd64 returned identical
values for all three.**

**`GT-0041.png`, `image/png` — a photograph. Backend `free_ocr`.**

| Field | Truth | Container returned | Verdict | Confidence |
|---|---|---|---|---|
| `party` | `ADVANCED PROPULSION CENTRE UK LTD` | `AQUANCED PROPULSION CENTRE UK LTO` | **READ-BUT-WRONG** — 2 characters | **0.33** |
| `date` | `2026-05-13` | `not_found` | not read | 0.0 |
| `total_paise` | `1020.70` | `not_found` | not read | 0.0 |
| `tax_paise` | `155.70` | `not_found` | not read | 0.0 |

**One field of four was read. Zero of four were correct.** The owner's pass
criterion for this measurement was *"at least one field is read, even if
confidence is low"*, and that is met — `party` carries the source `free_ocr`,
which is a READING, not a refusal. It is also wrong, and the confidence says so:
**0.33 against `ASK_FLOOR` 0.70** (`accountant/cage/decision.py`), so the cage
refuses to treat this name as a vendor identity and asks the person. That is the
F-03 guard doing its job on a real container. The three unread fields carry
`not_found` with a stated reason, not blanks.

This is what "the corpus numbers are poor" means in practice, and it is the same
poor number the host produces. The image is not the problem; reading a
photograph is.

**`GT-0021.pdf`, `application/pdf` — born digital. Backend `pdf_text_layer`.**

| Field | Truth | Container returned | Verdict | Confidence |
|---|---|---|---|---|
| `date` | `2026-09-21` | `2026-09-21` | **CORRECT** | 1.0 |
| `party` | `BALFOUR BEATTY VINCI JV - HS2 (N2)` | `BALFOUR BEATTY VINCI JV - HS2 (N2)` | **CORRECT** | 1.0 |
| `total_paise` | `584.10` | `58410` | **CORRECT** | 1.0 |
| `tax_paise` | `89.10` | `8910` | **CORRECT** | 1.0 |
| line item | `CONSULTANCY RETAINER` / `495.00` | `CONSULTANCY RETAINER` / `49500` | **CORRECT** | — |

Four of four, byte-identical to the host. `pypdf` behaves the same in the
container as on the machine the tests ran on.

**`GT-0061.jpg`, `image/jpeg` — a JPEG container with no image data.**

All four fields `not_found`, each carrying:

> *this file says it is a picture but there is no picture inside it, so there is
> nothing on it to read. Please send the original photograph or scan*

No confidence is stated for any field, which is correct — nobody scored them.
**Nothing raised.** A crash here would have been a real defect; a refusal is the
designed behaviour, and it survived the container.

### Nothing raised, anywhere

Across all three documents on both architectures, no exception escaped
`extract()`. The probe caught `BaseException` specifically so that a crash could
not be mistaken for a refusal, and reported `anything_raised: false` every time.

### The one disagreement with the host

The container and the host returned the same strings, the same fields, the same
sources and the same refusals — with **one** exception:

| | Host | Container |
|---|---|---|
| Tesseract | 5.5.3 (Homebrew), leptonica 1.87.0 | **5.5.0** (Debian trixie), leptonica 1.84.1 |
| `party` read off `GT-0041.png` | `AQUANCED PROPULSION CENTRE UK LTO` | *same* |
| `party` confidence | **0.30** | **0.33** |

**The number moved and the behaviour did not.** Both are far below `ASK_FLOOR`
(0.70) and further below `AUTO_POST_FLOOR` (0.95), so the cage reaches the same
decision — ask the person — on both. Nothing downstream can tell them apart.

It is still worth writing down, because it is **apt's unpinned install visible
in an output number**. The engine version is not held by anything in the
`Dockerfile`, the confidence score depends on it, and confidence is what decides
whether a value is questioned or believed. Today the gap is 0.03 in a region
where every value is refused anyway. The day a Debian upgrade moves a score
across 0.70, an image and a test suite would disagree about whether to ask a
human, and nothing in this repository would notice. That is the cost of an
unpinned apt line, measured rather than argued.

### Reproducing all of it

```bash
# 1. Build. Both architectures; the cloud one is not the laptop's.
docker build -t accountant-dad:check .
docker build --platform linux/amd64 -t accountant-dad:check-amd64 .

# 2. The costs.
docker image inspect accountant-dad:check --format '{{.Size}} {{.Architecture}}'
docker history accountant-dad:check --format '{{.Size}}\t{{.CreatedBy}}'

# 3. The binary is really there.
docker run --rm accountant-dad:check tesseract --version
docker run --rm accountant-dad:check sh -c 'command -v tesseract; tesseract --list-langs'

# 4. Read the three documents through the shipped default path.
#    docker cp rather than a bind mount: artifacts/ is in .dockerignore and a
#    bind mount would also have to clear Docker Desktop's file sharing.
cat > /tmp/probe.py <<'PY'
import sys
from accountant.extract import registry
for name, mime in (("GT-0041.png", "image/png"),
                   ("GT-0021.pdf", "application/pdf"),
                   ("GT-0061.jpg", "image/jpeg")):
    data = open(f"/docs/{name}", "rb").read()
    r = registry.default_extractor().extract(data, mime)   # app.py's exact call
    print(name, r.backend, r.date, repr(r.party), r.total_paise, r.tax_paise)
    print("  conf:", dict(r.per_field_confidence))
PY
docker create --name probe accountant-dad:check python /probe.py
docker cp /tmp/probe.py probe:/probe.py
docker cp artifacts/ground_truth/documents probe:/docs
docker start -a probe
docker rm probe

# 5. Blocker 1, happening rather than predicted. Exits 1.
docker run --rm -e ACCOUNTANT_TENANT=probe-tenant accountant-dad:check; echo $?
```

### What is still NOT measured

`tests/test_deploy_artefacts.py` is unchanged by any of this and remains a check
on the **text** of these files, plus one behavioural check that runs
`scripts/deploy` against a recorded stand-in for the `docker` CLI. The build
above was run by hand, once. **No gate runs it**, so this section is a
measurement with a date on it, not a property that stays true.

Still unproven, and each for a reason no build can fix:

- **That any registry has this image.** Nothing has been pushed.
- **That any host runs it.** Blockers 1 and 2 are unchanged; the default `CMD`
  exits 1 in any environment without a reachable TallyPrime, which is every
  cloud.
- **That the next build gets `tesseract 5.5.0`.** apt pins nothing.
- **That the amd64 image runs on real amd64 hardware.** It was built and
  executed under emulation on Apple Silicon. Emulation is strong evidence and
  not the same statement.
