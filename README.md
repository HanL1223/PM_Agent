# PM_Agent — a teaching walkthrough

This README is written to *teach*, not just document. It walks through every
file in this repo, explains the design decision behind it, and gives an
analogy for the underlying concept so you can rebuild the same ideas in a
different project. If you only want the terse facts, read `CLAUDE.md`
instead — that one is written for an AI coding assistant. This one is
written for you.

The repo contains **two independent implementations of the same idea**
(an AI assistant that helps run a software project — drafting tickets,
reporting on sprints, writing PRDs, and now drawing diagrams):

| Track | Where | Framework | Status |
|---|---|---|---|
| Prototyping sandbox | `pmagent/` | LangGraph (Python, runs anywhere) | Where new agent designs get tried first |
| Production target | `snowflake/` | Snowflake Cortex stored procedures | Where things move once proven — runs *inside* Snowflake |

They duplicate some logic on purpose right now (see `CLAUDE.md`). Reading
both, side by side, is actually the best way to learn this material: you'll
see the *same* handful of ideas implemented twice, once with a rich
Python framework and unrestricted network access, and once inside a
sandboxed, text-in/text-out execution environment. Seeing which parts survive
that constraint and which parts have to change is where the real lessons are.

---

## Getting started (testing this on a new machine)

```bash
git clone <this repo>
cd PM_Agent
uv sync                        # installs pmagent's deps into .venv (needs Python >=3.12; uv fetches it per .python-version)
cp ".env sample" .env          # then fill in at least ANTHROPIC_API_KEY (or OPENAI_API_KEY + LLM_PROVIDER=openai)
uv run main.py                 # currently just prints the placeholder "Hello from pm-agent!"
```

