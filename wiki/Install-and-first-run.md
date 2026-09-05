<!-- Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
     SPDX-License-Identifier: Apache-2.0 -->

## Quickstart

> **The demo is a hypothetical Indian bank**, so screenshots show rupee
> amounts and Indian digit grouping. Nothing in the platform is tied to
> that: currency, date order and ID checksums come from the country pack,
> and packs ship for `IN` and `US` today.

### Step 1 — install the prerequisites

Nothing here is pulled from us. There is no container registry to sign in to and
no image we publish — **you build the whole system on your own machine**, from
either a clone or a release download. What you need is a Docker daemon, a Python
that can make a virtualenv, and curl.

| Need | Why |
|------|-----|
| **Docker Engine 24+** with **Compose v2** | runs the whole stack. Compose v1 cannot parse the `include:` this uses |
| **16 GB+ RAM** (32 GB recommended) | Milvus plus the service fleet |
| **Python 3.9+**, with **`venv`** and **`pip`** | the setup and seed scripts. The seed builds a venv and installs into it |
| **curl** | the installer polls service health with it |
| **An OpenAI-compatible LLM key** | recommendations and NL->SQL -- OpenRouter, OpenAI, DeepSeek, or your own vLLM |
| **Internet access on first run** | pulling base images, and the seed's `pip install` |

> **Why `python3-venv` is listed separately below.** Debian and Ubuntu ship
> `python3` without `ensurepip`, so `python3 -m venv` fails on a machine where
> Python is plainly installed — *"the virtual environment was not created
> successfully because ensurepip is not available"*. The demo seed builds a
> venv, so installing `python3` alone is not enough.

**What you do _not_ need on the host**, despite the stack using them:

| | |
|---|---|
| **Node.js** | only ever run inside the containers (`docker compose exec ... node`) |
| **git** | needed to clone, not to build — the release tarball is self-contained |
| **make** | convenience only; every target is a one-line script call, see below |
| **openssl** | used for secrets if present, falls back to `/dev/urandom` |

**Ubuntu / Debian**

```bash
sudo apt update
sudo apt install -y python3 python3-venv python3-pip curl make
curl -fsSL https://get.docker.com | sudo sh
sudo usermod -aG docker "$USER"     # then log out and back in
```

**Fedora / RHEL / Rocky**

```bash
sudo dnf install -y python3 python3-pip curl make
curl -fsSL https://get.docker.com | sudo sh
sudo systemctl enable --now docker
sudo usermod -aG docker "$USER"     # then log out and back in
```

