# PM Agent — Learn-By-Building Tutorial

Goal: after this, you understand every moving part of this repo well enough to
rebuild an equivalent multi-agent LangGraph app from scratch, for a different
domain. Written for someone who knows Python but hasn't built an agent system
before.

Read order matters. Each step builds a concept the next step needs. Don't skip
around on the first pass.

---

## 0. The 60-second mental model

This is a **multi-agent system built as a graph**, not a single chatbot loop.

- A **state object** (`PMState`) flows through the graph. Every node reads it,
  does something, and returns a partial update.
- **Nodes** are either "call an LLM" or "run a tool." Some nodes are plain
  Python (no LLM at all) — arithmetic and formatting never go through a model.
- **Edges**, some conditional, decide what runs next. A router node inspects
  state and picks a path; that's how "classify this request, then send it to
  the right specialist" works.
- **Tools** are typed Python functions the LLM can choose to call
  (`@tool`-decorated). The LLM never touches Jira directly — it calls
  `search_jira_issues`, gets text back, and reasons over that.
- **Specialists ("lanes")** are just a `(system_prompt, tools)` pair. The
  node/router *plumbing* around a lane is generic and shared
  (`agents/common.py`) — adding a new specialist is "write a prompt + pick
  tools," not "write new graph wiring."

Everything below is one of these five ingredients: state, nodes, edges, tools,
prompts.

---

## 1. Prerequisites — LangGraph concepts you need

If you've never used LangGraph, read this section slowly; the rest of the
tutorial assumes it.

### 1.1 `StateGraph`

```python
from langgraph.graph import StateGraph, START, END

g = StateGraph(SomeStateClass)   # SomeStateClass is a Pydantic model
g.add_node("node_name", some_function)
g.add_edge(START, "node_name")
g.add_edge("node_name", END)
graph = g.compile()
graph.invoke({"messages": [...]})
```

- A node is any `function(state) -> dict`. The dict is a *partial* update —
  you don't return the whole state, just the fields that changed.
- `graph.compile()` turns the builder into something runnable.

### 1.2 Reducers — why partial updates don't clobber each other

```python
messages: Annotated[list[BaseMessage], add_messages] = []
```

Normally returning `{"messages": [new_msg]}` from a node would *replace* the
whole list. `Annotated[..., add_messages]` tells LangGraph "use this reducer
function to merge instead of overwrite" — `add_messages` appends. This is the
entire mechanism that gives an agent conversational memory across node calls.
See `pmagent/state.py:20` (the import) and `PMState.messages` at the bottom
of that file.

### 1.3 Conditional edges — routing

```python
g.add_conditional_edges("agent", router_fn)
```

`router_fn(state) -> str` returns the name of the next node (or `END`). This
is how both (a) the orchestrator sends a request to `ticket_agent` vs
`sprint_agent`, and (b) a single lane loops back to its own tool node when the
LLM asks for a tool call, and stops when it doesn't.

### 1.4 Tool calling loop — the shape every lane uses

```
   ┌──────────┐  tool_calls present   ┌───────┐
   │  agent   │ ─────────────────────►│ tools │
   │ (LLM)    │◄───────────────────── │(exec) │
   └────┬─────┘   tool results         └───────┘
        │ no tool_calls
        ▼
       END
```

The LLM is bound to a list of tools (`llm.bind_tools([...])`). When it decides
to call one, its response has `.tool_calls` populated instead of (or with)
text. A `ToolNode` executes those calls and appends `ToolMessage`s back into
state. The router checks `last_message.tool_calls` — present → go run tools,
absent → the LLM is done, end the turn. This exact loop is what
`agents/common.py` factors out (see §4).

### 1.5 Structured output — routing/review without parsing text

```python
router_llm = get_llm().with_structured_output(RouteDecision)
decision: RouteDecision = router_llm.invoke("...")
decision.route  # a real enum-like field, not a string you regex out of prose
```

`RouteDecision`, `ReviewResult`, `PRD`, `DiagramBrief` in `state.py` are all
Pydantic models used this way. This is the difference between a toy demo
("hope the model outputs valid JSON, `json.loads` it and hope again") and
something you'd put in production — the framework validates the shape for
you.

---

## 2. Repo map

