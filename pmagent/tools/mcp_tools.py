"""
Standard pattern for pulling an external MCP server's tools into a LangGraph
agent lane.

Mirrors `../../snowflake/sql/lucid_mcp_setup.sql`: on the Snowflake track, a
Cortex Agent references an `EXTERNAL MCP SERVER` object and Snowflake's own
connector runtime handles the OAuth handshake + tool-call plumbing. There's no
Snowflake-managed connector here, so `langchain-mcp-adapters`
(`MultiServerMCPClient`) plays that role: it opens the MCP session, does tool
discovery, and wraps each remote tool as a LangChain `BaseTool` — the exact
same type the local `@tool`s in `jira_tools.py` are, so a lane can bind both
in one `TOOLS` list (see `agents/diagram_agent.py`).

Keep the client construction isolated in this module, for the same reason
`snowflake/procs/*.py` isolates `_complete(session, prompt)`: it's the one
piece that talks to an external system, so everything downstream (agent
lanes, prompts, tests) stays plain and doesn't need network access to reason
about.

The one wrinkle vs. a local `@tool`: discovery (`get_tools`) is async and
must happen once, ahead of time, not inside a request/response cycle — see
`agents/diagram_agent.py` for how a lane composes its final `TOOLS` list from
this. A lane that binds MCP tools also needs its graph run with
`.ainvoke`/`.astream`, since the tool calls themselves stay async all the way
through.

Adding a second MCP server later is one more entry in `_SERVERS` plus one
more `get_..._tools()` wrapper below — not a new pattern.
"""

from __future__ import annotations

from langchain_core.tools import BaseTool
from langchain_mcp_adapters.client import MultiServerMCPClient

from pmagent import env

# One entry per external MCP server this app talks to. Keys are the
# `server_name` passed to `client.get_tools(server_name=...)` — keep them
# stable, since lanes refer to a server by name, not by config shape.
_SERVERS = {
    "lucid": {
        "url": env.LUCID_MCP_URL,
        "transport": "streamable_http",
        # Only sent if a static OAuth client secret was configured; Lucid's
        # Dynamic Client Registration flow (the default) needs no header —
        # the first real tool call triggers a one-time per-user OAuth consent
        # instead. See ../../snowflake/LUCID_DIAGRAM_AGENT.md for both paths.
        "headers": (
            {"Authorization": f"Bearer {env.LUCID_MCP_AUTH_TOKEN}"}
            if env.LUCID_MCP_AUTH_TOKEN
            else {}
        ),
    },
}


def _client() -> MultiServerMCPClient:
    return MultiServerMCPClient(_SERVERS)


async def get_lucid_tools() -> list[BaseTool]:
    """Discover and wrap Lucid's MCP tools (search, create_diagram, share, ...).

    Call this once when a lane's tool list is built, not per-turn — it's a
    network round trip to Lucid's MCP endpoint for tool discovery, and the
    returned `BaseTool`s open a fresh MCP session on every call on their own.
    """
    return await _client().get_tools(server_name="lucid")