**macOS** — install [Docker Desktop](https://docs.docker.com/desktop/install/mac-install/), then:

```bash
brew install python curl make
```

**Windows** — install [Docker Desktop](https://docs.docker.com/desktop/install/windows-install/)
and [Python](https://www.python.org/downloads/) (tick *Add python.exe to PATH*),
then run everything below from **Git Bash** or **WSL**, not `cmd` or PowerShell.
`make` is not present on Windows; use the `bash scripts/quickstart/...` form
shown beside every `make` command.

Check Docker is actually running — `docker version` must print a **Server**
section, not just a Client one:

```bash
docker version
```

### Step 2 — get the code

Either route gives you an identical, self-contained tree. Pick one.

```bash
# Clone it
git clone https://github.com/Trustedwear-Tech/citra-decision-system.git
cd citra-decision-system
```

```bash
# Or download the release — no git needed
curl -sSL https://github.com/Trustedwear-Tech/citra-decision-system/archive/refs/tags/v0.6.1.tar.gz | tar xz
cd citra-decision-system-0.6.1
```

### Step 3 — run the wizard

```bash
make wizard          # or, without make:  bash scripts/quickstart/wizard.sh
```

It re-checks your host and stops before writing anything if something is
missing, then asks for an OpenRouter (or other OpenAI-compatible) API key,
writes `.env` with freshly generated secrets, and does the rest itself:

1. starts the data stores — Mongo, Redis, Milvus, MinIO, Postgres
2. **builds all twelve application services from source**
3. **builds the three sandbox images**, including the OpenClaw-based
   `citra-agent-sandbox-base` and the `citra-app-builder` layered on top of it
4. creates your admin user
5. seeds the `acme-bank` demo

**Expect 20–40 minutes on the first run**, most of it compiling images. It is
cached afterwards — a second run takes a couple of minutes.

The one thing that still reaches the internet is base images: `python:3.11-slim`,
`mongo:7.0`, `ghcr.io/openclaw/openclaw` and the rest come from public
registries, and the seed `pip install`s into a venv. Both are ordinary public
downloads needing no account.

If the sandbox build fails, the wizard **warns and carries on**. That is
deliberate: running Decision Apps is unaffected, only *building* them and
code-exec in chat need those images. Re-run
`bash scripts/quickstart/build-sandboxes.sh` once it is fixed.

### Step 4 — sign in

The wizard prints the URL and the credentials it created when it finishes. See
[Signing in](#signing-in) below for what to do first.

### Or by hand -- two phases

```bash
# 1. SETUP -- generate .env with fresh secrets, start the data stores, and
#    create the DB resources (Mongo replica set, demo Postgres, MinIO bucket).
make setup                      # or: bash scripts/quickstart/setup.sh

# 2. Set your key in the generated .env  ->  LLM_API_KEY=sk-...

# 3. START -- every service, the super-admin, and the acme-bank demo.
make start                      # or: bash scripts/quickstart/start.sh
```

`make install` runs both. `make ps`, `make logs` and `make down` manage the
running stack; `make down ARGS=-v` also wipes the volumes. Without `make`, the
equivalents are `docker compose -f docker-compose.quickstart.yml ps` / `logs`
/ `down`. See the `Makefile` for the full target list.

> **Two phases, many containers.** `setup` initialises the databases; `start`
> brings up the service fleet -- each its own image, so you can scale, restart
> and debug them individually -- and on first run also builds the sandbox
> images the builder needs, which takes a few minutes and is cached after.

### What gets built, and in what order

Everything is built from source on your machine. Nothing is pulled from a
private registry, and there is no image you cannot rebuild yourself.

**1. The fleet — fourteen services.** `docker compose` builds these from
`docker-compose.dev.yml`, which `docker-compose.quickstart.yml` includes. Six of
them (`citra-service`, `smart-app-service`, `duckdb-query-service`,
`reranker-service`, `discovery-service`, `data-discovery-service`,
`playwright-render-service`) build with the **repository root** as their context,
because they copy the shared packages out of `citra-common/`. The rest build from
their own directory.

**2. The sandbox images — three, and one of them depends on another.** These are
*not* built by compose, because compose never runs them: `action-sandbox-host`
spawns them per user, at runtime, when someone builds a Decision App or executes
code in chat. `start.sh` builds them on first run via
`scripts/quickstart/build-sandboxes.sh`; you can also run it directly:

```bash
bash scripts/quickstart/build-sandboxes.sh
```

They form a chain, which is why the builder is a separate step rather than
another matrix entry:

```
ghcr.io/openclaw/openclaw:<pinned digest>      the upstream agent runtime
        │
        └── citra-agent-sandbox-base            infrastructure/action-sandbox/Dockerfile
                │                               neutral base: toolkit, shims, Chart.js
                │
                └── citra-app-builder           smart-app-service/builder-sandbox/Dockerfile
                                                adds the builder persona + workspace seed

quick-chat-sandbox                              Citra-Service/Dockerfile.quick-chat-sandbox
                                                independent, FROM python:3.11-slim
```

`citra-app-builder` is built `FROM citra-agent-sandbox-base`, so the base must
exist first — the script builds them in that order and skips the consumers if
the base fails, rather than producing a confusing error two layers down.

**`citra-app-builder` is not `smart-app-service`.** They are easy to confuse
because one is built from the other's directory. `smart-app-service` is a
long-running service in the fleet, `FROM python:3.11-slim`; it is the thing you
talk to when you build or run a Decision App. `citra-app-builder` is an
**ephemeral, per-user container** it spawns to do the building, isolated on its
own no-egress network. Rebuilding one does not rebuild the other.

The script also creates the two egress networks the host attaches sandboxes to.
`citra-action-egress` is `--internal` (no route off the box) and
`citra-action-approved-egress` deliberately is not; making the second one
internal breaks every spawn.

If the sandbox build fails, `start.sh` warns and carries on. That is deliberate:
**running** Decision Apps is unaffected, only *building* them and code execution
in chat need these images. Re-run the script once it is fixed.


### Running it again, and after a reboot

Every service is declared `restart: unless-stopped`, so **the stack comes back
by itself** when Docker starts — after a reboot you do not need to run anything.
The one exception is `mongodb-init-rs`, which is `restart: no` on purpose: it
initialises the Mongo replica set once and is meant to exit.

For everything else:

| | |
|---|---|
| `make stop` | stop the containers, keep them — fastest to resume |
| `make up` | bring them back, no rebuild and **no re-seeding** |
| `make down` | stop and remove the containers; **data volumes survive** |
| `make down ARGS=-v` | also wipe the volumes — this destroys the demo data |
| `make start` | full phase 2 again: services, super-admin, and re-seed the demo |

`make up` is the one you want after a `make down`. `make start` also works but
re-runs the seed, which is slower and unnecessary if the data is still there.

If you changed source code, `make up` will not rebuild — use
`docker compose -f docker-compose.quickstart.yml up -d --build <service>`.

### What the demo gives you

A bank with five departments and fourteen officer personas, a Postgres
system-of-record holding ~211,000 rows across 16 tables, an MCP serving it,
a SOP library in Milvus, and four published Decision Apps: loan triage,
collections priority, claims triage, and sales performance.


<p align="center">
  <img alt="The Decision Apps list after installing the demo: claim triage, collections priority, loan triage and a sales dashboard"
       src="https://raw.githubusercontent.com/Trustedwear-Tech/citra-decision-system/main/assets/screens/02-decision-apps.png" width="100%">
</p>

<p align="center"><i>What is on your home screen after <code>make wizard</code>.</i></p>


<p align="center">
  <img alt="The SOP Library listing the acme-bank Policy Library, readable org-wide"
       src="https://raw.githubusercontent.com/Trustedwear-Tech/citra-decision-system/main/assets/screens/panels/12-sop-library.png" width="100%">
</p>

<p align="center"><i>The rules layer. Recommendations cite these documents by section, and your SOPs always win over anything the app has learned from officers.</i></p>

More screens — memory, learning batch, money impact, screening health, the
kill switches — are in [`assets/screens/panels/`](assets/screens/panels/),
captured from a running install by
[`scripts/quickstart/capture_panels.py`](scripts/quickstart/capture_panels.py).

### Signing in

| What | Value |
|------|-------|
| Web UI | http://localhost:8081 |
| Super-admin | `admin@citra-ai.com` / `ADMIN_PASSWORD` from `.env` (printed by `make start`) |
| Home org | **Citra AI** (`citra-ai`) -- can impersonate into the demo org |
| MinIO console | http://localhost:9001 (`minioadmin` / `minioadmin`) |

Sign in as the super-admin, then **impersonate** an `acme-bank` persona (user
menu -> *Login as User*) to see what an officer sees. Open a Decision App and
the loop is all on one screen: a recommendation with its citations, approve or
override with a reason, the governed write back to Postgres, and the outcome
folded into memory for the next case.

`ALLOW_DEV_LOGIN=true` in `.env` also enables a passwordless local sign-in for
any seeded persona. It is local-only and fail-closed in production.

### Driving it headlessly

The same engine with no UI at all. Start from the app's own contract -- it is
self-describing, so you do not have to guess the request shape:

```bash
# Schema, endpoints, evidence requirements and governance rules for THIS app
curl -H "Authorization: Bearer $TOKEN" \
  http://localhost:9100/apps/loan-application-triage/decision-contract

# 1. Recommend -> reasoning, citations, cited_precedents, planned_writes, plan_hash
curl -X POST -H "Authorization: Bearer $TOKEN" \
  http://localhost:9100/apps/loan-application-triage/run -d '{...}'

# 2. Approve -> the schema-validated commit, keyed by the run's correlation_id
curl -X POST -H "Authorization: Bearer $TOKEN" \
  http://localhost:9100/apps/loan-application-triage/run/$CORRELATION_ID/approve \
  -d '{"decision":"approve","expected_plan_hash":"...","decision_reason":"..."}'
```

Two governance details worth knowing before you build against it:

- **`expected_plan_hash` is a display-equals-commit guard.** `/run` returns a
  hash of the writes as you displayed them; echo it back on approve and a plan
  that changed in between is rejected with a 409 rather than quietly
  committed.
- **Per-item review is server-enforced.** When an app's `item_review_gate` is
  `hard`, every non-case `item_finding` must be dispositioned via
  `POST /apps/{slug}/items/{item_id}/feedback` before approve will succeed --
  and a rejection requires a reason, because that reason is what trains the
  memory.

`POST /apps/{slug}/tool/{tool_name}` records a decision made **without** the AI,
so a human-direct call still lands on the same ledger.

### Pointing it at your own data

You are writing one ontology file. You do not have to write it by hand.

**Step 1 -- generate the skeleton from a live database.** Point the
introspection script at a system you already run and it writes the datasets,
columns, types, primary keys and foreign-key relationships for you, plus
enum/range hints for low-cardinality and numeric columns:

```bash
python scripts/quickstart/introspect_source.py --help
```

It speaks PostgreSQL, MySQL, SQL Server, MongoDB, OData/SAP, Salesforce and
REST. With `--describe` it will also draft column descriptions, semantic types
and PII flags with an LLM -- those are exactly the fields the query planner
reasons over, so they are worth having, and worth reading before you keep them.

**Step 2 -- add the meaning that no database can tell you.** This is the part
that is genuinely yours to decide, and it is short:

| What you are deciding | Where it goes |
|---|---|
| Who this deployment is, and where | `domain: { vertical, sub_vertical, country }` on each source. Drives currency, date order and which ID checksums run. |
| Who may read this system | `visibility.roles_allowed`, plus `cross_org_ids` / `public_within_org` if you need them. |
| How to reach it, without secrets in the file | `connection.env_prefix` -- the container reads `{PREFIX}_HOST`, `{PREFIX}_USER`, … from its own environment. |
| Which table holds completed decisions | `decision_history` on that dataset, naming the outcome column and which values are good / bad. This is what the learning loop feeds on. |
| What "value" means for this workflow | `value_semantics` -- recovered, prevented, sanctioned or settled, and which column carries the exposure. Pin it on day zero, not at the end of the pilot. |
| **Whether this table needs fraud screening** | `fraud_screening.applies: true` on that dataset -- plus the identity fields, value field, incident date, and GPS radius if location matters. Omit the block entirely and screening stays off unless a column declares an `artifact_role`. |
| **What each document column is** | `artifact_role` per column: `evidence`, `identity`, `supporting`, `payment_proof`. Without this, "we have seen this file before" cannot be interpreted. |
| **What may be written back, if anything** | `write_actions` on the dataset, each with a fixed parameterized `sql_template`. Only the columns that statement sets can ever change. Omit it and the table is read-only. |

Fraud screening is per table, and off by default. A table that says nothing
about fraud gets no fingerprinting at all -- so turn it on only where the
question "has this artifact appeared before?" is actually meaningful.

**Step 3 -- validate before you boot.** The registry rejects unknown keys, so
a typo is caught here rather than silently disabling a feature at runtime:

```bash
make validate-sources FILE=path/to/your/sources.json
```

**Step 4 -- restart that MCP and re-crawl** so the catalogue picks up the new
tables and the builder can offer them.

Starter files worth copying rather than starting blank:
`source-mcp-template/templates/` has one per vertical and country --
`insurance-claims-IN`, `banking-loan_recovery-IN`, `utility-power_recovery-IN`,
`insurance-claims-US`, `field_service-equipment_inspection-US`. The insurance
ones are the best worked example of a fully-armed fraud block.

`docs/change-the-demo.md` walks the whole path end to end.

### AI models

The wizard asks for **one OpenRouter key** and wires it to everything:

| Role | Default | Why |
|---|---|---|
| Reasoning / NL->SQL | `deepseek/deepseek-v4-pro:nitro` | open weights |
| Embeddings | `baai/bge-m3` at 768 | open weights; the client requests `dimensions` so it returns 768 rather than its native 1024, matching the Milvus collection |
| Vision | `qwen/qwen3-vl-32b-instruct` | open weights |
| Image generation | *off* -- Runware if you want it | not served by OpenRouter |

One key, one bill, one thing that can be wrong. Both defaults are open-weights,
so nothing here depends on a proprietary model.

Citra talks to any OpenAI-compatible API, so **swapping is an `.env` edit, not a
migration**: point `LLM_BASE_URL` / `EMBEDDING_BASE_URL` / `VISION_BASE_URL` at
your own vLLM or TGI endpoint and no prompt leaves your network. Self-hosting
`bge-m3` is the same edit -- keep 768, or set `EMBEDDING_DIMENSION=0` for its
native 1024 and match `MILVUS_VECTOR_DIM`.

> **Changing the embedding model means re-ingesting**, even at the same
> dimension. Vectors written by one model do not share an embedding space with
> another model's queries, so old rows quietly stop matching rather than
> failing. Re-run the seed (or your own ingestion) after any change.

The model is a commodity input you can swap. Your decision memory stays put.

---


## The screens

Every surface the demo ships with, captured on a freshly seeded install.

**Landing**

<p align="center">
  <img alt="The landing page before signing in" src="https://raw.githubusercontent.com/Trustedwear-Tech/citra-decision-system/main/assets/screens/00-landing.png" width="100%">
</p>

**Home**

<p align="center">
  <img alt="The home screen after signing in, showing the operations and admin sections" src="https://raw.githubusercontent.com/Trustedwear-Tech/citra-decision-system/main/assets/screens/01-home.png" width="100%">
</p>

**Admin section**

<p align="center">
  <img alt="The admin section of the home screen, full page" src="https://raw.githubusercontent.com/Trustedwear-Tech/citra-decision-system/main/assets/screens/panels/09-home-admin.png" width="100%">
</p>

**Dashboards**

<p align="center">
  <img alt="Live KPI and chart views" src="https://raw.githubusercontent.com/Trustedwear-Tech/citra-decision-system/main/assets/screens/panels/10-dashboards.png" width="100%">
</p>

**Operations chat**

<p align="center">
  <img alt="Governed natural-language questions over operational data" src="https://raw.githubusercontent.com/Trustedwear-Tech/citra-decision-system/main/assets/screens/panels/11-operations-chat.png" width="100%">
</p>

**SOP library**

<p align="center">
  <img alt="The policy corpus recommendations cite" src="https://raw.githubusercontent.com/Trustedwear-Tech/citra-decision-system/main/assets/screens/panels/12-sop-library.png" width="100%">
</p>

**Learning batch**

<p align="center">
  <img alt="Officer feedback folded into memory" src="https://raw.githubusercontent.com/Trustedwear-Tech/citra-decision-system/main/assets/screens/panels/18-learning-batch.png" width="100%">
</p>

**Success rate**

<p align="center">
  <img alt="How often recommendations are accepted" src="https://raw.githubusercontent.com/Trustedwear-Tech/citra-decision-system/main/assets/screens/panels/19-success-rate.png" width="100%">
</p>

**Money impact**

<p align="center">
  <img alt="Value recovered and protected" src="https://raw.githubusercontent.com/Trustedwear-Tech/citra-decision-system/main/assets/screens/panels/20-money-impact.png" width="100%">
</p>

**Screening health**

<p align="center">
  <img alt="Fraud checks and false alarms" src="https://raw.githubusercontent.com/Trustedwear-Tech/citra-decision-system/main/assets/screens/panels/21-screening-health.png" width="100%">
</p>

**Automation control**

<p align="center">
  <img alt="Kill switches: halt runs and writes" src="https://raw.githubusercontent.com/Trustedwear-Tech/citra-decision-system/main/assets/screens/panels/22-automation.png" width="100%">
</p>

**Manage users**

<p align="center">
  <img alt="Organisation membership and roles" src="https://raw.githubusercontent.com/Trustedwear-Tech/citra-decision-system/main/assets/screens/panels/14-manage-users.png" width="100%">
</p>

**Departures**

<p align="center">
  <img alt="Deactivation and handover" src="https://raw.githubusercontent.com/Trustedwear-Tech/citra-decision-system/main/assets/screens/panels/15-departures.png" width="100%">
</p>

**Managed resources**

<p align="center">
  <img alt="Connections and sources IT manages" src="https://raw.githubusercontent.com/Trustedwear-Tech/citra-decision-system/main/assets/screens/panels/16-resources.png" width="100%">
</p>

**Login as user**

<p align="center">
  <img alt="Audited impersonation" src="https://raw.githubusercontent.com/Trustedwear-Tech/citra-decision-system/main/assets/screens/panels/23-login-as-user.png" width="100%">
</p>


---

When something will not start, see [Troubleshooting](Troubleshooting).