`pmagent/` has no CLI/chat loop yet (`graph.py`, the thing that would wire the
agents into a runnable conversation, doesn't exist — see Part 2). What you
*can* run today is every individual piece — tools, agent tool-lists, prompt
loading, the mock Jira client — directly. This also doubles as your
"did the install actually work" check on a fresh device, since there's no
test suite:

```bash
uv run python -c "
from pmagent.tools.jira_tools import search_jira_issues, get_sprint_status
print(search_jira_issues.invoke({'jql': 'project = CSCI'})[:200])
print(get_sprint_status.invoke({'sprint_id': 24})[:200])
"
```

Both calls should print real text sourced from `pmagent/sample_data/mock_jira.json`
— no Jira account, no `JIRA_*` env vars needed (`JIRA_MOCK` turns on
automatically whenever `JIRA_BASE_URL` is unset). You *do* still need a
working LLM key in `.env` for anything that calls a model (routing, ticket
drafting, PRD writing, `draft_diagram_brief`) — mock mode only stands in for
Jira, not the LLM provider.

The `snowflake/` track is separate and only matters if you're deploying to an
actual Snowflake account — see Part 3 and `snowflake/LUCID_DIAGRAM_AGENT.md`.
It's not needed to run or test `pmagent/`.

---

## Part 1 — The core ideas (taught through `pmagent/`)

One term first, since everything below assumes it: an **AI agent**, as used
in this repo, just means *"an LLM call wired up so it can loop, use tools,
and decide what to do next — not just answer once and stop."* A chatbot
that answers a question is not an agent. A system that reads a request,
decides to search Jira, looks at the result, and *then* decides what to say
is an agent. Keep that distinction in mind — it's the difference between
"an LLM call" (single shot) and "an agent" (a loop with decisions in it)
used throughout this doc.

`pmagent/` is a **LangGraph** application. LangGraph lets you describe an
agent system as a graph: nodes are steps (usually "call an LLM" or "run some
code"), edges say what happens next, and a shared **state** object flows
through every node, picking up updates as it goes.

### 1.1 State: the shared clipboard

**File:** [`pmagent/state.py`](pmagent/state.py)

```python
class PMState(BaseModel):
    messages: Annotated[list[BaseMessage], add_messages] = []
    route: str = ""
    ticket_draft: dict = {}
    sprint_report: dict = {}
    prd: dict = {}
    diagram_brief: dict = {}
```

**Reading the code, piece by piece** (if any of this syntax is new to you):
- `BaseModel` comes from **Pydantic**, a library for defining a data shape
  as a Python class and getting free validation — if you try to put a
  string where `ticket_draft: dict` expects a dict, Pydantic raises an
  error immediately instead of letting bad data flow silently downstream.
  Every "structured object" mentioned in this doc (`PRD`, `TicketDraft`,
  `RouteDecision`, ...) is a Pydantic `BaseModel`.
- `BaseMessage` is LangChain's generic chat-message type (a `HumanMessage`,
  `AIMessage`, or `SystemMessage` all count as one) — `messages` is just
  "the conversation so far," typed generically enough to hold any of them.
- `Annotated[list[BaseMessage], add_messages]` looks intimidating but reads
  left to right: *"this field is a `list[BaseMessage]`, and when a node
  wants to update it, use the `add_messages` function to merge the update
  in"* — `Annotated` is Python's way of attaching that extra instruction to
  a type hint without changing the type itself.

Every node in the graph receives this object and returns a *partial* update
to it (e.g. `{"route": "ticket"}`), which LangGraph merges back in. The
`messages` field is special: it's annotated with `add_messages`, a
**reducer** — a merge function LangGraph calls instead of just overwriting
the field. `add_messages` *appends* new messages rather than replacing the
list, which is what gives the agent memory within a conversation.

> **Analogy:** think of `PMState` as a clipboard that gets physically passed
> from department to department in an office. Each department reads what's
> on it, does its job, and writes its own findings in its own section before
> passing it on — nobody erases what came before, and nobody has to
> re-explain the whole history verbally to the next department (that would
> be a game of telephone, where details get lost or garbled at every hop).
> `route`, `ticket_draft`, `sprint_report`, `prd`, and `diagram_brief` are each
> department's dedicated section on that clipboard.

The comment at the top of `state.py` names this explicitly: *"agents should
exchange structured data, not giant text blobs."* This is the single most
important habit in the codebase, and it shows up again in the Snowflake
track under a different name.

### 1.2 Structured output beats parsing free text

**File:** [`pmagent/state.py`](pmagent/state.py) (schemas), used in
[`pmagent/agents/orchestrator.py`](pmagent/agents/orchestrator.py)

```python
class RouteDecision(BaseModel):
    route: Literal["ticket", "sprint", "query", "requirements"] = Field(...)
```

```python
router_llm = get_llm(temperature=0).with_structured_output(RouteDecision)
decision: RouteDecision = router_llm.invoke(...)
return {"route": decision.route}
```

`with_structured_output(RouteDecision)` forces the model's API call to
return something that validates against the `RouteDecision` schema — not
prose you then have to regex out an answer from. If the model doesn't
produce a valid `"ticket" | "sprint" | "query" | "requirements"`, the call
itself fails loudly instead of silently routing wrong. (`Literal[...]` here
is Python's way of saying "only these exact string values are valid" —
Pydantic enforces it the same way it enforces any other field type.)

Two other pieces of unexplained syntax worth naming: `get_llm(temperature=0)`
— **temperature** is the knob that controls how random/creative a model's
output is, from `0` (as deterministic and repeatable as an LLM gets — right
for a routing decision, where you want the same input to reliably produce
the same route) up to around `1`+ (more varied, better for creative
writing, worse for anything you need to be consistent).

> **Analogy:** this is the difference between handing someone a
> multiple-choice form ("circle one: A, B, C, D") versus asking them to
> write you an essay and *you* trying to guess which choice they meant.
> The essay might be more "natural," but the form is the one you can build
> reliable automation on top of. Every schema in `state.py`
> (`TicketDraft`, `PRD`, `ReviewResult`, `RouteDecision`) is a form, not an
> essay prompt.

Same idea, same payoff, appears again in `snowflake/procs/*.py` — just
implemented differently, because Cortex's `COMPLETE` function has no native
"force this shape" feature (more on that in Part 3).

### 1.3 LLMs do judgment; plain code does arithmetic

**File:** [`pmagent/tools/jira_tools.py`](pmagent/tools/jira_tools.py) —
`compute_sprint_metrics`

```python
def compute_sprint_metrics(sprint_payload: dict, stuck_threshold_days: int = 3) -> dict:
    # [both `...` lines below are this README abbreviating the function for
    #  space — they are not literal code from the file. The real function
    #  also totals story points and builds the `blocked` tickets list before
    #  reaching these lines; see jira_tools.py for the whole thing.]
    ...
    completion_rate = round(done_points / total_points, 3) if total_points else 0.0
    if completion_rate >= 0.7 and len(blocked) <= 1:
        risk = "LOW"
    ...
```

This is a pure Python function — no LLM call anywhere in it. The Sprint
Agent's prompt (`prompts/sprint_agent.md`) is explicit about this boundary:
*"These numbers are calculated deterministically in code — trust them,
never recompute or estimate them yourself."*

> **Analogy:** you wouldn't want your accountant to "vibe" your tax
> total from memory, even if they're extremely good at math — you want
> them to run the actual numbers through a calculator, then use their
> judgment to explain what the numbers mean and what you should do about
> them. The LLM is the accountant's *explanation*; `compute_sprint_metrics`
> is the calculator. Never let the explanation-writer also be the one doing
> the arithmetic that a real decision depends on — LLMs are fluent, not
> reliably precise.

### 1.4 Tools are how an agent acts on the world

**Files:** [`pmagent/tools/jira_tools.py`](pmagent/tools/jira_tools.py),
[`pmagent/agents/common.py`](pmagent/agents/common.py)

A "tool" here is just a Python function wrapped in LangChain's `@tool`
decorator, which turns its signature + docstring into something the model
can choose to call:

```python
@tool
def search_jira_issues(jql: str) -> str:
    """Search Jira for existing issues. ...
    Args:
        jql: A Jira Query Language string, e.g. 'project = CSCI AND text ~ "DFIO"'.
    """
```

The model reads the docstring, decides *whether* and *how* to call it, and
the framework runs it and feeds the result back. This is the classic ReAct
loop (Reason → Act → Observe → repeat), and `agents/common.py` builds it
generically so every specialist doesn't reinvent the wiring:

```python
def make_agent_node(system_prompt: str, llm_with_tools: BaseChatModel) -> callable:
    def node(state: PMState) -> dict:
        response = llm_with_tools.invoke([SystemMessage(content=system_prompt)] + state.messages)
        return {"messages": [response]}

def make_tools_router(tools_node_name: str) -> Callable:
    def router(state: PMState) -> str:
        last_message = state.messages[-1]
        if getattr(last_message, "tool_calls", None):
            return tools_node_name
        return END
    return router
```

Notice the shape of both functions: `make_agent_node` doesn't itself call
the LLM — it *returns* `node`, a function that will call the LLM later,
each time the graph reaches that step. Same for `make_tools_router`: it
returns `router`, to be called later. This "function that builds and
returns another function" pattern is called a **closure**, and it's how you
customize behavior (which `system_prompt`, which `tools_node_name`) once,
up front, without writing three nearly-identical node functions by hand.
If you've never seen this pattern, read it as: *"`make_agent_node` is a
recipe for making a node; calling it with your specific prompt and tools
bakes a finished node."*

`make_agent_node` builds "call the LLM" as a graph node; `make_tools_router`
builds the conditional edge that says *"if the model just asked to run a
tool, go run it and come back; otherwise the turn is over."* Because this
plumbing is generic, each specialist module only has to declare **what it
is** — its tools and its prompt — not **how it's wired**:

```python
# pmagent/agents/ticket_agent.py
TOOLS = [search_jira_issues, create_jira_issue]
SYSTEM_PROMPT = prompts.ticket_agent_system_prompt

# pmagent/agents/sprint_agent.py
TOOLS = [get_sprint_status]
SYSTEM_PROMPT = prompts.sprint_agent_system_prompt
```

> **Analogy:** `make_agent_node`/`make_tools_router` are like a factory
> assembly line that's identical for every product — only the "recipe card"
> (system prompt) and the "toolbox" (tools list) change per product. Adding
> a fourth specialist agent is a copy-paste-and-tweak job, not a rebuild of
> the line.

Giving a model *tools* instead of asking it to know things from memory is
also its own lesson: `search_jira_issues` lets the model look something up
instead of guessing, the same way you'd rather have an assistant check the
filing cabinet than confidently make something up.

### 1.5 Reflection loops: Writer ↔ Reviewer

**File:** [`pmagent/agents/requirements.py`](pmagent/agents/requirements.py)
(currently incomplete scaffolding — see `CLAUDE.md` — but the design intent
is clear and fully realized in the Snowflake twin, `draft_prd.py`, covered
in Part 3)

The intended shape:

```
START → writer → reviewer ──approved?──► render → END
          ▲                  │ no (and iterations left)
          └──────────────────┘
```

The Writer drafts a `PRD` (a structured object, not prose — see 1.2). The
Reviewer checks it against a checklist and returns a `ReviewResult`. If
`approved=False`, the *same* draft plus the reviewer's specific complaints
go back to the Writer for another pass. The number of passes isn't
hard-coded to some fixed number chosen in advance — it depends on how good
the draft is, decided at runtime by the Reviewer's judgment.

> **Analogy:** this is a student submitting homework to a strict grader.
> The grader doesn't rewrite the essay for the student — it hands back a
> specific list of what's missing or wrong ("you didn't address requirement
> 3", "this metric isn't measurable"). The student revises *only* what was
> flagged and resubmits. This repeats until the grader is satisfied or a
> cap on attempts is hit (a safety valve, not the intended exit condition —
> see `MAX_PASSES` in `draft_prd.py`). Crucially, the grader is not allowed
> to just write the essay themselves — reviewing and writing are kept as
> separate roles, so the review stays honest instead of turning into a
> monologue.

The formatting step (`render_prd_markdown`) is, again, deterministic code,
not an LLM call — see 1.3. Writing *and* judging are language tasks (LLM);
turning a validated structure into an exact markdown template is not
(plain Python).

### 1.6 Human-gated writes

**Files:** [`pmagent/prompts/ticket_agent.md`](pmagent/prompts/ticket_agent.md),
[`pmagent/tools/jira_tools.py`](pmagent/tools/jira_tools.py)

The Ticket Agent's prompt lays out a strict sequence: search for
duplicates → draft the ticket *in the chat response* → ask "Shall I create
this in Jira?" → only call `create_jira_issue` after the user says yes. The
`create_jira_issue` tool's docstring backs this up directly at the point of
use, where the model actually reads it:

```python
@tool
def create_jira_issue(...) -> str:
    """Create a Jira issue.

    ONLY call this AFTER the user has explicitly confirmed the drafted ticket.
    Never call it to preview.
    """
```

> **Analogy:** a competent assistant will draft your email and show it to
> you, but won't hit "send" until you say "yes, send it." Anything that
> writes to a system of record — Jira, a database, another team's
> tracker — should go through this same "show, then confirm, then act"
> sequence. Never let a model treat "generate the write" and "perform the
> write" as the same step.

This is a *pattern*, not a hard technical guarantee here — it depends on
the model actually following its instructions. It's worth remembering that
distinction; it becomes very important again in Part 3, where the
Lucid/diagram feature runs into the same limitation but with no code layer
available to backstop it.

### 1.7 The adapter pattern: mock vs. real, same interface

**File:** [`pmagent/tools/jira_tools.py`](pmagent/tools/jira_tools.py) —
`JiraClient`

```python
class JiraClient:
    def __init__(self) -> None:
        self.mock = env.JIRA_MOCK
        if self.mock:
            with open(_MOCK_PATH, "r", encoding="utf-8") as f:
                self._data = json.load(f)
        else:
            self._session = requests.Session()
            ...
```

Every method (`search_issues`, `get_issue`, `create_issue`, ...) branches
internally on `self.mock`, but the *caller* — the `@tool` functions the
agents actually use — never has to know which mode it's in. `env.py` flips
this automatically: `JIRA_MOCK` turns on if you never set `JIRA_BASE_URL`,
so the whole app runs with zero configuration against
`sample_data/mock_jira.json`.

> **Analogy:** a flight simulator and a real cockpit have identical
> controls — the pilot practices on the exact same interface they'll use
> for real, so nothing has to be relearned when they switch. `JiraClient`
> is the cockpit; mock mode is the simulator. You can develop and test the
> entire agent system without a real Jira account, and flipping to
> production is changing an environment variable, not rewriting code.

### 1.8 Prompts and domain knowledge live in Markdown, not Python

**Files:** [`pmagent/prompts/prompts.py`](pmagent/prompts/prompts.py),
[`pmagent/skills/__init__.py`](pmagent/skills/__init__.py)

```python
def _load(name: str) -> str:
    with open(os.path.join(_DIR, name), "r", encoding="utf-8") as f:
        return f.read()

orchestrator_system_prompt = _load("orchestrator.md")
ticket_agent_system_prompt = _load("ticket_agent.md")
```

The Python here is almost nothing — a tiny loader. All the actual
instructions live in `.md` files (`prompts/ticket_agent.md`,
`prompts/sprint_agent.md`, etc.), and deeper domain knowledge lives in
"skills" — `skills/prd/SKILL.md` is the entire "how to write a good PRD"
playbook, loaded and injected into both the Writer's and Reviewer's prompts
via `.format(skill=...)` so they're checking the draft against the *same*
rules the Writer was told to follow.

> **Analogy:** this is a recipe binder in a professional kitchen, versus a
> chef who only knows the recipes from memory. With a binder, a new cook
> (or you, six months from now) can read exactly what the process is,
> tweak one line, and have it apply immediately — no recompiling, no
> digging through code. `SKILL.md` is doubly useful here because it's
> shared: both the Writer and the Reviewer read the *same* rulebook, which
> is what makes the Reviewer's checks meaningful instead of arbitrary.

### 1.9 Reserved seams: build the shape now, wire in the real thing later

**File:** [`pmagent/tools/company_knowledge.py`](pmagent/tools/company_knowledge.py)

```python
def retrieve_company_context(topic: str, max_chars: int = 4000) -> str:
    """... Current implementation: reads any `.md`/`.txt` files in
    `sample_data/company_docs/` and returns them (truncated)."""
    if not os.path.isdir(_DOCS_DIR):
        return ""
    ...
```

This function is a deliberate stand-in for a future Confluence/RAG
retriever. Today it does the simplest thing that could possibly work (read
local files, or return nothing). The *signature* — takes a topic string,
returns a context string — is what matters, because that's the contract
every caller (the Writer agent) depends on. When real retrieval gets built,
only this function's body changes.

> **Analogy:** this is like an electrician installing a wall outlet before
> you've bought the appliance that will plug into it. The outlet (function
> signature) gets wired into the house (call sites) now, correctly, even
> though nothing is plugged in yet. Later, plugging in the real appliance
> (a Confluence API call, a vector-store query) doesn't require ripping
> open the wall again.