```
pmagent/
  state.py            # PMState + every Pydantic schema (read this 2nd)
  env.py               # ALL config reads go through here — single source of truth
  llm.py                # get_llm() — one factory, swap Anthropic/OpenAI via env var
  agents/
    common.py           # make_agent_node / make_tools_router — generic lane wiring
    orchestrator.py      # classifies intent, also directly answers read-only queries
    ticket_agent.py      # declares TOOLS + SYSTEM_PROMPT (draft-then-confirm Jira issue)
    sprint_agent.py      # declares TOOLS + SYSTEM_PROMPT (sprint health reporting)
    requirements.py       # Writer<->Reviewer reflection subgraph (INCOMPLETE, see §6)
    diagram_agent.py      # local tool + MCP-discovered tools (external server pattern)
    spreadsheet_agent.py  # human-approval-gated spreadsheet writes
  tools/
    jira_tools.py         # JiraClient (mock/real) + the @tool functions + pure metrics fn
    diagram_tools.py      # draft_diagram_brief — LLM judgment + deterministic renderer
    mcp_tools.py           # MultiServerMCPClient wiring for Lucid's hosted MCP server
    spreadsheet_tools.py   # Microsoft Graph client + approval-queue tools
    company_knowledge.py   # deliberately-stubbed retrieval seam
  prompts/
    prompts.py            # loads each agents/*.md as a plain string constant
    *.md                  # the actual system prompts — edit these, not Python, for behavior
  skills/
    prd/SKILL.md            # PRD domain knowledge, injected into writer+reviewer prompts
  sample_data/
    mock_jira.json          # fake Jira dataset — zero-config local dev
snowflake/                # separate production track (Cortex stored procs) — ignore for now
tests/                    # pytest, currently just spreadsheet tool unit tests
```

There is **no `graph.py`** yet — nothing wires the lanes into one top-level
graph. That's intentional groundwork left for you (§7 is exactly that
exercise).

---

## 3. Setup — run it locally, zero cloud dependency

```powershell
uv sync
Copy-Item ".env sample" .env
```

Edit `.env`:
- `ANTHROPIC_API_KEY=<your key>` (or set `LLM_PROVIDER=openai` + `OPENAI_API_KEY`)
- Leave `JIRA_BASE_URL` blank — `JIRA_MOCK` auto-turns-on and reads
  `sample_data/mock_jira.json`. Zero Jira account needed.
- Leave `SPREADSHEET_*` / `LUCID_MCP_*` blank unless you're doing §6's optional
  extensions.

Sanity check deps + mock data resolve correctly:

```powershell
uv run python -c "from pmagent.tools.jira_tools import search_jira_issues; print(search_jira_issues.invoke({'jql': 'project = CSCI'})[:200])"
```

If your key only has access to newer model names, also set in `.env`:
```
LLM_MODEL=claude-haiku-4-5-20251001
```
(`claude-3-5-sonnet-latest`, the coded default in `env.py`, may 404 depending
on your key's model access — this is an account/model-availability thing, not
a bug in the repo.)

---

## 4. Read the code in this order

Open each file as you read its section. Don't just read this document —
cross-reference the real file every time.

### 4.1 `pmagent/state.py` — the shared vocabulary

Everything else in the app either fills in or reads one of these schemas.
Note the design principle stated at the top of the file: **agents exchange
structured data, not prose**. `TicketDraft`, `PRD`, `ReviewResult`,
`DiagramBrief`, `RouteDecision` are all typed — this is what lets you build a
UI on top of this later without parsing markdown.

### 4.2 `pmagent/env.py` + `pmagent/llm.py` — config and model factory

`env.py`: every environment variable the app reads, in one file, with
`validate()` failing fast if the selected provider's key is missing. Rule:
never call `os.getenv` anywhere else in the codebase — add new vars here.

`llm.py`: one function, `get_llm(model=None, temperature=0.1)`, that returns
either `ChatAnthropic` or `ChatOpenAI` depending on `env.LLM_PROVIDER`. This
indirection is the entire reason you can switch providers with one env var
instead of a code change.

### 4.3 `pmagent/tools/jira_tools.py` — tools + the "LLM does judgment, Python does arithmetic" rule

Three things live here, deliberately separated:

1. `JiraClient` — a class that transparently switches between reading
   `mock_jira.json` and hitting real Jira REST endpoints, based on
   `env.JIRA_MOCK`. Every caller is agnostic to which mode is active.
2. `compute_sprint_metrics(...)` — **plain Python**, no LLM. Completion %,
   risk level (`LOW`/`MODERATE`/`HIGH`), blocked-ticket detection — all
   deterministic. This is the principle called out in `CLAUDE.md`: never let
   an LLM compute a number a delivery decision depends on.
