"""
Orchestrator agent.

In this MVP the orchestrator wears two hats (kept as two small functions):

1. `classify_node` — a pure router. It uses the model's *structured output* to
   put the user's request into one of three buckets (ticket / sprint / query)
   and writes that to `state.route`. It deliberately does NOT touch `messages`,
   so the specialist lane sees the original user request untouched.

2. The **query lane** — when the request is a general read-only look-up, the
   orchestrator answers it itself using read-only Jira tools. The node + tools
   for this lane are assembled in `graph.py` from `QUERY_TOOLS` and
   `prompts.orchestrator_system_prompt`.

This matches the blueprint: a central coordinator that routes work and handles
simple queries directly, while delegating real work to specialists.
"""

from pmagent.llm import get_llm
from pmagent.state import PMState, RouteDecision
from pmagent.tools.jira_tools import search_jira_issues

# The query lane only ever reads from Jira — never creates anything.
QUERY_TOOLS = [search_jira_issues]

def classify_node(state:PMState) -> dict:
    """
    Classify the latest user message into a route.

    Uses `with_structured_output` so the model must return a valid
    `RouteDecision` (no brittle text parsing). Temperature 0 for determinism.
    """

    #Find the most recent human message to classify
    user_test = ""
    for msg in reversed(state.messages):
        if msg.type =='human':
            user_test = msg.content
            break
    router_llm = get_llm(temperature=0).with_structured_output(RouteDecision)

    decision:RouteDecision = router_llm.invoke(
        "Classify this project-management request into exactly one route. \n\n"
        f"Request: {user_test}"
    )

    # Only update `route`; leave `messages` alone so the specialist gets a clean
    # conversation.
    return {"route": decision.route}

def route_from_classifier(state: PMState) -> str:
    """
    Conditional-edge function: map `state.route` to a lane node name.

    The returned strings must match the node names registered in `graph.py`.
    """
    return {
        "ticket": "ticket_agent",
        "sprint": "sprint_agent",
        "query": "query_agent",
    }[state.route]
