"""
Diagram Agent — the pmagent-track twin of the Snowflake Cortex `diagram_agent`
in `../../snowflake/sql/lucid_mcp_setup.sql`.

Same shape as `ticket_agent.py`/`sprint_agent.py`: this module only declares
*what the agent is* (its toolset and system prompt); the node + tools-loop
wiring is assembled generically wherever the graph gets built, using the
helpers in `agents/common.py`.

The one difference from those two lanes is the toolset itself: alongside the
local `draft_diagram_brief` tool, this lane binds tools discovered from
Lucid's hosted MCP server at runtime (`pmagent/tools/mcp_tools.py` — the
reference pattern for wiring *any* MCP server into a lane going forward).
That discovery call is async network I/O, so it can't be a module-level
`TOOLS = [...]` constant like the other lanes — it has to run once, awaited,
before the graph is built:

    from pmagent.agents.diagram_agent import build_tools, SYSTEM_PROMPT
    from pmagent.llm import get_llm

    tools = await build_tools()                       # once, at startup
    llm_with_tools = get_llm().bind_tools(tools)
    # ... wire llm_with_tools + tools into a node/ToolNode pair as usual ...
    # graph.ainvoke(...) — not .invoke() — since the Lucid tools stay async
    # all the way through.

LOCAL_TOOLS is still exported as a plain constant so anything that only needs
the local half (e.g. a unit test for `draft_diagram_brief`) doesn't have to
touch MCP or the network at all.
"""

from pmagent.prompts import prompts
from pmagent.tools.diagram_tools import draft_diagram_brief
from pmagent.tools.mcp_tools import get_lucid_tools

LOCAL_TOOLS = [draft_diagram_brief]
SYSTEM_PROMPT = prompts.diagram_agent_system_prompt


async def build_tools() -> list:
    """Return this lane's full toolset: local tools + Lucid's MCP tools.

    Call once when the graph is built, not per-turn — see `mcp_tools.py`.
    """
    return [*LOCAL_TOOLS, *(await get_lucid_tools())]