3. The `@tool`-decorated functions (`search_jira_issues`, `get_sprint_status`,
   `create_jira_issue`, `get_supply_chain_sprint_status`) — these are what
   get bound to an LLM. Note `create_jira_issue`'s docstring: *"ONLY call
   this AFTER the user has explicitly confirmed... Never call it to
   preview."* That instruction is enforced by the **prompt**
   (`prompts/ticket_agent.md`), not by code — the tool itself will happily
   execute if called. Worth sitting with: in this architecture, safety gates
   for write actions are a prompt-engineering problem as much as a code one.

Exercise: run the sanity-check snippet from §3 again, then try
`get_sprint_status.invoke({'sprint_id': 24})` and read the JSON it returns —
that's `compute_sprint_metrics`'s output, unmodified by any model.

### 4.4 `pmagent/agents/common.py` — the reusable lane wiring

Two functions, ~40 lines total, and they're the reason adding a fourth
specialist is cheap:

```python
def make_agent_node(system_prompt, llm_with_tools):
    def node(state):
        response = llm_with_tools.invoke([SystemMessage(system_prompt)] + state.messages)
        return {"messages": [response]}
    return node

def make_tools_router(tools_node_name):
    def router(state):
        last = state.messages[-1]
        return tools_node_name if getattr(last, "tool_calls", None) else END
    return router
```

This *is* the tool-calling loop diagram from §1.4, turned into code. Every
lane (`ticket_agent`, `sprint_agent`, `diagram_agent`) reuses exactly these
two functions.

### 4.5 `pmagent/agents/ticket_agent.py` + `sprint_agent.py` — what a lane declaration looks like

```python
TOOLS = [search_jira_issues, create_jira_issue]
SYSTEM_PROMPT = prompts.ticket_agent_system_prompt
```

That's the entire file (plus a docstring). All the behavior lives in
`prompts/ticket_agent.md`, not here. This is the second design principle:
**prompts and domain knowledge live in Markdown**, so a PM/prompt-engineer can
change agent behavior without touching Python.

Go read `prompts/ticket_agent.md` and `prompts/sprint_agent.md` now — notice
how explicit the workflow instructions are ("draft in your response, NOT via
a tool," "ask for confirmation... do not call the create tool yet"). Prompt
specificity is doing real work here.

### 4.6 `pmagent/agents/orchestrator.py` — routing via structured output

```python
router_llm = get_llm(temperature=0).with_structured_output(RouteDecision)
decision = router_llm.invoke(f"Classify this... Request: {user_text}")
return {"route": decision.route}
```

`classify_node` only ever writes `state.route` — it deliberately never
touches `messages`, so whichever lane runs next sees the user's original,
unmodified request. `route_from_classifier` then maps that string to a node
name for a conditional edge. This file also holds the "query" lane inline
(answers read-only lookups itself with `search_jira_issues` rather than
delegating) — a reasonable shortcut for a lane that's "just one tool, no
write gate needed."

### 4.7 `pmagent/agents/requirements.py` — the reflection-loop pattern (currently incomplete)

Read the module docstring's diagram:

```
START → writer → reviewer ──approved?──► render → END
          ▲                  │ no (and iterations left)
          └──────────────────┘
```

This is the "actor-critic" pattern: a Writer produces a `PRD`, a Reviewer
produces a `ReviewResult` (with `missing_requirements` — see `state.py`), and
if not approved, the Writer runs again *with the Reviewer's feedback in
context*. The loop count isn't hardcoded — the Reviewer's judgment decides
when to stop. This is what makes it an agent instead of a fixed pipeline.

**This file stops at line 42** — `class RequirementsState(BaseModel):` has no
body. It won't even import successfully. Treat it as a spec, not working
code — §6 asks you to finish it as an exercise.

### 4.8 `pmagent/agents/diagram_agent.py` + `pmagent/tools/mcp_tools.py` — pulling in an external tool server

This is the pattern for wiring in *any* MCP (Model Context Protocol) server —
a standard way for a remote service to expose "tools" an LLM can call, the
same shape as your local `@tool` functions.

```python
async def build_tools() -> list:
    return [*LOCAL_TOOLS, *(await get_lucid_tools())]
```

Key wrinkle vs. everything else in the repo: tool *discovery* is a network
call, so it's `async` and has to happen once at graph-build time — you can't
have a static `TOOLS = [...]` module constant like `ticket_agent.py` does.
Once built, a lane using MCP tools has to run via `graph.ainvoke(...)`
instead of `.invoke(...)`, since the remote tool calls stay async end to end.
`mcp_tools.py`'s `_SERVERS` dict is the one place server configs (URL, auth,
transport) live — adding a second MCP server later is one more dict entry,
not a new pattern.