The Snowflake track has its own version of this seam: `context_text` in
`reporting_intent.py`, explained in Part 3.

### 1.10 One config module, one provider-swap seam

**Files:** [`pmagent/env.py`](pmagent/env.py),
[`pmagent/llm.py`](pmagent/llm.py)

`env.py` is the *only* place that calls `os.getenv` — every other module
imports constants from it. `llm.py` is a three-line factory whose entire
job is: read `env.LLM_PROVIDER`, return a `ChatAnthropic` or `ChatOpenAI`
instance behind the identical `BaseChatModel` interface, so the rest of the
codebase never branches on which provider is active.

> **Analogy:** one circuit-breaker panel for the whole house, instead of
> wiring hidden inside every individual wall. If you need to trace or
> change how power (config) flows, there's exactly one place to look, and
> swapping the supplier (Anthropic ↔ OpenAI) is flipping one switch, not
> rewiring every room.

### 1.11 Importing an external MCP server as a tool

**Files:** [`pmagent/tools/mcp_tools.py`](pmagent/tools/mcp_tools.py),
[`pmagent/tools/diagram_tools.py`](pmagent/tools/diagram_tools.py),
[`pmagent/agents/diagram_agent.py`](pmagent/agents/diagram_agent.py)

Every tool so far (`search_jira_issues`, `create_jira_issue`,
`get_sprint_status`) is a local Python function wrapped in `@tool`. **MCP**
(Model Context Protocol) is a standard for pulling in tools you *didn't*
write — a call to a remote server that returns "here are my tools, and here's
how to call them" — so an agent can use, say, Lucid's diagramming
capabilities without you hand-writing a `requests` wrapper around Lucid's
API. `langchain-mcp-adapters` is the library that speaks MCP and hands back
the result as ordinary LangChain `BaseTool` objects — indistinguishable, from
a lane's point of view, from a local `@tool`:

