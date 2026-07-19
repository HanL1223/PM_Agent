"""
Shared building blocks for an "agent lane".

Every specialist in this MVP follows the exact pattern the template uses for its
single agent: an LLM node that may emit tool calls, a `ToolNode` that executes
them, and a router that loops back to the LLM until there are no more tool calls.

Rather than copy that wiring three times, we factor the two reusable pieces here:

- `make_agent_node`: builds the LLM-calling node function for a lane.
- `make_tools_router`: builds the conditional-edge function for a lane.
"""

from collections.abc import Callable

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import SystemMessage
from langgraph.graph import END

from pmagent.state import PMState


def make_agent_node(system_prompt:str, llm_with_tools:BaseChatModel) -> callable:
    """
    Return a graph node that calls the LLM with a fixed system prompt.

    The node prepends the lane's system prompt to the running conversation and
    invokes the (tool-bound) model. The model's reply — which may contain tool
    calls — is appended to `messages` via the state reducer.

    Args:
        system_prompt: The lane's instructions.
        llm_with_tools: An LLM already bound to this lane's tools.

    Returns:
        A `node(state) -> partial_state` function for `StateGraph.add_node`.
    """

    def node(state:PMState) -> dict:
        response = llm_with_tools.invoke(
            [SystemMessage(content=system_prompt)] + state.messages
        )
        # Returning a partial dict lets the `add_messages` reducer append rather
        # than overwrite — cleaner than mutating `state` directly.
        return {"messages": [response]}

    return node

def make_tools_router(tools_node_name: str) -> Callable:
    """
    Return a conditional-edge function: go to this lane's tools node if the
    last message requested tool calls, otherwise end the turn.

    Args:
        tools_node_name: The graph node name of this lane's `ToolNode`.

    Returns:
        A `router(state) -> str` function returning either `tools_node_name`
        or the `END` sentinel.
    """
    def router(state: PMState) -> str:
        last_message = state.messages[-1]
        if getattr(last_message, "tool_calls", None):
            return tools_node_name
        return END

    return router