### 4.9 `pmagent/tools/spreadsheet_tools.py` — the other write-gate pattern

Same "don't let the agent write unsupervised" idea as `create_jira_issue`,
but enforced more strongly: `propose_cell_update` only *queues* a proposed
change into an approval table; `apply_approved_requests` only applies rows a
**human has already marked "Approved" inside the actual spreadsheet** — not
something the conversation confirms. Two enforcement levels for the same
underlying concern (LLM writes are dangerous) is worth noticing: prompt-level
gating (ticket agent) vs. data-level gating (spreadsheet agent, where even a
compromised/confused prompt can't skip the human step because the *approval
state lives outside the LLM's control*). Read `tests/test_spreadsheet_tools.py`
to see the plain-Python core (`propose_cell_update`/`apply_approved_requests`)
tested with a `FakeWorkbook` — no real Microsoft Graph auth needed to verify
that logic.

### 4.10 `pmagent/tools/company_knowledge.py` — a deliberately stubbed seam

`retrieve_company_context(topic)` returns `""` unless you drop files in
`sample_data/company_docs/`. Read the module docstring: this is intentional
scaffolding for "swap this for real Confluence/RAG retrieval later without
changing the call site." Recognizing *intentional* stubs vs. *accidental*
incompleteness (like `requirements.py`) is a real skill when reading someone
else's in-progress codebase — the comments are what tell them apart here.

---

## 5. Hands-on: run a lane yourself

`graph.py` doesn't exist, so to run any single lane you build a minimal
one-node-plus-tools graph around it. This is genuinely useful to internalize
— it's the smallest possible LangGraph program that does something real.

```powershell
uv run python -c "
from langchain_core.messages import HumanMessage
from langgraph.graph import StateGraph, START, END
from langgraph.prebuilt import ToolNode
from pmagent.state import PMState
from pmagent.agents.common import make_agent_node, make_tools_router
from pmagent.agents import ticket_agent as lane
from pmagent.llm import get_llm

llm = get_llm(temperature=0).bind_tools(lane.TOOLS)
g = StateGraph(PMState)
g.add_node('agent', make_agent_node(lane.SYSTEM_PROMPT, llm))
g.add_node('tools', ToolNode(lane.TOOLS))
g.add_edge(START, 'agent')
g.add_conditional_edges('agent', make_tools_router('tools'))
g.add_edge('tools', 'agent')
graph = g.compile()

result = graph.invoke({'messages': [HumanMessage('Draft a ticket for adding dark mode to the settings page')]})
print(result['messages'][-1].content)
"
```

Try it with `sprint_agent` too (swap `ticket_agent` → `sprint_agent`, and the
prompt to something like `"how's sprint 24 doing?"`).

**Exercise**: trace through what happens step by step. Put a `print(state)`
at the top of the `node` function inside `make_agent_node` (temporarily) and
watch `state.messages` grow across turns — you'll see the `HumanMessage`, then
an `AIMessage` with `tool_calls`, then a `ToolMessage` with the tool's return
value, then a final `AIMessage` with real text and no tool calls (which is
what makes the router return `END`).

---

## 6. Exercises, ordered easy → hard

1. **Add a new Jira tool.** Write `@tool def list_my_open_issues() -> str`
   that filters mock data to unresolved issues assigned to a hardcoded name.
   Wire it into `sprint_agent.TOOLS`, update `prompts/sprint_agent.md` to
   mention it, and run it through the §5 harness.

2. **Finish `RequirementsState`.** Using `PRD`, `ReviewResult` from
   `state.py`, and the diagram in `requirements.py`'s docstring, define the
   subgraph state (source notes, current `PRD`, current `ReviewResult`,
   iteration count) and build the `writer → reviewer → conditional(render/
   writer)` subgraph with `StateGraph`. Test it with a paragraph of messy
   fake meeting notes and confirm it actually loops back on a rejected draft.

3. **Add a guard against infinite reflection loops.** The Writer↔Reviewer
   loop has no hardcoded pass limit per the design — but an ungrounded loop
   is a real production risk. Add a max-iterations cutoff to your subgraph
   from #2 that forces `render` after N passes regardless of `approved`.

4. **Wire a second MCP server.** Pick any public MCP server (or run a toy one
   locally), add an entry to `mcp_tools.py`'s `_SERVERS`, write a
   `get_<name>_tools()` wrapper, and bind it into a throwaway test lane the
   same way `diagram_agent.py` does. This is the exercise that actually
   teaches you the MCP integration pattern instead of just reading about it.

5. **Capstone: write `graph.py`.** Assemble the whole top-level graph per
   §7 below. This is the one file that doesn't exist yet in the repo — you
   finishing it *is* "replicating the project."

---

## 7. Capstone — build the missing `graph.py`

This ties every earlier section together. Target shape (from `CLAUDE.md` /
the various module docstrings):

```
START → classify_node ──route_from_classifier──► ticket_agent
                                              ├──► sprint_agent
                                              ├──► spreadsheet_agent
                                              ├──► requirements (subgraph)
                                              └──► query_agent (handled inline
                                                    by the orchestrator's own
                                                    node + tools loop)
each specialist lane → its own ToolNode loop (agents/common.py) → END
```

Steps:

1. Import `PMState`, `classify_node`, `route_from_classifier`,
   `QUERY_TOOLS`, `prompts.orchestrator_system_prompt` from `orchestrator.py`.
2. For each of `ticket_agent`, `sprint_agent`, `spreadsheet_agent`: build an
   LLM bound to that lane's `TOOLS`, wrap with `make_agent_node`/
   `make_tools_router` (exactly like §5), and `add_node`/`add_conditional_edges`/
   `add_edge` it into the graph under a distinct node name per lane (e.g.
   `"ticket_agent"`, `"ticket_tools"`).
3. Add the orchestrator's own query lane the same way, using `QUERY_TOOLS`.
4. Add `classify_node` at `START`; `add_conditional_edges("classify_node",
   route_from_classifier)` — remember `route_from_classifier` returns strings
   like `"ticket_agent"` that must match your node names exactly.
5. Each lane's final node should edge to `END` once its router says so (same
   as the single-lane harness in §5, just repeated per lane, sharing the
   `classify_node` entry point instead of `START` directly).