```python
# pmagent/tools/mcp_tools.py
_SERVERS = {
    "lucid": {
        "url": env.LUCID_MCP_URL,
        "transport": "streamable_http",
        "headers": {"Authorization": f"Bearer {env.LUCID_MCP_AUTH_TOKEN}"} if env.LUCID_MCP_AUTH_TOKEN else {},
    },
}

async def get_lucid_tools() -> list[BaseTool]:
    return await MultiServerMCPClient(_SERVERS).get_tools(server_name="lucid")
```

The one genuinely new wrinkle: discovering an MCP server's tools is a
network round trip, so it's `async`, and it has to happen *once*, before the
graph is built — not lazily the first time a lane runs. That's why
`diagram_agent.py` can't declare a static `TOOLS = [...]` the way
`ticket_agent.py` does; instead it exposes a `build_tools()` coroutine:

```python
LOCAL_TOOLS = [draft_diagram_brief]

async def build_tools() -> list:
    return [*LOCAL_TOOLS, *(await get_lucid_tools())]
```

Whatever eventually builds `graph.py` awaits `build_tools()` once at startup
and binds the result, same as any other lane's tool list — the only other
consequence is that a lane holding MCP tools needs its graph run with
`.ainvoke`/`.astream` rather than `.invoke`/`.stream`, since the tool calls
stay async all the way through.

