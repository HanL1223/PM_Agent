"""
Sprint Agent.

Specialises in sprint health: completion %, blocked/stuck tickets, risk level,
and standup summaries.

Crucially, the *numbers* come from `compute_sprint_metrics` (plain Python) via
the `get_sprint_status` tool — the LLM only writes prose on top of them. This is
the blueprint's "LLM + deterministic systems" principle: never let the model do
the arithmetic that drives a delivery decision.

Like the Ticket Agent, this module only declares the toolset and prompt; the
graph wires the node + tools loop generically.
"""

from pmagent.prompts import prompts
from pmagent.tools.jira_tools import get_sprint_status

TOOLS = [get_sprint_status]
SYSTEM_PROMPT = prompts.sprint_agent_system_prompt