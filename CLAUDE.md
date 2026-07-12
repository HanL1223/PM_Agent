# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this repo is

A local multi-agent AI assistant for Jira project management (ticket drafting, sprint health, PRD writing,
NL querying), built as a LangGraph state graph. Package name is `jira-pm-agent` (`pmagent/`).

The repo also contains a **second, independent implementation** in `snowflake/`: Snowflake Cortex stored
procedures (real `.py` modules + a Snowflake CLI project, see below) that reimplement the PRD writer/reviewer
loop (`draft_prd`) and a delivery-pattern classifier (`classify_data_product`) for deployment inside Snowflake,
independent of the `pmagent` LangGraph app. This is the intended **production** track — `pmagent/` is the
prototyping sandbox for agent design and prompts. When editing PRD-writing logic, check whether the change
belongs in `pmagent/`, `snowflake/procs/`, or both — they currently duplicate prompts/logic rather than sharing
it.

This is a 12-agent target pipeline (workshop/requirements capture → discovery docs → data-product
classification → reporting intent → ingestion/integration design → Platinum modelling → Gold modelling →
reporting design → JIRA delivery → testing/regression → governance review → knowledge reuse). Only the first
few stages are implemented so far, because Snowflake Cortex here has no external network/API access
(no live Jira/Confluence/source-system connectivity) — so only agents that are pure text-in/text-out are
buildable today. Stages needing prior context (existing models, standards, source schemas) should accept that
context as a plain text/JSON parameter (a stand-in for a future connector) rather than assume live access.

### `snowflake/` — Cortex stored procedures (production track)

```
snowflake/
  snowflake.yml        # Snowflake CLI (`snow`) project definition — deploy with:
                        #   snow snowpark build && snow snowpark deploy   (run from snowflake/)
  procs/
    draft_prd.py             # entity `draft_prd`      -> DRAFT_PRD(notes)
    classify_data_product.py # entity `classify_data_product` -> CLASSIFY_DATA_PRODUCT(prd_text)
    reporting_intent.py      # entity `capture_reporting_intent` -> CAPTURE_REPORTING_INTENT(prd_text)
  tests/                # pure-Python unit tests, no Snowflake connection needed — run with:
                         #   uv run pytest snowflake/tests
```

Each proc module keeps the Cortex call isolated in one function (`_complete(session, prompt)`); everything
else (prompt-building, JSON parsing, markdown rendering, classification formatting) is plain, session-free
Python so it's unit-testable and independently iterable without deploying to Snowflake. Preserve that split
when adding new procs — it's what makes local development/testing possible at all instead of hand-escaping a
whole script inside a `CREATE PROCEDURE ... AS '...'` string literal.

## Commands

This project uses `uv` (see `uv.lock`, `pyproject.toml`).

```bash
uv sync                  # install dependencies
uv run main.py           # run the entry point (currently just a placeholder "Hello from pm-agent!")
```

There is no test suite, linter, or CI config in this repo yet.

Optional dev tooling (`dependency-groups.dev` in `pyproject.toml`): `langgraph-cli`/`langgraph-api` for
`langgraph dev` (LangGraph Studio), and `ipykernel`. Not required for the plain CLI.

### Verifying a fresh checkout / new device

`graph.py` doesn't exist yet (see Architecture below), so there's no end-to-end CLI to run — `uv run main.py`
is still the placeholder. To sanity-check that a fresh `uv sync` actually works (deps resolve, mock Jira data
loads, tools execute), exercise the pieces directly:

```bash
uv run python -c "
from pmagent.tools.jira_tools import search_jira_issues, get_sprint_status
print(search_jira_issues.invoke({'jql': 'project = CSCI'})[:200])
print(get_sprint_status.invoke({'sprint_id': 24})[:200])
"
```

Both should print real output sourced from `pmagent/sample_data/mock_jira.json` with zero env config. If this
fails with `FileNotFoundError` on `mock_jira.json`, something is wrong with the checkout path, not the code —
`JiraClient`'s mock path is resolved relative to `pmagent/tools/jira_tools.py`'s own `__file__`, not cwd.

### Environment

Copy `.env sample` to `.env`. Key vars (all read centrally in `pmagent/env.py` — never call `os.getenv`
elsewhere, add new vars there):

- `LLM_PROVIDER`: `anthropic` (default) or `openai`. Only that provider's API key is required.
- `LLM_MODEL`: overrides the per-provider default in `pmagent/env.py`.
- `ANTHROPIC_API_KEY` / `OPENAI_API_KEY`.
- `JIRA_BASE_URL`, `JIRA_EMAIL`, `JIRA_API_TOKEN`, `JIRA_PROJECT_KEY`, `JIRA_STORY_POINTS_FIELD` (defaults to
  `customfield_10052`).
- `JIRA_MOCK`: forced on automatically whenever `JIRA_BASE_URL` is unset, so the app runs against
  `pmagent/sample_data/mock_jira.json` with zero config.
- `LUCID_MCP_URL` (defaults to Lucid's hosted endpoint), `LUCID_MCP_AUTH_TOKEN` (only needed if your Lucid plan
  issued a static OAuth client secret instead of relying on per-user Dynamic Client Registration) — used by
  `pmagent/tools/mcp_tools.py`, the Diagram Agent's MCP client.