`draft_diagram_brief` (in `diagram_tools.py`) is the local half of this
lane — it never talks to Lucid, it only turns an approved PRD into a
`DiagramBrief` (a `with_structured_output` schema, per 1.2) that gets handed
to Lucid's MCP `create_diagram` tool once the user confirms it, per
`prompts/diagram_agent.md`'s gate (see 1.6 — same prompt-enforced-not-code-
enforced caveat applies here, since the write happens inside Lucid's own MCP
tool, outside any Python you control).

> **Analogy:** a local `@tool` is hiring a specialist yourself and writing
> their job description (the function + docstring). An MCP server is more
> like contracting an outside agency: you don't write their staff's job
> descriptions, you just ask the agency "what can your people do?" once
> (tool discovery) and then dispatch work to them the same way you would to
> your own hires.

This is the direct pmagent-track twin of the Snowflake track's Lucid MCP
integration (3.5) — same idea (local brief-writer + Lucid MCP tool, gated by
a confirm-before-write instruction), different plumbing because there's no
Snowflake-managed connector runtime here: `langchain-mcp-adapters` is doing
in Python what Snowflake's `EXTERNAL MCP SERVER`/`CREATE AGENT` machinery
does at the platform level. Worth reading both side by side.

---

## Part 2 — How it all connects (the intended graph)

Per `CLAUDE.md`, the full wiring (`graph.py`) doesn't exist yet, but the
intended shape, now that you've seen every piece, reads like this:

```
User message
     │
     ▼
classify_node (orchestrator.py)        ← RouteDecision, structured output (1.2)
     │
     ▼
route_from_classifier ──┬─→ ticket_agent   (make_agent_node + make_tools_router, 1.4)
                         ├─→ sprint_agent  (deterministic metrics underneath, 1.3)
                         ├─→ query_agent   (orchestrator's own read-only Jira lookup)
                         └─→ requirements  (Writer↔Reviewer subgraph, 1.5)
```

`classify_node` deliberately does *not* touch `state.messages` — it only
sets `state.route` — so whichever specialist runs next sees the user's
original, unedited request (re-read the docstring in `orchestrator.py` for
why that matters: it stops the classifier's own phrasing from leaking into
and confusing the specialist).

---

## Part 3 — The same ideas, inside Snowflake Cortex (`snowflake/`)

This track reimplements the PRD writer/reviewer loop and the data-product
classifier as **Snowflake Cortex stored procedures** — plain Python
functions, deployed *into* Snowflake, callable as SQL procedures. It's
aimed at production because it runs where the company's data already
lives, under Snowflake's governance — but it comes with one huge
constraint that reshapes everything: **Cortex has no outbound network
access of its own.** No calling Jira, no calling an arbitrary REST API, no
LangGraph (which assumes you can run an unrestricted Python process talking
to any API you like). Every design choice here is downstream of that one
constraint.

### 3.1 The shared toolbox

**File:** [`snowflake/procs/cortex_common.py`](snowflake/procs/cortex_common.py)

```python
def complete(session, model_name, prompt):
    row = session.sql(
        "select snowflake.cortex.complete(?, ?) as resp",
        params=[model_name, prompt],
    ).collect()
    return row[0]["RESP"]

def extract_json(text):
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        raise ValueError(f"Model did not return JSON: {text[:300]}")
    return json.loads(match.group(0))
```

**Reading the code:** `session.sql("select ... (?, ?) ...", params=[model_name, prompt])`
builds a SQL statement with `?` placeholders, filled in — safely, without
manual string-escaping — by the `params` list, in order. Snowpark queries
don't actually run until you call an action like `.collect()`, which
executes the SQL and pulls the result rows back into Python; `row[0]["RESP"]`
then reads the one column of the one row that query returns. In
`extract_json`, the regex `r"\{.*\}"` greedily matches everything from the
first `{` to the last `}` in the model's reply — `re.DOTALL` makes `.` also
match newlines, since a JSON object almost always spans multiple lines.

Two functions, imported by every proc in the folder. `complete` is the
*only* function in this entire package that touches the live Snowpark
`session` — every proc funnels its one LLM call through it. `extract_json`
exists because of a real gap versus Part 1: Anthropic/OpenAI's APIs offer a
native "force this exact schema" feature (`with_structured_output`), but
`snowflake.cortex.complete` is a plain text-in/text-out SQL function — no
such feature exists at that layer. So the structure is enforced the older,
scrappier way: ask the model very explicitly for JSON in the prompt, then
regex out the first `{...}` block and parse it, tolerating the model
wrapping it in commentary ("Sure! Here's the JSON: ...").

> **Analogy:** `with_structured_output` (Part 1.2) is filling out a web
> form with typed fields that reject bad input before you can even submit.
> `extract_json` is the equivalent of getting an answer over a *phone
> call* — pure text, no enforced format — so you ask the person very
> precisely to "read me back exactly the following fields, in this exact
> order," and then you carefully parse what they said. Less reliable, more
> defensive code required, but it's what you get on a text-only channel.

Why isolate the network call in one function per proc (`_complete`)? Same
reason as the mock/real split in Part 1.7: it's the seam. Every proc's
*other* logic (prompt building, JSON parsing, markdown rendering) is plain
Python with zero Snowflake dependency, which is exactly why
`snowflake/tests/` can unit-test all of it without ever connecting to a
real Snowflake account — look at `test_cortex_common.py`'s `_FakeSession`,
a tiny stand-in object with just enough shape (`.sql().collect()`) to
satisfy `complete()` without a real database anywhere nearby.

### 3.2 The reflection loop, without a graph framework

**File:** [`snowflake/procs/draft_prd.py`](snowflake/procs/draft_prd.py)

This is Part 1.5's Writer↔Reviewer loop, fully implemented — proof that the
*idea* doesn't require LangGraph, just a framework-appropriate
implementation:

