# Snowflake Cortex deployment & testing guide

This is a hands-on, step-by-step walkthrough for taking the stored procedures
in `snowflake/procs/` from "code on your laptop" to "callable SQL procedures
inside a real Snowflake account," and for testing them at every stage along
the way. It's written for learning, so it explains *why* each step exists,
not just the command to paste. If you want the concepts and design rationale
behind these procs (the reflection loop, the JSON-extraction workaround,
why there's no network access), read [`../README.md`](../README.md) Part 3
first — this doc assumes you've read that and picks up at "okay, now how do I
actually run this against a real account."

Every command and behavior below was checked against the current official
Snowflake docs as of this writing (linked inline); where a detail wasn't
documented explicitly, that's called out rather than guessed.

Two testing tracks exist and you'll use both:

| Track | What it tests | Needs a Snowflake account? |
|---|---|---|
| **Unit tests** (`snowflake/tests/`) | The pure-Python logic in each proc — prompt building, JSON parsing, markdown rendering | No |
| **Live SQL calls** (`CALL ...` after deploy) | The actual `snowflake.cortex.complete()` call, real model output, end-to-end behavior | Yes |

Run unit tests constantly while iterating; only deploy and run live calls
when you want to check the real thing.

---

## Part A — Local testing (no Snowflake account needed)

This is the fast inner loop. Every proc module keeps its one Snowflake-facing
call isolated in `cortex_common.complete()` (see `procs/cortex_common.py`);
everything else — prompt strings, `extract_json`, markdown rendering,
classification lookup tables — is plain, session-free Python. That split is
what makes this possible at all.

### A.1 Install dependencies

```bash
cd PM_Agent            # repo root, not snowflake/
uv sync --group dev     # pytest lives in the dev dependency group (pyproject.toml)
```

### A.2 Run the test suite

```bash
uv run pytest snowflake/tests -v
```

You should see one passing test file per proc (`test_draft_prd.py`,
`test_classify_data_product.py`, `test_reporting_intent.py`,
`test_draft_diagram_brief.py`) plus `test_cortex_common.py`. None of these
open a network connection or need Snowflake credentials — open
`snowflake/tests/conftest.py` and `test_cortex_common.py` and look for
`_FakeSession`: it's a small stand-in class shaped just enough like a real
Snowpark `Session` (`.sql(...).collect()`) to satisfy `cortex_common.complete`
without a real database anywhere nearby. Tests for the other procs go one
step further and monkeypatch `cortex_common.complete` directly, so they can
assert on prompt *content* (e.g. "does the reviewer prompt include the
previous draft's `missing_requirements`?") without caring what a fake LLM
would say back.

### A.3 What this catches vs. what it can't

Unit tests catch: broken JSON-key names, a reviewer prompt that forgot to
include prior feedback, a markdown renderer that crashes on an empty list,
regressions in `_ARTEFACTS_BY_PATTERN`/`_NEXT_AGENTS_BY_PATTERN` lookups.

Unit tests **cannot** catch: whether the real model actually returns valid
JSON for a given prompt, whether `claude-4-sonnet` is enabled in your
account/region, whether your role has permission to call
`snowflake.cortex.complete`, or whether the deployed procedure's signature
in `snowflake.yml` actually matches the Python handler. Those all require
Part B and C below.

### A.4 Editing a proc and re-testing

Change a prompt string or a piece of formatting logic in `procs/*.py`, then
re-run `uv run pytest snowflake/tests`. If you added a new field to the
JSON schema a proc expects back from the model, update both the prompt's
"Return ONLY a JSON object with keys: ..." instruction *and* the test's fake
response — they have to stay in sync since nothing enforces the shape at
this layer (this is exactly the "no native structured output on Cortex"
tradeoff explained in the main README, §3.1).

---

## Part B — One-time environment setup (per Snowflake account)

Do this once per account/environment you deploy to (e.g. once for a dev
account, again for prod). Skip anything you've already done.

### B.1 Install Snowflake CLI

The **Snowflake CLI** (`snow`) is the officially supported tool for this —
not the older `snowsql`, which Snowflake now documents as legacy in favor of
Snowflake CLI ([Migrating from SnowSQL to Snowflake CLI](https://docs.snowflake.com/en/user-guide/snowsql-migrate)).

```bash
pip install snowflake-cli
# or: brew tap snowflakedb/snowflake-cli && brew install snowflake-cli   (macOS)
# or: download a binary installer from the Snowflake CLI docs for Windows/Linux

snow --version
snow --help    # should list connection, sql, snowpark, streamlit, ... subcommands
```

Reference: [Getting Started with Snowflake CLI](https://www.snowflake.com/en/developers/guides/getting-started-with-snowflake-cli/).

### B.2 Create a named connection

```bash
snow connection add
```

This is interactive and asks for: connection name, account identifier,
username, authenticator/password, and (optionally) default role, warehouse,
database, and schema. Answer with values matching the account you're
deploying to — for this repo's default `snowflake.yml`, that means a role
that can create objects in database `PO_AI_DEV`, schema `PO_AGENT` (or set
your own via `--database`/`--schema` overrides at deploy time — see B.4).

Verify it works and set it as your default so you don't have to pass
`--connection` on every command:

```bash
snow connection test --connection <your-connection-name>
snow connection set-default <your-connection-name>
```

Reference: [Managing Snowflake connections](https://docs.snowflake.com/en/developer-guide/snowflake-cli/connecting/configure-connections).

### B.3 Create the database, schema, and a warehouse (if they don't already exist)

`snow snowpark deploy` will auto-create a **stage** for you if one doesn't
exist (see B.4), but it does not document creating the database or schema
for you — those need to already exist. Run this once, with a role that has
`CREATE DATABASE`/`CREATE SCHEMA` privileges (e.g. `SYSADMIN`), in a
Snowsight worksheet or via `snow sql`:

```sql
CREATE DATABASE IF NOT EXISTS PO_AI_DEV;
CREATE SCHEMA IF NOT EXISTS PO_AI_DEV.PO_AGENT;
CREATE WAREHOUSE IF NOT EXISTS PO_AGENT_WH WITH WAREHOUSE_SIZE = 'XSMALL' AUTO_SUSPEND = 60 AUTO_RESUME = TRUE;

-- Grant the deploying role what it needs on this schema:
GRANT USAGE ON DATABASE PO_AI_DEV TO ROLE <your_role>;
GRANT USAGE ON SCHEMA PO_AI_DEV.PO_AGENT TO ROLE <your_role>;
GRANT CREATE PROCEDURE ON SCHEMA PO_AI_DEV.PO_AGENT TO ROLE <your_role>;
GRANT CREATE STAGE ON SCHEMA PO_AI_DEV.PO_AGENT TO ROLE <your_role>;
GRANT USAGE ON WAREHOUSE PO_AGENT_WH TO ROLE <your_role>;
```

If you'd rather target a different database/schema than the `PO_AI_DEV` /
`PO_AGENT` names baked into `snowflake.yml`, either edit those `identifier:`
blocks in [`snowflake.yml`](snowflake.yml), or override them per-command with
`snow snowpark deploy --database <db> --schema <schema>`.

### B.4 Confirm Cortex access

`snowflake.cortex.complete()` requires the calling role to have access to
Cortex AI functions, and the specific model (`claude-4-sonnet`, set in
`cortex_common.DEFAULT_MODEL_NAME`) needs to be enabled for your account's
region — either natively or via cross-region inference. Check both:

```sql
-- Sanity-check Cortex works at all for your current role:
SELECT snowflake.cortex.complete('claude-4-sonnet', 'Say OK.');
```

If this errors with a permissions message, your account admin needs to grant
your role the `SNOWFLAKE.CORTEX_USER` database role:

```sql
GRANT DATABASE ROLE SNOWFLAKE.CORTEX_USER TO ROLE <your_role>;
```

If it errors with a "model not available" style message, either pick a model
your account's `CORTEX_MODELS_ALLOWLIST` permits, or enable cross-region
inference (`ALTER ACCOUNT SET CORTEX_ENABLED_CROSS_REGION = 'ANY_REGION';`,
run by an admin) so requests can route to a region where the model is
available. See [Cross-region inference](https://docs.snowflake.com/en/user-guide/snowflake-cortex/cross-region-inference)
and [Models and regional availability for Cortex AI Functions](https://docs.snowflake.com/en/user-guide/snowflake-cortex/aisql-regional-availability).

This is the one step most likely to trip you up on a fresh account — if
later steps fail with an opaque error from inside a deployed procedure,
come back and re-run the `SELECT snowflake.cortex.complete(...)` sanity check
above first.

---

## Part C — Deploying the stored procedures

### C.1 What `snowflake.yml` declares

**File:** [`snowflake.yml`](snowflake.yml)

This is a Snowflake CLI **project definition file** (`definition_version:
"2"`) — the officially documented format for declaring Snowpark objects
(see [About project definition files](https://docs.snowflake.com/en/developer-guide/snowflake-cli/project-definitions/about)
and [Create a Snowpark project definition](https://docs.snowflake.com/en/developer-guide/snowflake-cli/snowpark/create)).
It currently declares four `procedure` entities:

| Entity | SQL name | Handler | Signature |
|---|---|---|---|
| `draft_prd` | `PO_AI_DEV.PO_AGENT.DRAFT_PRD` | `draft_prd.run` | `(notes string)` |
| `classify_data_product` | `PO_AI_DEV.PO_AGENT.CLASSIFY_DATA_PRODUCT` | `classify_data_product.run` | `(prd_text string)` |
| `capture_reporting_intent` | `PO_AI_DEV.PO_AGENT.CAPTURE_REPORTING_INTENT` | `reporting_intent.run` | `(prd_text string, context_text string)` |
| `draft_diagram_brief` | `PO_AI_DEV.PO_AGENT.DRAFT_DIAGRAM_BRIEF` | `draft_diagram_brief.run` | `(prd_text string, reporting_intent_text string)` |

Each `run(session, ...)` function's parameter list (after `session`, which
Snowpark injects automatically and is never part of the SQL signature) must
match the `signature:` block exactly, in order — this is a common source of
deploy-time or call-time errors if the two drift apart.

Note there's no `requirements.txt` anywhere in `snowflake/` — every proc only
imports `json`, `re`, and `cortex_common` (this repo's own module, uploaded
alongside every proc via `artifacts: [{src: procs/, dest: ./}]`). All of
that is stdlib or Snowpark-provided, so there are no third-party packages to
resolve from Anaconda. If you add a proc that needs a real third-party
package later, `snow snowpark build` handles that automatically (it reads
`requirements.txt`, resolves what's available on Snowflake's Anaconda
channel into `requirements.snowflake.txt`, and packages anything else into a
`dependencies.zip` — see [Build a Snowpark project](https://docs.snowflake.com/en/developer-guide/snowflake-cli/snowpark/build)) —
you'd add a `snowflake/requirements.txt` at that point, nothing in
`snowflake.yml` needs to change.

### C.2 Build

```bash
cd snowflake            # snowflake.yml must be the current directory (or pass --project)
snow snowpark build
```

This zips up the `src` referenced by each entity's `artifacts` block (here,
the whole `procs/` directory) into a local `.zip` per the docs (see [Build a
Snowpark project](https://docs.snowflake.com/en/developer-guide/snowflake-cli/snowpark/build)).
No network call happens yet — this step is purely local packaging, safe to
run as often as you like.

### C.3 Deploy

```bash
snow snowpark deploy
```

This uploads the built artifact(s) to a stage (the `stage: dev_deployment`
named in each entity — created automatically if it doesn't exist yet, per
the [deploy command reference](https://docs.snowflake.com/en/developer-guide/snowflake-cli/command-reference/snowpark-commands/deploy))
and issues `CREATE PROCEDURE` for each entity.

**First deploy** to a fresh database/schema: this just works, since nothing
exists yet.

**Re-deploying** after changing a proc: Snowflake CLI is deliberately
"production-safe" here — if an entity with that name already exists, plain
`snow snowpark deploy` refuses to touch it. Use `--replace` to update
existing procedures with your local changes:

```bash
snow snowpark deploy --replace
```

Add `--prune` if you also want stale files removed from the stage during
redeploy (harmless for this repo since every deploy re-uploads the whole
`procs/` directory anyway).

### C.4 Verify what got created

```sql
SHOW PROCEDURES LIKE '%' IN SCHEMA PO_AI_DEV.PO_AGENT;
DESCRIBE PROCEDURE PO_AI_DEV.PO_AGENT.DRAFT_PRD(STRING);
```

`DESCRIBE PROCEDURE` needs the full argument-type signature in parentheses
(no argument names) — this trips people up the first time; `SHOW PROCEDURES`
first if you're not sure of the exact signature Snowflake registered.

---

## Part D — Testing the deployed procedures live

Now you're calling the real thing — a real warehouse spins up, a real
`snowflake.cortex.complete()` call goes out, real tokens get billed. Small
inputs are enough to prove the wiring; save larger PRDs for actual use.

### D.1 Pick a warehouse to run on

```sql
USE WAREHOUSE PO_AGENT_WH;   -- or whichever warehouse your connection defaults to
```

### D.2 Call each procedure

```sql
-- draft_prd: single string in, rendered PRD markdown out.
-- Internally loops writer -> reviewer up to 3 passes (MAX_PASSES in draft_prd.py) —
-- expect this call to take longer than the others, it's making up to 6 model calls.
CALL PO_AI_DEV.PO_AGENT.DRAFT_PRD(
  'Finance wants a weekly report of unpaid invoices over 30 days, broken out '
  'by region. Must be in Power BI. Nice to have: Slack alert when the total '
  'crosses $500k.'
);
```

```sql
-- classify_data_product: feed it a PRD (e.g. the DRAFT_PRD output above,
-- or plain notes) and get a delivery-pattern classification back.
CALL PO_AI_DEV.PO_AGENT.CLASSIFY_DATA_PRODUCT(
  'PRD: weekly unpaid-invoices-over-30-days report by region in Power BI. '
  'No existing Platinum model covers invoice aging today.'
);
```

```sql
-- capture_reporting_intent: second argument is the grounding-context seam
-- (see README.md 3.4) -- pass '' until you have a real retrieval step.
CALL PO_AI_DEV.PO_AGENT.CAPTURE_REPORTING_INTENT(
  'PRD: weekly unpaid-invoices-over-30-days report by region in Power BI.',
  ''
);
```

```sql
-- draft_diagram_brief: second argument is optional context from a prior
-- CAPTURE_REPORTING_INTENT call -- pass '' if you don't have one yet.
CALL PO_AI_DEV.PO_AGENT.DRAFT_DIAGRAM_BRIEF(
  'PRD: weekly unpaid-invoices-over-30-days report by region in Power BI.',
  ''
);
```

### D.3 What "it worked" looks like

Each call returns a single string (per `returns: string` in `snowflake.yml`)
— readable markdown/plain text, not a table or JSON blob, because every
proc's last step is its own `_render_markdown`/`_format_result` function
(Part A). If you get back a Python traceback instead, it's almost always one
of:

- **`ValueError: Model did not return JSON: ...`** — the model ignored the
  "Return ONLY a JSON object" instruction. Re-run (Cortex responses aren't
  perfectly deterministic even at low temperature); if it's frequent, the
  prompt's JSON instruction may need to be more explicit for the specific
  model you're targeting — see `cortex_common.extract_json` and the "no
  native structured output" tradeoff in the main README, §3.1.
- **A permissions/"model not available" SQL error surfacing immediately,
  before any proc logic could even run** — go back to Part B.4.
- **`Unknown user-defined function` / procedure not found** — the deploy
  didn't complete, or you're querying the wrong database/schema/role
  context; re-run `SHOW PROCEDURES` (C.4).

### D.4 Checking cost/duration

Every `CALL` shows up in Snowflake's query history like any other statement
— `Snowsight → Activity → Query History`, or:

```sql
SELECT query_text, total_elapsed_time, warehouse_name, credits_used_cloud_services
FROM table(information_schema.query_history_by_user())
WHERE query_text ILIKE '%DRAFT_PRD%'
ORDER BY start_time DESC
LIMIT 10;
```

`draft_prd` is the most expensive call by far (up to 6 `COMPLETE` calls per
invocation via its reflection loop) — keep that in mind if you're
iterating on prompts and calling it repeatedly.

---

## Part E — The Lucid diagram feature (separate, higher-privilege deploy)

`draft_diagram_brief` deploys the same way as the other three procs above —
it's just another entity in `snowflake.yml`. But actually *drawing* a
diagram in Lucid additionally requires a **Cortex Agent** and an **External
MCP Server**, which are account-level objects, not Snowpark entities, so
they're created by hand-running raw SQL with a more privileged role, as a
separate step from the routine `snow snowpark deploy` loop above. This is
covered in full in [`LUCID_DIAGRAM_AGENT.md`](LUCID_DIAGRAM_AGENT.md) —
read that once you've got the four base procedures deployed and tested via
Part D, since it assumes `draft_diagram_brief` already exists.

---

## Part F — Cleaning up

To tear down what you created in Part B/C (useful for a scratch/dev account
you don't want lingering objects in):

```sql
DROP PROCEDURE IF EXISTS PO_AI_DEV.PO_AGENT.DRAFT_PRD(STRING);
DROP PROCEDURE IF EXISTS PO_AI_DEV.PO_AGENT.CLASSIFY_DATA_PRODUCT(STRING);
DROP PROCEDURE IF EXISTS PO_AI_DEV.PO_AGENT.CAPTURE_REPORTING_INTENT(STRING, STRING);
DROP PROCEDURE IF EXISTS PO_AI_DEV.PO_AGENT.DRAFT_DIAGRAM_BRIEF(STRING, STRING);
DROP SCHEMA IF EXISTS PO_AI_DEV.PO_AGENT;
DROP DATABASE IF EXISTS PO_AI_DEV;   -- only if nothing else uses PO_AI_DEV
DROP WAREHOUSE IF EXISTS PO_AGENT_WH;
```

Only run the `DROP DATABASE`/`DROP WAREHOUSE` lines if you created them
solely for this exercise — don't run them against a shared account without
checking who else might be using those objects first.

---

## Troubleshooting quick reference

| Symptom | Likely cause | Fix |
|---|---|---|
| `snow: command not found` | CLI not installed / not on `PATH` | Re-run Part B.1; restart your shell |
| `snow connection test` fails | Wrong account identifier or credentials | Re-run `snow connection add`; account identifiers usually look like `orgname-accountname`, not a URL |
| `snow snowpark deploy` says object already exists and exits | Re-deploying without `--replace` (production-safe default) | Add `--replace` (C.3) |
| `Insufficient privileges to operate on schema` | Deploying role lacks `CREATE PROCEDURE`/`CREATE STAGE` | Re-run the `GRANT`s in B.3 |
| `SQL access control error` on `snowflake.cortex.complete` | Role lacks Cortex access | Grant `SNOWFLAKE.CORTEX_USER` database role (B.4) |
| Model/region error from `COMPLETE` | Model not in your account's allowlist/region | Check `CORTEX_MODELS_ALLOWLIST`, consider `CORTEX_ENABLED_CROSS_REGION` (B.4) |
| `ValueError: Model did not return JSON` at call time | Model didn't follow the JSON-only instruction | Usually transient — retry; see D.3 |
| Unit tests fail (`uv run pytest snowflake/tests`) | Actual logic bug, not a Snowflake issue | These need zero Snowflake access — reproduce and fix locally before even touching `snow` |

## See also

- [`../README.md`](../README.md) Part 3 — the *why* behind every design
  choice in this track (the JSON-extraction workaround, the reflection loop
  without a graph framework, the classifier, the grounding seam).
- [`LUCID_DIAGRAM_AGENT.md`](LUCID_DIAGRAM_AGENT.md) — the Cortex
  Agent + External MCP Server setup for the diagram feature specifically.
- [`../CLAUDE.md`](../CLAUDE.md) — terse, AI-assistant-oriented repo map.