## Architecture

### Graph shape (per code comments in `orchestrator.py` / `ticket_agent.py` — **`graph.py` does not exist yet**)

The intended top-level flow is: `classify_node` (orchestrator) routes on `state.route` to one of four lanes —
`ticket_agent`, `sprint_agent`, `query_agent` (handled inline by the orchestrator), `requirements` (a
Writer↔Reviewer subgraph) — via `route_from_classifier`. **This wiring is not yet implemented**: there is no
`graph.py`, and `pmagent/agents/requirements.py` is incomplete (the `RequirementsState` class body is empty,
cut off after line 42). Treat these as in-progress scaffolding, not working code, until `graph.py` exists.

### Core design principles baked into the code (evident from comments — follow them when extending)

- **Structured data over prose between agents.** `PMState` (in `state.py`) carries typed slots
  (`ticket_draft`, `sprint_report`, `prd`, `diagram_brief`) alongside the conversational `messages` channel, so
  the frontend and other agents consume typed data, not parsed text. Routing (`RouteDecision`) and PRD review
  (`ReviewResult`) also use `with_structured_output` / Pydantic schemas rather than parsing free text.
- **LLM does judgment, plain Python does arithmetic/formatting.** Sprint metrics (`compute_sprint_metrics` in
  `jira_tools.py`) and PRD markdown rendering are deterministic Python; the LLM only narrates over them. Never
  push arithmetic that drives a delivery decision into a prompt.
- **Write actions are human-gated.** The Ticket Agent's system prompt (`prompts/ticket_agent.md`) requires
  drafting and explicit user confirmation before `create_jira_issue` is ever called — it must not be used to
  "preview." Preserve this gate in any new write-capable tool/agent.
- **Reflection loop for PRDs.** `requirements.py` (once complete) is meant to be a self-contained LangGraph
  subgraph: `writer → reviewer → (approved? render : back to writer)`, with the Reviewer's structured
  `ReviewResult.missing_requirements` driving whether another pass runs — not a hardcoded loop count.
- **Agent "lanes" share generic wiring.** `agents/common.py` provides `make_agent_node` and
  `make_tools_router` so every specialist (ticket/sprint) is just a `(system_prompt, tools)` pair; the
  node/ToolNode/router plumbing is assembled generically wherever the graph gets built. Add a new specialist by
  declaring its tools + prompt (see `ticket_agent.py` / `sprint_agent.py` as templates), not by copying graph
  wiring.
- **Prompts and domain knowledge live in Markdown, not Python.** System prompts are `.md` files in
  `pmagent/prompts/`, loaded by `prompts/prompts.py`. Domain/process knowledge ("skills") lives in
  `pmagent/skills/<name>/SKILL.md` and is loaded via `pmagent/skills/__init__.py:load_skill` and injected into
  prompts with `.format(skill=...)` (see `_WRITER_PROMPT` / `_REVIEWER_PROMPT` in `requirements.py`). Edit the
  `.md` files for behavior changes; only touch Python for control flow.
- **Reserved seams are intentionally stubbed, not half-built by accident.** `tools/company_knowledge.py`'s
  `retrieve_company_context` is a deliberate no-op unless `sample_data/company_docs/*.md|*.txt` exist — it's
  meant to be swapped for real Confluence/RAG retrieval later without changing its signature or call sites.
  Don't "fix" it into something more complex unless asked to actually implement retrieval.
- **MCP servers follow one pattern: isolate the client, compose tools in the lane.** `tools/mcp_tools.py` is the
  reference for wiring any external MCP server in — `MultiServerMCPClient` config lives in one `_SERVERS` dict,
  discovery (`get_lucid_tools`) is `async`. Because discovery is a network call, a lane that binds MCP tools
  can't use a static `TOOLS = [...]` like `ticket_agent.py`; see `agents/diagram_agent.py`'s `build_tools()` for
  the shape — local tools + `await get_lucid_tools()`, called once at graph-build time, with the graph then run
  via `.ainvoke`/`.astream`. Mirrors `snowflake/sql/lucid_mcp_setup.sql` on the Snowflake track, minus the
  Snowflake-managed connector runtime.

### Jira integration (`pmagent/tools/jira_tools.py`)

- `JiraClient` transparently switches between mock mode (reads `sample_data/mock_jira.json`) and real Jira
  Cloud REST v3 + Agile API based on `env.JIRA_MOCK`. Both code paths must stay in sync in shape — tools call
  the client without knowing which mode is active.
- Search uses the Jira Cloud *enhanced* JQL search endpoint (`/rest/api/3/search/jql`), not the legacy one.
- Sprint lookups distinguish the **user-visible sprint number** (e.g. "Sprint 26") from Jira's **internal
  sprint ID**: `get_sprint_status` takes the internal ID directly, while
  `get_supply_chain_sprint_status`/`find_sprint_id_by_name` resolve a human name (`"Supply Chain Sprint {n}"`,
  per `prompts/sprint_agent.md`) to that ID first. Don't conflate the two when adding sprint tools.
- All read paths normalize raw Jira JSON via `JiraClient._normalise_issue` into one flat shape; downstream code
  (metrics, prompts) only ever sees the normalized shape.