```python
MAX_PASSES = 3

def run(session, notes):
    prd = None
    review = None
    for _ in range(MAX_PASSES):
        prompt = _writer_prompt(notes, prd, review)
        prd = _extract_json(_complete(session, prompt))

        review = _extract_json(_complete(session, _reviewer_prompt(notes, prd)))
        if review.get("approved"):
            break

    markdown = _render_markdown(prd)
    if not review.get("approved"):
        markdown = (
            f"_Not fully approved after {MAX_PASSES} review passes. "
            f"Outstanding: {review.get('missing_requirements') or review.get('issues')}_\n\n"
            + markdown
        )
    return markdown
```

(One small idiom in that last block: `review.get('missing_requirements') or
review.get('issues')` reads "show missing_requirements if there are any,
otherwise fall back to issues" — `or` between two "falsy" values like `[]`
and `None` just picks whichever one is non-empty first.)

Same actor-critic shape as the LangGraph version (Writer drafts → Reviewer
critiques → loop until approved), same separation of "LLM writes and
judges, plain Python renders the final markdown" (`_render_markdown` has no
LLM call in it at all — go look, it's pure string formatting over the
`prd` dict). The only real difference from the LangGraph subgraph is
*mechanical*: no graph object, no nodes, no edges — just a `for` loop with
an early `break`, because a Snowflake stored procedure is a single Python
function call, not a long-running orchestrated process. `MAX_PASSES` is the
safety valve (Part 1.5's "cap on attempts") made concrete: 3 tries, then
ship the best draft with a visible warning rather than looping forever.

> **Lesson:** the *pattern* (reflection loop, judge-then-revise) is
> reusable across completely different execution environments. What
> changes between a rich Python framework and a constrained stored
> procedure is the *plumbing* the pattern rides on, not the pattern itself.
> Learn the pattern, not the framework.

### 3.3 A single-shot classifier (the simplest possible agent)

**File:**
[`snowflake/procs/classify_data_product.py`](snowflake/procs/classify_data_product.py)

No loop, no back-and-forth — one prompt, one JSON extraction, one
formatted answer:

```python
def run(session, prd_text):
    prompt = "\n\n".join([_CLASSIFIER_ROLE, f"PRD / requirements:\n{prd_text}", "Return ONLY a JSON object with keys: ..."])
    result = _extract_json(_complete(session, prompt))
    pattern = result.get("delivery_pattern", "ad_hoc_extract")
    return _format_result(pattern, result.get("rationale", ""), result.get("open_questions") or [])
```

Worth noticing: `_ARTEFACTS_BY_PATTERN` and `_NEXT_AGENTS_BY_PATTERN` are
plain Python dictionaries mapping each of the five possible classifications
to a fixed checklist and a fixed list of downstream agents. The LLM only
ever picks *which* bucket (`delivery_pattern`) applies — it never generates
the checklist text itself. That's Part 1.3's principle again, in a new
outfit: judgment (which bucket?) stays with the LLM; the actual content of
"what happens for that bucket" is fixed, deterministic, and impossible for
the model to hallucinate a wrong artefact list for.

### 3.4 Grounding seams look the same everywhere

**File:**
[`snowflake/procs/reporting_intent.py`](snowflake/procs/reporting_intent.py)

```python
def _build_prompt(prd_text, context_text=""):
    parts = [_REPORTING_INTENT_ROLE]
    if context_text:
        parts.append(f"Company context (existing metrics/data objects/terms):\n{context_text}")
    parts.append(f"PRD / requirements:\n{prd_text}")
    ...
```

`context_text` is the exact same idea as `retrieve_company_context` in Part
1.9 — a seam for grounding the model in real company terminology so it
reuses existing metric names instead of coining near-duplicates — just
expressed as a plain string parameter instead of a Python function, because
this proc has no retrieval mechanism of its own to call (no outbound
network, remember). Whatever calls this stored procedure is responsible for
fetching that context first (e.g. from a Cortex Search service over a
metadata table) and passing it in as a plain argument. The signature is the
contract; the body stays swappable.

### 3.5 The newest example: `draft_diagram_brief.py` + Lucid MCP

**Files:**
[`snowflake/procs/draft_diagram_brief.py`](snowflake/procs/draft_diagram_brief.py),
[`snowflake/sql/lucid_mcp_setup.sql`](snowflake/sql/lucid_mcp_setup.sql),
[`snowflake/LUCID_DIAGRAM_AGENT.md`](snowflake/LUCID_DIAGRAM_AGENT.md)

This feature (draw a Lucidchart diagram from an approved PRD) is the
sharpest illustration in the whole repo of *"the constraint shapes the
design."* (See 1.11 for the pmagent-track twin of this same feature — same
idea, no Snowflake-managed connector runtime to lean on.) Two options
existed:

1. Call Lucid's REST API directly from a stored procedure, using a
   Snowflake `EXTERNAL ACCESS INTEGRATION` (network rule + secret) so the
   proc's own `requests` call can reach the internet.
2. Let a **Snowflake Cortex Agent** — a separate, newer Snowflake object
   type that sits *above* stored procedures — connect to Lucid's officially
   hosted MCP server as a native, governed tool, and never give the
   Python proc network access at all. (This uses **OAuth**, the standard
   protocol for "let Service A act on my behalf against Service B without
   ever handing Service A my Service-B password" — here, it's what lets
   Snowflake's Agent call Lucid's API under your identity.)

We took option 2, because the constraint in this repo right now is that
outbound network access for Cortex isn't available/approved — see the
architecture in `LUCID_DIAGRAM_AGENT.md`. So the design boundary became:

