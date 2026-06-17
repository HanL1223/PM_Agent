"""
Ticket Agent.

Specialises in backlog quality: it drafts a well-formed Jira issue from a plain
requirement and creates it only after the user approves.

This module just declares *what the agent is* — its toolset and system prompt.
The node + tools-loop wiring is assembled generically in `graph.py` using the
helpers in `agents/common.py`. Keeping the "what" and the "how it's wired"
separate is what makes adding a fourth agent later a copy-paste-and-tweak job.

Note the toolset: read (`search_jira_issues`) for duplicate/context lookup, and
write (`create_jira_issue`) for the gated creation step. The prompt enforces the
human-approval-before-write rule.
"""

from pmagent.prompts import prompts
from pmagent.tools.jira_tools import search_jira_issues, create_jira_issue


TOOLS = [search_jira_issues, create_jira_issue]
SYSTEM_PROMPT = prompts.ticket_agent_system_prompt