6. Skip wiring `requirements_agent`/`diagram_agent` into the top graph until
   you've done exercises 2 and 4 — they need the subgraph and the async
   `build_tools()` respectively, which don't fit the synchronous pattern the
   other three lanes use without extra handling (a subgraph node, and
   `.ainvoke` for the whole graph).

If you get this compiling and can route "draft me a ticket for X" to the
ticket lane and "how's sprint 24" to the sprint lane from one `graph.invoke`
call, you've rebuilt the intended architecture of this repo from its parts —
which is the whole point of the exercise.

---

## 8. Design principles worth internalizing (not just for this repo)

These are stated in `CLAUDE.md` but are general lessons, not local trivia:

- **Structured data between agents, not prose.** Typed Pydantic state beats
  parsing another agent's markdown output. It's slower to write the schema up
  front and pays for itself the moment two agents need to agree on a shape.
- **LLM does judgment, code does arithmetic.** Any number that drives a
  decision (a risk level, a completion %, a currency conversion) belongs in
  plain Python, verified by a unit test — not in a prompt hoping the model
  computes it right every time.
- **Prompts are the behavior layer.** If you catch yourself editing Python to
  change what an agent *does* rather than what tools it *has*, check whether
  the change actually belongs in the `.md` prompt file instead.
- **Human approval for anything that writes externally.** Two different
  enforcement strengths shown in this repo (prompt-level vs. data/state-level
  gating) — know which one your risk tolerance actually requires.
- **Stub seams honestly.** A no-op function with a docstring explaining what
  it'll become later is fine engineering, not laziness — as long as it's
  documented as a stub, not silently wrong.

---

## 9. Where to go after this repo

- Rebuild the same shape against a different tool (GitHub issues instead of
  Jira, Linear, a to-do API — anything with read + gated-write endpoints).
- Try LangGraph Studio locally (`uv sync --group dev`, then `uv run langgraph
  dev` once you have a `langgraph.json` and a finished `graph.py`) to
  visually watch state flow through your graph instead of only reading logs.
- Read `snowflake/procs/draft_prd.py` and compare it to
  `agents/requirements.py` once you've finished exercise 2 — same problem
  (Writer/Reviewer PRD loop), solved without LangGraph and without structured
  output binding (Cortex has neither), using prompted JSON + manual parsing
  instead. Seeing the same idea implemented with fewer framework guarantees
  is a good gut check on which parts of LangGraph you actually understand
  versus which parts you've just been trusting.