```python
# draft_diagram_brief.py's own docstring says it outright:
"""This proc does NOT talk to Lucid. It only turns an approved PRD ... into
a plain-language diagram description — the hand-off artifact a Cortex
Agent passes as the `description` argument to Lucid's own MCP
`create_diagram` tool once a human has confirmed it."""
```

The proc's job stops at producing text. The *Agent* (a different Snowflake
object, defined in `sql/lucid_mcp_setup.sql` via `CREATE AGENT ... FROM
SPECIFICATION`) is the only thing with a registered, OAuth-authenticated
path to the outside world (`CREATE EXTERNAL MCP SERVER` +
`CREATE API INTEGRATION`), and it's the one that actually calls Lucid.

> **Analogy:** think of the stored procedure as a research analyst who
> writes an extremely precise briefing memo, and the Agent as the
> diplomat who's the only one with a passport (OAuth credentials) allowed
> to actually cross the border (the public internet) and hand that memo
> to a foreign counterpart (Lucid's API). The analyst is not allowed to
> travel — full stop — no matter how good the memo is. That's not a
> workaround; it's the actual security boundary Snowflake enforces, and
> designing around it (rather than fighting it with a network-access
> request) is what made this feature buildable *today*.

Notice too where the Part 1.6 "human-gated write" idea reappears, and how
it gets *weaker* in this environment. In `pmagent/`, the gate is real code:
`create_jira_issue`'s docstring is read by the same process that's the only
thing capable of calling Jira, and there's a person in the loop by
construction (the LangGraph app is driven by a chat session). Here, the
gate is only ever a sentence in the Agent's `instructions` block:

```yaml
instructions:
  response: >
    ... Do NOT call any Lucid tool that creates, edits, or shares a
    document until the user has explicitly confirmed the brief is correct.
```

There's no code anywhere that *enforces* this — a model that ignores its
instructions could call Lucid's `create_diagram` tool immediately. This is
called out explicitly as a known limitation in `LUCID_DIAGRAM_AGENT.md`
rather than glossed over, because it's a real, honest gap versus the
Jira gate, not a detail to gloss over. **When you build something similar,
notice which of your safety rules are enforced by code your process
controls, and which ones are only ever a sentence in a prompt** — they are
not the same strength of guarantee.

### 3.6 Two different deployment mechanisms, on purpose

**Files:** [`snowflake/snowflake.yml`](snowflake/snowflake.yml),
[`snowflake/sql/lucid_mcp_setup.sql`](snowflake/sql/lucid_mcp_setup.sql)

`snowflake.yml` is a Snowflake CLI ("snow") project file. Each stored
procedure is declared as an `entity`:

```yaml
draft_diagram_brief:
  type: procedure
  identifier:
    name: DRAFT_DIAGRAM_BRIEF
    database: PO_AI_DEV
    schema: PO_AGENT
  handler: draft_diagram_brief.run
  signature:
    - name: prd_text
      type: string
    - name: reporting_intent_text
      type: string
  returns: string
  runtime: "3.11"
  stage: dev_deployment
  artifacts:
    - src: procs/
      dest: ./
```

Running `snow snowpark build && snow snowpark deploy` reads this file and
(re)creates each procedure. This only ever manages *procedures* — it has no
concept of an `API INTEGRATION` or an `AGENT`, which are account-level
objects, not Snowpark entities. Those get created by hand-running raw SQL
(`sql/lucid_mcp_setup.sql`), which needs a more privileged role
(`ACCOUNTADMIN` or similar), separately from the routine proc-deployment
workflow.

> **Analogy:** `snow snowpark deploy` is like restocking a shipping
> container with a versioned, repeatable manifest — you run it often, it's
> low-risk, and anyone on the team can do it. `lucid_mcp_setup.sql` is
> building the port infrastructure itself (the customs checkpoint, the
> OAuth "passport control") — a rare, higher-privilege, one-time-per-account
> operation you do carefully and deliberately, not something you fold into
> the routine deploy loop.

> **Staleness warning:** `CREATE EXTERNAL MCP SERVER` and Cortex Agents'
> MCP connector support are new (2026) Snowflake features with no stated GA
> status as of this writing. Treat the exact SQL in `sql/lucid_mcp_setup.sql`
> as "correct as researched," not as a stable API — re-check
> `docs.snowflake.com` before relying on it if much time has passed since
> this was written.

---

## Part 4 — The recipe, distilled (how to build your own)

Strip away the specific frameworks and you get a checklist that applies to
any LLM-based agent system:

1. **Design your shared state before writing any prompt.** What does each
   step need to read, and what does it need to write down for the next
   step? (`PMState`, `RequirementsState`.) If you can't answer that, you
   don't understand the workflow yet.
2. **Force structure at every hop between a model and the rest of your
   system.** Use native structured-output/tool-calling features if your
   provider has them; if it only gives you a text channel (like Cortex's
   `COMPLETE`), be extremely explicit in the prompt about the exact shape
   you want back, and parse defensively (`extract_json`).
3. **Never let an LLM compute a number a real decision depends on.** Write
   the deterministic function first (`compute_sprint_metrics`); let the
   model narrate over its output, not reproduce it from memory.
4. **Give the model tools instead of asking it to "just know" things.**
   A tool with a precise docstring (what it does, when to use it, when
   *not* to) beats hoping the model recalls the right fact.
5. **Reflection loops need two distinct roles and a hard cap.** A model
   critiquing its own single pass of work is weaker than a genuine
   Writer/Reviewer split with a real checklist (`SKILL.md`) both sides
   read — and always bound the loop (`MAX_PASSES`) so a stubborn
   disagreement can't run forever.
6. **Any action that writes to a system of record needs an explicit human
   confirmation step — and know whether that gate is code-enforced or only
   prompt-enforced.** The difference matters, and it's worth writing down
   which one you have (see 3.5).
7. **Build the "mock" and the "real" implementation behind one interface
   from day one.** It lets you develop and test the whole system before
   you have real credentials, and it stops "which mode am I in" logic from
   leaking into every call site (`JiraClient`).
8. **Put prompts and domain knowledge in editable files, not Python
   strings.** You'll iterate on wording far more than you'll iterate on
   control flow — don't make the two changes cost the same amount of
   effort.
9. **Stub seams you're not ready to build, but commit to the function
   signature now.** An empty-string default or a "no docs found" no-op is
   fine, as long as the *shape* of the call is the one your real
   implementation will eventually fill.
10. **Identify your hard constraints before you design, not after.** The
    entire architecture of Part 3.5 (proc writes text, Agent makes the
    call) exists because "Cortex has no outbound network" was treated as a
    fact to design around, not an obstacle to route past. Find your
    project's equivalent constraint early.

---

## Quick reference — "I want to change X"

| I want to... | Look at |
|---|---|
| Change how requests get routed to a specialist | `pmagent/state.py` (`RouteDecision`), `pmagent/agents/orchestrator.py` |
| Change what a PRD contains | `pmagent/state.py` (`PRD`), `pmagent/skills/prd/SKILL.md` |
| Change an agent's behavior/tone | the matching `pmagent/prompts/*.md` file — not the Python |
| Add a new specialist agent (pmagent) | copy `pmagent/agents/ticket_agent.py`'s shape: declare `TOOLS` + `SYSTEM_PROMPT`, wire with `agents/common.py` helpers |
| Wire a new external MCP server into a pmagent lane | follow `pmagent/tools/mcp_tools.py`'s pattern: one entry in `_SERVERS`, one `get_..._tools()` wrapper, composed via an async `build_tools()` in the lane (see `agents/diagram_agent.py`, and 1.11) |
| Change sprint risk thresholds | `compute_sprint_metrics` in `pmagent/tools/jira_tools.py` (plain code, not a prompt) |
| Point the app at real Jira | set `JIRA_BASE_URL`/`JIRA_EMAIL`/`JIRA_API_TOKEN` in `.env` — `JiraClient` flips automatically |
| Add a new Cortex stored-proc agent | copy `snowflake/procs/reporting_intent.py`'s shape: `_complete`/`_extract_json` via `cortex_common`, plain-Python formatting, register in `snowflake.yml` |
| Wire a new external tool into Cortex without giving a proc network access | follow `snowflake/sql/lucid_mcp_setup.sql`'s pattern: `API INTEGRATION` + `EXTERNAL MCP SERVER` + `CREATE AGENT` |
| Understand what's implemented vs. scaffolding | `CLAUDE.md`'s "Architecture" section is explicit about this |

## Glossary

- **Agent** — an LLM wired up to loop, use tools, and decide its own next
  step, as opposed to a single one-shot "ask, get an answer" call.
- **LangGraph** — a Python framework for building agent systems as an
  explicit graph of steps (nodes) and transitions (edges), with a shared,
  typed state object flowing through.
- **Reducer** — a merge function LangGraph uses to combine a node's partial
  state update into the existing state (e.g. `add_messages` appends instead
  of overwriting).
- **Pydantic `BaseModel`** — a Python class that defines a data shape and
  validates that data actually matches it; used here for both application
  state and forced LLM output shapes.
- **Closure / factory function** — a function whose job is to build and
  return *another* function, pre-customized with whatever arguments you
  passed in (`make_agent_node`, `make_tools_router`, `get_llm`). Lets you
  generate several similar functions from one template instead of copying
  code.
- **Structured output / tool calling** — a model provider feature that
  forces an LLM's response to conform to a given schema, instead of free
  text you'd otherwise have to parse.
- **Temperature** — the sampling knob controlling how random vs. repeatable
  an LLM's output is; `0` is as close to deterministic as it gets (good for
  routing/classification), higher values add variety (better for
  brainstorming, worse for anything that needs to be consistent).
- **ReAct loop** — Reason → Act (call a tool) → Observe (read the result) →
  repeat, until the model decides it's done.
- **Reflection / actor-critic loop** — one role produces work, a second,
  independent role critiques it against a checklist, and the first role
  revises — repeated until approved or a cap is hit.
- **Stored procedure** — a function stored and executed *inside* a
  database (here, inside Snowflake) rather than in an external application
  process — you call it like a SQL function, but its body can be a full
  Python program (via Snowpark, below).
- **Snowflake Cortex** — Snowflake's built-in LLM layer; `COMPLETE()` is a
  SQL function that sends a prompt to a model and returns text, callable
  from inside a stored procedure with no outbound internet access needed.
- **Snowpark** — the Python API/runtime for writing Snowflake stored
  procedures and UDFs in Python instead of SQL.
- **OAuth** — the standard protocol that lets one service act on your
  behalf against another service (e.g. Snowflake calling Lucid) without
  ever being handed your actual password for that other service.
- **API Integration** (Snowflake) — an account-level object where Snowflake
  stores the connection/authentication details (URL, OAuth settings) for
  an external service *once*, so procedures/agents can reference it by
  name instead of embedding credentials in code.
- **MCP (Model Context Protocol)** — an open standard letting an AI system
  discover and call a remote service's tools (e.g. "create a diagram") over
  a governed connection, without the calling system needing custom
  integration code for every provider.
- **Cortex Agent** — a Snowflake object (distinct from a stored procedure)
  that orchestrates tool calls — including calls to external MCP
  servers — under Snowflake's own OAuth/RBAC governance.
